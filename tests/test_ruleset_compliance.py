"""Compliance test for the `pr-quality` ruleset's `dismiss_stale_reviews_on_push`.

The org rulesets standard codifies the `pr-quality` ruleset in
`standards/rulesets/pr-quality.json` (source of truth), which requires the
pull-request rule's **`dismiss_stale_reviews_on_push` = `true`** so that a push
to a PR branch dismisses previously-approving reviews and forces re-review of
the new code (standards/github-settings.md#pr-quality--standard-ruleset). The
org's `scripts/apply-rulesets.sh` converges the live ruleset; this repo cannot
mutate the GitHub control plane from its working tree, so — mirroring
``test_repo_settings_compliance.py`` — this guard:

  * unit-tests the compliance predicate offline (always runs, fails closed), and
  * verifies the *live* ruleset via the GitHub Rulesets REST API when a token is
    present, skipping gracefully otherwise (green out of the box, real check when
    credentialed).

A ruleset lives in the GitHub control plane, not on disk, so there is nothing
static to assert against; the live half is the real drift guard and the offline
predicate gives it teeth. This is a regression guard for issue #91.

Ref: petry-projects/.github/standards/github-settings.md#pr-quality--standard-ruleset-all-repositories
"""

from __future__ import annotations

import os

import pytest

# Not a BroodMinder contract test; manages its own GitHub credential (or skips),
# so exempt from the BROODMINDER_API_KEY blanket skip.
pytestmark = pytest.mark.compliance

REPO_SLUG = os.environ.get("GITHUB_REPOSITORY", "petry-projects/broodminder-export")

# The codified `pr-quality` ruleset name and the required pull-request parameter.
RULESET_NAME = "pr-quality"
PARAM = "dismiss_stale_reviews_on_push"


def pr_rule_param_enabled(ruleset_json: dict, param: str) -> bool:
    """Return a ``pull_request`` rule parameter's boolean value from a ruleset.

    Given the JSON of a single ruleset (as returned by
    ``GET /repos/{repo}/rulesets/{id}``), locate the ``pull_request`` rule in its
    ``rules`` array and return ``param``'s value, defaulting to ``False`` when the
    ruleset, the rule, or the parameter is absent. Fails closed: only the boolean
    ``True`` counts as enabled, so a missing rule or a truthy non-boolean (e.g. the
    string ``"true"``) is reported as non-compliant rather than silently passing.
    """
    for rule in ruleset_json.get("rules", []) or []:
        if rule.get("type") == "pull_request":
            return (rule.get("parameters", {}) or {}).get(param, False) is True
    return False


# --- offline predicate tests (always run; give the guard teeth) --------------


def _ruleset_with_param(value):
    """Build a minimal ruleset payload whose pull_request rule sets PARAM=value."""
    return {
        "name": RULESET_NAME,
        "rules": [
            {"type": "required_linear_history", "parameters": {}},
            {"type": "pull_request", "parameters": {PARAM: value}},
        ],
    }


def test_predicate_true_when_enabled():
    assert pr_rule_param_enabled(_ruleset_with_param(True), PARAM) is True


def test_predicate_false_when_disabled():
    # A non-compliant payload must be reported as non-compliant.
    assert pr_rule_param_enabled(_ruleset_with_param(False), PARAM) is False


def test_predicate_false_when_param_missing():
    ruleset = {"name": RULESET_NAME, "rules": [{"type": "pull_request", "parameters": {}}]}
    assert pr_rule_param_enabled(ruleset, PARAM) is False


def test_predicate_false_when_pull_request_rule_missing():
    ruleset = {"name": RULESET_NAME, "rules": [{"type": "deletion", "parameters": {}}]}
    assert pr_rule_param_enabled(ruleset, PARAM) is False


def test_predicate_false_when_rules_absent():
    assert pr_rule_param_enabled({"name": RULESET_NAME}, PARAM) is False


def test_predicate_false_when_truthy_string():
    # The string "true" is truthy in Python; strict `is True` must reject it.
    assert pr_rule_param_enabled(_ruleset_with_param("true"), PARAM) is False


# --- live guard against the real ruleset (skips without a token) --------------


def _github_token() -> str | None:
    return os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")


def test_pr_quality_dismiss_stale_reviews_on_push_live():
    token = _github_token()
    if not token:
        pytest.skip("no GH_TOKEN/GITHUB_TOKEN in env; live ruleset check skipped")

    import httpx

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    try:
        listing = httpx.get(
            f"https://api.github.com/repos/{REPO_SLUG}/rulesets",
            headers=headers,
            timeout=15,
        )
    except httpx.HTTPError as exc:  # network unavailable — don't fail the suite
        pytest.skip(f"could not reach GitHub API: {exc}")

    # Only a successful read lets us make a compliance judgement. Any other status
    # (auth, not-found, rate limit, GitHub 5xx) means we *cannot verify*, so skip
    # rather than fail the suite on transient/credential issues.
    if listing.status_code != 200:
        pytest.skip(
            f"could not list {REPO_SLUG} rulesets (HTTP {listing.status_code}); "
            "live ruleset check skipped"
        )

    ruleset_id = next(
        (rs.get("id") for rs in listing.json() if rs.get("name") == RULESET_NAME),
        None,
    )
    assert ruleset_id is not None, (
        f"repository {REPO_SLUG} has no '{RULESET_NAME}' ruleset — the org standard "
        "requires it (github-settings.md#pr-quality); run "
        f"scripts/apply-rulesets.sh --repo {REPO_SLUG} to converge"
    )

    try:
        detail = httpx.get(
            f"https://api.github.com/repos/{REPO_SLUG}/rulesets/{ruleset_id}",
            headers=headers,
            timeout=15,
        )
    except httpx.HTTPError as exc:
        pytest.skip(f"could not reach GitHub API: {exc}")

    if detail.status_code != 200:
        pytest.skip(
            f"could not read '{RULESET_NAME}' ruleset detail (HTTP {detail.status_code}); "
            "live ruleset check skipped"
        )

    assert pr_rule_param_enabled(detail.json(), PARAM) is True, (
        f"ruleset '{RULESET_NAME}' parameter '{PARAM}' must be true on {REPO_SLUG} — "
        "the codified standard (standards/rulesets/pr-quality.json) is the source of "
        f"truth; run scripts/apply-rulesets.sh --repo {REPO_SLUG} to converge (issue #91)"
    )
