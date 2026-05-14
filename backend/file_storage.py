"""File storage abstraction for uploaded document bytes.

Decouples the upload / read / delete code paths from where the bytes actually
live. Two backends ship today; a third is documented but not implemented:

  • LocalFileStorage    Files live on the VPS disk under HELPMATE_UPLOADS_DIR.
                        Mirrors the original (pre-migration) behavior. Default
                        for development; fine for small-scale production.

  • SupabaseFileStorage Files live in a Supabase Storage bucket. Designed for
                        production where "Unlimited documents" must not fill
                        up the VPS disk. Reads are served via 302 redirects
                        to short-lived signed URLs so PDF.js can stream-render
                        with Range requests directly from the Supabase CDN —
                        zero bytes through our VPS for the read path.

  • (Future) R2FileStorage   Cloudflare R2 wins on egress ($0 vs $0.09/GB) at
                             larger scale. Drop-in replacement for the
                             Supabase backend when storage bills bite.

Backend selection is env-toggleable (HELPMATE_FILE_STORAGE_BACKEND) so dev
boxes can stay on local while prod runs Supabase. The choice lives in
`src/config.py` next to the existing `state_store_backend` /
`vector_store_backend` knobs that follow the same pattern.

KEY semantics
-------------
The "key" returned by save() is OPAQUE to callers. For the local backend
it happens to be an absolute filesystem path. For Supabase it's a
bucket-relative path like `<owner_id>/<document_id>.pdf`. Callers must
round-trip keys through this interface — never parse them, never assume
they map cleanly to a filesystem path.

DocumentRecord.source_path and DocumentRecord.viewable_pdf_path both hold
a storage key in this sense. Their dual nature (absolute path for local,
bucket key for Supabase) is the price of avoiding a schema migration on
DocumentRecord. If we ever rename the fields, "storage_key" would be more
honest.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
import logging
import tempfile

logger = logging.getLogger(__name__)


def _content_type_for(suffix: str) -> str:
    """MIME type for the file extension. Used by SupabaseFileStorage so the
    Storage CDN serves the right Content-Type header (PDF.js + browsers
    look at this to decide whether to render inline or download)."""
    s = suffix.lower().lstrip(".")
    return {
        "pdf": "application/pdf",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }.get(s, "application/octet-stream")


class FileStorage(ABC):
    """Storage interface. All file bytes for uploaded documents flow through
    this — never call FileSystem APIs or Supabase Storage SDK directly from
    the route layer. The pipeline service is allowed to during ingestion
    because it needs a local Path for parsing; it should hand the bytes back
    to the storage layer after normalize_upload_paths."""

    @abstractmethod
    def save_bytes(
        self,
        *,
        owner_id: str,
        document_id: str,
        suffix: str,
        payload: bytes,
    ) -> str:
        """Persist raw bytes. Returns the storage key for later retrieval."""

    @abstractmethod
    def save_from_path(
        self,
        *,
        owner_id: str,
        document_id: str,
        source: Path,
    ) -> str:
        """Persist an existing local file (e.g., the LibreOffice-produced
        viewable PDF rendition). The local file may or may not be cleaned up
        depending on backend — Supabase deletes it after upload to free the
        tempdir; local leaves it in place since it IS the storage."""

    @abstractmethod
    def get_signed_url(
        self,
        key: str,
        *,
        expires_in: int = 3600,
        download: bool = False,
        filename: str | None = None,
    ) -> str | None:
        """URL the browser can fetch directly. Returns None for backends
        without URL-based reads (local) — callers fall back to streaming
        via open_for_read()."""

    @abstractmethod
    def delete(self, key: str) -> None:
        """Remove the file. Safe to call on a non-existent key (no-op).
        Logs but does not raise — cleanup is best-effort because the
        document metadata has already been deleted by the time we get here."""

    @abstractmethod
    def exists(self, key: str) -> bool:
        """True if the file is currently in storage."""

    # open_for_read is concrete on the base class because both backends
    # implement it as "yield a local Path" — local trivially, remote via a
    # tempfile download. Subclasses override the private helper instead.
    @contextmanager
    def open_for_read(self, key: str) -> Iterator[Path]:
        """Context manager yielding a local Path the caller can read from.
        For remote backends downloads to a tempfile (cleaned up on exit).
        For local returns the file path directly. Used by re-indexing /
        DOCX rendition / any pipeline step that needs the raw bytes."""
        path = self._resolve_local(key)
        try:
            yield path
        finally:
            self._cleanup_local(key, path)

    @abstractmethod
    def _resolve_local(self, key: str) -> Path:
        """Internal: return a local Path that contains the file's bytes.
        Local backend returns the storage path directly. Remote backends
        download to a tempfile."""

    def _cleanup_local(self, key: str, path: Path) -> None:
        """Internal: called after open_for_read yields. Default no-op
        (local backend); remote backends override to unlink the tempfile."""
        del key, path


class LocalFileStorage(FileStorage):
    """Filesystem-backed storage. Files land at
    `<uploads_dir>/<document_id><suffix>`. Keys are absolute paths so that
    legacy DocumentRecords (whose source_path predates this abstraction)
    keep resolving — _resolve_local for the local backend is essentially
    `Path(key)`."""

    def __init__(self, uploads_dir: Path):
        self.uploads_dir = uploads_dir
        self.uploads_dir.mkdir(parents=True, exist_ok=True)

    def save_bytes(
        self,
        *,
        owner_id: str,
        document_id: str,
        suffix: str,
        payload: bytes,
    ) -> str:
        # owner_id is unused for local — single-user dev DX. Kept in the
        # signature so callers don't branch on the backend.
        del owner_id
        target = self.uploads_dir / f"{document_id}{suffix.lower()}"
        target.write_bytes(payload)
        return str(target)

    def save_from_path(
        self,
        *,
        owner_id: str,
        document_id: str,
        source: Path,
    ) -> str:
        del owner_id
        target = self.uploads_dir / f"{document_id}{source.suffix.lower()}"
        if source.resolve() == target.resolve():
            return str(target)
        # Move rather than copy — caller passed ownership of `source`.
        target.write_bytes(source.read_bytes())
        try:
            source.unlink()
        except OSError:
            pass
        return str(target)

    def get_signed_url(
        self,
        key: str,
        *,
        expires_in: int = 3600,
        download: bool = False,
        filename: str | None = None,
    ) -> str | None:
        # Local backend doesn't serve URLs — the route layer streams the
        # file via FileResponse (Range-aware) on this same path.
        del key, expires_in, download, filename
        return None

    def delete(self, key: str) -> None:
        try:
            path = Path(key)
            if path.exists() and path.is_file():
                path.unlink()
        except OSError as exc:
            logger.warning("Local delete failed for %s: %s", key, exc)

    def exists(self, key: str) -> bool:
        path = Path(key)
        return path.exists() and path.is_file()

    def _resolve_local(self, key: str) -> Path:
        path = Path(key)
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(f"Local file missing: {key}")
        return path


class SupabaseFileStorage(FileStorage):
    """Supabase Storage backend. Files live under `<owner_id>/<document_id><ext>`
    inside the configured private bucket. Reads are served via 302 redirects
    to short-lived signed URLs — the browser fetches directly from Supabase's
    CDN with Range support, so PDF.js streaming works without proxying bytes
    through our VPS.

    Key format `<owner_id>/<document_id><ext>` partitions storage by user, so
    user-deletion can clean up an entire prefix in one call without a row-by-
    row scan."""

    def __init__(self, client: Any, bucket: str):
        self.client = client
        self.bucket = bucket
        # Cache the storage-namespace handle so we don't go through the
        # client.storage.from_() factory on every call.
        self._store = client.storage.from_(bucket)

    def _key(self, owner_id: str, document_id: str, suffix: str) -> str:
        return f"{owner_id}/{document_id}{suffix.lower()}"

    def save_bytes(
        self,
        *,
        owner_id: str,
        document_id: str,
        suffix: str,
        payload: bytes,
    ) -> str:
        key = self._key(owner_id, document_id, suffix)
        # upsert=true so re-uploading the same document_id (e.g. on re-index
        # of the same fingerprint) replaces in place rather than 409'ing.
        self._store.upload(
            key,
            payload,
            file_options={
                "upsert": "true",
                "content-type": _content_type_for(suffix),
                # cache-control on the CDN — 1 hour is fine since we issue
                # signed URLs that expire on a similar timescale; longer
                # values would survive past the URL's TTL anyway.
                "cache-control": "3600",
            },
        )
        return key

    def save_from_path(
        self,
        *,
        owner_id: str,
        document_id: str,
        source: Path,
    ) -> str:
        return self.save_bytes(
            owner_id=owner_id,
            document_id=document_id,
            suffix=source.suffix,
            payload=source.read_bytes(),
        )

    def get_signed_url(
        self,
        key: str,
        *,
        expires_in: int = 3600,
        download: bool = False,
        filename: str | None = None,
    ) -> str | None:
        options: dict[str, Any] = {}
        if download:
            # Supabase honors a "download" option in create_signed_url to
            # force Content-Disposition: attachment with the given filename.
            # If filename is None, the bucket-side name is used as-is.
            options["download"] = filename or True
        try:
            response = self._store.create_signed_url(
                key,
                expires_in,
                options or None,
            )
        except Exception as exc:
            logger.error("Supabase signed-url failed for %s: %s", key, exc)
            return None
        # supabase-py's response shape has shifted across versions. Handle
        # both the dict-of-string-keys and object-attribute shapes.
        if isinstance(response, dict):
            return response.get("signedURL") or response.get("signed_url")
        return getattr(response, "signed_url", None) or getattr(response, "signedURL", None)

    def delete(self, key: str) -> None:
        try:
            self._store.remove([key])
        except Exception as exc:
            logger.warning("Supabase delete failed for %s: %s", key, exc)

    def exists(self, key: str) -> bool:
        try:
            # supabase-py has no direct exists() — list the parent prefix
            # and check the filename. Cheap because Supabase Storage's list
            # endpoint indexes by prefix.
            parent, _, filename = key.rpartition("/")
            result = self._store.list(parent or "")
            if not isinstance(result, list):
                return False
            return any(
                isinstance(item, dict) and item.get("name") == filename
                for item in result
            )
        except Exception:
            return False

    def _resolve_local(self, key: str) -> Path:
        # Download the bytes to a tempfile so callers (DOCX renderer,
        # re-indexer) can use a plain Path interface.
        try:
            payload = self._store.download(key)
        except Exception as exc:
            raise FileNotFoundError(f"Supabase storage missing {key}: {exc}")
        suffix = Path(key).suffix or ".bin"
        # delete=False on the NamedTemporaryFile so we can yield it, then
        # clean up explicitly in _cleanup_local (NTF auto-deletes on close
        # on POSIX but is closed-and-deleted on Windows differently —
        # explicit lifecycle keeps it cross-platform).
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(payload)
            tmp_path = Path(tmp.name)
        return tmp_path

    def _cleanup_local(self, key: str, path: Path) -> None:
        del key
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def build_file_storage(settings: Any) -> FileStorage:
    """Factory: pick the backend based on settings.file_storage_backend.

    Lazy-imports the Supabase client so dev environments without the
    supabase package installed don't blow up at import time — the local
    backend has zero Supabase dependency.
    """
    backend = getattr(settings, "file_storage_backend", "local")
    if backend == "supabase":
        from src.cloud.supabase import create_supabase_client

        client = create_supabase_client(
            settings.supabase_url, settings.supabase_key
        )
        bucket = settings.supabase_storage_bucket
        if not bucket:
            raise RuntimeError(
                "HELPMATE_SUPABASE_STORAGE_BUCKET must be set when "
                "HELPMATE_FILE_STORAGE_BACKEND=supabase."
            )
        return SupabaseFileStorage(client, bucket)
    return LocalFileStorage(settings.uploads_dir)
