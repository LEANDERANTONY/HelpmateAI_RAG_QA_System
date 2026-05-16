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
 * Unambiguous leading boilerplate that is ALWAYS safe to skip: a bare
 * page number ("Page 7", "7 / 24") or a lone citation marker
 * ("[12]", "(Smith, 2020)"). These are chunker stitching artefacts
 * that never appear as body text in the rendered PDF, so skipping
 * them is unconditionally correct.
 */
function isHardBoilerplate(line: string): boolean {
  // "Page 7", "PAGE 7", "7", "7 / 24"
  if (/^(page\s+)?\d+\s*(\/\s*\d+)?\s*$/i.test(line)) {
    return true;
  }
  // Citation markers like "[12]" or "(Smith, 2020)" on their own line.
  if (/^\s*[\[(][^\])]{1,40}[\])]\s*$/.test(line)) {
    return true;
  }
  return false;
}

/**
 * Whether a line *looks* like a lone section heading: short,
 * title-case-ish, few words, no terminal sentence punctuation.
 *
 * This is INTENTIONALLY only advisory. These bounds also match a real
 * short first sentence ("The motion carried", "Risk factors apply"),
 * so the caller skips a heading-looking line ONLY when genuine body
 * text follows it. If it is the chunk's only/last content it IS the
 * body and must be kept — otherwise the anchor starts mid-chunk or
 * empties entirely (the over-stripping bug this guards against).
 */
function looksLikeHeading(line: string): boolean {
  if (line.length > 60 || !/^[A-Z0-9]/.test(line)) {
    return false;
  }
  if (/[.!?]$/.test(line)) {
    return false;
  }
  const wordCount = line.split(/\s+/).filter(Boolean).length;
  return wordCount >= 1 && wordCount <= 8;
}

const ANCHOR_TARGET_LEN = 80;
const ANCHOR_MIN_LEN = 40;

/**
 * Strip leading boilerplate and collapse whitespace, returning the
 * chunk's normalized body (single-spaced, newline-free) — the common
 * preprocessing both anchor builders share. Empty string only when
 * nothing survives cleanup.
 */
function extractNormalizedBody(chunkText: string): string {
  if (!chunkText) {
    return "";
  }
  const lines = chunkText.split(/\r?\n/);

  // A line that is genuine body text: non-empty, not hard boilerplate,
  // not heading-looking. Used as the lookahead test below.
  const isBodyLine = (raw: string): boolean => {
    const t = raw.trim();
    return t.length > 0 && !isHardBoilerplate(t) && !looksLikeHeading(t);
  };

  // Skip blank lines and leading boilerplate. Stop at the first "real"
  // content line; the rest of the chunk is its body.
  let bodyStart = 0;
  for (let i = 0; i < lines.length; i++) {
    const trimmed = lines[i].trim();
    if (!trimmed) {
      bodyStart = i + 1;
      continue;
    }
    if (isHardBoilerplate(trimmed)) {
      bodyStart = i + 1;
      continue;
    }
    // A heading-looking line is skipped ONLY if real body text follows
    // it. If nothing real follows, this short line IS the chunk's
    // content — keep it, instead of stripping it and anchoring on
    // nothing / mid-chunk. This is the over-stripping fix: real short
    // first sentences were being eaten as "headings".
    if (looksLikeHeading(trimmed) && lines.slice(i + 1).some(isBodyLine)) {
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
  return lines.slice(bodyStart).join(" ").replace(/\s+/g, " ").trim();
}

/**
 * Take up to ANCHOR_TARGET_LEN chars of `body` from `start`, snapped
 * to word boundaries on both ends so PDF.js isn't asked to match a
 * half-word (a precision + perf hit). Hard-cuts only when snapping
 * would leave it implausibly short.
 */
function clampAnchorFrom(body: string, start: number): string {
  let s = Math.max(0, start);
  // Don't begin mid-word (matters for the middle/end slices). Advance
  // to the next space, but only a bounded amount so we never skip a
  // whole short sentence.
  if (s > 0) {
    const sp = body.indexOf(" ", s);
    if (sp !== -1 && sp - s <= 24) {
      s = sp + 1;
    }
  }
  const slice = body.slice(s, s + ANCHOR_TARGET_LEN);
  // Trim a trailing partial word, unless that leaves it too short or
  // nothing was actually cut (we're at the body's end).
  if (s + ANCHOR_TARGET_LEN < body.length) {
    const lastSpace = slice.lastIndexOf(" ");
    if (lastSpace >= ANCHOR_MIN_LEN) {
      return slice.slice(0, lastSpace).trim();
    }
  }
  return slice.trim();
}

/**
 * Single distinctive ~80-char anchor from the chunk head. Empty only
 * if chunkText is empty after cleanup. Kept for the single-anchor
 * contract; buildSearchAnchors is the multi-phrase superset the
 * viewer uses.
 */
export function buildSearchAnchor(chunkText: string): string {
  const normalized = extractNormalizedBody(chunkText);
  if (normalized.length <= ANCHOR_TARGET_LEN) {
    return normalized;
  }
  return clampAnchorFrom(normalized, 0);
}

/**
 * D2.2: a ~1200-char chunk reduced to ONE ~80-char prefix only ever
 * highlights its opening phrase. Instead sample up to three short,
 * word-snapped sub-phrases — start, middle, end — so PDF.js (v5
 * accepts a string[] query) paints matches spanning the cited
 * passage, not just its head. Deduped; each >= ANCHOR_MIN_LEN.
 *
 * MUST be rendered page-scoped (D1): more phrases mean more incidental
 * document-wide matches, and D1 strips everything off the resolved
 * page so the extra terms can't bleed onto other pages.
 */
export function buildSearchAnchors(chunkText: string): string[] {
  const body = extractNormalizedBody(chunkText);
  if (!body) {
    return [];
  }
  if (body.length <= ANCHOR_TARGET_LEN) {
    return [body];
  }

  const offsets = [
    0,
    Math.floor(body.length / 2 - ANCHOR_TARGET_LEN / 2),
    body.length - ANCHOR_TARGET_LEN,
  ];
  const seen = new Set<string>();
  const anchors: string[] = [];
  for (const off of offsets) {
    const a = clampAnchorFrom(body, off);
    if (a.length >= ANCHOR_MIN_LEN && !seen.has(a)) {
      seen.add(a);
      anchors.push(a);
    }
  }
  // Guarantee at least one even if the slices collapse to sub-MIN_LEN
  // (body only slightly over the target).
  return anchors.length > 0 ? anchors : [clampAnchorFrom(body, 0)];
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
