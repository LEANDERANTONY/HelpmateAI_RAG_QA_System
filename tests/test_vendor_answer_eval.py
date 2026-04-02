from src.evals.vendor_answer_eval import _strip_references


def test_strip_references_removes_references_block() -> None:
    answer = "This is the answer.\n\nReferences:\n- [1] Page 4"
    assert _strip_references(answer) == "This is the answer."
