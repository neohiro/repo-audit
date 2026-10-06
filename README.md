# repo-audit
Repository migration audit + runbook + manifest for neohiro org

[![Visitors](https://api.visitorbadge.io/api/visitors?path=github.com/neohiro/repo-audit&label=Visitors&countColor=%23263759)](https://visitorbadge.io/status?path=github.com/neohiro/repo-audit)

## merge_bot_prs

Merges the bot-authored pull requests that are already safe to merge, and
declines the rest with a stated reason.

```sh
python scripts/merge_bot_prs.py --repo owner/name      # one repo
python scripts/merge_bot_prs.py --all-owned            # everything reachable
python scripts/merge_bot_prs.py --all-owned --dry-run  # decide, print, merge nothing
python tests/test_merge_bot_prs.py                     # the refusal rules
```

`GH_TOKEN` is read from the environment, or pass `--token`.

### Why this exists

The backlog is boring and entirely self-inflicted. A schedule opens a
dependabot PR, nothing merges it, and three weeks later there are nine of them
stacked on the same workflow file, each conflicting with the last. In one pass
across 92 repositories that produced 40 open PRs, of which:

- 9 were the same `Cargo.lock` regenerated, conflicting with each other
- 6 were a duplicate `verify_data.py` step reading an empty workspace, which
  looked like a missing file and was a missing `actions/checkout`
- 3 were on archived repositories, where they can never merge at all
- 1 was a placeholder link in a code fence that a linter read as a real link

None of those needed a decision. They needed something to merge them.

### What it will not do

Three gates, each of which has a test:

| Gate | Refuses | Because |
| --- | --- | --- |
| Authorship | any human PR | a human's PR is a request for review |
| Required checks | any failing, pending, queued, or **absent** required check | absent is the important one: a required check that stops running must not read as passing |
| Mergeability | `DIRTY`, `BLOCKED`, and `UNKNOWN` | `BLOCKED` is also what a missing review looks like, and `UNKNOWN` is GitHub mid-computation, not a verdict |

`skipped` and `neutral` required checks are allowed: those are a check that ran
and deliberately stood down, and refusing them would wedge any repo whose checks
are path-filtered.

Merges are squash, branch deleted.

### Archived repositories

Skipped, with a count in the summary. An archived repository is read-only, so
its PRs cannot merge and must not be reported as if they could. Two PRs sat open
on archived repos for a month here because "unmerged PR" and "mergeable PR"
looked identical in the report that prompted this.

### Scheduling

`.github/workflows/merge-green-bot-prs.yml` runs hourly on weekdays. It prints
every decision — merge or refusal — into the run summary, so "why did this merge
itself" has an answer recorded at the time rather than reconstructed later.

`--dry-run` in the same job is the record, not a gate on the merge that follows
it.