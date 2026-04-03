from __future__ import annotations

import json
from pathlib import Path

from src.config import Settings
from src.schemas import DocumentRecord, IndexRecord


class ApiRecordStore:
    def __init__(self, settings: Settings):
        self.root = settings.data_dir / "api_state"
        self.documents_dir = self.root / "documents"
        self.indexes_dir = self.root / "indexes"
        self.documents_dir.mkdir(parents=True, exist_ok=True)
        self.indexes_dir.mkdir(parents=True, exist_ok=True)

    def _document_path(self, document_id: str) -> Path:
        return self.documents_dir / f"{document_id}.json"

    def _index_path(self, document_id: str) -> Path:
        return self.indexes_dir / f"{document_id}.json"

    def save_document(self, document: DocumentRecord) -> None:
        self._document_path(document.document_id).write_text(
            json.dumps(document.to_dict(), indent=2),
            encoding="utf-8",
        )

    def save_index(self, index_record: IndexRecord) -> None:
        self._index_path(index_record.document_id).write_text(
            json.dumps(index_record.to_dict(), indent=2),
            encoding="utf-8",
        )

    def get_document(self, document_id: str) -> DocumentRecord | None:
        path = self._document_path(document_id)
        if not path.exists():
            return None
        return DocumentRecord(**json.loads(path.read_text(encoding="utf-8")))

    def get_index(self, document_id: str) -> IndexRecord | None:
        path = self._index_path(document_id)
        if not path.exists():
            return None
        return IndexRecord(**json.loads(path.read_text(encoding="utf-8")))
