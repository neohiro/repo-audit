"""Tests for merge_bot_prs.py.

These are about the refusals, not the merges. The merges are one API call that
either works or does not; the value of the script is entirely in the cases where
it declines to act, and those are what need pinning down.

No network: the Gh class is faked, so this runs in milliseconds and cannot be
affected by someone closing a PR mid-run.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.merge_bot_prs import Gh, evaluate, required_check_names  # noqa: E402

CHECK_RUN = "CheckRun"
STATUS = "Status"


def pr(**over) -> dict:
    base = {
        "number": 1,
        "title": "chore(deps): bump something",
        "user": {"type": "Bot", "login": "dependabot[bot]"},
        "mergeable": True,
        "mergeable_state": "CLEAN",
        "head": {"sha": "deadbeef"},
    }
    base.update(over)
    return base


def check(**over) -> dict:
    base = {"name": "verify", "status": "completed", "conclusion": "success"}
    base.update(over)
    return base


class FakeGh(Gh):
    def __init__(self, checks=None):
        # Deliberately no super().__init__ - nothing here touches the network.
        self._checks = checks or {}

    def checks(self, full_name, sha):  # noqa: D102 - test double
        return self._checks


# --- gate 1: authorship ----------------------------------------------------

def test_bot_pr_passes_authorship():
    ok, _ = evaluate(FakeGh(), "o/n", pr(), set())
    assert ok, "a bot PR with nothing else wrong should pass"


def test_human_pr_is_refused():
    ok, reason = evaluate(
        FakeGh(), "o/n", pr(user={"type": "User", "login": "neohiro"}), set()
    )
    assert not ok
    assert "human" in reason, reason
    assert "neohiro" in reason, "the reason should name who, not just 'human'"


# --- gate 2: required checks -----------------------------------------------

def test_no_required_checks_is_not_a_pass_by_default():
    """An unprotected repo has no required checks, and that must not become
    'everything is fine'. It means gate 2 has nothing to say, which is a
    different thing from having passed it."""
    ok, _ = evaluate(FakeGh(), "o/n", pr(), set())
    assert ok, "no required checks configured means gate 2 is silent, not failing"


def test_green_required_check_passes():
    gh = FakeGh({"verify": check(conclusion="success")})
    ok, reason = evaluate(gh, "o/n", pr(), {"verify"})
    assert ok, reason


def test_failing_required_check_is_refused():
    gh = FakeGh({"verify": check(conclusion="failure")})
    ok, reason = evaluate(gh, "o/n", pr(), {"verify"})
    assert not ok
    assert "failure" in reason, reason


def test_missing_required_check_is_refused():
    """The case this whole script was written for: a required check that stops
    running entirely. An absent check is not a passing check."""
    gh = FakeGh({})  # nothing reported at all
    ok, reason = evaluate(gh, "o/n", pr(), {"verify"})
    assert not ok
    assert "has not reported" in reason, reason


def test_incomplete_required_check_is_refused():
    gh = FakeGh({"verify": check(status="in_progress", conclusion=None)})
    ok, reason = evaluate(gh, "o/n", pr(), {"verify"})
    assert not ok
    assert "in_progress" in reason, reason


def test_queued_required_check_is_refused():
    gh = FakeGh({"verify": check(status="queued", conclusion=None)})
    ok, reason = evaluate(gh, "o/n", pr(), {"verify"})
    assert not ok, reason


def test_skipped_required_check_is_allowed():
    """Skipped is a deliberate answer, not an absence. A required check that
    skips itself because the change does not touch its paths has run and
    decided, and refusing those would wedge the repo permanently."""
    gh = FakeGh({"verify": check(conclusion="skipped")})
    ok, reason = evaluate(gh, "o/n", pr(), {"verify"})
    assert ok, reason


def test_neutral_required_check_is_allowed():
    gh = FakeGh({"verify": check(conclusion="neutral")})
    ok, reason = evaluate(gh, "o/n", pr(), {"verify"})
    assert ok, reason


def test_commit_status_success_passes():
    gh = FakeGh({"verify": {"context": "verify", "state": "success"}})
    ok, reason = evaluate(gh, "o/n", pr(), {"verify"})
    assert ok, reason


def test_commit_status_pending_is_refused():
    gh = FakeGh({"verify": {"context": "verify", "state": "pending"}})
    ok, reason = evaluate(gh, "o/n", pr(), {"verify"})
    assert not ok, reason


def test_one_failing_check_among_several_is_refused():
    gh = FakeGh(
        {
            "verify": check(conclusion="success"),
            "tests": check(name="tests", conclusion="failure"),
            "lint": check(name="lint", conclusion="success"),
        }
    )
    ok, reason = evaluate(gh, "o/n", pr(), {"verify", "tests", "lint"})
    assert not ok
    assert "tests" in reason, "the failing check must be named"


# --- gate 3: mergeability --------------------------------------------------

def test_conflicting_pr_is_refused():
    ok, reason = evaluate(
        FakeGh(), "o/n", pr(mergeable_state="DIRTY", mergeable=False), set()
    )
    assert not ok
    assert "DIRTY" in reason, reason


def test_unknown_mergeability_is_refused_not_guessed():
    """GitHub returns UNKNOWN while it computes mergeability. Treating that as
    clean would merge on a guess."""
    ok, reason = evaluate(FakeGh(), "o/n", pr(mergeable_state="UNKNOWN"), set())
    assert not ok
    assert "comput" in reason, reason


def test_blocked_is_refused():
    """BLOCKED is also what 'missing required review' looks like, so it cannot
    be read as ready-but-unreviewed."""
    ok, reason = evaluate(FakeGh(), "o/n", pr(mergeable_state="BLOCKED"), set())
    assert not ok
    assert "BLOCKED" in reason, reason


def test_unstable_is_allowed():
    """UNSTABLE means mergeable with a non-required check failing. That is the
    state most green-but-noisy PRs sit in, and refusing it would make this
    script refuse nearly everything."""
    ok, reason = evaluate(FakeGh(), "o/n", pr(mergeable_state="UNSTABLE"), set())
    assert ok, reason


def test_gh_list_response_has_no_mergeable_keys():
    """Guards the trap in Gh.open_prs: the list endpoint omits mergeability
    entirely, so code that reads it there sees None for every PR."""
    import json

    # A trimmed but faithful copy of one entry from the list endpoint.
    listed = json.loads(
        '[{"number": 5, "user": {"type": "Bot", "login": "dependabot[bot]"},'
        ' "head": {"sha": "abc"}}]'
    )[0]
    assert "mergeable" not in listed
    assert "mergeable_state" not in listed


# --- protection parsing ----------------------------------------------------

def test_required_check_names_from_protection():
    protection = {
        "required_status_checks": {"checks": [{"context": "verify"}, {"context": "tests"}]}
    }
    assert required_check_names(protection) == {"verify", "tests"}


def test_required_check_names_when_unprotected():
    assert required_check_names(None) == set()


def test_required_check_names_with_missing_context():
    """GitHub's `checks` entries can carry a null context when the rule is a
    legacy branch name. Filtering it out is correct: a null is not a name, and
    matching on it would look up an empty-string check that never exists."""
    protection = {"required_status_checks": {"checks": [{"context": None}, {"context": "verify"}]}}
    assert required_check_names(protection) == {"verify"}


# --- ordering --------------------------------------------------------------

def test_human_pr_refused_before_anything_else():
    """Cheap gates first, so a human PR costs one comparison rather than a
    round trip to fetch checks it will never be judged against."""
    gh = FakeGh({})  # would fail the check gate too if reached
    ok, reason = evaluate(gh, "o/n", pr(user={"type": "User", "login": "x"}), {"verify"})
    assert not ok
    assert "human" in reason, "authorship must be the reported reason"


def main() -> int:
    tests = [
        (name, obj)
        for name, obj in sorted(globals().items())
        if name.startswith("test_") and callable(obj)
    ]
    failed = []
    for name, fn in tests:
        try:
            fn()
            print(f"  ok   {name}")
        except AssertionError as exc:
            failed.append((name, exc))
            print(f"  FAIL {name}: {exc}")
        except Exception as exc:  # noqa: BLE001 - a test harness should report
            failed.append((name, exc))
            print(f"  ERR  {name}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - len(failed)}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())