"""Compliance test for the org push-protection `gitignore_secrets_block` check.

The org push-protection standard requires every repository's `.gitignore` to
carry the baseline secret patterns so key material and credential files can
never be committed. The compliance audit's minimum is that `.gitignore`
contains at least `.env`, `*.pem`, and `*.key`.

Ref: petry-projects/.github/standards/push-protection.md#required-gitignore-entries

This is a text-based check so it runs without extra dependencies (CI only
installs requirements.txt, which has no YAML/parsing helpers).
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GITIGNORE = ROOT / ".gitignore"

# Baseline secret patterns the push-protection audit expects to find.
BASELINE_SECRET_PATTERNS = (".env", "*.pem", "*.key")


def _gitignore_lines() -> set[str]:
    assert GITIGNORE.exists(), f"{GITIGNORE} is missing"
    return {
        line.strip()
        for line in GITIGNORE.read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }


def test_gitignore_has_pem_pattern():
    # The `gitignore_secrets_block` finding for this repo flagged the missing
    # `*.pem` TLS/SSH key-material pattern specifically.
    assert "*.pem" in _gitignore_lines(), ".gitignore must ignore `*.pem` key material"


def test_gitignore_has_baseline_secret_patterns():
    lines = _gitignore_lines()
    missing = [p for p in BASELINE_SECRET_PATTERNS if p not in lines]
    assert not missing, f".gitignore is missing baseline secret patterns: {missing}"
