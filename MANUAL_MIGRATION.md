# Manual Migration Runbook

This document is the canonical, step-by-step procedure for migrating a
`neohiro/*` game (or similar small repo) into a destination org (typically
`frenzypenguin-media/*`) using only `git`, `gh`, and a temp directory.

Use this when the source repo is small enough that the GitHub transfer API
+ admin approval cycle is overkill, or when the source is already archived
and cannot be transferred. The pre-flight `repo-audit/scripts/preflight.ps1`
and the batch driver `repo-audit/scripts/execute-all.ps1` cover the
multi-repo case; this runbook covers the per-repo manual case.

## When to use this runbook

- Source is a single game or utility with a handful of tags/releases.
- You do not want to wait for org-admin approval of a `gh api` transfer
  (which can take minutes to hours depending on org policy).
- You want a single audited trail per repo.
- The target org already has admin grant to the executing token.

If you need to transfer many repos in a single run, use
`execute-all.ps1 -Phase 2 -Execute` instead.

## Pre-flight

1. Confirm `gh auth status` shows the executing account.
2. Confirm you have admin on the source AND `repo:create` on the target org.

   ```bash
   gh auth status
   gh api /repos/<source> --jq .permissions.admin
   gh api /orgs/<target-org> --jq .members_can_create_repos
   ```

3. Record the source's full ref set BEFORE you start:

   ```bash
   gh api /repos/<source>/branches
   gh api /repos/<source>/git/matching-refs/tags
   gh release list -R <source>
   ```

## Migration steps

### 1. Create the target repo

```bash
gh repo create <target-org>/<target-slug> \
    --description "<same as source>" \
    --public \
    --add-readme
```

Use a **lowercase-hyphen** slug for the target (e.g. `tristar-mania`,
`zombie-shoot`). Even if the source uses `TristarMania` (CamelCase), the
target slug should be normalized to lowercase-hyphen per the
`frenzypenguin-media` org convention. This is the only place the casing
matters; the source URL stays the original case for backwards-compat.

### 2. Mirror-push all refs

```bash
TMP=$(mktemp -d)
cd "$TMP"
git clone --bare https://github.com/<source>.git src.git
cd src.git
git remote add dst https://github.com/<target-org>/<target-slug>.git
git push dst --mirror
```

Verify:

```bash
git show-ref | sort > /tmp/src-refs.txt
gh api /repos/<target-org>/<target-slug>/git/matching-refs/tags
gh api /repos/<target-org>/<target-slug>/branches
```

All SHAs in the source's `show-ref` output must appear on the target.

### 3. Recreate releases + re-upload assets

`git push --mirror` copies refs but **not** GitHub's release metadata or
the binary assets attached to each release. Both must be re-created.

```bash
SRC=<source>
DST=<target-org>/<target-slug>

# 3a. List the source releases (preserve order; preserves "Latest" tag)
gh release list -R "$SRC" --json tagName,name,body,isPrerelease,isDraft \
  | jq -c '.[]' > /tmp/releases.jsonl

# 3b. For each release, create on target + re-upload its assets.
#      We do not assume the source has only one asset per release;
#      older TristarMania releases had a single TristarMania.exe,
#      v1.0.0 has 3 OS-specific zips.
while IFS= read -r r; do
  tag=$(echo "$r" | jq -r .tagName)
  name=$(echo "$r" | jq -r .name)
  body=$(echo "$r" | jq -r .body)
  echo "== $tag =="
  gh release create "$tag" -R "$DST" \
    --title "$name" \
    --notes "$body" \
    --target main

  # Download source assets to a per-tag dir
  d=$(mktemp -d)
  (cd "$d" && gh release download "$tag" -R "$SRC" -p "*")

  # Upload each to the target
  for f in "$d"/*; do
    [ -f "$f" ] || continue
    gh release upload "$tag" -R "$DST" "$f"
  done
  rm -rf "$d"
done < /tmp/releases.jsonl
```

> **Note on binary preservation.** `.exe` and other raw binaries are
> preserved byte-for-byte. `.zip` archives are re-compressed by GitHub
> during upload, so the SHA differs but the extracted content matches.
> If exact-byte preservation of a zip is required, host the file
> elsewhere (e.g. `frenzypenguin-media/frenzypenguin-media.github.io`
> `/downloads/`) and link to it from the release body.

### 4. Parity check

File-tree and blob SHA must be identical (modulo the auto-generated
`README.md` from `gh repo create --add-readme`):

```bash
git clone https://github.com/<source>.git /tmp/src-final
git clone https://github.com/<target-org>/<target-slug>.git /tmp/dst-final
diff <(cd /tmp/src-final && git ls-tree -r HEAD) \
     <(cd /tmp/dst-final && git ls-tree -r HEAD)
```

Expect zero diffs. The auto-README from step 1 will differ; remove it
from the comparison if needed:

```bash
diff <(cd /tmp/src-final && git ls-tree -r HEAD | grep -v '\tREADME.md$') \
     <(cd /tmp/dst-final && git ls-tree -r HEAD | grep -v '\tREADME.md$')
```

### 5. Replace target's auto-README with a real one

`--add-readme` creates a generic README; replace it with the source's
README (or a redirect notice). The cleanest path is to copy the source
README byte-for-byte:

```bash
cp /tmp/src-final/README.md /tmp/dst-final/README.md
cd /tmp/dst-final
git add README.md
git commit -m "docs: import source README"
git push
```

### 6. Archive the source

The source can only be archived while still writable. Once archived, all
writes (including `repo edit`) are rejected with HTTP 403, so update the
description AND homepage BEFORE the archive call:

```bash
gh api -X PATCH /repos/<source> \
  -f description="[ARCHIVED $(date -u +%Y-%m-%d)] Moved to <target-org>/<target-slug>" \
  -f homepage="https://github.com/<target-org>/<target-slug>"

gh api -X PATCH /repos/<source> -f archived=true
```

> **Order matters.** The `archived=true` patch must come AFTER the
> metadata patch. Doing them in the other order silently drops the
> metadata changes.

### 7. Update workspace references

Search-and-replace `<source-org>/<source>` -> `<target-org>/<target-slug>`
in:

- `*/_tools/*.md` (game catalog pages)
- `*/_data/repos.yml` (Jekyll data file)
- `*/_includes/repo-list.html` (cross-org repo list)
- `monetization/MIGRATIONS.md` and per-org equivalents (status: `DONE`)
- `repo-audit/scripts/{preflight,execute-all}.ps1` (skip-list entries)
- `repo-audit/RETIREMENT_PLAN.md` (annotate migration complete)
- Any `fpm-bundles/*` mirrors

## Worked example: `neohiro/TristarMania` -> `frenzypenguin-media/tristar-mania`

Executed 2026-08-31. Source had 1 branch, 6 tags, 6 releases, 9 assets
(6 .exe + 3 .zip). One issue encountered: when assets share a name
across releases (`TristarMania.exe` for all 5 older releases), `gh
release download` to a single dir fails with `already exists`. Fix: use
a per-tag subdirectory as shown in step 3b.

Outcome: 10/10 blobs identical, 6/6 tags identical SHAs, 6/6 releases
recreated, 9/9 assets re-uploaded (sizes byte-identical for .exe;
sizes differ for .zip due to GitHub re-compression), 0 workflow files
needed manual transfer (they were mirrored with `--mirror`).

## Cleanup

- Remove the temp dirs created in steps 2, 3, 4:

  ```bash
  rm -rf /tmp/src.git /tmp/src-final /tmp/dst-final /tmp/releases.jsonl
  ```

- `mktemp -d` created dirs are usually under the OS temp dir and the
  `gh release download` per-tag dirs from step 3b are removed at end of
  the loop, but a final sweep is cheap insurance.

## See also

- `repo-audit/scripts/preflight.ps1` — pre-flight checks
- `repo-audit/scripts/execute-all.ps1` — batch driver (Phase 2)
- `repo-audit/RETIREMENT_PLAN.md` — full plan across all migrations
- `monetization/MIGRATIONS.md` — status table (cross-org)
- `frenzypenguin-monetization/MIGRATIONS.md` — status table (FPM-specific)
