from __future__ import annotations

import streamlit as st

from src.schemas import AnswerResult, IndexRecord


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
    col1, col2, col3 = st.columns(3)
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


def render_answer(answer_result: AnswerResult | None) -> None:
    if answer_result is None:
        return

    st.markdown('<div class="section-card"><h3>Answer</h3></div>', unsafe_allow_html=True)
    st.caption("Support status: " + ("Supported" if answer_result.supported else "Unsupported"))
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
        st.markdown(
            f"""
            <div class="evidence-card">
              <div class="evidence-kicker">{label}</div>
              <div>{candidate.text}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with st.expander("Retrieval Debug"):
        st.write({"query_used": answer_result.query_used, "query_variants": answer_result.query_variants})
        for candidate in answer_result.evidence:
            st.write(
                {
                    "citation": candidate.citation_label or candidate.metadata.get("page_label", "Document"),
                    "dense_score": round(candidate.dense_score, 4),
                    "lexical_score": round(candidate.lexical_score, 4),
                    "fused_score": round(candidate.fused_score, 4),
                    "rerank_score": None if candidate.rerank_score is None else round(candidate.rerank_score, 4),
                }
            )
