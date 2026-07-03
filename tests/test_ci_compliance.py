"""Compliance tests for the CI secret-scan job.

Enforces the org push-protection standard's `secret_scan_ci_job_present`
requirement: the primary CI workflow must run gitleaks, and the repo must
ship a root .gitleaks.toml so `--config .gitleaks.toml` resolves.

Ref: petry-projects/.github/standards/push-protection.md#required-ci-job

These are text-based checks so they run without extra dependencies (CI only
installs requirements.txt, which has no YAML parser).
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
GITLEAKS_CONFIG = ROOT / ".gitleaks.toml"


def _ci_text() -> str:
    assert CI_WORKFLOW.exists(), f"{CI_WORKFLOW} is missing"
    return CI_WORKFLOW.read_text()


def test_ci_has_gitleaks_secret_scan_job():
    text = _ci_text()
    assert "secret-scan:" in text, "ci.yml must declare a `secret-scan` job"
    assert "gitleaks detect" in text, "secret-scan job must run `gitleaks detect`"
    assert "--config .gitleaks.toml" in text, "gitleaks must use the repo .gitleaks.toml"
    assert "--exit-code 1" in text, "gitleaks must fail the build on detection"
    assert "--redact" in text, "gitleaks must redact leaked values from logs"


def test_secret_scan_job_uses_checksum_verified_install():
    text = _ci_text()
    assert "GITLEAKS_VERSION" in text, "install step must pin a gitleaks version"
    assert "GITLEAKS_CHECKSUM" in text, "install step must verify a checksum (GITLEAKS_CHECKSUM)"
    assert "sha256sum -c" in text, "install step must verify the download with sha256sum -c"
    assert "fetch-depth: 0" in text, "checkout must fetch full history for a complete scan"


def test_gitleaks_config_present():
    assert GITLEAKS_CONFIG.exists(), ".gitleaks.toml must exist at the repo root"
    text = GITLEAKS_CONFIG.read_text()
    assert "[allowlist]" in text, ".gitleaks.toml must define an [allowlist] section"
