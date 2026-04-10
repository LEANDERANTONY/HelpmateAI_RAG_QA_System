from __future__ import annotations

import re
from collections import Counter, defaultdict

from src.schemas import SectionRecord, SectionSynopsisRecord, TopologyEdge


_STOPWORDS = {
    "about",
    "after",
    "also",
    "among",
    "and",
    "are",
    "because",
    "been",
    "being",
    "between",
    "both",
    "but",
    "can",
    "could",
    "data",
    "does",
    "document",
    "each",
    "for",
    "from",
    "have",
    "into",
    "more",
    "most",
    "other",
    "over",
    "paper",
    "report",
    "section",
    "should",
    "that",
    "their",
    "them",
    "there",
    "these",
    "they",
    "this",
    "those",
    "thesis",
    "using",
    "what",
    "when",
    "where",
    "which",
    "with",
    "would",
}

_REGION_KEYWORDS: dict[str, set[str]] = {
    "overview": {
        "abstract",
        "background",
        "context",
        "executive summary",
        "introduction",
        "objective",
        "objectives",
        "overview",
        "purpose",
        "summary",
    },
    "definitions": {"definition", "definitions", "glossary", "meaning", "terminology", "terms"},
    "procedure": {
        "approach",
        "implementation",
        "method",
        "methodology",
        "methods",
        "process",
        "procedure",
        "workflow",
    },
    "evidence": {
        "analysis",
        "evaluation",
        "experiment",
        "experiments",
        "finding",
        "findings",
        "financial",
        "metric",
        "metrics",
        "performance",
        "result",
        "results",
    },
    "discussion": {
        "challenge",
        "conclusion",
        "conclusions",
        "discussion",
        "future",
        "implication",
        "limitations",
        "outlook",
        "recommendation",
        "recommendations",
    },
    "rules": {
        "benefit",
        "benefits",
        "clause",
        "condition",
        "conditions",
        "coverage",
        "exclusion",
        "exclusions",
        "obligation",
        "obligations",
        "policy",
        "rights",
        "rule",
        "rules",
    },
    "appendix": {"appendix", "appendices", "reference", "references", "supplementary"},
    "general": set(),
}

_EARLY_REGION_QUERY_TERMS = {
    "abstract",
    "aim",
    "contribution",
    "contributions",
    "focus",
    "introduction",
    "objective",
    "objectives",
    "overview",
    "purpose",
    "scope",
    "summary",
    "topic",
}

_LATE_REGION_QUERY_TERMS = {
    "conclusion",
    "conclusions",
    "discussion",
    "future",
    "implication",
    "implications",
    "limitation",
    "limitations",
    "next",
    "outlook",
    "recommendation",
    "recommendations",
}

_LOW_VALUE_PATTERNS = (
    "author manuscript",
    "available in pmc",
    "copyright holder",
    "extended data",
    "table of contents",
    "list of figures",
    "list of tables",
    "nih-pa author manuscript",
    "pmcid",
    "pmid",
)


class DocumentTopologyService:
    @staticmethod
    def _tokenize(text: str) -> list[str]:
        return [
            token
            for token in re.findall(r"[A-Za-z0-9]+", text.lower())
            if len(token) > 2 and token not in _STOPWORDS
        ]

    @classmethod
    def _sentences(cls, text: str) -> list[str]:
        normalized = re.sub(r"\s+", " ", text).strip()
        if not normalized:
            return []
        return [sentence.strip() for sentence in re.split(r"(?<=[.!?])\s+", normalized) if sentence.strip()]

    @classmethod
    def _representative_sentences(cls, section: SectionRecord, region_kind: str) -> list[str]:
        title = section.title.strip()
        summary_sentences = cls._sentences(section.summary)
        text_sentences = cls._sentences(section.text)
        candidates = summary_sentences + text_sentences[:4] + text_sentences[-2:]

        unique: list[str] = []
        seen: set[str] = set()
        for sentence in candidates:
            compact = sentence.strip()
            if len(compact) < 24:
                continue
            lowered = compact.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            unique.append(compact)

        if not unique and title:
            return [title]

        if region_kind == "overview":
            return unique[:3]
        if region_kind in {"discussion", "evidence"} and len(unique) >= 3:
            return [unique[0], unique[-1]]
        return unique[:2] if unique else [title]

    @classmethod
    def _key_terms(cls, section: SectionRecord) -> list[str]:
        text = " ".join(
            [
                section.title,
                " ".join(section.section_path),
                section.summary,
                " ".join(section.metadata.get("section_aliases", []))
                if isinstance(section.metadata.get("section_aliases", []), list)
                else str(section.metadata.get("section_aliases", "")),
            ]
        )
        counts = Counter(cls._tokenize(text))
        return [token for token, _ in counts.most_common(8)]

    @classmethod
    def _region_kind(cls, section: SectionRecord) -> str:
        heading_tokens = " ".join(
            [
                section.title.lower(),
                " ".join(section.section_path).lower(),
                str(section.metadata.get("section_kind", "")).lower(),
                str(section.metadata.get("content_type", "")).lower(),
            ]
        )
        numeric_density = sum(char.isdigit() for char in section.text[:1500]) / max(min(len(section.text), 1500), 1)
        table_density = (
            section.text[:1200].count("|") + section.text[:1200].count("\t") + section.text[:1200].lower().count("table ")
        ) / max(min(len(section.text), 1200), 1)

        best_kind = "general"
        best_score = 0.0
        for region_kind, keywords in _REGION_KEYWORDS.items():
            score = sum(1.0 for keyword in keywords if keyword in heading_tokens)
            if region_kind == "evidence":
                score += 1.5 if numeric_density > 0.05 else 0.0
                score += 1.0 if table_density > 0.003 else 0.0
            if region_kind == "appendix" and "reference" in heading_tokens:
                score += 1.5
            if score > best_score:
                best_score = score
                best_kind = region_kind

        return best_kind

    @classmethod
    def _build_synopsis(cls, section: SectionRecord, region_kind: str, key_terms: list[str]) -> str:
        snippets = cls._representative_sentences(section, region_kind)
        path_text = " > ".join(section.section_path)
        aliases = section.metadata.get("section_aliases", [])
        alias_text = " | ".join(aliases[:4]) if isinstance(aliases, list) else str(aliases)
        parts = [section.title]
        if path_text and path_text != section.title:
            parts.append(path_text)
        if alias_text:
            parts.append(alias_text)
        if key_terms:
            parts.append("Key terms: " + ", ".join(key_terms[:6]))
        if snippets:
            parts.append(" ".join(snippets[:3]))
        return "\n".join(part for part in parts if part).strip()

    @staticmethod
    def _is_low_value_text(text: str) -> bool:
        lowered = text.lower()
        if any(pattern in lowered for pattern in _LOW_VALUE_PATTERNS):
            return True
        if re.search(r"\bextended data (fig|figure|table)", lowered):
            return True
        if re.search(r"\.{10,}", text):
            return True
        if ".pdf" in lowered and any(char.isdigit() for char in lowered):
            return True
        return False

    @classmethod
    def _section_similarity(cls, left: SectionSynopsisRecord, right: SectionSynopsisRecord) -> float:
        left_terms = set(left.key_terms)
        right_terms = set(right.key_terms)
        if not left_terms or not right_terms:
            return 0.0
        return len(left_terms & right_terms) / max(len(left_terms | right_terms), 1)

    def build(self, sections: list[SectionRecord]) -> tuple[list[SectionSynopsisRecord], list[TopologyEdge]]:
        synopses: list[SectionSynopsisRecord] = []
        for section in sections:
            region_kind = self._region_kind(section)
            key_terms = self._key_terms(section)
            synopsis = self._build_synopsis(section, region_kind, key_terms)
            synopses.append(
                SectionSynopsisRecord(
                    section_id=section.section_id,
                    document_id=section.document_id,
                    title=section.title,
                    synopsis=synopsis,
                    region_kind=region_kind,
                    page_labels=list(section.page_labels),
                    key_terms=key_terms,
                    metadata={
                        **section.metadata,
                        "section_path": list(section.section_path),
                        "section_heading": section.title,
                        "topology_low_value": self._is_low_value_text(
                            " ".join([section.title, section.summary, section.text[:400]])
                        ),
                    },
                )
            )

        by_id = {synopsis.section_id: synopsis for synopsis in synopses}
        edges: list[TopologyEdge] = []

        for index, section in enumerate(sections):
            if index + 1 < len(sections):
                edges.append(
                    TopologyEdge(
                        source_section_id=section.section_id,
                        target_section_id=sections[index + 1].section_id,
                        edge_type="previous_next",
                        weight=1.0,
                    )
                )

            if len(section.section_path) > 1:
                parent_path = section.section_path[:-1]
                for candidate in sections:
                    if candidate.section_id == section.section_id:
                        continue
                    if candidate.section_path == parent_path:
                        edges.append(
                            TopologyEdge(
                                source_section_id=candidate.section_id,
                                target_section_id=section.section_id,
                                edge_type="parent_child",
                                weight=1.0,
                            )
                        )
                        break

        grouped_by_region: defaultdict[str, list[SectionSynopsisRecord]] = defaultdict(list)
        for synopsis in synopses:
            grouped_by_region[synopsis.region_kind].append(synopsis)

        for members in grouped_by_region.values():
            for index, synopsis in enumerate(members):
                for neighbor in members[index + 1 : index + 3]:
                    edges.append(
                        TopologyEdge(
                            source_section_id=synopsis.section_id,
                            target_section_id=neighbor.section_id,
                            edge_type="same_region_family",
                            weight=0.75,
                        )
                    )

        for synopsis in synopses:
            neighbors = sorted(
                (
                    (other.section_id, self._section_similarity(synopsis, other))
                    for other in synopses
                    if other.section_id != synopsis.section_id
                ),
                key=lambda item: item[1],
                reverse=True,
            )
            for target_section_id, weight in neighbors[:2]:
                if weight < 0.2:
                    continue
                edges.append(
                    TopologyEdge(
                        source_section_id=synopsis.section_id,
                        target_section_id=target_section_id,
                        edge_type="semantic_neighbor",
                        weight=weight,
                    )
                )

        return synopses, edges

    @staticmethod
    def _page_number(page_label: str) -> int | None:
        match = re.search(r"(\d+)", page_label)
        return int(match.group(1)) if match else None

    @classmethod
    def select_candidate_region_ids(
        cls,
        question: str,
        synopses: list[SectionSynopsisRecord],
        *,
        target_region_kinds: list[str] | None = None,
        explicit_section_terms: list[str] | None = None,
        top_k: int = 6,
    ) -> list[str]:
        question_terms = set(cls._tokenize(question))
        explicit_terms = [term.lower() for term in (explicit_section_terms or []) if term.strip()]
        page_numbers = [cls._page_number(synopsis.page_labels[0]) for synopsis in synopses if synopsis.page_labels]
        page_numbers = [page for page in page_numbers if page is not None]
        max_page = max(page_numbers) if page_numbers else None
        wants_early_region = bool(question_terms & _EARLY_REGION_QUERY_TERMS)
        wants_late_region = bool(question_terms & _LATE_REGION_QUERY_TERMS)
        scored: list[tuple[str, float]] = []
        preferred_kinds = {kind for kind in (target_region_kinds or []) if kind}

        for synopsis in synopses:
            heading_tokens = set(cls._tokenize(" ".join([synopsis.title, synopsis.synopsis])))
            score = len(question_terms & heading_tokens) / max(len(question_terms), 1)
            if preferred_kinds and synopsis.region_kind in preferred_kinds:
                score += 0.35
            page_number = cls._page_number(synopsis.page_labels[0]) if synopsis.page_labels else None
            if page_number is not None and max_page:
                page_position = page_number / max(max_page, 1)
                if wants_early_region and synopsis.region_kind in {"overview", "general"}:
                    score += 0.18 * (1 - page_position)
                if wants_late_region and synopsis.region_kind in {"discussion", "overview"}:
                    score += 0.18 * page_position
            if wants_early_region and synopsis.region_kind == "appendix":
                score -= 0.65
            if wants_early_region and synopsis.region_kind == "evidence":
                score -= 0.12
            if wants_early_region and synopsis.title.lower().strip() in {"overview", "abstract", "document overview", "introduction"}:
                score += 0.22
            if synopsis.metadata.get("topology_low_value"):
                score -= 0.45
            if explicit_terms:
                explicit_hit = any(
                    term in synopsis.title.lower()
                    or term in synopsis.synopsis.lower()
                    or any(term in str(value).lower() for value in synopsis.metadata.get("section_path", []))
                    for term in explicit_terms
                )
                if explicit_hit:
                    score += 1.2
            if score > 0:
                scored.append((synopsis.section_id, score))

        scored.sort(key=lambda item: item[1], reverse=True)
        return [section_id for section_id, _ in scored[:top_k]]

    @staticmethod
    def neighbor_section_ids(
        section_id: str,
        edges: list[TopologyEdge],
        *,
        edge_types: set[str] | None = None,
        top_k: int = 2,
    ) -> list[str]:
        candidates: list[tuple[str, float]] = []
        for edge in edges:
            if edge.source_section_id != section_id:
                continue
            if edge_types and edge.edge_type not in edge_types:
                continue
            candidates.append((edge.target_section_id, edge.weight))
        candidates.sort(key=lambda item: item[1], reverse=True)
        return [section_id for section_id, _ in candidates[:top_k]]

    @staticmethod
    def region_family_lookup(synopses: list[SectionSynopsisRecord]) -> dict[str, list[str]]:
        grouped: defaultdict[str, list[str]] = defaultdict(list)
        for synopsis in synopses:
            grouped[synopsis.region_kind].append(synopsis.section_id)
        return dict(grouped)
