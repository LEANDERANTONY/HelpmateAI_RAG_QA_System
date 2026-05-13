# ADR-014: In-App Source Viewer With PDF.js And LibreOffice DOCX Rendition

Date: 2026-05-12

Status: Shipped

## Context

The "Open in source" affordance on every evidence card was a dead link until this work. The product's verification loop ran citation pill → evidence card → done. The final step — confirming the cited paragraph against the original document — required the user to download the file and find the page themselves. That broke the verification posture HelpmateAI sells, and it left the most discriminating users (the ones who actually check citations) doing the rest of the work in another window.

Three constraints shaped the build:

- The source verification has to happen inside the workspace, not in a separate tab, because losing the conversation context makes a follow-up question harder
- PDF and DOCX uploads have to behave identically from the user's perspective; the viewer cannot ship as PDF-only and ask DOCX users to "download to view"
- The mobile experience has to be a first-class part of the design, not a fallback, because document QA is a use case people genuinely reach for on phones

## Decision

Build the source viewer as a layout posture ("Read Mode"), not a per-citation modal, and unify the format handling at ingest so the viewer only ever loads PDF.

### Backend

Add `GET /documents/{document_id}/file` that:

- Is auth-gated by the same Supabase JWT pattern as `/workspace/current` and `/qa`
- Streams the source file with `Content-Type` set per extension (PDF or DOCX)
- Supports HTTP `Range` requests via Starlette's `FileResponse`, which PDF.js requires for progressive rendering on large documents
- Returns the PDF rendition by default (`Content-Disposition: inline`) so PDF.js can render it
- Returns the original source format under `?download=1` (`Content-Disposition: attachment`) so DOCX users can still download their original
- Returns `415 Unsupported Media Type` when an inline request lands on a legacy DOCX record with no rendition, so the frontend can fall back to a "download to view" affordance

Rename uploads on disk to `{document_id}{ext}` so collision-safe storage holds for repeated uploads, and store a `viewable_pdf_path` field on the document record. For PDF uploads it aliases the source path. For DOCX uploads it points at a sibling PDF produced by LibreOffice at ingest.

DOCX → PDF conversion runs through:

```
libreoffice --headless --norestore --nologo --nodefault --nolockcheck \
            --convert-to pdf --outdir <output_dir> <input_path>
```

with a configurable timeout (default 60s) and a wrapper that resolves the `soffice` binary via `shutil.which` so a missing install fails fast with a clear error rather than a `FileNotFoundError`. The conversion failure path is tolerant: a corrupt DOCX still indexes from extracted text and only loses the inline viewer, surfacing as a 415 the frontend handles.

The Docker base image installs `libreoffice-core` and `libreoffice-writer` (~400MB), not the full LibreOffice suite, to keep the production image lean.

### Frontend

Build Read Mode as a layout state on top of the existing workspace, not a separate route. While in Read Mode:

- Desktop (≥901px): the workspace grid switches to a two-pane chat-left, source-right layout (~45/55 split). The document strip and evidence rail hide. Citation pills in any answer scroll the source viewer to that chunk.
- Mobile (≤900px): the source becomes a Vaul-based draggable bottom sheet with three snap points — `FULL` (covers screen), `SPLIT` (~55% from bottom, the default working posture), `COMPACT` (~25%, auto-snapped to when the soft keyboard is up). User-initiated drag is constrained to `FULL ↔ SPLIT`; `COMPACT` is keyboard-only.

Wrap `pdfjs-dist` in a lazy-loaded module facade that:

- Imports the core + viewer bundles via dynamic `import()` so PDF.js only ships when Read Mode opens
- Hosts the worker as a static asset at `/pdf.worker.min.mjs` via a postinstall copy script
- Attaches the Supabase access token to every `getDocument` request through `httpHeaders.Authorization`
- Refreshes the session and retries once on a `401` response from the file endpoint

Drive navigation with a hint-page + window strategy rather than blind whole-document search:

1. Parse the chunk's `page_label` to a 1-based page index and scroll there for the fast first paint
2. Build a short search anchor from the chunk text — first ~80 characters of body text, with leading boilerplate (page numbers, lone section headings, citation markers) stripped, whitespace normalized, truncated at the last word boundary down to a 40-character floor
3. Dispatch a `find` against the whole document with `highlightAll: true` and `phraseSearch: true`
4. On `updatefindmatchescount`, walk the `±3` page ring around the hint page in expanding order `[0, +1, -1, +2, -2, +3, -3]` and scroll to the first page with a non-empty match list
5. Fall back to a soft banner ("Showing Page N. We couldn't pinpoint the exact passage — try scrolling a page or two in either direction.") when nothing matches in the window, rather than jumping to a far-page false-positive

Wire the workspace's existing citation pill handler to branch on Read Mode state: in normal mode the pill flashes the evidence card in the right rail; in Read Mode it calls `setCurrentChunk` on the viewer store, which re-runs the find pipeline against the new chunk without remounting the PDF.

Auto-jump the viewer to the first evidence of every new answer while in Read Mode, even when the user has manually scrolled away — the contract is "the source follows the conversation". Manual page navigation uses an imperative `scrollToPage` handle that does not re-run find, so the user's session-local navigation does not fight the anchor highlight.

### What we deliberately did not build

- A Tier 1 "open PDF in new browser tab" affordance. The viewer is fully inline. Opening a new tab destroys the conversation context the product is built around.
- A Tier 3 bounding-box-precise highlight via Docling-extracted coordinates at ingest. The text-find based highlight is adequate for the dominant prose-heavy use case (policy, contracts, manuals, reports) and avoids a 1-2 second per page indexing cost.
- Pinch-to-zoom on mobile. PDF.js's native pinch-zoom is janky; the viewer can be opened at full screen via the `FULL` snap if a user needs maximum reading area.
- Persisting Read Mode posture across page reloads or remembering scroll position on re-entry. Each Read Mode session starts fresh at the current chunk.
- An automatic Read Mode trigger on citation pill click. Read Mode is an explicit user opt-in via "Open in source"; pills retain their lightweight evidence-card flash in normal mode.

## Consequences

The verification loop is visible end-to-end inside the workspace. The user can now see the citation pill, see the evidence card, and see the cited passage on its actual page in the actual document, without leaving the conversation. This makes the abstention story (ADR-011) more credible, because the user can confirm by sight rather than by trust.

The Docker image grows by roughly 400MB due to LibreOffice. The image build time grows by roughly 30-45 seconds on cold cache. Both are one-time costs that show up at deploy, not at request time.

DOCX uploads add 2-5 seconds of ingest latency (LibreOffice conversion runs inline). For policy and contract corpora the trade-off pays for itself because those formats are common and the unified viewer experience is more important than the latency.

The new endpoint is the first non-trivial binary-data path the API serves. CORS, `Range`, auth, and `Content-Disposition` semantics all needed to land together. The Caddy reverse proxy in `deploy/vps/` passes range headers through without further configuration.

In production behind Cloudflare, the `app.helpmateai.xyz/api/*` rewrite-via-Vercel-edge path to `api.helpmateai.xyz` triggers Cloudflare's bot challenge on data-center origins. The PDF viewer code calls `API_BASE_URL` directly (`https://api.helpmateai.xyz/documents/{id}/file` in production) so the browser hits Cloudflare with a residential IP and passes through. This is documented inline in `pdf-viewer.tsx` and is the same direct-URL pattern `api.ts` already uses for non-binary endpoints.

The "Download Original" buttons still use `window.open`, which cannot attach a bearer token. They currently fail with a 401 in production. The known follow-up is a `fetch` + `Blob` + `anchor[download]` refactor so the Authorization header flows; that is filed as a follow-up rather than a blocker because the inline viewer is the headline feature and the download is a secondary affordance.

## Validation

Manual smoke tests cover the dominant flows:

- PDF upload + ask + Open in source: viewer lands on the cited page with the chunk highlighted in `--accent-soft`
- DOCX upload + ask + Open in source: the LibreOffice rendition loads transparently and behaves identically to the PDF case
- Mobile sheet drag: `FULL ↔ SPLIT` works smoothly; drag past `SPLIT` is intercepted and snaps back, with `COMPACT` reachable only via soft-keyboard appearance
- New answer auto-jump: the source viewer scrolls to the first evidence of every new answer, including when the user has manually navigated to a different page
- Citation pill click in Read Mode: scrolls the viewer; same pill in normal mode flashes the evidence card
- Session expiry mid-read: a `401` on the initial `getDocument` triggers a `refreshSession()` and retries once; if the retry also fails, the viewer shows the "Session timed out" banner with a reload action

Known limits:

- the `±3` window assumes the chunker did not place a chunk anchor more than three pages off its `page_label`; in practice this only fails on documents with mid-paragraph page boundaries combined with LibreOffice pagination drift
- range requests issued by PDF.js during streaming bypass the loader's refresh-and-retry path; a token that expires mid-read surfaces as a generic transport error, not the auth-specific banner
- the find pipeline depends on PDF.js's text extraction matching the anchor text; rendered glyph differences (ligatures, special spaces) can produce zero matches on otherwise valid passages, which falls through to the soft "Showing Page N" banner rather than a hard failure

A follow-up smoke audit confirmed the find pipeline lands on the correct page for typical prose chunks against the FOMC minutes, NIST AI RMF, and a public UPenn thesis from the held-out eval suite.
