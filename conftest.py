"""Pytest bootstrap.

`config.settings` requires ANTHROPIC_API_KEY at import time, and several
pipeline modules construct an Anthropic client at module load. Tests of the
pure (offline) functions must not depend on real credentials, so we inject a
dummy key before anything imports `config.settings`. A real key in `.env`
still takes precedence for anyone running an end-to-end pass.
"""
from __future__ import annotations

import os

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-not-used-offline")
