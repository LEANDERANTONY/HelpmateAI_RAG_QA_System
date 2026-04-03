from src.retrieval.section_retriever import SectionRetriever
from src.schemas import SectionRecord


def test_section_retriever_seeds_future_work_sections_for_summary_queries():
    retriever = SectionRetriever()
    sections = [
        SectionRecord(
            section_id="results",
            document_id="doc1",
            title="Results",
            summary="The model achieved strong results.",
            text="The model achieved strong results.",
            page_labels=["Page 10"],
            section_path=["Results"],
            clause_ids=[],
            metadata={"section_kind": "results", "primary_page_label": "Page 10", "source_file": "paper.pdf"},
        ),
        SectionRecord(
            section_id="future",
            document_id="doc1",
            title="Future Work",
            summary="Future work includes external validation and prospective studies.",
            text="Future work includes external validation and prospective studies.",
            page_labels=["Page 20"],
            section_path=["Future Work"],
            clause_ids=[],
            metadata={"section_kind": "future work", "primary_page_label": "Page 20", "source_file": "paper.pdf"},
        ),
    ]

    seeded = retriever.seed_summary_sections(
        "What kinds of future work or next steps does the thesis suggest?",
        sections,
        top_k=2,
    )

    assert seeded
    assert seeded[0].chunk_id == "future"


def test_section_retriever_penalizes_reference_like_sections_for_summary_queries():
    retriever = SectionRetriever()
    sections = [
        SectionRecord(
            section_id="refs",
            document_id="doc1",
            title="References",
            summary="Reference list.",
            text="Reference list.",
            page_labels=["Page 30"],
            section_path=["References"],
            clause_ids=[],
            metadata={"section_kind": "references", "primary_page_label": "Page 30", "source_file": "paper.pdf"},
        ),
        SectionRecord(
            section_id="abstract",
            document_id="doc1",
            title="Abstract",
            summary="This paper focuses on multimodal oncology models.",
            text="This paper focuses on multimodal oncology models.",
            page_labels=["Page 1"],
            section_path=["Abstract"],
            clause_ids=[],
            metadata={"section_kind": "abstract", "primary_page_label": "Page 1", "source_file": "paper.pdf"},
        ),
    ]

    ranked = retriever.rank(
        question="What is the main focus of the paper?",
        sections=sections,
        dense_scores={"refs": 0.8, "abstract": 0.4},
        lexical_scores={"refs": 0.2, "abstract": 0.4},
        top_k=2,
    )

    assert ranked[0].chunk_id == "abstract"
