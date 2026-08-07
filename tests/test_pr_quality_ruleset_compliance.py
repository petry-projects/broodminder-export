"""Compliance test for the `pr-quality` ruleset's `require_last_push_approval`.

The org rulesets standard codifies the `pr-quality` branch ruleset in
`standards/rulesets/pr-quality.json` (the source of truth). One of its
`pull_request` rule parameters, `require_last_push_approval`, must be `true`:
after a reviewer approves, any subsequent push must be re-approved before the
PR can merge (standards/github-settings.md#pr-quality--standard-ruleset-all-repositories).

Rulesets live in the GitHub control plane, not in a file inside this repo, so
there is nothing on disk to assert against. This test therefore mirrors
``test_repo_settings_compliance.py``:

  * unit-tests the compliance predicate offline (always runs), and
  * verifies the *live* ruleset via the GitHub REST API when a token is present,
    skipping gracefully otherwise — green out of the box, real check when
    credentialed.

Regression guard for issue #92 (ruleset `pr-quality` drifted to
`require_last_push_approval: false`).
"""

from __future__ import annotations

import os

import pytest

# Not a BroodMinder contract test; manages its own GitHub credential (or skips),
# so exempt it from the BROODMINDER_API_KEY blanket skip.
pytestmark = pytest.mark.compliance

REPO_SLUG = os.environ.get("GITHUB_REPOSITORY", "petry-projects/broodminder-export")
RULESET_NAME = "pr-quality"
PARAM = "require_last_push_approval"


def ruleset_param_enabled(rule_params: dict, param: str) -> bool:
    """Return a ruleset parameter's boolean value, defaulting to ``False``.

    A missing parameter is treated as *not enabled* so the compliance assertion
    fails closed rather than silently passing on an unexpected API payload.
    Only the boolean ``True`` is accepted; truthy non-boolean values (e.g. the
    string ``"false"``) are treated as non-compliant.
    """
    return rule_params.get(param, False) is True


def pull_request_params(ruleset_json: dict) -> dict:
    """Extract the ``pull_request`` rule's parameters from a ruleset payload.

    Returns an empty dict when the rule is absent so the compliance predicate
    fails closed on an unexpected payload shape.
    """
    for rule in ruleset_json.get("rules", []) or []:
        if rule.get("type") == "pull_request":
            return rule.get("parameters", {}) or {}
    return {}


# --- offline predicate tests (always run; give the guard teeth) --------------


def test_predicate_true_when_enabled():
    assert ruleset_param_enabled({PARAM: True}, PARAM) is True


def test_predicate_false_when_disabled():
    # The exact drift this issue flags: the param present but set to False.
    assert ruleset_param_enabled({PARAM: False}, PARAM) is False


def test_predicate_false_when_missing():
    assert ruleset_param_enabled({}, PARAM) is False


def test_predicate_false_when_truthy_string():
    # The string "false" is truthy in Python; strict `is True` must reject it.
    assert ruleset_param_enabled({PARAM: "false"}, PARAM) is False


def test_pull_request_params_extracted():
    ruleset = {
        "rules": [
            {"type": "creation", "parameters": {}},
            {"type": "pull_request", "parameters": {PARAM: True}},
        ]
    }
    assert pull_request_params(ruleset) == {PARAM: True}


def test_pull_request_params_empty_when_rule_absent():
    assert pull_request_params({"rules": [{"type": "creation", "parameters": {}}]}) == {}


# --- live guard against the real ruleset (skips without a token) --------------


def _github_token() -> str | None:
    return os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")


def test_require_last_push_approval_enabled_live():
    token = _github_token()
    if not token:
        pytest.skip("no GH_TOKEN/GITHUB_TOKEN in env; live ruleset check skipped")

    import httpx

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    # Only a successful read lets us make a compliance judgement. Any other
    # status (auth, not-found, rate limit, GitHub 5xx) means we *cannot verify*,
    # so skip rather than fail the suite on transient/credential issues.
    try:
        listing = httpx.get(
            f"https://api.github.com/repos/{REPO_SLUG}/rulesets",
            headers=headers,
            timeout=15,
        )
    except httpx.HTTPError as exc:  # network unavailable — don't fail the suite
        pytest.skip(f"could not reach GitHub API: {exc}")

    if listing.status_code != 200:
        pytest.skip(
            f"could not list {REPO_SLUG} rulesets (HTTP {listing.status_code}); "
            "live ruleset check skipped"
        )

    match = next((r for r in listing.json() if r.get("name") == RULESET_NAME), None)
    assert match is not None, (
        f"repository must define the `{RULESET_NAME}` ruleset "
        "(org rulesets standard)"
    )

    # The listing does not include rule parameters; fetch the ruleset by id.
    try:
        detail = httpx.get(
            f"https://api.github.com/repos/{REPO_SLUG}/rulesets/{match['id']}",
            headers=headers,
            timeout=15,
        )
    except httpx.HTTPError as exc:
        pytest.skip(f"could not reach GitHub API: {exc}")

    if detail.status_code != 200:
        pytest.skip(
            f"could not read `{RULESET_NAME}` ruleset (HTTP {detail.status_code}); "
            "live ruleset check skipped"
        )

    params = pull_request_params(detail.json())
    assert ruleset_param_enabled(params, PARAM) is True, (
        f"`{PARAM}` must be enabled on the `{RULESET_NAME}` ruleset — after a "
        "reviewer approves, a subsequent push must be re-approved before merge "
        "(org rulesets standard, pr-quality). Converge with "
        "`scripts/apply-rulesets.sh --repo petry-projects/broodminder-export`."
    )
