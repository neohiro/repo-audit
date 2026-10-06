# Retired repository â€” archived 2026-10-06

**This repository is archived and no longer maintained.** It is kept read-only so its history and any inbound links stay resolvable. Nothing here should be built on.

## Superseded by

**[neohiro/doctor](https://github.com/neohiro/doctor)** â€” cross-repo diagnostics, reusable CI, and the knowledge base in `knowledge/`.

## Why it was retired

This held an audit, a migration runbook and a repository manifest â€” documents describing what was true once. `neohiro/doctor` now owns the same concerns as *executable checks*: branch-protection drift, CI jobs missing their checkout, Pages redirects after a rename, and agreement between `_tools/`, `_data/repos.yml` and `assets/data/repos.json`.

That is the substantive difference, not a reorganisation. A runbook is correct on the day it is written; a check is correct until the thing it watches changes. The runbook's most load-bearing claims had already gone stale â€” it described repositories that no longer exist â€” and nothing noticed, because nothing executed.

Issue `D-04` on `neohiro/doctor` keeps the cross-repo knowledge base complete; see `knowledge/cross-repo-invariants.md` for the invariants themselves.