import streamlit as st


def initialize_state() -> None:
    st.session_state.setdefault("document_record", None)
    st.session_state.setdefault("index_record", None)
    st.session_state.setdefault("answer_result", None)
    st.session_state.setdefault("last_error", None)
    st.session_state.setdefault("question_input", "")
