"""
Config helper for Incident Response Copilot.

Reads secrets from Streamlit when deployed, falls back to .env locally.
Import get_secret() instead of os.environ.get() for any API key.
"""

from __future__ import annotations

import os
from dotenv import load_dotenv

load_dotenv()


def get_secret(key: str) -> str | None:
    """
    Get a secret by key.
    Tries Streamlit secrets first (production), falls back to environment variables (local).
    """
    try:
        import streamlit as st
        return st.secrets.get(key)
    except Exception:
        return os.environ.get(key)