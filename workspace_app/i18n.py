"""Translate application copy for the current Streamlit session."""
import json
from functools import lru_cache
from pathlib import Path

import streamlit as st


@lru_cache(maxsize=1)
def _english():
    path = Path(__file__).with_name("resources") / "en.json"
    return json.loads(path.read_text(encoding="utf-8"))


def language():
    return st.session_state.get("workspace-language", "id")


def tr(message, *values):
    """Format after lookup so project names and other input stay literal."""
    if language() == "en":
        message = _english().get(message, message)
    return message.format(*values) if values else message


def language_selector():
    names = {"id": "Bahasa Indonesia", "en": "English"}
    if "workspace-language" not in st.session_state:
        initial = st.query_params.get("lang", st.context.cookies.get("workspace_language", "id"))
        st.session_state["workspace-language"] = initial if initial in names else "id"
    selected = st.selectbox("Bahasa / Language", list(names), key="workspace-language",
                            format_func=names.get)
    if st.query_params.get("lang") != selected:
        st.query_params["lang"] = selected
    # Only the two allowlisted locale codes can reach this first-party preference cookie.
    if selected in names:
        st.html(f'<script>document.cookie = "workspace_language={selected}; Max-Age=31536000; '
                'Path=/; SameSite=Lax";</script>', unsafe_allow_javascript=True)
