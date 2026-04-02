from __future__ import annotations

from pathlib import Path

import streamlit as st

from src.config import get_settings
from src.pipeline import HelpmatePipeline
from src.ui.components import render_answer, render_benchmark_panel, render_document_status, render_intro, render_metric_cards
from src.ui.state import initialize_state
from src.ui.theme import apply_theme


def _save_uploaded_file(uploaded_file, uploads_dir: Path) -> Path:
    target = uploads_dir / uploaded_file.name
    target.write_bytes(uploaded_file.getbuffer())
    return target


def main() -> None:
    settings = get_settings()
    pipeline = HelpmatePipeline(settings)

    st.set_page_config(
        page_title="HelpmateAI",
        page_icon="AI",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    apply_theme()
    initialize_state()

    with st.sidebar:
        st.markdown("### Workspace")
        st.caption("Local-first indexing with backend-ready service boundaries.")
        st.markdown("### Retrieval")
        st.caption("Hybrid retrieval: dense + lexical + reranking")
        st.markdown("### Scope")
        st.caption("Focused on long-document QA. Paraphrasing stays a separate future app.")
        st.markdown("### Benchmarking")
        st.caption("Vectara is the primary retrieval baseline. Ragas is the answer-quality meter.")

    render_intro()
    render_metric_cards(st.session_state.index_record, st.session_state.answer_result)
    render_document_status(st.session_state.document_record, st.session_state.index_record)

    qa_tab, benchmark_tab = st.tabs(["Ask", "Benchmarks"])

    with qa_tab:
        st.markdown('<div class="section-card"><h3>Upload Document</h3><p class="section-copy">PDF and DOCX are supported in the first release.</p></div>', unsafe_allow_html=True)
        uploaded_file = st.file_uploader("Choose a document", type=["pdf", "docx"])
        if uploaded_file is not None:
            saved_path = _save_uploaded_file(uploaded_file, settings.uploads_dir)
            if st.button("Build Or Reuse Index", use_container_width=True):
                try:
                    document_record = pipeline.ingest_document(saved_path)
                    index_record = pipeline.build_or_load_index(document_record)
                    st.session_state.document_record = document_record
                    st.session_state.index_record = index_record
                    st.session_state.answer_result = None
                    st.success(
                        "Index ready. "
                        + ("Reused an existing index." if index_record.reused else "Built a fresh index.")
                    )
                except Exception as error:
                    st.session_state.last_error = str(error)
                    st.error(str(error))

        if st.session_state.document_record and st.session_state.index_record:
            st.markdown('<div class="section-card"><h3>Ask A Question</h3><p class="section-copy">Answers stay grounded in the indexed document and surface citations.</p></div>', unsafe_allow_html=True)
            question = st.text_area("Question", height=120, placeholder="What are the key exclusions, deadlines, or obligations described in this document?")
            if st.button("Generate Grounded Answer", use_container_width=True):
                if not question.strip():
                    st.warning("Enter a question before generating an answer.")
                    return
                try:
                    answer_result = pipeline.answer_question(
                        st.session_state.document_record,
                        st.session_state.index_record,
                        question,
                    )
                    st.session_state.answer_result = answer_result
                except Exception as error:
                    st.session_state.last_error = str(error)
                    st.error(str(error))

        render_answer(st.session_state.answer_result)

    with benchmark_tab:
        render_benchmark_panel()
