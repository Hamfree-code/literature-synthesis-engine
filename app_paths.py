"""Cross-platform paths for the bundled app.

RESOURCE_DIR: where bundled resources (prompts, templates) live.
              In dev: project root. In PyInstaller bundle: sys._MEIPASS.

APP_DATA_DIR: where the app writes runtime data (papers.jsonl, checkpoints, reports).
              On Windows: %LOCALAPPDATA%\\HamsCoResearch\\LongCovid
              Otherwise: ~/HamsCoResearch/LongCovid
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def _resource_dir() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent


def _app_data_dir() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "HamsCoResearch" / "LongCovid"
    return Path.home() / "HamsCoResearch" / "LongCovid"


def _user_desktop() -> Path:
    if sys.platform == "win32":
        # Try OneDrive Escritorio first, then English Desktop, then home as fallback.
        home = Path.home()
        for cand in [
            home / "OneDrive" / "Escritorio",
            home / "OneDrive" / "Desktop",
            home / "Desktop",
        ]:
            if cand.exists():
                return cand
        return home
    return Path.home() / "Desktop"


RESOURCE_DIR = _resource_dir()
APP_DATA_DIR = _app_data_dir()
USER_DESKTOP = _user_desktop()

APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
(APP_DATA_DIR / "data" / "raw").mkdir(parents=True, exist_ok=True)
(APP_DATA_DIR / "data" / "filtered").mkdir(parents=True, exist_ok=True)
(APP_DATA_DIR / "data" / "checkpoints").mkdir(parents=True, exist_ok=True)
(APP_DATA_DIR / "reports").mkdir(parents=True, exist_ok=True)


def resource(*parts: str) -> Path:
    return RESOURCE_DIR.joinpath(*parts)


def app_data(*parts: str) -> Path:
    return APP_DATA_DIR.joinpath(*parts)
