import textwrap

import streamlit as st


def apply_theme() -> None:
    st.markdown(
        textwrap.dedent(
            """
            <style>
            :root {
                --page-ink: #e7eefc;
                --ink: #142033;
                --muted: #5b6b83;
                --surface-line: rgba(20, 32, 51, 0.14);
                --accent-strong: #2563eb;
                --shadow: 0 24px 48px rgba(0, 0, 0, 0.34);
            }
            .stApp {
                background:
                    radial-gradient(circle at top left, rgba(37, 99, 235, 0.22), transparent 26%),
                    radial-gradient(circle at top right, rgba(14, 165, 233, 0.14), transparent 24%),
                    linear-gradient(180deg, #070a10 0%, #0b1220 48%, #05070c 100%);
            }
            .block-container {
                max-width: 1220px;
                padding-top: 2rem;
                padding-bottom: 3rem;
            }
            .stApp, .stMarkdown, .stMarkdown p, .stMarkdown li, .stCaption,
            [data-testid="stMarkdownContainer"] p, [data-testid="stMarkdownContainer"] li,
            [data-testid="stWidgetLabel"] p {
                color: var(--page-ink);
            }
            [data-testid="stSidebar"] {
                background: linear-gradient(180deg, rgba(7, 10, 16, 0.96), rgba(11, 18, 32, 0.98));
                border-right: 1px solid rgba(148, 163, 184, 0.14);
            }
            [data-testid="stSidebar"] * { color: var(--page-ink) !important; }
            .app-hero, .metric-card, .section-card {
                background: #ffffff !important;
                border: 1px solid var(--surface-line);
                box-shadow: var(--shadow);
                border-radius: 22px;
            }
            .app-hero {
                padding: 1.15rem 1.35rem;
                margin-bottom: 1rem;
            }
            .app-kicker, .metric-label {
                text-transform: uppercase;
                letter-spacing: 0.12em;
                font-weight: 700;
            }
            .app-kicker {
                font-size: 0.72rem;
                color: var(--accent-strong);
                margin-bottom: 0.35rem;
            }
            .app-hero h1, .section-card h3, .metric-value {
                color: var(--ink) !important;
            }
            .app-copy, .metric-note, .section-copy {
                color: #2563eb !important;
            }
            .metric-card {
                padding: 1rem 1rem 0.9rem;
                min-height: 160px;
                margin-bottom: 0.8rem;
            }
            .metric-label {
                font-size: 0.74rem;
                color: var(--muted) !important;
                margin-bottom: 0.35rem;
            }
            .metric-value {
                font-size: 1.55rem;
                font-weight: 800;
                line-height: 1.08;
                margin-bottom: 0.4rem;
            }
            .section-card {
                padding: 1rem 1.05rem;
                margin-bottom: 1rem;
            }
            .stButton > button, .stDownloadButton > button, .stFormSubmitButton > button {
                background: var(--accent-strong) !important;
                color: #f8fafc !important;
                border: 1px solid var(--accent-strong) !important;
                border-radius: 14px;
                font-weight: 700;
            }
            .stTextInput input, .stTextArea textarea, div[data-baseweb="select"] > div {
                background: #ffffff !important;
                color: var(--ink) !important;
                border-color: rgba(20, 32, 51, 0.14) !important;
            }
            div[data-testid="stFileUploader"] {
                background: linear-gradient(180deg, rgba(5, 12, 24, 0.98), rgba(9, 20, 38, 0.98)) !important;
                border: 1px solid rgba(96, 165, 250, 0.16) !important;
                border-radius: 18px !important;
                padding: 0.8rem !important;
                box-shadow: 0 18px 34px rgba(0, 0, 0, 0.18) !important;
                margin-bottom: 0.95rem !important;
            }
            div[data-testid="stFileUploader"] [data-testid="stFileUploaderDropzoneInstructions"] > div,
            div[data-testid="stFileUploader"] small {
                color: #dbe8ff !important;
            }
            .evidence-card {
                background: linear-gradient(180deg, rgba(5, 12, 24, 0.98), rgba(9, 20, 38, 0.98));
                border: 1px solid rgba(96, 165, 250, 0.18);
                box-shadow: 0 18px 34px rgba(0, 0, 0, 0.22);
                border-radius: 18px;
                padding: 1rem;
                margin-bottom: 0.9rem;
            }
            .evidence-card * {
                color: #eef4ff !important;
            }
            .evidence-kicker {
                text-transform: uppercase;
                letter-spacing: 0.12em;
                font-size: 0.68rem;
                color: #93c5fd !important;
                font-weight: 700;
                margin-bottom: 0.35rem;
            }
            .evidence-meta {
                color: #c7d8ff !important;
                font-size: 0.82rem;
                margin-bottom: 0.55rem;
                font-weight: 600;
            }
            .status-panel {
                background: #ffffff;
                border: 1px solid var(--surface-line);
                border-radius: 20px;
                padding: 1rem 1.05rem;
                box-shadow: var(--shadow);
                margin-bottom: 1rem;
            }
            .status-panel-dark {
                background: linear-gradient(180deg, rgba(5, 12, 24, 0.98), rgba(9, 20, 38, 0.98));
                border: 1px solid rgba(96, 165, 250, 0.18);
            }
            .status-title {
                color: var(--ink) !important;
                font-size: 1.05rem;
                font-weight: 800;
                margin-bottom: 0.8rem;
            }
            .status-title-light {
                color: #eef4ff !important;
            }
            .status-row {
                display: flex;
                justify-content: space-between;
                gap: 1rem;
                padding: 0.35rem 0;
                color: var(--muted) !important;
                border-bottom: 1px dashed rgba(20, 32, 51, 0.08);
            }
            .status-row strong {
                color: var(--ink) !important;
                text-align: right;
            }
            .status-row-light {
                color: #bdd2ff !important;
                border-bottom: 1px dashed rgba(147, 197, 253, 0.12);
            }
            .status-row-light strong {
                color: #ffffff !important;
            }
            .answer-badge {
                display: inline-block;
                background: rgba(37, 99, 235, 0.14);
                color: #dbeafe;
                border: 1px solid rgba(96, 165, 250, 0.25);
                border-radius: 999px;
                padding: 0.28rem 0.7rem;
                font-size: 0.82rem;
                margin-bottom: 0.9rem;
                font-weight: 700;
            }
            </style>
            """
        ),
        unsafe_allow_html=True,
    )
