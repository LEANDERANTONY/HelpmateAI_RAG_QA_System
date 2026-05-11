// Build a short search anchor from chunk text for PDF.js find.
//
// Why a prefix and not the whole chunk?
// PDF.js's findController is whitespace-tolerant but doesn't do fuzzy
// matching across line breaks, hyphenation, ligatures, or column
// boundaries. A long chunk text rarely matches verbatim in the
// rendered PDF text stream. A ~80-char prefix is short enough to
// match cleanly but long enough to be unique within most documents.
//
// Why strip leading boilerplate?
// Chunks often start with metadata: a bare page number ("Page 7"),
// a section heading on its own line, or a citation marker. None of
// those reliably appear in the rendered PDF — they're artefacts of
// how our chunker stitched extracted text. Skipping them gets us to
// the real body text where PDF find has a fighting chance.

/**
 * Whether a line looks like leading boilerplate we should skip before
 * picking the search anchor. Conservative — only the patterns we've
 * actually seen at the start of chunks in the corpus.
 */
function looksLikeBoilerplate(line: string): boolean {
  // "Page 7", "PAGE 7", "7", "7 / 24"
  if (/^(page\s+)?\d+\s*(\/\s*\d+)?\s*$/i.test(line)) {
    return true;
  }
  // Lone section heading: short line, no terminal punctuation, no
  // sentence-mid commas. Title-case-ish (starts with capital, mostly
  // letters/spaces). Avoids matching real first sentences which tend
  // to be longer and end with a period.
  if (line.length <= 60 && /^[A-Z0-9]/.test(line)) {
    const noEndPunct = !/[.!?]$/.test(line);
    const wordCount = line.split(/\s+/).filter(Boolean).length;
    if (noEndPunct && wordCount >= 1 && wordCount <= 8) {
      return true;
    }
  }
  // Citation markers like "[12]" or "(Smith, 2020)" on their own line.
  if (/^\s*[\[(][^\])]{1,40}[\])]\s*$/.test(line)) {
    return true;
  }
  return false;
}

const ANCHOR_TARGET_LEN = 80;
const ANCHOR_MIN_LEN = 40;

/**
 * Extract a short, distinctive substring of chunk text suitable for
 * PDF.js find. Returns the empty string only if chunkText is empty
 * after cleanup — otherwise some non-empty anchor is always produced.
 */
export function buildSearchAnchor(chunkText: string): string {
  if (!chunkText) {
    return "";
  }
  const lines = chunkText.split(/\r?\n/);

  // Skip blank lines and boilerplate lines from the front. Stop as soon
  // as we hit a "real" content line; the rest of the chunk is its body.
  let bodyStart = 0;
  for (let i = 0; i < lines.length; i++) {
    const trimmed = lines[i].trim();
    if (!trimmed) {
      bodyStart = i + 1;
      continue;
    }
    if (looksLikeBoilerplate(trimmed)) {
      bodyStart = i + 1;
      continue;
    }
    bodyStart = i;
    break;
  }

  // Re-join from the first non-boilerplate line. Collapse whitespace
  // (including newlines) into single spaces — PDF.js's text extraction
  // produces a single string per page, so multiple runs of whitespace
  // in the query just hurt match likelihood.
  const body = lines.slice(bodyStart).join(" ");
  const normalized = body.replace(/\s+/g, " ").trim();

  if (normalized.length <= ANCHOR_TARGET_LEN) {
    return normalized;
  }

  // Truncate to ~80 chars, then walk back to the previous word boundary
  // so we don't cut a word in half (PDF.js can match the broken word
  // but it's a perf-and-precision hit). If the boundary is too close
  // to the start (<= 40 chars), keep the hard cut — that means the
  // chunk's first 80 chars is one giant word like a URL.
  const head = normalized.slice(0, ANCHOR_TARGET_LEN);
  const lastSpace = head.lastIndexOf(" ");
  if (lastSpace >= ANCHOR_MIN_LEN) {
    return head.slice(0, lastSpace);
  }
  return head;
}

/**
 * Parse our `"Page N"` page-label format into a 1-based page number.
 * Returns 1 (a safe default for `viewer.currentPageNumber`) when the
 * label is missing, malformed, or just `"Document"` (the fallback
 * label our ingest produces for DOCX files with no real pagination).
 */
export function parsePageLabel(label: string | null | undefined): number {
  if (!label) return 1;
  const match = /\d+/.exec(label);
  if (!match) return 1;
  const n = parseInt(match[0], 10);
  if (!Number.isFinite(n) || n < 1) return 1;
  return n;
}
