from src.schemas import SectionRecord
from src.schemas import SectionSynopsisRecord
from src.topology import DocumentTopologyService


def test_topology_service_builds_synopses_and_edges():
    service = DocumentTopologyService()
    sections = [
        SectionRecord(
            section_id="abstract",
            document_id="doc1",
            title="Abstract",
            summary="This study evaluates multimodal prediction and outlines the objective.",
            text="This study evaluates multimodal prediction and outlines the objective.",
            page_labels=["Page 1"],
            section_path=["Abstract"],
            clause_ids=[],
            metadata={"section_kind": "abstract", "source_file": "paper.pdf", "section_aliases": ["Abstract"]},
        ),
        SectionRecord(
            section_id="discussion",
            document_id="doc1",
            title="Discussion",
            summary="The discussion covers limitations and future directions.",
            text="The discussion covers limitations and future directions.",
            page_labels=["Page 8"],
            section_path=["Discussion"],
            clause_ids=[],
            metadata={"section_kind": "discussion", "source_file": "paper.pdf", "section_aliases": ["Discussion"]},
        ),
    ]

    synopses, edges = service.build(sections)

    assert len(synopses) == 2
    assert synopses[0].region_kind == "overview"
    assert synopses[1].region_kind == "discussion"
    assert any(edge.edge_type == "previous_next" for edge in edges)


def test_topology_service_prefers_early_overview_regions_for_main_aim_questions():
    service = DocumentTopologyService()
    synopses = [
        SectionSynopsisRecord(
            section_id="abstract",
            document_id="doc1",
            title="Abstract",
            synopsis="Abstract summary with study aim and objective.",
            region_kind="overview",
            page_labels=["Page 1"],
            key_terms=["aim", "objective"],
            metadata={"section_path": ["Abstract"], "source_file": "paper.pdf"},
        ),
        SectionSynopsisRecord(
            section_id="discussion",
            document_id="doc1",
            title="Discussion",
            synopsis="Discussion summary with implications and future work.",
            region_kind="discussion",
            page_labels=["Page 12"],
            key_terms=["discussion", "future"],
            metadata={"section_path": ["Discussion"], "source_file": "paper.pdf"},
        ),
    ]

    selected = service.select_candidate_region_ids(
        "What is the main aim of this paper?",
        synopses,
        target_region_kinds=["overview", "discussion", "general"],
        top_k=1,
    )

    assert selected == ["abstract"]


def test_topology_service_prefers_late_discussion_regions_for_future_work_questions():
    service = DocumentTopologyService()
    synopses = [
        SectionSynopsisRecord(
            section_id="abstract",
            document_id="doc1",
            title="Abstract",
            synopsis="Abstract summary with study aim and objective.",
            region_kind="overview",
            page_labels=["Page 1"],
            key_terms=["aim", "objective"],
            metadata={"section_path": ["Abstract"], "source_file": "paper.pdf"},
        ),
        SectionSynopsisRecord(
            section_id="discussion",
            document_id="doc1",
            title="Discussion and Future Work",
            synopsis="Discussion summary with future directions and recommendations.",
            region_kind="discussion",
            page_labels=["Page 12"],
            key_terms=["future", "recommendations"],
            metadata={"section_path": ["Discussion"], "source_file": "paper.pdf"},
        ),
    ]

    selected = service.select_candidate_region_ids(
        "What future work or next steps does the paper suggest?",
        synopses,
        target_region_kinds=["overview", "discussion", "general"],
        top_k=1,
    )

    assert selected == ["discussion"]


def test_topology_service_penalizes_bibliographic_noise_synopses():
    service = DocumentTopologyService()
    synopses = [
        SectionSynopsisRecord(
            section_id="overview",
            document_id="doc1",
            title="Abstract",
            synopsis="Abstract summary with the main focus and study objective.",
            region_kind="overview",
            page_labels=["Page 1"],
            key_terms=["focus", "objective"],
            metadata={"section_path": ["Abstract"], "source_file": "paper.pdf", "topology_low_value": False},
        ),
        SectionSynopsisRecord(
            section_id="noise",
            document_id="doc1",
            title="BILLS-114hr134enr.pdf (2016)",
            synopsis="Author manuscript; available in PMC 2023 October 06.",
            region_kind="evidence",
            page_labels=["Page 11"],
            key_terms=["bills", "pdf"],
            metadata={"section_path": ["Abstract", "Noise"], "source_file": "paper.pdf", "topology_low_value": True},
        ),
    ]

    selected = service.select_candidate_region_ids(
        "What is the main focus of this paper?",
        synopses,
        target_region_kinds=["overview", "discussion", "evidence"],
        top_k=1,
    )

    assert selected == ["overview"]


def test_topology_service_marks_table_of_contents_as_low_value():
    service = DocumentTopologyService()

    assert service._is_low_value_text("TABLE OF CONTENTS\nChapter 1 ............ 12\nChapter 2 ............ 18")


def test_topology_service_respects_low_value_section_flags_from_indexing():
    service = DocumentTopologyService()
    sections = [
        SectionRecord(
            section_id="ack",
            document_id="doc1",
            title="Acknowledgements",
            summary="We thank the faculty and laboratory staff for their support.",
            text="We thank the faculty and laboratory staff for their support.",
            page_labels=["Page 3"],
            section_path=["Acknowledgements"],
            clause_ids=[],
            metadata={
                "section_kind": "general",
                "source_file": "report.pdf",
                "section_aliases": ["Acknowledgements"],
                "front_matter_kind": "acknowledgements",
                "front_matter_score": 0.92,
                "low_value_section_flag": True,
            },
        )
    ]

    synopses, _ = service.build(sections)

    assert synopses[0].metadata["topology_low_value"] is True
