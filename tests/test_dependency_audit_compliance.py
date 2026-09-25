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
DEPENDENCY_AUDIT_WORKFLOW = ROOT / ".github" / "workflows" / "dependency-audit.yml"

# The dependency-audit caller stub is a thin caller adopted verbatim from the org
# standard; its `on:` trigger surface is owned centrally and must not drift. The
# canonical surface is pull_request + push (both filtered to `main`) + merge_group,
# where `merge_group` is required so the `dependency-audit / Detect ecosystems`
# status check reports on a merge queue's `gh-readonly-queue/*` ref. The mapping is
# compared *exactly* (not as a subset): a removed/retargeted `branches:` filter or
# an added event is caught as drift rather than silently accepted. `None` marks a
# trigger with no `branches:` filter.
#
# Ref: petry-projects/.github/standards/ci-standards.md#centralization-tiers
_CANONICAL_ON: dict[str, list[str] | None] = {
    "pull_request": ["main"],
    "push": ["main"],
    "merge_group": None,
}

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


# --- dependency-audit stub `on:` surface guard --------------------------------


def _on_triggers(text: str) -> dict[str, list[str] | None]:
    """Return the ``on:`` trigger mapping: each top-level trigger key mapped to
    its ``branches:`` filter list, or ``None`` when it has no branch filter.

    Text-based (no YAML parser, matching this module's other guards): find the
    ``on:`` mapping, then

    * tolerate optional single/double quotes around ``on`` and its child keys;
    * detect the child-key indent dynamically (``\\s+``) rather than assuming a
      fixed width, so a re-indent of the stub does not fool the guard;
    * skip blank and comment lines — a column-0 ``# comment`` no longer
      prematurely ends the block and falsely reports later triggers missing;
    * capture each trigger's ``branches: [..]`` flow filter so branch drift is
      visible to the exact-match comparison.

    A non-blank, non-comment, non-indented (column-0) line ends the ``on:`` block.
    """
    lines = text.splitlines()
    triggers: dict[str, list[str] | None] = {}
    in_on = False
    indent: str | None = None
    current: str | None = None
    for line in lines:
        if not in_on:
            if re.match(r"^['\"]?on['\"]?:\s*$", line):
                in_on = True
            continue
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        # A non-indented line ends the `on:` block.
        if not line.startswith((" ", "\t")):
            break
        if indent is None:
            m = re.match(r"^(\s+)['\"]?[A-Za-z_]+['\"]?:", line)
            if m:
                indent = m.group(1)
        key = re.match(rf"^{re.escape(indent)}['\"]?([A-Za-z_]+)['\"]?:", line) if indent else None
        if key:
            current = key.group(1)
            triggers[current] = None
            continue
        # A more-deeply-indented line: capture a `branches: [..]` filter for the
        # trigger currently in scope.
        if current is not None:
            bm = re.match(r"^\s+['\"]?branches['\"]?:\s*\[([^\]]*)\]", line)
            if bm:
                triggers[current] = [
                    b.strip().strip("'\"") for b in bm.group(1).split(",") if b.strip()
                ]
    return triggers


def test_dependency_audit_stub_on_surface_matches_standard():
    assert DEPENDENCY_AUDIT_WORKFLOW.exists(), (
        f"{DEPENDENCY_AUDIT_WORKFLOW} is missing — it is the thin caller stub "
        "adopted verbatim from petry-projects/.github standards/workflows/"
    )
    triggers = _on_triggers(DEPENDENCY_AUDIT_WORKFLOW.read_text(encoding="utf-8"))
    assert triggers == _CANONICAL_ON, (
        f"dependency-audit.yml `on:` surface has drifted from the standard: "
        f"expected {_CANONICAL_ON} but found {triggers}. The stub's trigger "
        "surface is owned centrally — re-sync from "
        "standards/workflows/dependency-audit.yml (merge_group is required for "
        "merge-queue status reporting, and both pull_request/push must stay "
        "filtered to `main`)."
    )


def test_on_trigger_parser_flags_missing_merge_group():
    # Teeth: a stub missing `merge_group` must be detected as drift.
    drifted = "on:\n  pull_request:\n    branches: [main]\n  push:\n    branches: [main]\n"
    assert _on_triggers(drifted) == {"pull_request": ["main"], "push": ["main"]}


def test_on_trigger_parser_flags_dropped_branch_filter():
    # Teeth: dropping `branches: [main]` from push must be visible as drift.
    drifted = "on:\n  pull_request:\n    branches: [main]\n  push:\n  merge_group:\n"
    assert _on_triggers(drifted) != _CANONICAL_ON
    assert _on_triggers(drifted)["push"] is None


def test_on_trigger_parser_flags_retargeted_branch_filter():
    # Teeth: retargeting a filter to another branch must be visible as drift.
    drifted = (
        "on:\n  pull_request:\n    branches: [develop]\n"
        "  push:\n    branches: [main]\n  merge_group:\n"
    )
    assert _on_triggers(drifted) != _CANONICAL_ON


def test_on_trigger_parser_flags_added_event():
    # Teeth: an extra unexpected trigger must be visible as drift.
    drifted = (
        "on:\n  pull_request:\n    branches: [main]\n  push:\n    branches: [main]\n"
        "  merge_group:\n  schedule:\n"
    )
    assert _on_triggers(drifted) != _CANONICAL_ON
    assert "schedule" in _on_triggers(drifted)


def test_on_trigger_parser_ignores_column0_comment():
    # Regression (codeant/gemini): a column-0 comment inside/after the `on:` block
    # must not end parsing and drop later triggers.
    text = (
        "on:\n  pull_request:\n    branches: [main]\n"
        "# a stray column-0 comment\n  push:\n    branches: [main]\n  merge_group:\n"
    )
    assert _on_triggers(text) == _CANONICAL_ON


def test_on_trigger_parser_tolerates_alternate_indent_and_quotes():
    # Robustness (gemini): a 4-space indent and quoted keys parse identically.
    text = (
        "'on':\n    'pull_request':\n        branches: [main]\n"
        "    push:\n        branches: [main]\n    merge_group:\n"
    )
    assert _on_triggers(text) == _CANONICAL_ON
