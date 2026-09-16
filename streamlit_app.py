# Don't Remove Credit Tg - @NexonBots
# Subscribe YouTube Channel For Amazing Bot https://youtube.com/@NexonBots
# Ask Doubt on telegram @NexonContactBot

"""Streamlit Community Cloud entrypoint for the Telegram polling bot."""

import os
import subprocess
import sys
from pathlib import Path

import streamlit as st


PROJECT_DIR = Path(__file__).resolve().parent


def read_streamlit_secrets() -> dict[str, object]:
    """Return configured secrets without crashing when the file is absent."""
    try:
        return dict(st.secrets)
    except Exception:
        return {}


def load_streamlit_secrets(secrets: dict[str, object]) -> None:
    """Expose flat Streamlit TOML secrets as environment variables."""
    for key, value in secrets.items():
        if isinstance(value, (str, int, float, bool)):
            os.environ[str(key)] = str(value)


@st.cache_resource
def start_bot_process() -> subprocess.Popen:
    """Start exactly one bot child per Streamlit application process."""
    load_streamlit_secrets(read_streamlit_secrets())
    child_environment = os.environ.copy()
    # Streamlit owns its web port; the child only needs Telegram polling.
    child_environment.pop("PORT", None)
    child_environment.setdefault(
        "DATA_DIR", str(PROJECT_DIR / ".runtime_data")
    )
    return subprocess.Popen(
        [sys.executable, str(PROJECT_DIR / "main.py")],
        cwd=str(PROJECT_DIR),
        env=child_environment,
    )


st.set_page_config(page_title="NexTube Bot", page_icon="🎬", layout="centered")
st.title("🎬 NexTube Bot")
st.caption("Advanced Telegram YouTube downloader service")

required_secrets = ("DB_URI", "API_ID", "API_HASH", "BOT_TOKEN", "OWNER_ID")
configured_secrets = read_streamlit_secrets()
missing_secrets = [key for key in required_secrets if not configured_secrets.get(key)]
if missing_secrets:
    st.error("Missing required Streamlit secrets: " + ", ".join(missing_secrets))
    st.info("Open App settings → Secrets and add the required values.")
    st.stop()

bot_process = start_bot_process()
if bot_process.poll() is not None:
    start_bot_process.clear()
    st.error("The Telegram bot process stopped. Reload this page to restart it.")
else:
    st.success("Telegram bot is running")
    st.write(f"Process ID: `{bot_process.pid}`")

st.warning(
    "Streamlit Community Cloud may hibernate inactive apps and uses ephemeral "
    "storage. Use a VPS, Northflank, Railway, or another always-on container "
    "platform for reliable production operation."
)
st.divider()
st.markdown("Telegram: **@NexonBots**")
st.markdown("YouTube: **https://youtube.com/@NexonBots**")
st.markdown("Support: **@NexonContactBot**")
