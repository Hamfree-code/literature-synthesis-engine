"""Template for bundled credentials — copy this to `bundled_credentials.py` at
the project root and fill in your own keys IF you want to build a self-contained
PyInstaller executable that ships with credentials baked in.

For local development (running `python app_server.py` directly) you do NOT need
this file at all — `config.settings` reads from `.env` via pydantic-settings.

This template is the ONLY safe-to-commit version. The real `bundled_credentials.py`
must NEVER be committed (already covered by `.gitignore`).
"""

import os

CREDS = {
    "ANTHROPIC_API_KEY": "",  # sk-ant-api03-...
    "ANTHROPIC_HAIKU_MODEL": "claude-haiku-4-5-20251001",
    "ANTHROPIC_SONNET_MODEL": "claude-sonnet-4-6",
    "NCBI_API_KEY": "",  # 32-char hex from NCBI account
    "NCBI_EMAIL": "you@example.com",
    "SUPABASE_URL": "https://YOUR-PROJECT.supabase.co",
    "SUPABASE_KEY": "",  # sb_secret_... (service_role) — never publish
    "MAX_PAPERS": "5000",
    "MAX_DEEP_ANALYSIS": "500",
    "BATCH_SIZE": "100",
    "LOG_LEVEL": "WARNING",
}


def install() -> None:
    """Force baked credentials into the environment. Overrides any empty/placeholder
    inherited from a parent shell (e.g. Claude Code sets ANTHROPIC_API_KEY="")."""
    for k, v in CREDS.items():
        existing = os.environ.get(k)
        if not existing:
            os.environ[k] = v
    if not os.environ.get("ANTHROPIC_BASE_URL"):
        os.environ.pop("ANTHROPIC_BASE_URL", None)
