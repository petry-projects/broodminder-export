"""Guard tests for the Dev-Lead Agent caller workflow (.github/workflows/dev-lead.yml).

Fleet Monitor applies several labels (dev-lead, fleet-tracker, health-check) to a
tracking issue in one burst. Each label add fires a separate `issues: [labeled]`
run; because they share the reusable workflow's per-issue concurrency lane
(cancel-in-progress), the redundant runs cancel each other — including the real
`dev-lead` pickup. See issue #7.

The fix is a job-level `if:` guard that proceeds for every event EXCEPT an
`issues` event whose triggering label is not `dev-lead`. These tests pin that
behavior by evaluating the actual guard expression against representative event
contexts, so a regression that widens or breaks the gate fails CI.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

WORKFLOW = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "dev-lead.yml"

# The single label that must route an `issues` event to the engine — mirrors the
# `case "$label_name" in dev-lead)` gate in dev-lead-intent.sh.
DEV_LEAD_LABEL = "dev-lead"


# ── a small GitHub Actions `if:` expression evaluator ────────────────────────
# Supports the subset used by the guard: context access (github.<dotted.path>),
# single-quoted string literals, == / != comparisons, ! negation, && / ||, and
# parentheses. GitHub treats a missing context value as null; we model that as
# Python None and compare with string coercion, matching Actions' loose equality
# for the value shapes this guard uses.

_TOKEN_RE = re.compile(
    r"""\s*(?:
        (?P<lparen>\()
      | (?P<rparen>\))
      | (?P<or>\|\|)
      | (?P<and>&&)
      | (?P<eq>==)
      | (?P<neq>!=)
      | (?P<not>!)
      | (?P<str>'(?:[^']|'')*')
      | (?P<ident>[A-Za-z_][A-Za-z0-9_.\-]*)
    )""",
    re.VERBOSE,
)


def _tokenize(expr: str):
    pos = 0
    tokens = []
    while pos < len(expr):
        if expr[pos].isspace():
            pos += 1
            continue
        m = _TOKEN_RE.match(expr, pos)
        if not m:
            raise ValueError(f"cannot tokenize at: {expr[pos:]!r}")
        kind = m.lastgroup
        tokens.append((kind, m.group(kind)))
        pos = m.end()
    tokens.append(("end", ""))
    return tokens


class _Parser:
    def __init__(self, tokens, context):
        self.toks = tokens
        self.i = 0
        self.ctx = context

    def _peek(self):
        return self.toks[self.i]

    def _next(self):
        tok = self.toks[self.i]
        self.i += 1
        return tok

    def parse(self):
        val = self._parse_or()
        if self._peek()[0] != "end":
            raise ValueError(f"trailing tokens: {self.toks[self.i:]}")
        return val

    def _parse_or(self):
        val = self._parse_and()
        while self._peek()[0] == "or":
            self._next()
            rhs = self._parse_and()
            val = bool(val) or bool(rhs)
        return val

    def _parse_and(self):
        val = self._parse_not()
        while self._peek()[0] == "and":
            self._next()
            rhs = self._parse_not()
            val = bool(val) and bool(rhs)
        return val

    def _parse_not(self):
        if self._peek()[0] == "not":
            self._next()
            return not bool(self._parse_not())
        return self._parse_cmp()

    def _parse_cmp(self):
        left = self._parse_atom()
        kind = self._peek()[0]
        if kind in ("eq", "neq"):
            self._next()
            right = self._parse_atom()
            equal = self._loose_eq(left, right)
            return equal if kind == "eq" else not equal
        return left

    def _parse_atom(self):
        kind, val = self._next()
        if kind == "lparen":
            inner = self._parse_or()
            if self._next()[0] != "rparen":
                raise ValueError("missing )")
            return inner
        if kind == "str":
            return val[1:-1].replace("''", "'")
        if kind == "ident":
            return self._resolve(val)
        raise ValueError(f"unexpected token {kind!r} ({val!r})")

    def _resolve(self, ident):
        if ident in ("true", "false"):
            return ident == "true"
        if ident == "null":
            return None
        parts = ident.split(".")
        cur = self.ctx
        for p in parts:
            if isinstance(cur, dict) and p in cur:
                cur = cur[p]
            else:
                return None
        return cur

    @staticmethod
    def _loose_eq(a, b):
        if a is None or b is None:
            return a is None and b is None
        return str(a) == str(b)


def eval_if(expr: str, context: dict) -> bool:
    return bool(_Parser(_tokenize(expr), context).parse())


def _ctx(event_name: str, label_name: str | None = None) -> dict:
    event: dict = {}
    if label_name is not None:
        event["label"] = {"name": label_name}
    return {"github": {"event_name": event_name, "event": event}}


# ── fixtures ─────────────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text())


@pytest.fixture(scope="module")
def guard(workflow) -> str:
    job = workflow["jobs"]["dev-lead"]
    assert "if" in job, "dev-lead job must carry an `if:` guard (issue #7)"
    return str(job["if"]).strip()


# ── self-check: the tiny evaluator behaves like GitHub for our operators ──────
def test_evaluator_basics():
    assert eval_if("github.event_name == 'issues'", _ctx("issues")) is True
    assert eval_if("github.event_name != 'issues'", _ctx("issues")) is False
    assert eval_if("github.event_name != 'issues'", _ctx("push")) is True
    # missing context value is null -> not equal to a string literal
    assert eval_if("github.event.label.name == 'dev-lead'", _ctx("push")) is False
    assert eval_if(
        "github.event_name != 'issues' || github.event.label.name == 'dev-lead'",
        _ctx("issues", "dev-lead"),
    ) is True


# ── behavioral tests against the real guard ──────────────────────────────────
def test_dev_lead_labeled_issue_runs(guard):
    """The whole point: a dev-lead-labeled issue must still be picked up."""
    assert eval_if(guard, _ctx("issues", DEV_LEAD_LABEL)) is True


@pytest.mark.parametrize("label", ["fleet-tracker", "health-check", "bug", "wontfix"])
def test_non_dev_lead_labeled_issue_is_skipped(guard, label):
    """Redundant Fleet Monitor / arbitrary labels must not spin up a run."""
    assert eval_if(guard, _ctx("issues", label)) is False


@pytest.mark.parametrize(
    "event_name",
    ["pull_request", "pull_request_review", "pull_request_review_comment",
     "issue_comment", "check_run", "repository_dispatch"],
)
def test_other_events_always_run(guard, event_name):
    """The guard must only gate `issues`; every other trigger proceeds."""
    assert eval_if(guard, _ctx(event_name)) is True
