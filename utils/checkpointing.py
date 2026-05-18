"""Resume-from-crash logic via simple marker files."""
from __future__ import annotations
# __APP_PATHS_INSTALLED__
from app_paths import app_data, resource

from pathlib import Path

CHECKPOINT_DIR = app_data("data/checkpoints")
CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)


class Checkpoint:
    def __init__(self, name: str) -> None:
        self.path = CHECKPOINT_DIR / f"{name}.done"

    def is_complete(self) -> bool:
        return self.path.exists()

    def mark_complete(self) -> None:
        self.path.touch()

    def reset(self) -> None:
        self.path.unlink(missing_ok=True)