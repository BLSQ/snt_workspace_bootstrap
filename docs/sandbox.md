# Sandbox — repo, workspace and fixture releases

> Everything the deployer and checker are tested against. Commands with `git`/`gh` in them are for a
> human to run (R19). Folded in 2026-10-01 from the local runbooks `sandbox_fixture_plan.md` and
> `sandbox_reset_runbook.md`, which are deleted; their one-time steps (the 2026-09-21 reset itself,
> creating each fixture) are not repeated here.

## Sandbox

All of this was built against a disposable sandbox rather than the real repo or its CI. Two
distinct things with confusingly similar names:

* `BLSQ/snt_development_sandbox` — the **GitHub repo**. An independent repo, not a fork; local
  remote is named `sandbox`. It holds one branch, `main`, seeded from `snt_development` @ `551ddd8`
  minus the 20 `push_snt_*.yaml` deployment workflows (those target the real `snt-development`
  workspace and must never fire from a sandbox). Cut every fixture release from `main`.
* `snt-development-sandbox` — the **OpenHEXA workspace** the Workspace Deployer pipeline runs in. Its
  `.snt_release` marker names a tag that no longer exists — harmless, and itself a usable test of how
  the checker handles an unresolvable declared release.

Fixture releases are listed in §6.1; the tag series starts at `v0.1.0-test`.
Earlier tag names are retired and must not be reused ([`HISTORY.md`](HISTORY.md) §4).

## 6.1 Test fixtures in the sandbox

The fixture releases exist in `BLSQ/snt_development_sandbox` and are verified. Two naming rules:
earlier `-test` tags are retired and must not be reused ([`HISTORY.md`](HISTORY.md) §4.2), and do
**not** create a release named `latest` — GitHub's `/releases/latest` endpoint already resolves to
the newest non-prerelease release automatically.

| Tag | Asset | Role |
|---|---|---|
| `v0.1.0-test` | manifest, 163 files | baseline; deploy target for check run 1 |
| `v0.2.0-test` | manifest, 163 files | **dud**, byte-identical to `v0.1.0-test`; kept because tags are never deleted |
| **`v0.2.1-test`** | manifest, 163 files | **the verification target** |
| `v0.3.0-test` | manifest, 163 files | ahead of the target; deploy target for check run 2 |
| `v0.4.0-test` | **none** | `manifest_available: false`, `incomplete: true`. Also the repo's GitHub "latest", so an empty-tag deployer run resolves to it and aborts on the missing manifest |

The dud is harmless: its `published_at` puts it on the older side of the target, where duplicate
content changes no expected status, and it incidentally covers the case of **two releases with
identical manifests**, which attribution must not double-count.

The set produces every status at least once:

* a file **changed** between two releases → `mismatch_known` in both directions;
* a file **added** in the newer release → `missing` when checking a workspace at the older one;
* a file **removed** in the newer release → `removed_in_target`;
* a **pipeline added** and a **pipeline removed** between releases;
* a file **unchanged across all releases** → attribution to several releases at once;
* a file **changed and then reverted** → `position: both`, and the only non-contiguous
  attribution span ([`checker.md`](checker.md) §5.1.2, [`checker.md`](checker.md) §7.8);
* a release published with **no manifest asset** → `manifest_available: false` and
  `incomplete: true` ([`checker.md`](checker.md) §5.4, [`checker.md`](checker.md) §5.5), made by disabling the generator workflow for one release;
* one hand-edited file in the workspace → `unknown_content`.

Four cases are **not** release fixtures and are made in the workspace immediately before a check
run: `unknown_content`, `untracked`, `unreadable`, and the [`checker.md`](checker.md) §5.3 untracked-inside-a-pipeline-zip
case. One more, `position: unordered`, is not reachable at all — GitHub always sets `published_at`
— so it is a unit test against a stubbed release list, not a fixture.

The full command-by-command plan, with the expected status for each fixture in each of three check
runs, is below, under *Fixture releases — as built*.

## How the sandbox repo was seeded

### Why delete the repo rather than force-push it

Two reasons, both practical:

* **The `v*` tag ruleset blocks its own cleanup.** `release_strategy.md` §"Release tags are never moved"
  set *Restrict deletions* on `v*` with an **empty bypass list** — deliberately, so that not even a
  repository admin can move a tag. That is exactly what stops you deleting `v0.0.1-test` and
  `v0.0.2-test` in place. You would have to disable the ruleset, delete, and re-enable it.
* **Deleting the repo needs no force-push.** A brand-new empty repo accepts `git push -u origin
  main` as an ordinary fast-forward. Nothing in this runbook rewrites published history.

What you lose and must restore afterwards (§C): the tag ruleset, collaborator/team access, and any
repo-level secrets or variables. Actions run history and releases go too — which is the point.

### Why the 20 `push_snt_*.yaml` are stripped

Each of them runs, on a push to `main`:

```yaml
- uses: blsq/openhexa-cli-action@v1
  with:
    workspace: "snt-development"          # ← the REAL workspace, not the sandbox
    token: ${{ secrets.OH_TOKEN }}
- run: openhexa pipelines push <name> --code "<slug>" … --yes
```

If `OH_TOKEN` is an **organization** secret, the recreated sandbox inherits it, and the seed push —
which creates all 20 `pipeline.py` files at once — could publish 20 new versions of the real SNT
template pipelines, propagating to every subscribed country workspace (**R5**). A repo-level secret
would not be inherited and the jobs would merely fail red on every push, which is noise you do not
need either.

`generate_manifest.yaml` is kept: it is the one workflow the sandbox exists to exercise, and it
touches nothing outside the repo.

This is a **deliberate deviation** from `551ddd8` — the only one. Record it in the seed commit
message (the command below does).

## Fixture releases — as built

### As built — 2026-09-21

**The fixture releases exist.** Steps 0–4 below are the recipe that produced them; this section is
what actually came out, and it differs from the recipe in one place.

| Tag | `published_at` | Asset | Role |
|---|---|---|---|
| `v0.1.0-test` | 15:20 | manifest, 163 files | baseline — deploy target for check run 1 |
| `v0.2.0-test` | 15:40 | manifest, 163 files | **DUD — not a fixture.** Manifest byte-identical to `v0.1.0-test`'s |
| `v0.2.1-test` | 16:00 | manifest, 163 files | **the verification target** |
| `v0.3.0-test` | 16:04 | manifest, 163 files | ahead of the target; deploy target for check run 2 |
| `v0.4.0-test` | 16:16 | **none** | `manifest_available: false`, `incomplete: true` |

Plus one tag with **no release**: the first `v0.2.0-test`, whose release was deleted. The checker
enumerates releases, so it never sees it.

**The target is `v0.2.1-test`, not `v0.2.0-test`.** Substitute accordingly everywhere below. All
verified: three distinct hashes for `fixture_changing.r`, `fixture_reverted.py` revision A matching
`v0.1.0-test` and `v0.3.0-test` but not the target, and `fixture_stable.r` identical across all
four manifests.

#### The dud, and why it stays

`v0.2.0-test` was cut twice from the `v0.1.0-test` tree (see the two traps below) and its manifest
duplicates `v0.1.0-test`'s exactly. It is **not** deleted, because tags are never deleted and its
release is now labelled as a dud. It contaminates nothing: its timestamp puts it on the *older*
side of the target, where duplicate content changes no expected status — `older` stays `older`,
`both` stays `both`, `missing` and `removed_in_target` are untouched. It adds one release to spans
that were already contiguous, and it is an unplanned but real fixture for **two releases with
identical manifests**, which attribution must handle without double-counting.

One cosmetic wart, left alone deliberately: the fixture *file contents* say `removed in
v0.2.0-test` / `added in v0.2.0-test`, because they were written before the renumbering. The bytes
are what the manifest hashes, so changing them now would mean re-cutting every release. The text is
wrong by one patch number; the fixtures are right.

#### Two traps this hit, both worth knowing

1. **The generator reads the working tree, not the commit.** Step 0's verification passed on
   uncommitted, unpushed changes, so the manifest measured locally had nothing to do with what the
   tag would carry. **Verify the push, not the working tree:** `git rev-parse HEAD` must equal
   `gh api repos/<repo>/commits/main --jq .sha` *before* any `gh release create`.
2. **Deleting a GitHub release does not delete its tag** — and when a tag already exists,
   `gh release create` attaches to it and **silently ignores `--target`**. With *Restrict deletions*
   on `v*`, the surviving tag cannot be removed either, so the second attempt reproduced the first
   one's tree exactly. The ruleset is working as designed. The only remedy is the one the convention
   already prescribes: burn the number, cut the next.

### Before you start — three things to know

**1. A fixture pipeline directory becomes a real OpenHEXA pipeline object.** `snt_workspace_deployer`
deploys every pipeline directory the manifest describes. Creating `snt_fixture_pipeline_added/`
therefore registers a pipeline in the `snt-development-sandbox` workspace, and **removing it later
needs a destructive action** ([`deployer.md`](deployer.md) §7.5) — which is exactly the case phase 2 wants to observe, but it
means the object stays. Acceptable in a disposable sandbox; never do this in a country workspace.

**2. Tags are never moved.** If a fixture comes out wrong, cut the next number — do not re-tag.
That is also *why* attribution-by-hash is safe to build on.

**3. `.gitignore` will eat badly-named fixtures.** The seed inherits `**/[Tt]est*`, so anything
called `test_*` is silently never committed — hence the `fixture_` prefix throughout. `*.json` is
ignored except two negations, so **do not add a `.json` fixture**. `*.yaml` is ignored except
`.github/workflows/*.yaml`. After every `git add` below, `git status --short` before committing.

### Step 0 — verify the generator at the exact tree you are about to tag

The script reads the **working tree**, not the commit — so a clean-looking local run proves nothing
about what a tag will carry. Make the working tree, `HEAD` and the **remote** agree first:

```bash
cd /home/gpuntin/Bluesquare/snt_development_sandbox_seed
git checkout main
git pull origin main
git status --short          # must print nothing — nothing staged, nothing modified
git rev-parse HEAD
gh api repos/BLSQ/snt_development_sandbox/commits/main --jq .sha    # must equal the line above
```

Those last two lines are not optional. Skipping them is how `v0.2.0-test` got cut twice from the
wrong tree (see "Two traps" above).

Then run the generator — it reproduces exactly what the Action does, with no side effects beyond
one JSON file in the working directory:

```bash
# `python3`, not `python` — plain WSL/Linux has no `python` on PATH, while the GitHub
# runner (actions/setup-python) provides both.
python3 - << 'PY' > /dev/null
import pathlib
yaml = pathlib.Path('.github/workflows/generate_manifest.yaml').read_text()
body = yaml.split("cat << 'EOF' > generate_manifest.py\n", 1)[1].split("\n          EOF", 1)[0]
pathlib.Path('generate_manifest.py').write_text(
    "\n".join(line[10:] for line in body.split("\n")) + "\n")
PY
python3 generate_manifest.py

python3 -c "
import json; m=json.load(open('release_manifest.json'))
print(len(m['files']), 'files;', len(m['pipelines']), 'pipelines')
print(json.dumps(m['pipelines']['snt_map_extracts'], indent=2))"

rm generate_manifest.py release_manifest.json
```

**At the seed commit, expect `156 files; 21 pipelines`**, and `snt_map_extracts` reporting 8 zip
files rather than 1. Re-run this before each `gh release create`; the counts below say what to
expect each time.

### Step 1 — `v0.1.0-test`, the baseline

| File | Purpose | v0.1.0 | v0.2.0 | v0.3.0 |
|---|---|---|---|---|
| `code/fixture_changing.r` | `mismatch_known`, `older` / `newer` | `A` | `B` | `C` |
| `code/fixture_stable.r` | one contiguous attribution span across all releases | `S` | `S` | `S` |
| `code/fixture_removed.r` | `removed_in_target` | present | **gone** | gone |
| `pipelines/snt_dhis2_incidence/utils/fixture_added.r` | `missing` | absent | **added** | present |
| `snt_dhis2_incidence/fixture_reverted.py` | `position: both`; a **non-contiguous** span; proves helper `.py` is tracked | `A` | `B` | **`A`** |
| `snt_fixture_pipeline_removed/` | pipeline removed between releases ([`deployer.md`](deployer.md) §7.5) | present | **gone** | gone |
| `snt_fixture_pipeline_added/` | pipeline added between releases | absent | **added** | present |

Re-run **step 0**. Expect **`163 files; 22 pipelines`**: 156 baseline, + 3 `code/fixture_*.r`,
+ `snt_dhis2_incidence/fixture_reverted.py`, + all 3 files of `snt_fixture_pipeline_removed/`
(`pipeline.py`, `requirements.txt` and `readme.md` are each tracked since phase 0).

The count stays **163** at `v0.2.0-test` too — one `.r` and one 3-file pipeline leave, one `.r` and
one 3-file pipeline arrive — and at `v0.3.0-test`, which only changes bytes. So a *changing* total
across steps 2–3 means something went in that you did not intend; check `git status --short` before
blaming the generator.

**The assertion that matters:** the manifest must contain **all three**
`snt_fixture_pipeline_removed/*` entries, `readme.md` and `requirements.txt` included. If it holds
only `pipeline.py`, the widened generator did not take effect — stop and fix that first, because
every status in phase 2 depends on it.

### Step 4 — `v0.4.0-test`, a release with **no** manifest

This one is not about file contents. [`checker.md`](checker.md) §5.4 requires the report to carry `incomplete: true` and
[`checker.md`](checker.md) §5.5 requires `releases_considered[].manifest_available: false` — neither is reachable while every
release has an asset. Manufacture it by cutting a release with the generator switched off:

1. *Actions → Generate Release Manifest → ⋯ → **Disable workflow***.
2. Cut the tag at **the same commit as `v0.3.0-test`**, so no file hash changes and the only
   variable is the missing asset:

   ```bash
   gh release create v0.4.0-test \
     --repo BLSQ/snt_development_sandbox \
     --target main \
     --title "v0.4.0-test — deliberately has no manifest asset" \
     --notes "Fixture: the manifest workflow was disabled when this was published. Exercises manifest_available=false and incomplete=true. Same tree as v0.3.0-test."
   ```

3. *Actions → Generate Release Manifest → ⋯ → **Enable workflow*** — **do not skip this**, or every
   later release silently ships without a manifest.
4. Confirm the state you wanted:

   ```bash
   gh release view v0.4.0-test --repo BLSQ/snt_development_sandbox   # no assets listed
   gh release view v0.3.0-test --repo BLSQ/snt_development_sandbox   # still has its asset
   ```

Put the reason in the release notes (the command above does): a release with no manifest looks
exactly like a broken Action, and in six months nobody will remember which it was.

### Step 5 — the check runs, once the checker exists

Three runs cover [`checker.md`](checker.md) §5. `snt_workspace_deployer` deploys; the checker only reads.

#### Run 1 — "behind": deploy at `v0.1.0-test`, check with target `v0.2.1-test`

| Expected | From |
|---|---|
| `match` | every unchanged file, incl. `code/fixture_stable.r` |
| `mismatch_known` / `older` | `code/fixture_changing.r` (holds `A`, target wants `B`) |
| `mismatch_known` / `both` | `snt_dhis2_incidence/fixture_reverted.py` (holds `A`; `B` in target, `A` again in v0.3.0) |
| `missing` | `pipelines/snt_dhis2_incidence/utils/fixture_added.r` |
| `removed_in_target` | `code/fixture_removed.r`, and all three `snt_fixture_pipeline_removed/*` |
| pipeline removed in target ([`deployer.md`](deployer.md) §7.5) | `snt_fixture_pipeline_removed` — report-and-leave |
| pipeline missing entirely | `snt_fixture_pipeline_added` — in the target, never deployed |

#### Run 2 — "ahead": deploy at `v0.3.0-test`, check with target `v0.2.1-test`

Yields `mismatch_known` / `newer` on `code/fixture_changing.r` (holds `C`). This is the mirror run;
without it `newer` is never exercised.

#### Run 3 — attribution mode: no target release

Run after `.snt_release` is removed (or with no `release_tag` and no marker) to force [`checker.md`](checker.md) §4.1. Expect
a per-release percentage breakdown, `code/fixture_stable.r` attributed to all three releases as one
span, and `fixture_reverted.py` as two spans.

#### The four statuses that are **not** release fixtures

Make these in the workspace by hand, immediately before a check run:

```
unknown_content   append a comment line to <workspace>/code/snt_palettes.r
untracked         create <workspace>/scratch_notes.ipynb
unreadable        chmod 000 a deployed file  (restore it afterwards)
untracked-in-zip  push a pipeline version by hand from a tree carrying one extra file
                  — the [`checker.md`](checker.md) §5.3 "sharper case": an untracked file inside a pipeline
                  directory ships in the deploy zip and is not inert
```

And two that no fixture can produce:

* **`not_covered`** should be **unreachable by construction** — that is the point of phase 0. If the
  checker ever reports it, the manifest has fallen behind the SDK's zip rules (most likely the
  SDK's suffix list changed). Treat it as the alarm it is, not as noise.
* **`position: unordered`** requires a release whose `published_at` cannot be read, and GitHub always
  sets it. It is a defensive branch, so test it with a **stubbed release list in a unit test**, not
  a fixture — and do not quietly drop it: a release list fetched from a cache or an index asset
  ([`checker.md`](checker.md) §7.2) is exactly where a missing timestamp will one day come from.

#### Coverage against [`checker.md`](checker.md) §5, in one table

| [`checker.md`](checker.md) §5 requirement | Covered by |
|---|---|
| `match` | run 1 |
| `mismatch_known` + `older` | run 1, `fixture_changing.r` |
| `mismatch_known` + `newer` | run 2, `fixture_changing.r` |
| `mismatch_known` + `both` | run 1, `fixture_reverted.py` |
| `mismatch_known` + `unordered` | **unit test only** — not fixture-able |
| `unknown_content` | hand edit in workspace |
| `missing` | run 1, `fixture_added.r` |
| `removed_in_target` | run 1, `fixture_removed.r` + removed pipeline |
| `untracked` | hand-created workspace file |
| `untracked` inside a pipeline dir ([`checker.md`](checker.md) §5.3) | hand-pushed pipeline version |
| `not_covered` | unreachable by design; assert it never appears |
| `unreadable` | `chmod 000` in workspace |
| [`checker.md`](checker.md) §5.1.2 contiguous span | `fixture_stable.r`, run 3 |
| [`checker.md`](checker.md) §5.1.2 non-contiguous span | `fixture_reverted.py`, run 3 |
| [`checker.md`](checker.md) §5.2 ordering by `published_at` | all four tags, published minutes apart |
| [`checker.md`](checker.md) §5.4 `incomplete: true` | `v0.4.0-test` |
| [`checker.md`](checker.md) §5.5 `manifest_available: false` | `v0.4.0-test` |
| [`checker.md`](checker.md) §5.5 `declared_release` ≠ `target_release` | run 1 with `release_tag=v0.2.1-test` and marker `v0.1.0-test` |
| [`checker.md`](checker.md) §5.5 `resolved_from: marker` | any run with no `release_tag` parameter |
| [`checker.md`](checker.md) §3.1 both sources hashed | every run — `.r` on the filesystem, `pipeline.py` in the zip |
| [`checker.md`](checker.md) §3.1 version name vs content | after run 2: rename a pipeline version in the UI, re-check |

That last row is worth doing once by hand. It is the failure mode [`checker.md`](checker.md) §3.1 argues is the whole point —
a workspace whose version labels have stopped meaning anything looks perfectly healthy in the
OpenHEXA UI, which only shows names.
