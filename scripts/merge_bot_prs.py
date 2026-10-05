#!/usr/bin/env python3
"""Merge the pull requests that are safe to merge without a human.

The backlog this exists to prevent is boring and self-inflicted: a schedule
opens a dependabot PR, nothing merges it, and three weeks later there are nine
of them stacked on the same workflow file, each conflicting with the last. This
script is the thing that closes them, and its whole value is in the refusals.

Three gates, all of which must pass, and each of which exists because a real PR
in this org tripped it:

1. **Bot-authored only.** A human's PR is a request for review. Merging one
   unattended would mean the review gate exists but is not respected.

2. **Every required check green.** `required_status_checks` from branch
   protection, not "all checks green" - a repo with no required checks has
   nothing to enforce, and one with required checks can have optional ones
   failing that are not a reason to block. A required check that has not run
   at all is a failure, not a pass: the distinction matters, because an
   unrun check and a skipped check look identical in the rollup.

3. **Mergeable, not merely approved.** `mergeable_state` has to be CLEAN or
   UNSTABLE. `BLOCKED` is refused even though it also means "no conflicts",
   because BLOCKED is also what branch protection returns when a required
   review is missing - so it cannot be distinguished from "ready but
   unreviewed" without asking again.

Failing any gate is a refusal, reported, not an error. The script exits 0 when
it did its job, which includes doing nothing.

Usage:
    python scripts/merge_bot_prs.py --repo owner/name [--repo owner/name ...]
    python scripts/merge_bot_prs.py --all-owned [--dry-run]
    python scripts/merge_bot_prs.py --all-owned --token "$GH_TOKEN" --dry-run
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

API = "https://api.github.com"
MERGEABLE_OK = {"CLEAN", "UNSTABLE"}


class Gh:
    """Thin GitHub REST wrapper. Uses the API rather than `gh` so this runs
    identically in Actions, in a cron, and from a laptop."""

    def __init__(self, token: str):
        self.token = token

    def _get(self, path: str):
        req = urllib.request.Request(API + path)
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        req.add_header("User-Agent", "neohiro-merge-bot")
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")[:300]
            raise RuntimeError(f"GET {path} -> {exc.code}: {body}") from exc

    def repo(self, full_name: str) -> dict:
        return self._get(f"/repos/{full_name}")

    def protection(self, full_name: str):
        """Branch protection, or None. A 404 means unprotected, which is not an
        error - it just means gate 3 has nothing to enforce beyond mergeability.
        A 403 means the token is not an admin on a private repo; also None,
        because a token that cannot read protection must not guess at it."""
        try:
            return self._get(f"/repos/{full_name}/branches/main/protection")
        except RuntimeError as exc:
            if "-> 404" in str(exc) or "-> 403" in str(exc):
                return None
            raise

    def open_prs(self, full_name: str) -> list[dict]:
        """The open PRs, from the list endpoint.

        Note what is NOT here: the list endpoint omits `mergeable` and
        `mergeable_state` entirely. Those keys are absent, not null, which is a
        difference worth being explicit about - reading them off the list gives
        None and a naive check then compares None against {"CLEAN","UNSTABLE"}
        and refuses everything, or worse, treats "absent" as "no conflicts".
        Use `pull()` for a single PR when mergeability matters.
        """
        return self._get(f"/repos/{full_name}/pulls?state=open&per_page=100")

    def pull(self, full_name: str, number: int) -> dict:
        """One PR, from the single-item endpoint. This is the only place
        `mergeable` and `mergeable_state` are populated."""
        return self._get(f"/repos/{full_name}/pulls/{number}")

    def checks(self, full_name: str, sha: str) -> dict:
        """Rollup keyed by check name, for both check runs and statuses."""
        out = {}
        for kind in ("check-runs", "statuses"):
            url = (
                f"/repos/{full_name}/commits/{sha}/{kind}"
                f"?per_page=100"
            )
            data = self._get(url)
            items = data.get("check_runs", data.get("statuses", []))
            for item in items:
                out[item.get("name") or item.get("context")] = item
        return out

    def merge(self, full_name: str, number: int, method: str) -> tuple[bool, str]:
        url = f"/repos/{full_name}/pulls/{number}/merge"
        payload = json.dumps(
            {"merge_method": method, "delete_branch_on_merge": True}
        ).encode()
        req = urllib.request.Request(url, data=payload, method="PUT")
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        req.add_header("User-Agent", "neohiro-merge-bot")
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return True, json.load(resp).get("message", "merged")
        except urllib.error.HTTPError as exc:
            return False, f"{exc.code}: {exc.read().decode('utf-8', 'replace')[:200]}"


def required_check_names(protection) -> set[str]:
    if not protection:
        return set()
    rsc = protection.get("required_status_checks") or {}
    return {c.get("context") for c in rsc.get("checks", []) if c.get("context")}


def evaluate(gh: Gh, full_name: str, pr: dict, required: set[str]) -> tuple[bool, str]:
    """Return (mergeable_by_us, reason). The reason is what gets reported, so
    it has to say which gate refused and what the value actually was - a bare
    'no' tells the next person nothing."""
    if not pr["user"]["type"] == "Bot":
        return False, f"authored by a human ({pr['user']['login']})"

    state = pr.get("mergeable_state")
    if state not in MERGEABLE_OK:
        # UNKNOWN is GitHub's transient answer while it computes mergeability.
        # It is not a verdict, so refuse rather than guess, and say so.
        if state == "UNKNOWN":
            return False, "mergeability still being computed"
        return False, f"mergeable_state={state}"

    if required:
        rollup = gh.checks(full_name, pr["head"]["sha"])
        for name in sorted(required):
            check = rollup.get(name)
            if check is None:
                # Never ran. Treating this as a pass is the bug this whole
                # function exists to avoid: a required check that silently
                # stops running would otherwise merge everything.
                return False, f"required check {name!r} has not reported"
            if "conclusion" in check:
                # A check run. `conclusion` is null until it finishes, which is
                # a pass or a fail depending on `status`, so both are consulted.
                verdict = check.get("conclusion")
                if verdict is None:
                    return False, f"required check {name!r} is still {check.get('status')}"
                ok = verdict in ("success", "neutral", "skipped")
                reported = verdict
            else:
                # A commit status, the older mechanism, which uses `state`.
                reported = check.get("state")
                ok = reported == "success"
            if not ok:
                return False, f"required check {name!r} is {reported}"

    return True, "all gates pass"


def owned_repos(gh: Gh) -> list[str]:
    """Every repo the token's owner can merge into, excluding archived ones.

    Archived is the filter that matters: an archived repo is read-only, so its
    PRs cannot be merged at all and must not be reported as mergeable. This
    script was written after two PRs sat open on archived repos for a month,
    because 'unmerged PR' looked the same as 'mergeable PR' in the report that
    prompted it.
    """
    names, page = [], 1
    while True:
        batch = gh._get(
            f"/user/repos?per_page=100&page={page}&affiliation=owner,organization"
        )
        if not batch:
            break
        names.extend(r["full_name"] for r in batch)
        page += 1
    return sorted(set(names))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", action="append", default=[], metavar="OWNER/NAME")
    ap.add_argument("--all-owned", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--token", default=os.environ.get("GH_TOKEN", ""))
    args = ap.parse_args()

    if not args.repo and not args.all_owned:
        ap.error("give --repo or --all-owned")
    token = args.token
    if not token:
        sys.exit("no token: pass --token or set GH_TOKEN")

    gh = Gh(token)
    repos = args.repo or owned_repos(gh)
    print(f"scanning {len(repos)} repo(s)", flush=True)

    merged = refused = skipped = 0
    for full_name in repos:
        try:
            meta = gh.repo(full_name)
        except RuntimeError as exc:
            print(f"  ! {full_name}: {exc}", flush=True)
            continue
        if meta.get("archived"):
            skipped += 1
            continue

        try:
            protection = gh.protection(full_name)
            required = required_check_names(protection)
            prs = gh.open_prs(full_name)
        except RuntimeError as exc:
            print(f"  ! {full_name}: {exc}", flush=True)
            continue

        for pr in prs:
            # Mergeability is only populated by the single-item endpoint, so
            # the list entry is not enough to judge. Re-fetch per PR.
            try:
                full = gh.pull(full_name, pr["number"])
            except RuntimeError as exc:
                print(f"  ! {full_name} #{pr['number']}: {exc}", flush=True)
                continue
            ok, reason = evaluate(gh, full_name, full, required)
            label = f"#{pr['number']} {pr['title'][:60]}"
            if not ok:
                refused += 1
                print(f"  - {full_name} {label}: {reason}", flush=True)
                continue
            if args.dry_run:
                merged += 1
                print(f"  ~ WOULD MERGE {full_name} {label}", flush=True)
                continue
            done, message = gh.merge(full_name, pr["number"], "squash")
            if done:
                merged += 1
                print(f"  + MERGED {full_name} {label}", flush=True)
            else:
                refused += 1
                print(f"  ! {full_name} {label}: merge failed: {message}", flush=True)

    print(
        f"\n{merged} merged, {refused} refused, {skipped} archived repo(s) skipped",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())