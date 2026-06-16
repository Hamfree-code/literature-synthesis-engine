"""Pytest bootstrap for the Literature Synthesis Engine test suite.

Ensures the repository root is importable so tests can ``import methodology``
and friends, and installs harmless dummy credentials so that importing a module
which transitively pulls in ``config.settings`` (which declares a required
``ANTHROPIC_API_KEY``) does not explode in CI where no ``.env`` is present.

The v3.2 methodology engines under ``methodology/`` are deliberately free of
settings/network/app_paths dependencies so they unit-test in isolation; the
dummy env below is purely defensive for any incidental transitive import.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Defensive dummy credentials — never used for real calls in unit tests.
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-not-real")
os.environ.setdefault("NCBI_EMAIL", "test@example.com")
