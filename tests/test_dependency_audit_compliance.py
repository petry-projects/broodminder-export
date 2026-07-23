"""Compliance test for the pip-audit requirements file the dependency-audit
workflow depends on.

``.github/workflows/dependency-audit.yml`` is a thin caller stub adopted verbatim
from the org standards; all logic lives in the reusable workflow
(``petry-projects/.github`` → ``dependency-audit-reusable.yml``). That reusable's
``pip-audit`` step installs its tooling with::

    pip install --require-hashes --only-binary :all: -r scripts/pip-audit-requirements.txt

so the audit job is only green when ``scripts/pip-audit-requirements.txt`` exists
and is valid for ``--require-hashes`` mode (every requirement pinned to an exact
version and carrying at least one SHA-256 hash, with ``pip-audit`` itself pinned).

Issue #68 (Fleet Monitor) flagged a dependency-audit failure whose sole failing
run errored with ``Could not open requirements file: ...
'scripts/pip-audit-requirements.txt'`` — the file was transiently absent. This
guard is a local regression check so that file cannot silently disappear or lose
its hashes between org audits. The stub workflow must not be edited (its trigger
events, ``uses:`` line, and job name are a required status check), so the invariant
is enforced here instead.

It is text-based so it runs with only ``requirements.txt`` installed (no YAML or
packaging parser needed), mirroring ``test_ci_compliance.py`` and
``test_gitignore_secrets_compliance.py``.

Ref: petry-projects/.github/standards/ci-standards.md#7-dependency-audit-dependency-audityml
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REQUIREMENTS = ROOT / "scripts" / "pip-audit-requirements.txt"

# The reusable dependency-audit workflow installs pip-audit from this file with
# `--require-hashes`, so it must be pinned by exact version like the rest.
REQUIRED_PIN = "pip-audit=="

# A requirement line opens a pinned spec: `name==version [\]`. Hash lines and
# blank/comment lines are continuations or noise, not new requirements.
_PIN_LINE = re.compile(r"^(?P<name>[A-Za-z0-9._-]+)==(?P<version>[^\s\\]+)")
_HASH = re.compile(r"--hash=sha256:[0-9a-f]{64}", re.IGNORECASE)


def _requirements_text() -> str:
    """Read the requirements file, returning ``""`` when it is absent.

    Fail closed: a missing file yields empty text so parsing tests report a clean
    compliance violation (``requirements_violations`` flags it) instead of a raw
    ``FileNotFoundError`` traceback. ``test_pip_audit_requirements_file_exists``
    remains the primary, explicit guard for existence.
    """
    return REQUIREMENTS.read_text(encoding="utf-8") if REQUIREMENTS.exists() else ""


def requirements_violations(text: str) -> list[str]:
    """Return reasons ``text`` is not a valid ``--require-hashes`` file.

    Fails closed: empty text, a missing ``pip-audit`` pin, any requirement that
    is not pinned with ``==``, or any pinned requirement lacking a SHA-256 hash
    each produce a violation string. An empty list means the file is compliant.
    """
    violations: list[str] = []
    if not text.strip():
        return ["requirements file is empty"]

    # Split into logical requirement blocks: each starts at a `name==` line and
    # runs until the next one, gathering its trailing `--hash=` continuation lines.
    blocks: list[tuple[str, list[str]]] = []
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        m = _PIN_LINE.match(stripped)
        if m:
            blocks.append((m.group("name"), [stripped]))
        elif stripped.startswith("--hash"):
            if blocks:
                blocks[-1][1].append(stripped)
            else:
                violations.append(f"hash line without requirement: {stripped!r}")
        else:
            violations.append(f"unpinned requirement or invalid line: {stripped!r}")

    if not blocks:
        violations.append("no pinned requirements found")

    # Validate pip-audit is present as an actual pinned requirement (not just in a
    # comment), by checking parsed block names rather than raw text.
    _norm = lambda n: re.sub(r"[-_.]+", "-", n).lower()
    if not any(_norm(name) == "pip-audit" for name, _ in blocks):
        violations.append(f"missing exact pin for pip-audit ('{REQUIRED_PIN}')")

    for name, lines in blocks:
        if not any(_HASH.search(line) for line in lines):
            violations.append(f"requirement {name!r} is missing a --hash=sha256 entry")

    return violations


# --- guards against the real requirements file -------------------------------


def test_pip_audit_requirements_file_exists():
    assert REQUIREMENTS.exists(), (
        f"{REQUIREMENTS} is missing — the reusable dependency-audit workflow "
        "installs pip-audit from it with `pip install --require-hashes -r "
        "scripts/pip-audit-requirements.txt`; without it the audit job fails "
        "(issue #68)"
    )


def test_pip_audit_requirements_is_hash_pinned():
    violations = requirements_violations(_requirements_text())
    assert not violations, (
        "scripts/pip-audit-requirements.txt is not valid for `--require-hashes` "
        f"install: {violations} — regenerate it with pip-compile "
        "--generate-hashes (see the file header)"
    )


# --- predicate teeth: prove the guard fails on non-compliant content ----------


def test_predicate_flags_missing_file_content():
    assert requirements_violations("") == ["requirements file is empty"]


def test_predicate_flags_missing_pip_audit_pin():
    # A validly-hashed but wrong package (no pip-audit) must be rejected.
    text = (
        "requests==2.34.2 \\\n"
        "    --hash=sha256:" + "a" * 64 + "\n"
    )
    violations = requirements_violations(text)
    assert any("pip-audit" in v for v in violations), violations


def test_predicate_flags_pip_audit_pin_in_comment_only():
    # A comment containing 'pip-audit==' must not satisfy the pip-audit pin guard.
    text = (
        "# pip-audit==2.9.0 must be pinned\n"
        "requests==2.34.2 \\\n"
        "    --hash=sha256:" + "a" * 64 + "\n"
    )
    violations = requirements_violations(text)
    assert any("pip-audit" in v for v in violations), violations


def test_predicate_flags_requirement_without_hash():
    text = "pip-audit==2.9.0\nrequests==2.34.2\n"
    violations = requirements_violations(text)
    assert any("requests" in v and "hash" in v for v in violations), violations


def test_predicate_flags_unpinned_line_after_pinned_block():
    # Regression: an unpinned name after a valid hash-pinned block must be flagged,
    # not silently merged into the preceding block's hash list.
    text = (
        "pip-audit==2.9.0 \\\n"
        "    --hash=sha256:" + "b" * 64 + "\n"
        "requests\n"
    )
    violations = requirements_violations(text)
    assert any("requests" in v for v in violations), violations


def test_predicate_flags_hash_line_without_requirement():
    # A bare --hash line before any pinned block is not valid.
    text = "--hash=sha256:" + "c" * 64 + "\npip-audit==2.9.0 \\\n    --hash=sha256:" + "d" * 64 + "\n"
    violations = requirements_violations(text)
    assert any("hash line without requirement" in v for v in violations), violations


def test_predicate_accepts_the_real_file():
    # Sanity anchor: the shipped file must be accepted, so the teeth above are
    # exercising genuinely-bad input rather than a perpetually-broken guard.
    assert requirements_violations(_requirements_text()) == []
