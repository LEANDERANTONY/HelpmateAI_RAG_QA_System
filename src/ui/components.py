from __future__ import annotations

from pathlib import Path

import streamlit as st

from src.evals.report_loader import get_latest_benchmark_report
from src.question_starters import get_question_starters
from src.schemas import AnswerResult, DocumentRecord, IndexRecord


def render_intro() -> None:
    st.markdown(
        """
        <div class="app-hero">
          <div class="app-kicker">Grounded Document QA</div>
          <h1>HelpmateAI</h1>
          <p class="app-copy">
            Upload a long PDF or DOCX, build a reusable local index, and get citation-aware answers with visible evidence.
          </p>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_metric_cards(index_record: IndexRecord | None, answer_result: AnswerResult | None) -> None:
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.markdown(
            f"""
            <div class="metric-card">
              <div class="metric-label">Index Status</div>
              <div class="metric-value">{'Ready' if index_record else 'Pending'}</div>
              <div class="metric-note">{'Existing index reused' if index_record and index_record.reused else 'Builds a local Chroma index for the uploaded document'}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with col2:
        st.markdown(
            f"""
            <div class="metric-card">
              <div class="metric-label">Chunks</div>
              <div class="metric-value">{index_record.chunk_count if index_record else 0}</div>
              <div class="metric-note">Deterministic chunking with overlap and citation metadata.</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with col3:
        st.markdown(
            f"""
            <div class="metric-card">
              <div class="metric-label">Sections</div>
              <div class="metric-value">{index_record.section_count if index_record else 0}</div>
              <div class="metric-note">Section-first retrieval can narrow broad narrative questions before chunk grounding.</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with col4:
        cache_text = "Cache hit" if answer_result and answer_result.cache_status.answer_cache_hit else "Fresh answer"
        st.markdown(
            f"""
            <div class="metric-card">
              <div class="metric-label">Answer Mode</div>
              <div class="metric-value">{cache_text}</div>
              <div class="metric-note">Shows whether the latest answer came from the conservative answer cache.</div>
            </div>
            """,
            unsafe_allow_html=True,
        )


def render_document_status(document_record: DocumentRecord | None, index_record: IndexRecord | None) -> None:
    if document_record is None:
        return

    metadata = document_record.metadata or {}
    style = metadata.get("document_style", "unknown")
    section_hint = metadata.get("section_heading") or metadata.get("section_path")
    st.markdown('<div class="section-card"><h3>Document Status</h3><p class="section-copy">Ingestion and index details for the current document.</p></div>', unsafe_allow_html=True)
    left, right = st.columns((1.2, 1))
    with left:
        st.markdown(
            f"""
            <div class="status-panel">
              <div class="status-title">{document_record.file_name}</div>
              <div class="status-row"><span>Type</span><strong>{document_record.file_type.upper()}</strong></div>
              <div class="status-row"><span>Pages</span><strong>{document_record.page_count or 'Unknown'}</strong></div>
              <div class="status-row"><span>Characters</span><strong>{document_record.char_count:,}</strong></div>
              <div class="status-row"><span>Document Style</span><strong>{style.replace('_', ' ').title()}</strong></div>
              <div class="status-row"><span>Index</span><strong>{'Reused' if index_record and index_record.reused else 'Fresh build' if index_record else 'Pending'}</strong></div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with right:
        st.markdown(
            f"""
            <div class="status-panel status-panel-dark">
              <div class="status-title status-title-light">Active Retrieval Profile</div>
              <div class="status-row status-row-light"><span>Chunk Count</span><strong>{index_record.chunk_count if index_record else 0}</strong></div>
              <div class="status-row status-row-light"><span>Section Count</span><strong>{index_record.section_count if index_record else 0}</strong></div>
              <div class="status-row status-row-light"><span>Embedding Model</span><strong>{index_record.embedding_model if index_record else 'Pending'}</strong></div>
              <div class="status-row status-row-light"><span>Structure Hint</span><strong>{str(section_hint)[:42] if section_hint else 'Derived from headings and clauses'}</strong></div>
            </div>
            """,
            unsafe_allow_html=True,
        )


def render_question_starters(document_record: DocumentRecord | None) -> str | None:
    if document_record is None:
        return None

    style = (document_record.metadata or {}).get("document_style")
    suggestions = get_question_starters(style)
    st.markdown('<div class="section-card"><h3>Starter Questions</h3><p class="section-copy">These prompts adapt to the document style so you can pressure-test the index quickly.</p></div>', unsafe_allow_html=True)
    selected: str | None = None
    cols = st.columns(len(suggestions))
    for idx, suggestion in enumerate(suggestions):
        with cols[idx]:
            if st.button(suggestion, key=f"starter_{style}_{idx}", use_container_width=True):
                selected = suggestion
    return selected


def render_benchmark_panel() -> None:
    report, report_path = get_latest_benchmark_report()
    st.markdown('<div class="section-card"><h3>Benchmark Snapshot</h3><p class="section-copy">Latest saved comparison from the local benchmark harness.</p></div>', unsafe_allow_html=True)
    if report is None or report_path is None:
        st.info("No saved benchmark report is available yet.")
        return

    retrieval = report.get("local_retrieval", {})
    ragas = report.get("ragas", {})
    vectara = report.get("vectara", {})
    openai = report.get("openai_file_search", {})
    vendor_eval = report.get("vendor_answer_eval", {})

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Our Hit Rate", f"{retrieval.get('top_k_page_hit_rate', 0):.2f}")
    col2.metric("Our MRR", f"{retrieval.get('mean_reciprocal_rank', 0):.2f}")
    col3.metric("Vectara Retrieval", f"{vectara.get('snippet_fragment_match_rate', 0):.2f}")
    col4.metric("OpenAI Retrieval", f"{openai.get('snippet_fragment_match_rate', 0):.2f}")

    st.caption(f"Latest report: {report_path.name}")

    ragas_cols = st.columns(3)
    ragas_cols[0].metric("Ragas Faithfulness", f"{ragas.get('faithfulness_mean', 0):.2f}")
    ragas_cols[1].metric("Ragas Answer Relevancy", f"{ragas.get('answer_relevancy_mean', 0):.2f}")
    ragas_cols[2].metric("Ragas Context Precision", f"{ragas.get('context_precision_mean', 0):.2f}")

    with st.expander("Vendor Answer Eval"):
        for vendor_name in ("vectara", "openai"):
            vendor = vendor_eval.get(vendor_name)
            if not vendor:
                continue
            st.markdown(f"**{vendor_name.title()} Retrieval + Shared Answer Model**")
            st.write(
                {
                    "ragas_faithfulness_mean": vendor.get("ragas_faithfulness_mean"),
                    "ragas_answer_relevancy_mean": vendor.get("ragas_answer_relevancy_mean"),
                    "ragas_context_precision_mean": vendor.get("ragas_context_precision_mean"),
                }
            )

    with st.expander("Benchmark Notes"):
        st.markdown(
            """
            - Vectara is the primary external retrieval baseline.
            - OpenAI File Search stays as a historical/reference retrieval baseline.
            - `ragas` is the active answer-quality meter for routine benchmarking.
            """
        )

        summary_path = Path(__file__).resolve().parents[2] / "docs" / "evals" / "benchmark_summary.md"
        if summary_path.exists():
            st.markdown(summary_path.read_text(encoding="utf-8"))


def render_answer(answer_result: AnswerResult | None) -> None:
    if answer_result is None:
        return

    st.markdown('<div class="section-card"><h3>Answer</h3></div>', unsafe_allow_html=True)
    support_badge = "Supported" if answer_result.supported else "Unsupported"
    st.markdown(f'<div class="answer-badge">Support status: {support_badge}</div>', unsafe_allow_html=True)
    st.write(answer_result.answer)
    if answer_result.note:
        st.caption(answer_result.note)
    if answer_result.citation_details:
        st.markdown("**Citations**")
        for citation in answer_result.citation_details:
            st.caption(citation)
    elif answer_result.citations:
        st.caption("Citations: " + ", ".join(answer_result.citations))
    if answer_result.retrieval_notes:
        st.markdown("**Retrieval Notes**")
        for note in answer_result.retrieval_notes:
            st.caption(note)

    if not answer_result.evidence:
        return

    st.markdown('<div class="section-card"><h3>Evidence</h3></div>', unsafe_allow_html=True)
    for candidate in answer_result.evidence:
        label = candidate.metadata.get("page_label", "Document")
        meta = candidate.metadata
        section_kind = meta.get("section_kind") or meta.get("title") or meta.get("section_heading")
        st.markdown(
            f"""
            <div class="evidence-card">
              <div class="evidence-kicker">{label}</div>
              <div class="evidence-meta">{section_kind or 'Retrieved evidence chunk'}</div>
              <div>{candidate.text}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with st.expander("Retrieval Debug"):
        st.write(
            {
                "query_used": answer_result.query_used,
                "query_variants": answer_result.query_variants,
                "retrieval_notes": answer_result.retrieval_notes,
            }
        )
        for candidate in answer_result.evidence:
            st.write(
                {
                    "citation": candidate.citation_label or candidate.metadata.get("page_label", "Document"),
                    "dense_score": round(candidate.dense_score, 4),
                    "lexical_score": round(candidate.lexical_score, 4),
                    "fused_score": round(candidate.fused_score, 4),
                    "rerank_score": None if candidate.rerank_score is None else round(candidate.rerank_score, 4),
                    "section_kind": candidate.metadata.get("section_kind"),
                    "content_type": candidate.metadata.get("content_type"),
                }
            )
