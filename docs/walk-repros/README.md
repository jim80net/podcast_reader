# Durable product-walk repros

Product walks often begin with useful scripts and screenshots in a temporary
directory. Capture the smallest useful set before diagnosis or editing begins;
then turn every captured item into durable evidence or explicitly discard it.

## 1. Capture into quarantine

Name every file explicitly. Capture refuses traversal, symlinks, files over 1 MiB,
common credential filenames, and secret-like content. It never crawls a directory,
so `node_modules`, browser profiles, generated media, and unrelated screenshots do
not enter by accident.

```bash
uv run python scripts/walk_repros.py capture \
  --source /tmp/pr-walk2 \
  --output .walk-repros/2026-07-16-search \
  --walk-id 2026-07-16-search \
  --issue 92 \
  --include repro-extension.mjs \
  --include repro-ext-before.mjs
```

The output directory is atomic, mode-restricted, and gitignored. It contains the
selected files, their hashes, and a draft `manifest.json`. It is quarantine, not a
place to leave the result.

## 2. Prove and disposition every artifact

For each captured artifact, choose exactly one final disposition:

- `regression-test`: behavior is represented by a stable automated test. Set
  `prior_failure_verified` only after proving the test fails on the prior code.
- `retained-evidence`: a small, reviewed fixture or document remains necessary.
- `environment-recipe`: the behavior depends on hardware or an external surface;
  retain a reproducible, secret-free recipe.
- `discarded`: the artifact adds no value after normalization; explain why.

Inventory each verified scenario in plain language. For an integrity or security
guard, set `integrity_sensitive` and retain at least one hostile control proving the
real guard still trips. This is the #92 lesson: accepting benign extension DOM
decoration was incomplete until genuine transcript mutation still invalidated
search.

Move the curated manifest to `docs/walk-repros/<walk-id>.json`. Durable paths are
repository-relative files; paths back into `.walk-repros/` are rejected. Do not
commit browser profiles, dependency trees, user data, secrets, or redundant raw
screenshots.

## 3. Verify without the temporary source

```bash
uv run python scripts/walk_repros.py verify docs/walk-repros/*.json
```

Verification requires every item to have a final disposition, rationale, valid
hash, and existing durable target. It intentionally succeeds after `/tmp` is gone.
While the source still exists, also prove the capture hashes:

```bash
uv run python scripts/walk_repros.py verify \
  docs/walk-repros/2026-07-16-search.json \
  --source-root /tmp/pr-walk2
```

The checked-in #92 manifest is the worked example. Its two temporary drivers were
normalized into one stable Playwright regression using committed HTML fixtures;
the manifest retains the original hashes and complete six-case inventory without
retaining machine-specific paths or a duplicate copy of the drivers.
