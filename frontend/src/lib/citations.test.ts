import { describe, expect, it } from "vitest";

import type { RetrievalCandidate } from "@/lib/api-types";
import {
  resolveCitationTarget,
  splitCitationSegments,
  visibleEvidence,
} from "@/lib/citations";

function candidate(
  chunkId: string,
  overrides: Partial<RetrievalCandidate> = {},
): RetrievalCandidate {
  return {
    chunk_id: chunkId,
    text: `text for ${chunkId}`,
    metadata: {},
    dense_score: 0,
    lexical_score: 0,
    fused_score: 0,
    rerank_score: null,
    citation_label: "",
    ...overrides,
  };
}

const EVIDENCE: RetrievalCandidate[] = [
  candidate("c1", {
    metadata: { page_label: "Page 7", section_heading: "Coverage", section_id: "s-results" },
  }),
  candidate("c2", {
    metadata: { page_label: "Page 13", section_heading: "Waiting Period", section_id: "s-waiting" },
  }),
];

describe("resolveCitationTarget", () => {
  it("resolves [Source N] to evidence[N-1]", () => {
    const target = resolveCitationTarget("Source 2", EVIDENCE);
    expect(target?.chunkId).toBe("c2");
    expect(target?.evidenceIndex).toBe(1);
  });

  it("returns null for an out-of-range [Source N]", () => {
    expect(resolveCitationTarget("Source 5", EVIDENCE)).toBeNull();
  });

  it("resolves [Page N] by page label", () => {
    expect(resolveCitationTarget("Page 13", EVIDENCE)?.chunkId).toBe("c2");
  });

  it("resolves [p. N] by page label", () => {
    expect(resolveCitationTarget("p. 7", EVIDENCE)?.chunkId).toBe("c1");
  });

  it("resolves [Section X] by heading", () => {
    expect(resolveCitationTarget("Section Waiting Period", EVIDENCE)?.chunkId).toBe("c2");
  });

  it("returns null when nothing matches", () => {
    expect(resolveCitationTarget("Page 999", EVIDENCE)).toBeNull();
    expect(resolveCitationTarget("Section Nonexistent", EVIDENCE)).toBeNull();
  });
});

describe("visibleEvidence", () => {
  it("returns only the cited chunks, in reading order", () => {
    const answer = "The waiting period is 30 days [Source 2]. Coverage applies [Source 1].";
    expect(visibleEvidence(answer, EVIDENCE).map((c) => c.chunk_id)).toEqual(["c2", "c1"]);
  });

  it("falls back to the first `fallbackCount` chunks when nothing is cited", () => {
    expect(
      visibleEvidence("No citations here.", EVIDENCE, { fallbackCount: 2 }).map((c) => c.chunk_id),
    ).toEqual(["c1", "c2"]);
  });

  it("returns nothing on a clean abstention (fallbackCount 0)", () => {
    expect(visibleEvidence("Unsupported by the evidence.", EVIDENCE, { fallbackCount: 0 })).toEqual(
      [],
    );
  });

  it("dedupes repeated citations to the same chunk", () => {
    expect(visibleEvidence("[Source 1] and again [Source 1].", EVIDENCE).map((c) => c.chunk_id)).toEqual([
      "c1",
    ]);
  });
});

describe("splitCitationSegments", () => {
  it("marks an out-of-range citation with a null target (orphan pill)", () => {
    const segments = splitCitationSegments("See [Source 9].", EVIDENCE);
    const citation = segments.find((segment) => segment.type === "citation");
    expect(citation && citation.type === "citation" ? citation.target : "missing").toBeNull();
  });
});
