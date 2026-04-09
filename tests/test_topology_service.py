from src.schemas import SectionRecord
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
