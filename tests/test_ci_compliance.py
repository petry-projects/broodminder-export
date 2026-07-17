"""Compliance tests for the CI secret-scan job.

Enforces the org push-protection standard's `secret_scan_ci_job_present`
requirement: the primary CI workflow must run gitleaks, and the repo must
ship a root .gitleaks.toml so `--config .gitleaks.toml` resolves.

Ref: petry-projects/.github/standards/push-protection.md#required-ci-job

Also enforces the dev-lead caller-stub channel pin
(`dev-lead-stub-agent-ref`): the stub must pass `with: agent_ref:
dev-lead/<channel>` and pin the reusable's `uses:` ref to the same channel.

Ref: petry-projects/.github/standards/ci-standards.md#dev-lead-agent

These are text-based checks so they run without extra dependencies (CI only
installs requirements.txt, which has no YAML parser).
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
GITLEAKS_CONFIG = ROOT / ".gitleaks.toml"
DEV_LEAD_WORKFLOW = ROOT / ".github" / "workflows" / "dev-lead.yml"

# A dev-lead channel is `stable`, `next`, or `ring<N>` (see ci-standards.md).
DEV_LEAD_CHANNEL = re.compile(r"^dev-lead/(stable|next|ring\d+)$")


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


# --- dev-lead caller-stub channel pin (dev-lead-stub-agent-ref) ---------------


def _dev_lead_text() -> str:
    assert DEV_LEAD_WORKFLOW.exists(), f"{DEV_LEAD_WORKFLOW} is missing"
    return DEV_LEAD_WORKFLOW.read_text()


def test_dev_lead_stub_passes_valid_agent_ref():
    """The stub must pass `with: agent_ref: dev-lead/<channel>` where the
    channel is `stable`, `next`, or `ring<N>` (ci-standards.md#dev-lead-agent)."""
    text = _dev_lead_text()
    m = re.search(r"^\s*agent_ref:\s*(\S+)\s*$", text, re.MULTILINE)
    assert m, "dev-lead.yml must pass `with: agent_ref: dev-lead/<channel>`"
    ref = m.group(1)
    assert DEV_LEAD_CHANNEL.match(ref), (
        f"agent_ref '{ref}' must be a valid dev-lead channel "
        "(dev-lead/stable, dev-lead/next, or dev-lead/ring<N>)"
    )


def test_dev_lead_uses_ref_matches_agent_ref():
    """The reusable `uses:` ref and `agent_ref` must pin the same channel so the
    reusable checks out its own scripts/prompts from the channel it runs."""
    text = _dev_lead_text()
    uses = re.search(
        r"^\s*uses:\s*petry-projects/\.github-private/\.github/workflows/"
        r"dev-lead-reusable\.yml@(\S+)",
        text,
        re.MULTILINE,
    )
    agent = re.search(r"^\s*agent_ref:\s*(\S+)\s*$", text, re.MULTILINE)
    assert uses, "dev-lead.yml must pin the reusable via `uses: ...@<channel>`"
    assert agent, "dev-lead.yml must pass `with: agent_ref: dev-lead/<channel>`"
    assert DEV_LEAD_CHANNEL.match(uses.group(1)), (
        f"uses ref '{uses.group(1)}' must pin a valid dev-lead channel"
    )
    assert uses.group(1) == agent.group(1), (
        f"uses ref '{uses.group(1)}' and agent_ref '{agent.group(1)}' must pin "
        "the same channel"
    )
