"""Compliance test for the push-protection `gitignore_secrets_block` check.

The org push-protection standard requires every repo's `.gitignore` to carry a
baseline set of secret patterns (see
standards/push-protection.md#required-gitignore-entries). This test guards
against regressing that requirement.
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
GITIGNORE = ROOT / ".gitignore"

# Minimum secret patterns the compliance check enforces.
REQUIRED_PATTERNS = [".env", "*.pem", "*.key"]

# These are static file checks — they never hit the live API, so they run
# regardless of whether BROODMINDER_API_KEY is set (see conftest.py).
pytestmark = pytest.mark.offline


def _gitignore_lines() -> list[str]:
    text = GITIGNORE.read_text(encoding="utf-8")
    return [line.strip() for line in text.splitlines()]


def test_gitignore_exists():
    assert GITIGNORE.is_file(), ".gitignore must exist at the repo root"


@pytest.mark.parametrize("pattern", REQUIRED_PATTERNS)
def test_gitignore_contains_required_pattern(pattern):
    lines = _gitignore_lines()
    assert pattern in lines, f".gitignore is missing required secret pattern: {pattern}"


def test_gitignore_contains_pem():
    assert "*.pem" in _gitignore_lines(), ".gitignore must ignore *.pem key material"
