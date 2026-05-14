import type { RetrievalCandidate } from "@/lib/api-types";

export type CitationTarget = {
  chunkId: string;
  evidenceIndex: number;
  label: string;
};

export type CitationSegment =
  | { type: "text"; text: string }
  | { type: "citation"; label: string; raw: string; target: CitationTarget | null };

const CITATION_PATTERN = /\[(Source\s+\d+|Section\s+[^\]]+|p\.\s*\d+|Page\s+\d+)\]/gi;

function metadataValue(candidate: RetrievalCandidate, key: string) {
  const value = candidate.metadata[key];
  if (Array.isArray(value)) {
    return value.join(" ");
  }
  if (value === null || value === undefined) {
    return "";
  }
  return String(value);
}

function normalize(value: string) {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
}

function compact(value: string) {
  return normalize(value).replace(/\s+/g, "");
}

export function stripReferencesBlock(text: string) {
  return text.replace(/\s*references?\s*:\s*[\s\S]*$/i, "").trim();
}

export function resolveCitationTarget(
  rawLabel: string,
  evidence: RetrievalCandidate[],
): CitationTarget | null {
  const sourceMatch = rawLabel.match(/^Source\s+(\d+)$/i);
  if (sourceMatch) {
    const evidenceIndex = Number(sourceMatch[1]) - 1;
    const candidate = evidence[evidenceIndex];
    if (!candidate) {
      return null;
    }
    return {
      chunkId: candidate.chunk_id,
      evidenceIndex,
      label: rawLabel,
    };
  }

  const normalizedRaw = normalize(rawLabel);
  const compactRaw = compact(rawLabel.replace(/^Section\s+/i, ""));
  const pageMatch = rawLabel.match(/(?:p\.|Page)\s*(\d+)/i);
  const pageNeedle = pageMatch ? `page ${pageMatch[1]}` : "";

  const evidenceIndex = evidence.findIndex((candidate) => {
    const haystack = [
      candidate.citation_label,
      metadataValue(candidate, "page_label"),
      metadataValue(candidate, "section_id"),
      metadataValue(candidate, "section_heading"),
      metadataValue(candidate, "numbered_heading"),
      metadataValue(candidate, "chapter_number"),
      metadataValue(candidate, "chapter_title"),
    ];
    const normalizedHaystack = normalize(haystack.join(" "));
    if (pageNeedle && normalizedHaystack.includes(pageNeedle)) {
      return true;
    }
    if (normalizedHaystack.includes(normalizedRaw)) {
      return true;
    }
    return compact(haystack.join(" ")).includes(compactRaw);
  });

  if (evidenceIndex < 0) {
    return null;
  }

  return {
    chunkId: evidence[evidenceIndex].chunk_id,
    evidenceIndex,
    label: rawLabel,
  };
}

export function splitCitationSegments(
  text: string,
  evidence: RetrievalCandidate[],
): CitationSegment[] {
  const segments: CitationSegment[] = [];
  let lastIndex = 0;

  for (const match of text.matchAll(CITATION_PATTERN)) {
    const start = match.index ?? 0;
    const full = match[0];
    const raw = match[1].trim();
    if (start > lastIndex) {
      segments.push({ type: "text", text: text.slice(lastIndex, start) });
    }
    segments.push({
      type: "citation",
      label: raw,
      raw: full,
      target: resolveCitationTarget(raw, evidence),
    });
    lastIndex = start + full.length;
  }

  if (lastIndex < text.length) {
    segments.push({ type: "text", text: text.slice(lastIndex) });
  }

  return segments.length ? segments : [{ type: "text", text }];
}

export function uniqueCitationTargets(
  answerText: string,
  evidence: RetrievalCandidate[],
) {
  const seen = new Set<string>();
  return splitCitationSegments(stripReferencesBlock(answerText), evidence)
    .filter((segment): segment is Extract<CitationSegment, { type: "citation" }> => segment.type === "citation")
    .map((segment) => segment.target)
    .filter((target): target is CitationTarget => Boolean(target))
    .filter((target) => {
      if (seen.has(target.chunkId)) {
        return false;
      }
      seen.add(target.chunkId);
      return true;
    });
}

// Reduce a turn's retrieved evidence down to what we actually surface in
// the UI's evidence panels (right rail on desktop, combined accordion on
// mobile). Retrieval returns up to HELPMATE_FINAL_TOP_K (default 4)
// chunks, and the generator promotes the LLM-selected 2 to the front but
// still ships all four — so the rail used to render every retrieved
// chunk, often four near-duplicates from the same page.
//
// Policy:
//   • If the LLM cited specific [Source N] markers in the answer, show
//     exactly those chunks (in citation order). Every card is then
//     reachable via a matching inline pill, no orphans, no duplicates.
//   • If no citations parsed (abstention or unsupported answer), fall
//     back to the first `fallbackCount` retrieved chunks so the user
//     still has something concrete to look at — set fallbackCount=2 for
//     abstention panels so the user can see what the retriever thought
//     was closest before deciding to abstain.
//
// Order matters: uniqueCitationTargets walks the answer in reading
// order, so mapping back via evidenceIndex preserves the order the
// reader would naturally encounter the chunks.
export function visibleEvidence(
  answerText: string,
  evidence: RetrievalCandidate[],
  { fallbackCount = 0 }: { fallbackCount?: number } = {},
): RetrievalCandidate[] {
  const targets = uniqueCitationTargets(answerText, evidence);
  if (targets.length > 0) {
    return targets
      .map((target) => evidence[target.evidenceIndex])
      .filter((candidate): candidate is RetrievalCandidate => Boolean(candidate));
  }
  return evidence.slice(0, fallbackCount);
}
