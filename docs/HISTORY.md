# SNT Release Management — history, closed issues and lessons learned

> **Read this before re-investigating anything in `docs/`.**
>
> Nothing in this file describes the current state of the system. It is the opposite: it is where
> superseded designs, closed problems, deleted fixtures and dead ends are kept, so that a reader —
> human or agent — does not rediscover them, re-litigate a settled decision, or mistake an old
> artefact for a live one.
>
> The current state lives in the files [`CLAUDE.md`](../CLAUDE.md) routes to. Until 2026-10-01 these
> were `PRODUCT_SPEC.md`, `release_strategy.md` and `pipeline_deployment_mechanism.md` (§3.3).
>
> Rule of thumb when editing any of those: if a paragraph explains what something *used to be*,
> or records a verification against something that no longer exists, it belongs here instead.

**Contents**

1. [Dead ends — things that did not work](#1-dead-ends--things-that-did-not-work)
2. [Closed issues](#2-closed-issues)
3. [Superseded artefacts](#3-superseded-artefacts)
4. [Retired sandbox state and stale verification records](#4-retired-sandbox-state-and-stale-verification-records)
5. [Timeline](#5-timeline)

---

## 1. Dead ends — things that did not work

Kept so nobody repeats the troubleshooting. Each of these cost real time.

### `.gitignore` silently swallows new workflow files

The repo has blanket `*.yml` **and** `*.yaml` ignore rules, grouped with the data-export rules. A
new workflow file does not even show as untracked — `git add` just does nothing. The existing
`push_snt_*.yaml` files are tracked only because they predate the rule, and `git add -f` is
forbidden by this repo's rules (**R1**).

Fixed with a negation mirroring the existing `!configuration/SNT_config_*.json` pattern:

```
# GitHub Actions workflows ------------------------
!.github/workflows/*.yaml
```

Two consequences worth remembering: use `.yaml`, never `.yml` (no negation exists for `.yml`), and
the fix is repo-wide — it also unblocks any future `push_<name>.yaml`.

### Per-file GitHub API fetches hit the rate limit

Unauthenticated GitHub API calls are capped at **60/hour**, and pulling a release file-by-file via
the Contents API needs ~106 requests for a single release. Fix: download the release **source
tarball** — one request — and extract.

The same limit later drove the question of how the checker obtains every release's manifest. That
turned out not to be a constraint, because asset downloads are not charged per file
([`checker.md`](checker.md) §7.2, closed 2026-09-29).

### A pipeline version's name does not read back as it was submitted

OpenHEXA appends the version number to the name. A version deployed by `snt_workspace_manager` as
`v0.1.0-test` comes back from `currentVersion.versionName` as **`v0.1.0-test [v1]`**. (A version
pushed by the CLI with no name of its own reads back as just `v3`.)

`snt_workspace_check` compared that field to the target tag with `==`, so the comparison was never
true and `version_name_matches_content` came back `null` for all 22 pipelines — the entire
name-versus-content check of [`checker.md`](checker.md) §3.1 was silently switched off while the report looked
perfectly healthy. Caught on the checker's first real run (2026-09-22) only because *every* pipeline
reported `null`, which is not a plausible distribution.

Fixed by stripping a trailing ` [v<number>]` before comparing, and by reporting the raw name and the
parsed tag side by side (`current_version_name`, `current_version_claims_tag`) so the parsing is
visible rather than implied. **Do not compare a version name to a tag with `==`.**

### Copying `pipeline.py` into the workspace filesystem does nothing

This looked like a completed deployment and was not. OpenHEXA runs each pipeline from its registered
version's stored zip, never from `workspace/files/`. A `pipeline.py` sitting on the filesystem is
inert while looking authoritative — **the most misleading failure mode encountered in this work**,
and the reason the checker hashes pipeline version zips as a separate source. Fix: deploy through
the API, and deliberately do *not* leave a filesystem copy.

### A pipeline run's own `HEXA_TOKEN` cannot deploy pipelines

The API answers `PERMISSION_DENIED`. Same payload, same code, a workspace API token read from the
`oh` CUSTOM connection → accepted. Only the header differs, and both tokens are 95 characters, so
they are indistinguishable by shape. **If a deployment call 403s, check which token is in the header
before anything else.**

This is about **writing** only. The same run token reads a version's zip contents perfectly well —
see §2.4, which is why the checker needs no credential.

### Measuring the GitHub rate limit across runs does not work

To test whether manifest downloads count against the 60/hour limit ([`checker.md`](checker.md) §7.2), the
first attempt compared the `X-RateLimit-Remaining` header across two consecutive checker runs, on
2026-09-29 at 12:25 and 12:43. Both logged **59**. That proved nothing, for two reasons:

* the header was read from the release-list response, **before** any download, so no single run's
  figure contained the download cost;
* the second run should have shown 58 if it shared the first run's counter. It evidently did not:
  runs apparently leave from different egress IPs, so each run starts with a counter of its own.

Fix: read `GET /rate_limit` (which is itself free) **after** the downloads, and compare within one
run. That settled it the same day (§7.2). The general lesson: on OpenHEXA, **a pipeline run is the
unit of measurement**. Nothing about a shared-IP budget can be inferred by comparing runs.

---

## 2. Closed issues

### 2.1 The manifest under-described what is deployed — closed 2026-09-21

*Was: [`release_manifest.md`](contracts/release_manifest.md) §7.1, blocking phase 1. Fixed as phase 0 (decision D10).*

**The problem.** The manifest's `*/pipeline.py` pattern tracked one file per pipeline, but
deployment zips the **whole pipeline directory**. `snt_map_extracts` was deployed with `utils.py`,
`worldpopclient.py`, the `malariaAtlasProject/` package, `readme.md` and `requirements.txt` — none of
them in the manifest, none verifiable, and a change to any of them altered no manifest hash.
`requirements.txt` was the sharpest case: it carries the two unpinned Git dependencies the repo
already worries about, ships in the zip, and was invisible to the manifest. Verifying against a
manifest that describes a third of what is deployed gives false assurance, which is worse than no
verification.

**The fix that was rejected: a wider glob list.** The obvious patch was to add patterns:

```python
patterns = [
    ..., '*/requirements.txt', '*/readme.md', '*/**/*.py',
]
```

This was not done, and should not be revived. A glob list restates the SDK's rule in a second,
drifting dialect — one pattern per suffix *per depth* — and the list above is already incomplete: it
has no `.sql` pattern at all, and its `*/requirements.txt` and `*/readme.md` only reach the pipeline
root, so an equivalent file one directory down (inside `malariaAtlasProject/`, say) would still ship
unverified. `*/**/*.py` additionally sweeps up unrelated top-level directories (`dev/`,
`deprecated/`). Every future nesting or suffix would need another line nobody remembers to add.

**The fix that was applied** — reimplementing the SDK's own selection rule, anchored on
`*/pipeline.py` — is current design and is described in
[`release_manifest.md`](contracts/release_manifest.md).

### 2.2 Widening the manifest broke an existing consumer

Recorded because it is the general shape of the risk here: **the manifest is an interface, and
widening it changes the behaviour of everything that reads it.**

`split_manifest()` in `snt_workspace_manager` classified every entry that was not
`<name>/pipeline.py` as an analytics file and **copied it into the workspace bucket**. Under the
widened manifest that meant 49 `readme.md` / `requirements.txt` / helper-module files strewn across
the workspace filesystem, where OpenHEXA never reads them — the precise "inert and misleading copy"
failure this whole effort exists to detect (§1 above).

Fixed in the same change: the split is now by *directory*, taking the directory list from the
manifest's `pipelines` block where present, and falling back to the old `<name>/pipeline.py`
derivation for pre-phase-0 manifests. Checked against the `v0.0.1-test` and `v0.0.2-test` manifests
and the new one: **86 analytics files in all three**, so old releases deployed exactly as before.

Both of those releases were deleted in the sandbox reset (§4), so that verification stands as a
record but is no longer repeatable, and **no live release exercises the fallback path**. That leaves
one open item, carried in [`release_manifest.md`](contracts/release_manifest.md) §2.1: keep a legacy manifest as a local test fixture, or
delete the fallback in a PR of its own. Untested back-compat code for a case that can no longer
occur is worse than either.

### 2.3 `snt_workspace_manager` existed only in the sandbox workspace

It was built as a pipeline version inside `snt-development-sandbox` with no copy in the repository —
exactly the "no version-propagation story" problem this project exists to fix. Committed in
`5cb7995`, widened in `16bd149`; it now lives at [`snt_workspace_manager/`](../snt_workspace_deployer/)
with `pipeline.py`, `requirements.txt` and `readme.md`. No `push_*.yaml` workflow yet, pending the
R5 wording question (`openhexa_deployment.md`).

### 2.4 Does *reading* a pipeline version need the `oh` token? — closed 2026-09-22

*Was: [`openhexa_deployment.md`](openhexa_deployment.md) §7.3, the last thing blocking phase 1.*

**The question.** Deployment is refused with a run's own `HEXA_TOKEN` (§1, above). If *reading* a
version's zip were refused too, the checker would need a workspace-scoped credential in every
country workspace — which would have chained the read-only checker to the unsolved operational
problem in §7.3b, and taken the unattended daily check ([`checker.md`](checker.md) §7.6) with it.

**The answer: no. `HEXA_TOKEN` reads `currentVersion.zipfile` in full.** The checker needs no
credential.

**How it was established.** A throwaway read-only pipeline, `snt-token-probe`, deployed to
`snt-development-sandbox` and run once (run `07d90db2-6aae-4963-8fd9-8f78b2d9d423`, 2026-09-22).
It ran two queries per credential against two pipelines deployed at `v0.3.0-test`:

| Credential | Pipeline | metadata | `zipfile` |
|---|---|---|---|
| run's `HEXA_TOKEN` | `snt-dhis2-extract` | ok (v1) | **full**, 65345 B, `pipeline.py` `readme.md` `requirements.txt` |
| run's `HEXA_TOKEN` | `snt-dhis2-population-transformation` | ok (v2) | **full**, 20295 B, same three |
| `oh` connection | both | ok | full, byte-identical sizes |

Two design points worth keeping, because they are why the result is trustworthy:

* **A 200 OK proves nothing.** GraphQL field-level denial typically returns `zipfile: null` inside a
  successful response. The probe's pass condition was that the field decodes from base64, opens as a
  `ZipFile`, and its `namelist()` contains `pipeline.py` — not that the request succeeded.
* **Two queries, not one.** A metadata-only query alongside the zipfile query separates "cannot see
  the pipeline at all" from "can see it but not its contents". Both came back `ok`, so neither
  failure mode is in play.

The probe source is `ignore/SNT25-670/token_probe/` in a local `snt_development` clone (never committed, not carried over to this repo).

---

## 3. Superseded artefacts

### 3.1 The pre-phase-0 manifest generator

The original `generate_manifest.yaml` embedded this script. It tracked 106–107 files. It is
**superseded** by the committed
[`.github/workflows/generate_manifest.yaml`](https://github.com/BLSQ/snt_development/blob/main/.github/workflows/generate_manifest.yaml), which
tracks 156 and emits a `pipelines` block. Kept only so a manifest found in the wild can be dated:
a manifest with no `pipelines` key and ~106 entries came from this.

```python
def main():
    version = os.environ.get('GITHUB_REF_NAME', 'unknown')
    manifest = {"version": version, "files": {}}

    # Directories to track based on our strategy
    patterns = [
        'pipelines/**/code/*.ipynb',
        'pipelines/**/reporting/*.ipynb',
        'pipelines/**/utils/*.r',
        'code/**/*.r',
        '*/pipeline.py',
    ]

    tracked_files = []
    for pattern in patterns:
        tracked_files.extend(glob.glob(pattern, recursive=True))
    tracked_files = list(set(tracked_files))  # Deduplicate

    for fpath in tracked_files:
        manifest["files"][fpath.replace('\\', '/')] = hash_file(fpath)

    with open('release_manifest.json', 'w') as f:
        json.dump(manifest, f, indent=2)
```

Verified 2026-09-16 against `v0.0.1-test`: `version` read the tag correctly (not `"unknown"`), 106
files tracked, every hash a valid 64-char sha256, all 20 `pipeline.py` files matched a local `find`
excluding `deprecated/`.

### 3.2 The original product-spec draft — 2026-09-18

`product_spec_draft.md` was the user's free-form statement of intent. It was consolidated into
`PRODUCT_SPEC.md` (§3.3) the same day and the file removed. Its substance is reproduced
here because it is the only record of what was asked for before the requirements were formalised —
useful if a requirement in the spec ever looks arbitrary.

> Final product vision: an OH webapp that allows the user to install a specific release of the SNT
> Stratification suite (SNT pipelines collection + associated webapps), and/or to check what is the
> status of the workspace content relative to a specific release (check that all expected files are
> there and if they are of the correct version for a given release, else flag anything that is off)
> and then decide to either fix things (if files are missing or are of the wrong version: install and
> update to match a target release) or leave as is (it is possible that some changes were made
> manually and the user wants to keep them).
>
> This webapp is built as a nice UI/UX layer on top of an OH pipeline. This is because the pipeline
> is needed to do things that are a bit too much for a web app: namely pulling files (saving to OH ws
> file system) and deploying pipelines in OH, from the GitHub repo. So for now I want to focus on the
> pipeline.
>
> What I want the pipeline to do: check the status of the workspace — look at all files present in
> the workspace and extract their SHA; compare against the `release_manifest.json` for each release
> tag of the reference GitHub repo, and derive which release each file belongs to. Options: belongs
> to target release = correct; does not = "behind" (older), "ahead" (newer) or "unknown" (belongs to
> no release, probably edited manually or corrupted); plus files that are missing — defined in the
> manifest but absent from the file system or the pipelines database. It could output a summary file
> with the status of all relevant files, conceived to be readable by the future webapp.
>
> The GitHub repo will be public. The user eventually will not be able to choose the repo (so it
> should be hard coded, so we are in control), but for initial stages let's make it a parameter for
> ease of testing. The user should be allowed to choose the release version though, for
> reproducibility (a country may run an analysis now and in a year want the exact same analysis with
> newer data). The pipeline should be very verbose, explicit and clear, to avoid any "blackbox"
> feeling. Older or stale files should never be deleted, but moved to an "archive" location findable
> by the user.

The draft's four open questions, and where each landed:

| Draft question | Resolution |
|---|---|
| One pipeline for check + fix, or two? | Two — decision **D1**/**D2**, [`decisions.md`](decisions.md) §8. |
| A mechanism to import every release's manifest | Closed 2026-09-29: fetch all on every run, which costs about one API request ([`checker.md`](checker.md) §7.2). |
| What to do with files not in the manifest ("ignore"?) | Not ignored: **reported** in an `untracked` bucket, never acted on — decision **D6**. |
| Different releases having different file lists | Covered by the `missing` / `removed_in_target` statuses, [`checker.md`](checker.md) §5.1. |
| Test repo needs a `latest` release | **Rejected.** GitHub's `/releases/latest` endpoint already resolves to the newest non-prerelease release; a release *named* `latest` would collide with it. |

### 3.3 The `docs/wip/` layout — superseded 2026-10-01

Until the move to `BLSQ/snt_workspace_bootstrap`, these docs lived in `snt_development/docs/wip/` as
five files plus two schemas. Session 3 of the move split them by component (see `CLAUDE.md`). What
the old index and the old preambles said, kept here because they are no longer true (links removed,
names as they were):

#### `docs/wip/` — SNT release management

Working documents for the release / workspace-versioning effort (SNT25-670). Nothing described here
is live in a country workspace yet.

> **This folder is destined to become its own repository** (`PRODUCT_SPEC.md` D18). It lives inside
> `snt_development` for now so that agents working on the pipelines have the context at hand. Keep it
> self-contained: machine-readable contracts go in `docs/` (today,
> `status_report.schema.json`, the frozen report schema, and
> `release_manifest.schema.json`, the manifest contract), and a
> link that leaves this folder is a link to revisit at the move.

**Six files, and they do not overlap.** Each states only what is *currently* true; anything that
stopped being true moved to `HISTORY.md`.

| File | Answers | Read it when |
|---|---|---|
| `release_strategy.md` | **Why** the release mechanism exists, what it delivers, and what is built today. | You need the shape of the whole thing, or the state of the manifest generator / Workspace Manager. |
| `PRODUCT_SPEC.md` | **What** the workspace checker must do — statuses, report contract, build phases, open decisions. | You are building or reviewing the checker. |
| `pipeline_deployment_mechanism.md` | **How** a pipeline is deployed into a workspace through the OpenHEXA API. | You are touching deployment, tokens or the GraphQL calls. |
| `docs/status_report.schema.json` | **The contract**: the frozen JSON Schema of the checker's report (`schema_version: 1`). Machine-readable, not prose. | You are writing or validating a consumer of the report, or changing the report's shape. |
| `docs/release_manifest.schema.json` | **The contract** between a release's producer and its consumers: the JSON Schema of `release_manifest.json` (D26). Machine-readable, not prose. | You are changing how the manifest is generated or read, or publishing releases from another repository. |
| `HISTORY.md` | **What is no longer true** — superseded designs, closed issues, dead ends, deleted fixtures, the original spec draft. | **Before** investigating anything that smells already-solved, or before reopening a decision. |

Local, uncommitted companions under `ignore/SNT25-670/` (runbooks containing `git`/`gh` commands for
a human to run) are referenced by name where relevant.

#### `PRODUCT_SPEC.md` and `release_strategy.md` preambles

#### SNT Release Management — Product Spec

> Status: specification, work in progress. This is where the checker's **requirements** live;
> `release_strategy.md` keeps the *why* and the state of what is built,
> `pipeline_deployment_mechanism.md` the deployment *how*, and
> `HISTORY.md` everything that is no longer true — superseded designs, closed issues,
> dead ends and deleted fixtures. Check `HISTORY.md` before reopening any question here.
>
> Nothing specified here is live in a country workspace. Sections marked **BUILT** describe code
> that exists and has been verified in a sandbox; everything else is a requirement, not a report.
>
> **This folder (`docs/wip/`) is destined to become its own repository** (decision D18). It sits
> inside `snt_development` for now only so that agents working on the pipelines have the context
> at hand. Write everything here as if the parent repo were not there: no links that only make
> sense from the parent's layout unless they are marked as such, and machine-readable contracts
> (such as `docs/status_report.schema.json`) live *inside* this
> folder, never beside the pipelines. Links that point out of the folder today (to
> `../../snt_workspace_check/`, `../PIPELINE_README_STANDARD.md`, `../../.github/`) are the ones to
> revisit at the move.

> Status: work in progress. Manifest generation, the Workspace Manager and Python deployment are
> built and verified in a sandbox; the verification pipeline is not started. Nothing here is live
> in a country workspace yet.
>
> This document describes the **current** design and state. Superseded designs, closed problems,
> dead ends and verifications against deleted fixtures are in `HISTORY.md` — read that
> before re-investigating anything here.

### 3.4 The deployer's legacy-manifest fallback — deleted 2026-10-02

From phase 0 (2026-09-21) until 2026-10-02, `split_manifest()` in the deployer read the manifest's
`pipelines` block when present, and otherwise recovered the pipeline directories from the
`<name>/pipeline.py` entries of `files`:

```python
if pipelines:
    pipeline_dirs = set(pipelines)
else:
    pipeline_dirs = {
        Path(p).parts[0]
        for p in tracked_files
        if len(Path(p).parts) == 2 and Path(p).parts[1] == "pipeline.py"
    }
```

The checker never carried it. D26 (2026-10-01) decided legacy manifests are not supported, so the
fallback was untested code for a case that can no longer occur. It was deleted in a change of its
own. `download_manifest()` now refuses a manifest with no `pipelines` block. One behaviour went with
it: an empty block (`{}`) used to fall through to the derivation, and is now read as "no pipelines",
as the schema says.

---

## 4. Retired sandbox state and stale verification records

### 4.1 Sandbox reset — 2026-09-21

The sandbox repository had accumulated two branches, two manifest generations and a fixture set
built in stages; disentangling it was worth less than restarting. `BLSQ/snt_development_sandbox` was
**deleted and recreated** under the same name, then seeded with a single parentless commit carrying
the tree of `snt_development` @ `551ddd8` — minus the 20 `push_snt_*.yaml` deployment workflows,
which target the **real** `snt-development` workspace via `secrets.OH_TOKEN` and must never fire
from a sandbox.

Deleting the repository also deleted its tag-protection ruleset, which had to be recreated. That is
also the reason a sandbox cannot be cleaned up *in place*: *Restrict deletions* with an empty bypass
list stops an admin deleting the very tags they want gone.

The OpenHEXA workspace `snt-development-sandbox` was **not** reset. It still holds pipelines and a
`.snt_release` marker naming a tag that no longer exists — harmless, and itself a usable test of how
the checker handles an unresolvable declared release.

Procedure: the local runbook `sandbox_reset_runbook.md`, never committed; what still matters of it is in [`sandbox.md`](sandbox.md).

### 4.2 `v0.0.1-test` and `v0.0.2-test` are retired names

Both releases were deleted in the reset. **The numbers are deliberately not reused**: they are
attached in writing — in this file and in the git history — to a 106-file legacy manifest, and
reusing them would make a tag mean two things, which is the exact failure the tag-protection
convention exists to prevent. The fixture series restarts at `v0.1.0-test`.

Verifications performed against those tags, which stand as a record but **cannot be re-run**:

| What was verified | When | Result |
|---|---|---|
| Manifest generation (legacy generator) against `v0.0.1-test` | 2026-09-16 | 106 files, tag read correctly, all hashes valid |
| `snt_workspace_manager` full run against `v0.0.1-test` | 2026-09-16 | 60s; `.snt_release`, the three shared `code/*.r` files and pipeline code all confirmed |
| API deployment of `snt_dhis2_extract` + `snt_map_extracts` from `v0.0.1-test` | 2026-09-16 | Correct codes; parameters round-tripped through `Parameter.to_dict()` including the `dhis2_connection` connection-typed parameter; deployed `pipeline.py` read back at sha256 `44290bd9…d77755cd`, byte-identical to the manifest hash |
| `split_manifest()` old and new paths against both legacy manifests | 2026-09-21 | 86 analytics files in both, unchanged (§2.2) |

`backup_existing` was **never** exercised by any of these — the verified run was against an empty
workspace, so there was nothing to archive. That gap is still open.

---

## 5. Timeline

| Date | Event |
|---|---|
| 2026-09-16 | Legacy manifest generator verified; `snt_workspace_manager` v3 proven end to end against `v0.0.1-test`; API deployment mechanism written up. |
| 2026-09-18 | `product_spec_draft.md` reviewed with Giulia; decisions D1–D9, D11, D12 taken; `PRODUCT_SPEC.md` written. §7.2 (obtaining every manifest) deferred to a dedicated session. Tag-protection ruleset created on the sandbox. |
| 2026-09-21 | Phase 0: manifest generator rewritten to mirror the SDK's zip rule (107 → 156 files) and given a `pipelines` block; `split_manifest()` fixed in the same change. Sandbox repo reset; fixture releases `v0.1.0-test` … `v0.4.0-test` cut. |
| 2026-09-22 | `docs/wip/` split: current state in the three live documents, history consolidated here. §7.3a closed by the `snt-token-probe` run (§2.4): a run's own `HEXA_TOKEN` reads pipeline version zips, so the checker is credential-free and **phase 1 is unblocked**. §7.3b (placing the `oh` token in a country workspace) parked as low priority. **Phase 1 built and verified**: `snt_workspace_check` reports 163/163 `match` in the sandbox at `v0.1.0-test` ([`checker.md`](checker.md) §6.2). Two defects surfaced on the way — the version-name `[vN]` suffix (§1 above, fixed) and the manager's `DUPLICATE_PIPELINE_VERSION_NAME` on re-deploy (`release_strategy.md`, open). |
| 2026-09-29 | **Phase 2 built and verified** ([`checker.md`](checker.md) §6.3). The checker loads every release's manifest and walks the filesystem; the full taxonomy reported exactly as predicted in the sandbox. That run also found 55 inert filesystem copies left by the pre-fix `split_manifest()` (§2.2), which Giulia deleted by hand. §7.5 closed (report-and-leave). D9 decided (no notebook normalisation), from one real country workspace. §7.8 decided as D14 (spans). `added_after_target` accepted as D13. **§7.2 closed**: manifest downloads are not charged per file, so fetch-all is the design (first measurement attempt failed, §1). Phase-3 handover written ([`checker.md`](checker.md) §6.4). Two new blocking decisions for it: §7.10 (what an attribution percentage means) and §7.11 (status name; forcing attribution mode). |
| 2026-09-30 | Phase 4 done (schema frozen, D18–D20). Phase 5 started with the manager on its own: `sync_analytics`, `deploy_pipelines`, `only_pipelines` and `create_missing` removed (D21); the `DUPLICATE_PIPELINE_VERSION_NAME` re-deploy defect addressed by comparing contents and relabelling unchanged pipelines with the tag (D22). Written and linted, stub-tested offline, **not yet run in a workspace** ([`deployer.md`](deployer.md) §6.7). Recorded as limits to keep in mind: no factory reset, and pipelines dropped by a release remain as strays (§7.5). |
| 2026-10-01 | Manager: an empty `release_tag` now deploys the latest release (D23), and every failure is logged to the run's Messages as `[ERROR] Cannot deploy: …` (D24). Prompted by a sandbox run that resolved to `v0.4.0-test` and stopped on its missing manifest with no message. Both confirmed in `snt-development-sandbox`; the D21/D22 test plan is still open ([`deployer.md`](deployer.md) §6.7). Working assumption recorded as D25: one workspace per release, so rolling back and forth only has to be safe, not clean ([`release_strategy.md`](release_strategy.md) §1.3). |
| 2026-10-01 | Moved to `BLSQ/snt_workspace_bootstrap`, then renamed (M7): `snt_workspace_check` → `snt_workspace_checker`, `snt_workspace_manager` → `snt_workspace_deployer` (Workspace Manager → Workspace Deployer). Entries above keep the names they were written with; the report key `snt_workspace_check_version` stays frozen. |
| 2026-10-02 | Deployer: the pre-phase-0 manifest fallback in `split_manifest()` deleted (D26, §3.4). A manifest with no `pipelines` block now stops the run with `[ERROR] Cannot deploy: …` before anything is written. Tested in `snt-development-sandbox` with `v0.3.0-test` and `v0.0.1-test`. |
| 2026-10-02 | Report key `snt_workspace_check_version` renamed `snt_workspace_checker_version` (M7), inside `schema_version: 1` because no checker release or report consumer existed yet ([`checker.md`](checker.md) §5.5). Reports written before this carry the old key. |
