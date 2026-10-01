# SNT Release Management — Product Spec

> Status: specification, work in progress. This is where the checker's **requirements** live;
> [`release_strategy.md`](release_strategy.md) keeps the *why* and the state of what is built,
> [`pipeline_deployment_mechanism.md`](pipeline_deployment_mechanism.md) the deployment *how*, and
> [`HISTORY.md`](HISTORY.md) everything that is no longer true — superseded designs, closed issues,
> dead ends and deleted fixtures. Check `HISTORY.md` before reopening any question here.
>
> Nothing specified here is live in a country workspace. Sections marked **BUILT** describe code
> that exists and has been verified in a sandbox; everything else is a requirement, not a report.
>
> **This folder (`docs/wip/`) is destined to become its own repository** (decision D18). It sits
> inside `snt_development` for now only so that agents working on the pipelines have the context
> at hand. Write everything here as if the parent repo were not there: no links that only make
> sense from the parent's layout unless they are marked as such, and machine-readable contracts
> (such as [`docs/status_report.schema.json`](docs/status_report.schema.json)) live *inside* this
> folder, never beside the pipelines. Links that point out of the folder today (to
> `../../snt_workspace_checker/`, `../PIPELINE_README_STANDARD.md`, `../../.github/`) are the ones to
> revisit at the move.

## 1. The product

A country workspace should be able to answer two questions without anyone reading a diff:

1. **What is in here?** Which release does each file in this workspace belong to, and is anything
   of unknown origin?
2. **Is it what I asked for?** Given a target release, what matches, what is behind, what is ahead,
   what is missing — and can I bring it into line, or deliberately keep what I have?

The user-facing product is eventually an OpenHEXA web app. The web app is a UI/UX layer only: the
work that cannot be done from a web app — pulling files into the workspace filesystem and
registering pipeline versions through the OpenHEXA API — is done by pipelines. **This spec covers
the pipelines. The web app is out of scope beyond the report contract it will consume (§5.5).**

### 1.1 Components

| Component | Role | State |
|---|---|---|
| `snt_workspace_deployer` | **Fix / install.** Deploys one pinned release, always whole, into the workspace: R analytics to the filesystem, `pipeline.py` via the API. Cannot delete anything (§7.5). | **BUILT**, prototype — changed 2026-09-30 (D21, D22), **not yet tested in a workspace** (§6.7) |
| `snt_workspace_checker` | **Check.** Read-only. Hashes what is actually in the workspace, attributes each file to a release, and writes a status report. | **BUILT**, phase 4 — both modes verified in the sandbox (§6.3, §6.5); report schema frozen at `schema_version: 1` (§5.5, D18). No deployment workflow, by decision (D19) |
| Release manifest generation | GitHub Action producing `release_manifest.json` per release | **BUILT** — covers everything the deploy zip ships |
| Status web app | Reads the checker's report; offers "fix" or "leave as is" | Deferred |

The checker and `snt_workspace_deployer` are **separate pipelines** (decision D2). The checker
never writes anything except its own report; `snt_workspace_deployer` is the only component that changes
workspace state.

### 1.2 In scope / out of scope

In scope for the checker: everything the release manifest tracks — `pipelines/**/code/*.ipynb`,
`pipelines/**/reporting/*.ipynb`, `pipelines/**/utils/*.r`, `code/**/*.r`, and each deployed
pipeline's registered version contents.

Out of scope, and to be stated as blind spots in the report:

* **OpenHEXA web apps** (decision D8) — there is no deployment story for them yet; see
  `release_strategy.md`.
* **`configuration/`** — managed by the Config Editor web app.
* **`data/`** — bootstrapped by the pipelines themselves.
* **Country-specific notebook variants as *overrides*** (decision D5) — in v1 they fall out as
  ordinary untracked files. Recognising them as deliberate overrides is deferred, and until it is
  done the report will look alarming in any workspace that has one.

### 1.3 Expected usage: one workspace per release (D25)

The working assumption, until real users say otherwise: **a country keeps one workspace on one
release.** Typically that is a main workspace that tracks the latest release and is updated forward
as releases come out. To run data through an old release (say, last year's), a country is expected
to use a **dedicated workspace** deployed at that release, not to roll its main workspace back and
then forward again.

Consequence for this work: **moving forwards is the path that has to be solid; going back and
forth between releases in one workspace is not a priority.** Downgrades and back-and-forth must stay
*safe* — nothing deleted, superseded files archived, every failure reported — but they do not have
to be *clean*: `+redeploy-` version names, extra versions, strays and checker statuses that read
oddly after a rollback are acceptable. Do not spend effort polishing that path until there is
feedback from actual use. Revisit this assumption once countries are running the release process.

## 2. Vocabulary

| Term | Meaning |
|---|---|
| **Release tag** | A GitHub release tag, e.g. `v1.2.0`. The single source of truth for "what version is this workspace on". Tags are protected and never moved (`release_strategy.md` §"Release tags are never moved"). |
| **Release manifest** | `release_manifest.json`, attached as an asset to each release: `{version, files: {path: sha256}, pipelines: {dir: {code, zip_files}}}`. `files` is flat and covers both sources; the `pipelines` block says which of those paths ship inside which pipeline zip, and under what name once inside it (§2.1). |
| **Tracked file** | A path present in some release manifest. |
| **Target release** | The release the workspace is being compared *against*, when one is given. |
| **Declared release** | What `.snt_release` says the workspace was last deployed to. Intent, not verified fact (§3.2). |
| **Attribution** | The set of releases whose manifest contains a file's observed hash. |
| **Drift** | An observed hash that matches no manifest of any release. |

### 2.1 The manifest's `pipelines` block

`files` alone does not say *where* an entry lives in a workspace, and
the two answers are not interchangeable: `code/snt_utils.r` is a file on the filesystem, while
`snt_map_extracts/utils.py` exists **only** inside a pipeline's registered version zip and is at no
path on the filesystem at all. Without the block, every consumer has to re-derive that split by
reapplying the generator's rule, and a consumer that gets it wrong reports `missing` for files that
are deployed and correct — a false alarm in the one component whose job is to be trusted.

```json
"pipelines": {
  "snt_map_extracts": {
    "code": "snt-map-extracts",
    "zip_files": ["malariaAtlasProject/__init__.py", "pipeline.py", "readme.md", "utils.py"]
  }
}
```

* The **key** is the repository directory. Prefix it to a `zip_files` entry to get the repository
  path, which is the key into `files` and therefore the expected hash.
* `zip_files` are paths **inside the zip**, so they can be compared directly against the
  `namelist()` of what `get_pipeline` returns.
* `code` is the OpenHEXA pipeline code — the directory name with underscores replaced by hyphens.
  A consumer needs it to look the pipeline up over the API and can get it from nowhere else in the
  manifest. Verified against all 20 `push_snt_*.yaml` `--code` values and against
  `snt_workspace_deployer`'s own derivation.

`files` keeps its exact previous shape, so the block is additive: a reader that ignores `pipelines`
behaves as before. Legacy (pre-phase-0) manifests have no such block and are not supported: a
consumer refuses one. `split_manifest()` in `snt_workspace_deployer` still carries a fallback that
derives pipeline directories from `<name>/pipeline.py` entries; it is to be deleted (below).

**Decided 2026-10-01 (D26): delete the fallback.** Legacy manifests only ever existed during
development, no live release lacks the block, and the fallback is unreachable in testing — untested
back-compat code for a case that can no longer occur. Delete it in a PR of its own; until then it is
dead code. The contract is [`docs/release_manifest.schema.json`](docs/release_manifest.schema.json),
which requires the block.

`snt_workspace_checker` deliberately does **not** carry the fallback: it refuses a manifest with no
`pipelines` block, naming the phase-0 cutover in the error. That makes the two components disagree on
purpose, and the disagreement is the argument — one of them has untested code for an impossible case
and the other does not. Resolved in the checker's favour (D26).

## 3. What the checker observes

### 3.1 Two sources of truth, hashed separately

Every entry in the report carries which source it came from.

| Source | What is hashed | Notes |
|---|---|---|
| `filesystem` | Files under `workspace.files_path` at their repository-relative paths | Where the R analytics live |
| `pipeline_version` | The files inside each OpenHEXA pipeline's **current registered version zip** | `pipeline.py` is *never* on the filesystem — a copy there is inert and misleading (`pipeline_deployment_mechanism.md`) |

Both are read from v1 (decision D7). The zip is readable through the GraphQL field
`pipelineByCode.currentVersion.zipfile`, which returns the base64 of the whole archive — the same
read the SDK's `download_pipeline_sourcecode()` performs.

**Verified 2026-09-22:** a run's own `HEXA_TOKEN` reads that field in full, so the checker needs
**no workspace credential**. Reading and deploying have different credential requirements, and only
deploying needs the `oh` connection (§7.3, [`HISTORY.md`](HISTORY.md) §2.4).

#### Which pipeline version is hashed, and why the version name is not evidence

An OpenHEXA pipeline is not a file — it is a registered object that accumulates **versions**, each
storing its own zipped copy of the code. A pipeline that has been deployed ten times has ten
versions; only one of them, the **current version**, is what a run actually executes. So:

* **The checker hashes the current version only.** Older versions are history. They are not what
  would run, and reporting on them would drown the report in files nobody can act on.
* **Each version has a *name*, which is free text.** `snt_workspace_deployer` sets it to the release
  tag it deployed from, so in the normal case a pipeline whose current version is called
  `v0.2.1-test` really does contain `v0.2.1-test`'s code.

The name is metadata someone typed; the hash is evidence. They can disagree, and the ways they
disagree are exactly the ways the old system failed silently:

* somebody deployed by hand with the CLI, from a working tree that was not at that tag, and named
  the version after the tag anyway;
* `snt_workspace_deployer` was run at a tag but a source file had been edited in the workspace
  before the zip was built;
* a version was named by hand in the UI.

So the rule is: **when the name and the contents disagree, the hash wins and the name is reported
as misleading.** This is not a per-file status — it is a per-pipeline flag in the report
(`current_version_name` alongside `version_name_matches_content: false`), because the mismatch is a
property of the deployment, not of any one file inside the zip. The point of surfacing it at all is
that a workspace where the version labels have stopped meaning anything looks perfectly healthy
from the OpenHEXA UI, which only shows the names.

### 3.2 `.snt_release`

`snt_workspace_deployer` writes `{"snt_release": "<tag>"}` at the workspace root at the end of
every run. Two honest limitations, both of which the report must reflect rather than paper over:

* It records only the tag, **not the repository it came from**, while `github_repo` is still a
  parameter.
* It is written after a partial run too, so it states **intent**, not verified fact.

A workspace with no marker is the normal starting state today — every existing country workspace.
That must never be an error.

## 4. The two modes

The checker has one parameter that decides its mode: an optional target release.

### 4.1 Attribution mode — no target release given

Assess every file and report which releases it belongs to, or that it belongs to none. No verdicts,
no green or red: a factual inventory plus **two scores per release** (D15, §7.10), each judged only on
the paths that release ships. So the web app can show e.g. *"best fit v0.3.0-test: of its files
found here, 100% have its exact bytes (agreement); 98.2% of its files are here (completeness); 4
files here are ones it does not ship."* The scores do not add up to 100% across releases: a file
unchanged across releases agrees with each of them.

This mode needs the manifest of **every** release. The checker loads them all as of phase 2 (§7.2,
closed).

### 4.2 Verification mode — target release given

Everything attribution mode reports, plus a qualitative layer per file relative to the target. The
per-release scores are computed in this mode too.

The target release is resolved in this order, and the report always records which was used
(`target_release.resolved_from`):

1. the `release_tag` parameter, if given. The reserved value **`none`** (any case) means no target →
   mode 4.1, whatever the marker says (D17);
2. otherwise `.snt_release`, if present → mode 4.2;
3. otherwise no target → mode 4.1 (`resolved_from: "nothing_given"`).

## 5. Requirements

### 5.1 Status taxonomy

Per entry, exactly one status:

| Status | Meaning | Mode |
|---|---|---|
| `match` | Present; hash equals the target manifest's | 4.2 |
| `mismatch_known` | Present; hash differs from target but matches ≥1 other release. Carries `matching_releases` and `position` (`older` / `newer` / `both` / `unordered`) | 4.2 |
| `attributed` | Present at a tracked path; hash matches ≥1 release. Carries `matching_releases` over **all** of them; `position` is `null`. Added in phase 3 (D16) | 4.1 |
| `unknown_content` | **Known path, unknown content.** The path is tracked, but its hash matches no release. Edited by hand, or corrupted | both |
| `missing` | In the target manifest; absent from both filesystem and pipeline versions | 4.2 |
| `removed_in_target` | Present, and in an older manifest, but not in the target's | 4.2 |
| `added_after_target` | Present, not in the target's manifest, and **only** in manifests newer than it — the workspace is ahead. Added in phase 2 (§6.3): without it, "ahead" would read as "removed" | 4.2 |
| `untracked` | **Unknown path.** This path appears in no manifest of any release — the repo has never shipped a file here | both |
| `not_covered` | Deployed inside a pipeline zip, but no manifest describes it. Should not occur now the manifest mirrors the SDK's zip rule; retained as the tripwire for that rule drifting | both |
| `unreadable` | Present but could not be hashed (permissions, I/O, API error) | both |

In attribution mode, files resolve to `attributed` (with `matching_releases`, see §5.1.2),
`unknown_content`, `untracked`, `not_covered` or `unreadable`.

#### 5.1.1 `unknown_content` vs `untracked` — two different questions

They sound alike and are not. One is about the **path**, the other about the **bytes**:

| | Is this path in any manifest? | Does the content match any release? | Meaning |
|---|---|---|---|
| `unknown_content` | **yes** | no | A file we ship, whose copy here is not any version we ever released. Someone edited it, or it is corrupt. **Actionable** — this is drift. |
| `untracked` | **no** | not asked | A file the release has no opinion about: an analyst's scratch notebook, a stray export. **Not actionable** — reported for completeness only (§5.3). |

The content question is never even asked for an `untracked` file, because there is no manifest
entry to compare it against.

#### 5.1.2 Attribution is a set, not a value — and it needs collapsing

**"Belongs to release X" is not a function.** A file that did not change between `v0.1.0` and
`v0.6.0` has one hash that is correct for all seven of those releases. A single verdict only exists
relative to a target release; attribution on its own is a *set*.

Enumerating that set per file would make the report unreadable — most files are unchanged most of
the time, so most entries would carry a list of nearly every release that exists. The report must
collapse it. Representation, **decided 2026-09-29** (D14, §7.8):

```json
"matching_releases": [{"from": "v0.1.0", "to": "v0.6.0", "count": 7}]
```

i.e. contiguous spans in `published_at` order rather than a flat list, with the common case
rendering as a single span the web app can show as "unchanged since v0.1.0". A non-contiguous set
(content introduced, changed, then reverted) yields more than one span, which is itself the signal
worth seeing. A single release is a span of one. Any release outside the set breaks a span,
including the target and a release whose manifest could not be read.

### 5.2 Ordering: behind vs ahead

`older` / `newer` are computed from the GitHub release **`published_at`** timestamp (decision D4),
not from tag-string parsing — the sandbox's `-test` tags do not parse as semver, and tag protection
makes republication of an old release a non-concern. A release whose timestamp cannot be read is
reported `unordered` rather than guessed.

`position` describes the matching span relative to the **target**, and all four values are reachable:

| Value | When |
|---|---|
| `older` | Every release matching this content is before the target — the file is **behind**. |
| `newer` | Every match is after the target — the file is **ahead**. |
| `both` | Matches exist on both sides of the target. |
| `unordered` | At least one matching release has no usable timestamp. |

`both` is not the unchanged-across-releases case — an unchanged file that includes the target in
its span is simply `match`. It means the content was **changed in the target release and later
reverted**: `v1` has hash H, `v2` (the target) has H′, `v3` goes back to H. A workspace holding H
then matches a release older *and* a release newer than the target, while matching the target
itself not at all. Rare, but it happens whenever a change is rolled back, and calling it "behind"
would be wrong.

### 5.3 Untracked files

Reported in an `untracked` bucket with count and paths (decision D6); never acted on. One case is
sharper than the rest and must be marked as such: **an untracked file inside a pipeline directory
ships in the deployment zip**, so it is not inert the way a stray file on the filesystem is.

Excluded from the scan entirely, so the bucket stays readable: `archive/`, `papermill_outputs/`,
`reporting/outputs/`, `data/`, `configuration/`, and any checker output directory.

### 5.4 Non-negotiables

* **The checker is read-only.** It writes its report and nothing else. It never deletes, moves,
  overwrites, deploys or archives — `snt_workspace_deployer` does that.
* **Nothing is ever deleted, by either component.** Superseded files are moved to
  `archive/<release_tag>/`, findable by the user, who deletes manually if they want to.
* **Verbose and explicit.** No black-box feeling: every decision that shaped the verdict is either
  logged or in the report. With 100+ tracked files, per-file logging is a summary in the log and
  the full detail in the JSON — not 300 log lines.
* **Partial results are labelled.** If a source could not be read, the report carries
  `incomplete: true` and says which, rather than implying a clean bill of health.
* **The repository is a parameter now, hard-coded later.** Testing runs against
  `BLSQ/snt_development_sandbox`; the shipped product must not let a user point it anywhere.

### 5.5 The report contract

**Frozen at `schema_version: 1` (phase 4, decision D18).** The authority is the JSON Schema
[`docs/status_report.schema.json`](docs/status_report.schema.json), not this section. This section
says what the contract is *for* and shows the shape; if the two ever disagree, the schema wins and
this section is the bug.

A JSON file, written to the workspace filesystem, timestamped **and** latest (decision D3):

```
<workspace root>/snt_status/status_<UTC timestamp>.json    ← colons in the time replaced by '-'
<workspace root>/snt_status/status_latest.json             ← byte-identical; the stable path the web app reads
```

Top-level keys, all always present: `schema_version`, `generated_at`, `snt_workspace_check_version`,
`workspace`, `repo`, `mode`, `target_release`, `declared_release`, `releases_considered`,
`incomplete`, `summary` (`by_status`, `attribution`), `pipelines`, `entries`,
`inert_filesystem_copies`, `scan_exclusions`, `errors`, `blind_spots`. An abridged example:

```json
{
  "schema_version": 1,
  "generated_at": "2026-09-29T13:39:32Z",
  "snt_workspace_check_version": null,
  "workspace": "<slug>",
  "repo": "BLSQ/snt_development_sandbox",
  "mode": "attribution",
  "target_release": {"tag": null, "resolved_from": "parameter", "published_at": null},
  "declared_release": {"tag": "v0.3.0-test", "source": ".snt_release"},
  "releases_considered": [{"tag": "v0.1.0-test", "published_at": "2026-09-21T15:20:03Z", "manifest_available": true}],
  "incomplete": true,
  "summary": {
    "by_status": {"attributed": 164, "untracked": 1},
    "attribution": {
      "files_scored": 164,
      "rule": "<display text: how every number below was computed>",
      "by_release": [
        {"tag": "v0.3.0-test", "published_at": "2026-09-21T16:04:03Z", "manifest_available": true,
         "shipped": 163, "present": 160, "agreeing": 160, "extra": 4, "agreement": 1.0, "completeness": 0.9816}
      ],
      "best_fit": {"tag": "v0.3.0-test", "agreement": 1.0, "completeness": 0.9816},
      "matches_no_release": {"count": 0, "share": 0.0},
      "unreadable": {"count": 0, "share": 0.0}
    }
  },
  "pipelines": [
    {"pipeline": "snt_dhis2_extract", "code": "snt-dhis2-extract", "in_target": null, "in_any_release": true,
     "current_version_name": "v0.1.0-test [v2]", "current_version_claims_tag": "v0.1.0-test",
     "version_name_matches_content": true, "remediation": null}
  ],
  "entries": [
    {
      "path": "code/fixture_stable.r",
      "source": "filesystem",
      "pipeline": null,
      "status": "attributed",
      "observed_sha256": "<64 hex>",
      "target_sha256": null,
      "matching_releases": [{"from": "v0.1.0-test", "to": "v0.3.0-test", "count": 4}],
      "position": null,
      "remediation": null
    }
  ],
  "inert_filesystem_copies": [],
  "scan_exclusions": {"top_level_directories": ["archive", "configuration", "data", "snt_status"],
                      "directories_at_any_depth": ["papermill_outputs"], "subpaths": ["reporting/outputs"],
                      "root_files": [".snt_release"]},
  "errors": [{"scope": "manifest:v0.4.0-test", "message": "<display text>"}],
  "blind_spots": ["<display text>"]
}
```

**Compatibility rules** (what "frozen" means):

* **Not breaking, stays `schema_version: 1`:** a new key; a new `status`, `position`,
  `resolved_from`, `source` or `errors[].scope` value; a new `blind_spots` line. The schema is
  updated in the same change.
* **Breaking, needs `schema_version: 2`:** renaming or removing a key or an enum value, changing a
  type, or changing what a value *means* (for instance redefining `agreement`).
* The schema is deliberately **strict** (`additionalProperties: false`, closed enums) so the
  *producer* cannot drift silently. A *consumer* must do the opposite: ignore unknown keys and
  tolerate unknown enum values, or the first additive change breaks it.
* **Display-only strings.** `remediation`, `errors[].message`, `blind_spots[]` and
  `summary.attribution.rule` are text for a human, marked `x-display-only` in the schema. Never
  parse or compare them; their wording may change at any time without a version bump.
* Every key that could be absent is present with `null`, never omitted.

**`snt_workspace_check_version`** records which version of the `snt_workspace_checker` pipeline
generated the report, so a report can be tied to the code that wrote it. **It is `null` today.** A
run has no way to read the name of the pipeline version executing it, and no source for the value
has been found; the key is frozen and nullable so that filling it later is not a breaking change.
It replaces the earlier name `checker_version`, renamed for clarity before the freeze.

## 6. Build phases

Each phase ends with something demonstrable. Do not start a phase whose blocking decision (§7) is
still open.

| # | Deliverable | Exit criterion | Blocked by |
|---|---|---|---|
| **0** | ✅ **DONE** — manifest gap closed, sandbox reset, fixture releases cut (§6.1). Details: [`HISTORY.md`](HISTORY.md) §2.1, §4. | — | — |
| **1** | ✅ **DONE** 2026-09-22 — checker skeleton in [`snt_workspace_checker/`](../../snt_workspace_checker/): verification mode against a single target release, both sources hashed, four statuses, report written (§6.2). | Met: `snt-development-sandbox` at `v0.1.0-test` reported **163/163 `match`**. | — |
| **2** | ✅ **DONE** 2026-09-29 — taxonomy verified in the sandbox, D9 decided (§6.3). Full taxonomy: `removed_in_target`, `untracked`, `not_covered`, the pipeline-directory case. Plus **measure notebook drift** on a real workspace and decide D9. | A workspace at T-1 with one hand-edited file reports exactly the expected mix. | 1 |
| **3** | ✅ **DONE** 2026-09-29 — attribution mode, per-release agreement and completeness in both modes, D15–D17 (§6.4, §6.5). | Met: the mixed sandbox workspace scores `v0.3.0-test` as best fit, agreement 1.0, completeness 0.9816. | — |
| **4** | ✅ **DONE** 2026-09-30 — `schema_version: 1` frozen as [`docs/status_report.schema.json`](docs/status_report.schema.json); `snt_workspace_checker/readme.md` re-verified against the code (§6.6, D18). | Met: schema documented and validated against reports the code builds; readme verified by reading, discrepancies listed in §6.6. | — |
| **5** | `snt_workspace_deployer` integration: report before and after a fix; enrich `.snt_release` (§7.4). **In progress**: step 1, making the deployer solid on its own (re-deploy defect, whole-release-only), is written and untested (§6.7). Integration with the checker comes after. | A fix run links to the before/after reports it produced. | 4 |
| **6** | Web app. | Out of scope for this spec. | 5 |

### 6.1 Test fixtures in the sandbox

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
  attribution span (§5.1.2, §7.8);
* a release published with **no manifest asset** → `manifest_available: false` and
  `incomplete: true` (§5.4, §5.5), made by disabling the generator workflow for one release;
* one hand-edited file in the workspace → `unknown_content`.

Four cases are **not** release fixtures and are made in the workspace immediately before a check
run: `unknown_content`, `untracked`, `unreadable`, and the §5.3 untracked-inside-a-pipeline-zip
case. One more, `position: unordered`, is not reachable at all — GitHub always sets `published_at`
— so it is a unit test against a stubbed release list, not a fixture.

The full command-by-command plan, with the expected status for each fixture in each of three check
runs, is `ignore/SNT25-670/sandbox_fixture_plan.md` (local, not committed — it contains `git`/`gh`
commands for a human to run).

### 6.2 Phase 1, as verified

Run 2026-09-22 against `snt-development-sandbox`, deployed by `snt_workspace_deployer` at
`v0.1.0-test`, checked with `release_tag=v0.1.0-test`:

```
163/163 match   |   89 filesystem + 74 pipeline_version   |   incomplete: false, errors: []
148 distinct observed hashes, no null hashes
21/22 pipelines version_name_matches_content: true
```

Three things that run established, beyond the exit criterion itself:

* **A version name does not read back as it was submitted.** OpenHEXA appends the version number, so
  `v0.1.0-test` returns as `v0.1.0-test [v1]`. An `==` comparison silently disabled the §3.1
  name-versus-content check on the first run — the exact silent-success failure this product exists
  to catch, in the checker itself. Fixed; recorded in [`HISTORY.md`](HISTORY.md) §1. The report now
  carries the raw name and the parsed tag side by side.
* **§5.1.2 is not a theoretical concern.** `snt_dhis2_population_transformation` sits at
  `v0.3.0-test` and correctly reports all-`match` against `v0.1.0-test`: nothing in its directory
  changed between the two, so its bytes belong to both releases. "Belongs to release X" really is a
  set, and a workspace can be labelled one release while being genuinely at another.
* **The checker does not describe itself.** `snt_workspace_checker` is not in any release yet, so it
  appears in no manifest and in no `pipelines` block. From phase 2 it reports as a pipeline no
  release describes (`in_any_release: false`). Expected, not a defect. Both it and
  `snt_workspace_deployer` are to move to a separate repository of their own eventually (Giulia,
  2026-09-29), so the checker may never describe itself through *these* manifests.

### 6.3 Phase 2, as verified

Written 2026-09-29. **Verified the same day** in `snt-development-sandbox`, deployed at `v0.1.0-test`
with `code/snt_palettes.r` hand-edited and `scratch_notes.ipynb` created, checked with
`release_tag=v0.2.1-test`:

```
157 match | 2 mismatch_known | 3 missing | 4 removed_in_target | 1 unknown_content | 1 untracked
incomplete: true — solely v0.4.0-test (no manifest), as designed
```

Every non-`match` entry is the one the Run 1 table predicts, including `fixture_reverted.py` →
`position: both` and the removed pipeline → `in_target: false`. All 21 deployed release pipelines
report `version_name_matches_content: true`, including `snt-dhis2-population-transformation` named
`v0.3.0-test` (only judgeable now that every manifest is loaded). The workspace pipeline-list query
works: `snt-workspace-checker` appears with `in_any_release: false`.

**The run found a real defect.** It listed 55 `inert_filesystem_copies`: every `readme.md`,
`requirements.txt` and helper module of every pipeline, sitting on the filesystem. No `pipeline.py`
is among them. That is the signature of the pre-fix `split_manifest()` bug
([`HISTORY.md`](HISTORY.md) §2.2), which copied everything except `pipeline.py` into the bucket.
The copies include the fixture pipelines' files, so a deployer with that bug ran at `v0.2.x` or
later. They are leftovers, not something the current deployer does. This is exactly the
failure the checker exists to catch.

**D9 decided 2026-09-29: no normalisation** (§8), from the measurement below.

What changed from phase 1, and why:

* **Every release's manifest is loaded, not only the target's** (Giulia, 2026-09-29).
  `removed_in_target` and `untracked` are both defined against *any* release. With the target alone,
  a file removed in the target would be misreported `untracked`. Listing releases is one GitHub API
  request per 100 releases, the same cost as phase 1's single lookup. Manifests come from
  `browser_download_url` on github.com, which is **measured** not to be charged per download
  (§7.2, closed). Loading them all also makes `mismatch_known` and `position` available now,
  rather than in phase 3. Phase 3 keeps attribution mode and the distribution.
* **A release with no usable manifest makes the report `incomplete`**, and it becomes an `errors`
  entry. A file only that release shipped would otherwise be mislabelled with nothing saying so. The
  run stops only if the *target* has none.
* **The filesystem is walked**, minus the §5.3 exclusions. Dot-directories are also excluded,
  because Jupyter's `.ipynb_checkpoints/` would otherwise double the untracked bucket. The report
  carries the exclusions as `scan_exclusions`. Untracked files are listed, not read.
* **`added_after_target`** is a new status (§5.1). A known path that only newer releases ship is
  "ahead", not "removed". Accepted by Giulia 2026-09-29 (D13).
* **`matching_releases` became spans** once §7.8 was decided (D14). **Verified** in a second sandbox
  run the same day (report `status_2026-09-29T12-14-49Z.json`): identical counts, and
  `fixture_reverted.py` carries two spans, `v0.1.0-test..v0.2.0-test` and `v0.3.0-test`. That run
  also shows `inert_filesystem_copies: []`, because Giulia deleted the 55 leftovers by hand in between.
* **An undescribed zip member is split by the SDK's own zip rule.** If the generator would have
  hashed it had it ever been in the repo, it is `untracked` in the zip, the §5.3 sharper case, marked
  by `source: pipeline_version` and its own remediation text. If the generator could never have seen
  it, it is `not_covered`.
* **Filesystem copies inside pipeline directories** go to `inert_filesystem_copies`. They are listed
  and not classified: they never run, and giving them a status would mix them into the verdict.
* **Every release-described pipeline is probed by code**, which is how a deployed pipeline removed in
  the target is found. It gets `in_target: false`, report-and-leave (§7.5). The workspace pipeline
  list is read only to name pipelines no release describes, with
  `pipelines(...) { totalPages items { code currentVersion { versionName } } }` (verified above).
* **`version_name_matches_content` now judges any release name**, not only the target's. A zip must
  hold exactly that release's files for the pipeline; an extra file is a disagreement.
* `files_in_zip_not_in_manifest` was removed from the pipeline block. Those files are now per-file
  entries.

**D9 measurement tool:** `ignore/SNT25-670/d9_notebook_drift.py` (local, standard library). It
compares a downloaded copy of a real workspace's `pipelines/` folder against the repo and puts each
notebook at the first level where the two become equal: `identical`, `formatting`, `outputs`,
`metadata` or `source`.

**Local stub test:** `ignore/SNT25-670/test_workspace_check_stub.py`. It imports the checker with
`current_run` stubbed and asserts every status, position and span case against fake manifests shaped
like the §6.1 fixtures, including `unordered`, which no fixture can reach (§6.1). Run it with
`conda run -n snt_development python ignore/SNT25-670/test_workspace_check_stub.py`. It is the
closest thing this work has to a test suite. Extend it rather than starting a new one.

### 6.4 Phase 3 — handover

Written 2026-09-29 for the session that builds phase 3.

> **Status, 2026-09-29 (later the same day):** §7.10 and §7.11 decided (D15–D17). Items 1–7 below
> are **built**. The stub test (`ignore/SNT25-670/test_workspace_check_stub.py`) asserts the whole
> list, including the exit-criterion breakdown for the mixed workspace described under *Test design*.
> Two things were built beyond the list. The per-release scores are computed in **verification mode
> too**, as §4.2 promises ("everything attribution mode reports"). They are taken from the observed
> bytes, not from statuses, so both modes use one definition. And `target_release` stays an object
> in attribution mode, with `tag: null` and `resolved_from` giving the reason. The first sandbox run
> led to D15 being revised, and the second verified the revision (§6.5). **Phase 3 is done.**

**What already exists, and does not need building.** Everything the old §7.2 worry was about.
`list_releases()` and `load_manifests()` fetch every release's manifest on each run.
`build_index()` re-keys them by path into a `ReleaseIndex`. `to_spans()` produces D14 spans over
`index.all_tags`, where any non-member breaks a span, including a release with no manifest.
`position_of()` places a set relative to the target. `classify()` already does the path-before-bytes
split (`untracked` vs known), and `name_matches_content()` already judges a version against whatever
release its name claims. Phase 3 therefore adds a **mode**, not new machinery.

**What to build:**

1. `resolve_target()` currently raises in its third case (no parameter, no marker). Return "no
   target" instead, and let `mode` in the report become `"attribution"`.
2. Make `ReleaseIndex.target_tag` optional. `target_files` and `target_pipelines` are then empty,
   and `require_target()` is skipped.
3. Add an attribution branch to classification. There is no target, so there is no `match`,
   `mismatch_known`, `missing`, `removed_in_target` or `added_after_target`, and `position` is
   `null`. A file at a known path whose bytes match ≥1 release gets spans over **all** its matching
   releases, under the status name §7.11 decides. One whose bytes match none is `unknown_content`.
   `untracked`, `not_covered` and `unreadable` are unchanged.
4. Filesystem side: skip the "target paths the walk did not see → `missing`" loop in
   `check_filesystem()`. Absence has no meaning without a target.
5. Pipeline side: `check_pipeline_versions()` already probes every release-described pipeline. With
   no target, `expected_members` is empty and `in_target` should be `null`, not `false`. A pipeline
   that no release describes is still listed only.
6. `summary.attribution`: the distribution, computed by the rule §7.10 decides. The denominator must
   be stated in the report, not implied. Recommended: every classified entry except `untracked`,
   both sources together, and `unreadable` shown separately rather than hidden.
7. Update `blind_spots` (drop the "not built" line), `log_summary`, and the readme's statuses table.

**Test design — exit criterion "a mixed workspace produces a correct per-release breakdown".**

* Attribution mode is forced with `release_tag=none` (D17). The marker no longer needs removing by
  hand.
* Expected with the workspace as it is now (analytics at `v0.1.0-test`, one pipeline at
  `v0.3.0-test`): `code/fixture_stable.r` gets **one span of 4**, `v0.1.0-test..v0.3.0-test`.
  `v0.4.0-test` has no manifest, but it is the newest release, so it ends the list without breaking
  the span. `snt_dhis2_incidence/fixture_reverted.py` (content A) gets **two spans**,
  `v0.1.0-test..v0.2.0-test` and `v0.3.0-test`, broken by `v0.2.1-test`, which holds B.
  `code/snt_palettes.r` is `unknown_content`.
* The workspace is **not really mixed yet**. Everything is at `v0.1.0-test`, and the one pipeline
  labelled `v0.3.0-test` holds bytes identical to `v0.1.0-test`. Between `v0.1.0-test` and
  `v0.3.0-test` **no pipeline's zip content differs** except the fixture pipelines
  (`fixture_reverted.py` goes A → B → A). So mix it through the analytics: run the deployer at
  `v0.3.0-test` with pipeline deployment **off**. Expected afterwards:
  * `code/fixture_changing.r` (C) spans `v0.3.0-test` only;
  * `pipelines/snt_dhis2_incidence/utils/fixture_added.r` spans `v0.2.1-test..v0.3.0-test`;
  * `code/fixture_removed.r` remains, since the deployer removes nothing, and spans
    `v0.1.0-test..v0.2.0-test`;
  * the pipeline zips stay at `v0.1.0-test`.

  Alternatively, deploy just `snt_dhis2_incidence` at `v0.2.1-test` with `only_pipelines`, so its
  helper holds B and matches `v0.2.1-test` alone. That tag has never been used on that pipeline, so
  the `DUPLICATE_PIPELINE_VERSION_NAME` defect does not bite. **Either deployer run rewrites
  `.snt_release`**, which no longer matters with `release_tag=none` (D17).
* Add the attribution cases to the stub test first, and let it define the expected numbers before
  the sandbox run.
* **Historical.** This recipe used the deployer's `deploy_pipelines` and `only_pipelines` options.
  Both were removed on 2026-09-30 (D21). A mixed workspace is now made by deploying an older tag
  first, then a newer one, which also relabels unchanged pipelines (D22).

**Unchanged constraints to keep in mind:** the checker is read-only (§5.4); `status` values are
added, never renamed (§5.5); `schema_version` stays 1 until phase 4.

### 6.5 Phase 3, as verified

**Run 1**, 2026-09-29 (report `status_2026-09-29T13-18-04Z.json`). `snt-development-sandbox` after
`snt_workspace_deployer` at `v0.3.0-test` with pipeline deployment off, checked with
`release_tag=none`:

```
mode: attribution, resolved_from: parameter (marker v0.3.0-test present and ignored, as D17 intends)
164 attributed | 1 untracked   |   90 filesystem + 74 pipeline_version
incomplete: true — solely v0.4.0-test (no manifest), as designed
```

**Every per-file span is the one §6.4 predicts.** 157 files carry the single span
`v0.1.0-test..v0.3.0-test`. `fixture_changing.r` is `v0.3.0-test` alone, and `fixture_added.r` is
`v0.2.1-test..v0.3.0-test`. The leftover `fixture_removed.r` and the three files of the still-deployed
`snt_fixture_pipeline_removed` are `v0.1.0-test..v0.2.0-test`. `fixture_reverted.py` has two spans.
Every pipeline block has `in_target: null`. `snt_palettes.r` was restored by the deployer run, so the
run had no `unknown_content`. The stub test covers that case.

**The headline was wrong, and that revised D15.** Under the first D15 each release was scored out of
all 164 files. The best fit came out as **`v0.2.0-test` (162/164)** over `v0.3.0-test` (160/164),
for a workspace just upgraded to `v0.3.0-test`. The arithmetic was correct. The definition was not.
The four files holding `v0.3.0-test` down were leftovers of files it removed, which the deployer never
deletes and a pipeline cannot delete. So every upgrade would name an older release as the best fit,
and more so with each release that removes something. D15 was revised the same day: each release is
judged only on the paths it ships, with completeness beside agreement (§7.10).

**Run 2 — verified 2026-09-29** (report `status_2026-09-29T13-39-32Z.json`), same workspace,
revised checker, `release_tag=none`. Every number matches the prediction written beforehand.
Unchanged from run 1: 164 `attributed` and 1 `untracked`, no file matching no release, none
unreadable, and `incomplete` solely because of `v0.4.0-test`.

| Release | Agreement | Completeness | Extra |
|---|---|---|---|
| **`v0.3.0-test`, best fit** | 1.0 (160/160) | 0.9816 (160/163) | 4 |
| `v0.1.0-test`, `v0.2.0-test` | 0.9939 (162/163) | 1.0 | 1 |
| `v0.2.1-test` | 0.9875 (158/160) | 0.9816 | 4 |

The three files absent for `v0.3.0-test` are the never-deployed `snt_fixture_pipeline_added`. The
exit criterion is met: the mixed workspace gets a correct per-release breakdown, and the best fit is
the release it was actually upgraded to.

### 6.6 Phase 4, as verified

Done 2026-09-30. Three changes, one new file.

* **The schema** is [`docs/status_report.schema.json`](docs/status_report.schema.json) (JSON Schema
  2020-12), the authority for `schema_version: 1`. §5.5 now points to it and states the
  compatibility rules.
* **`checker_version` became `snt_workspace_check_version`** (D18). It was hardcoded to `null` and
  still is: a run cannot read the name of the pipeline version executing it. The key is frozen and
  nullable, so filling it later is not a breaking change.
* **`snt_workspace_checker/readme.md`** was checked against `pipeline.py`. Discrepancies found and
  fixed, so the record shows what the previous readme got wrong:
  * it said the run stops only when the target has no usable manifest; it also stops when the target
    is not among the published releases (`require_target`);
  * it said `files_scored` excludes only `untracked`; it also excludes `missing`;
  * it did not say that a `not_covered` file counts in `files_scored` and in `matches_no_release`
    (no release holds its path). Left as it is, and now documented;
  * it did not list the report's top-level keys, the `errors[].scope` values, or which strings are
    display-only.

**What was verified, and how.** `ruff check snt_workspace_checker/` is clean. The local stub test
(`ignore/SNT25-670/test_workspace_check_stub.py`, not committed) now builds a report with
`build_report()` in **each mode** (the verification one with an unreadable pipeline, so `unreadable`
and `errors` appear) and validates it against the schema, in memory and after a JSON round trip. It
also asserts the report's keys equal the schema's, in both directions, so a key added to the code
without the schema, or the reverse, fails. The saved sandbox report
`status_2026-09-29T13-39-32Z.json` (attribution, real workspace) validates once
`checker_version` is renamed. Deliberately corrupted copies (renamed status, old key, omitted key,
`schema_version: 2`, unknown `by_status` key) are rejected.

**Sandbox run, 2026-09-30.** The pipeline was pushed and run after the rename. Its report
(`status_2026-09-30T08-33-01Z.json`, attribution mode, 164 `attributed` + 1 `untracked`) validates
against the schema with no errors, and its top-level keys equal the schema's exactly, including
`snt_workspace_check_version: null`.

**Not verified.** No real *verification-mode* report has been
validated, only one built by the stub. Older saved reports predate the current shape and do not
validate, as expected.

### 6.7 Phase 5, step 1 — the deployer on its own (written 2026-09-30, **not yet tested**)

The deployer is treated as a standalone pipeline first; integration with the checker (before/after
reports, enriched `.snt_release`) is the next step and has not been started. Changes made, all in
`snt_workspace_deployer/pipeline.py` and its readme:

* **D21:** `sync_analytics`, `deploy_pipelines`, `only_pipelines` and `create_missing` removed. The
  release is always deployed whole. `api_connection` is therefore always required, dry run included.
* **D22:** `deploy_new_version()` compares the current version's contents with the release and
  either skips, relabels or registers, as tabulated in `release_strategy.md` (§ Workspace Deployer).
  An identical version *is* registered again when its name is not the tag; it is never registered
  when it already carries the tag.
* **D23 (2026-10-01):** `release_tag` is optional; empty deploys GitHub's latest release, and the
  resolved tag names the versions, the archive folder and `.snt_release`.
* **D24 (2026-10-01):** every failure is logged as an `[ERROR]` message to the run's Messages before
  the run stops (`abort_run()`, plus a catch-all in the pipeline function), and a GitHub 404 on the
  release is diagnosed into its cause.

**Verified so far:** `ruff check` and `ruff format --check` clean; `get_pipeline` parses the deployer
and yields exactly `github_repo, release_tag, api_connection, backup_existing, dry_run`; an offline
stub of `deploy_new_version()` (fake `upload_version`, fake `current_run`) passes seven cases: skip,
relabel, plain register, `+redeploy-` for a taken current name, fallback when an older version holds
the tag, dry run, no current version. That stub was thrown away; recreate it before extending.
**Verified in `snt-development-sandbox` on 2026-10-01 (D23, D24 only):** an empty tag resolves to the
latest release (`v0.4.0-test`), and its missing manifest now stops the run with an `[ERROR]` message in
Messages. **Not verified: the D21/D22 test plan below**, nor the other D24 diagnoses (no release, only
pre-releases, unknown tag, private repo).

**Test plan for `snt-development-sandbox`** (use tags never before deployed to these pipelines; run
by a human):

1. Fresh tag `A`. Everything deploys, missing pipelines are created.
2. Same tag `A` again. Every pipeline logs "already up to date, skipped"; the run ends without error.
   This is the case that failed on 2026-09-22.
3. Newer tag `B`. Unchanged pipelines are relabelled `B`, changed ones get a new version `B`. Then run
   the checker with `release_tag=none`: the best fit should be `B`, and pipeline version names should
   read `B` throughout.
4. Roll back to `A`. Pipelines where an older version holds `A` get `A+redeploy-<date>` and a warning.
   Low priority (§1.3): check it is safe — no error, nothing lost — not that the result is tidy.
5. Hand-edit one pipeline's code under the current tag and re-run that tag: expect a `+redeploy-` name
   and a warning.
6. Dry run of steps 3 and 5: log lines only, nothing registered.
7. Also untested from before: `backup_existing` against a workspace that already holds files.
8. D24 messages: a misspelled tag, and a public repository with no release. Each should end on an
   `[ERROR] Cannot deploy:` line in Messages naming the cause.

**Known consequences to check or decide:**

* The checker reads `+redeploy-` names as claiming no release (`null`). Decide whether to strip the
  suffix in `claimed_release_tag` (recommended, one line); if so, extend the stub test and
  `snt_workspace_checker/readme.md`, and re-validate a report against the schema.
* Version count grows by about one per unchanged pipeline per release. Watch for an OpenHEXA limit or
  a slow run (21 uploads instead of a few).
* `+` in a version name is accepted by OpenHEXA, on Giulia's word; confirm in the first test.
* Strays: see §7.5.

## 7. Open decisions

Blocking ones name the phase they block. None may be resolved by guessing.

### 7.1 ~~The manifest under-described what is deployed~~ — **closed**

Closed as phase 0. Kept as a numbered heading only so the §7.x references elsewhere still line up.
What the problem was, why a wider glob list was rejected, and the consumer it broke:
[`HISTORY.md`](HISTORY.md) §2.1–2.2. What the generator does now:
[`release_strategy.md`](release_strategy.md) § "Manifest generation".

Phase 1 was left blocked only by the token question in §7.3, which closed on 2026-09-22. **Phase 1
is unblocked and ready to start.**

### 7.2 ~~How to obtain every release's manifest~~ — **closed 2026-09-29**

**Answer: fetch them all, on every run. No cache, no index asset, no GitHub token.** The fear was
that ~20 releases would exhaust the unauthenticated 60 requests/hour
([`HISTORY.md`](HISTORY.md) §1). It does not hold:

* `GET /repos/<repo>/releases?per_page=100` is **one** API request for up to 100 releases.
* Manifests are downloaded from each asset's `browser_download_url` on github.com, and those
  downloads are **not charged per file**. Measured in the sandbox, 2026-09-29 14:14: after listing
  releases and downloading 4 manifests, `/rate_limit` reported **57** remaining. Listing costs at
  least 1 request, so the reading before the downloads was at most 59. The 4 downloads therefore cost
  at most 2 between them. Charged per file, the reading would have been 55 or lower.

So a check run costs about one API request whatever the release count, and the checker stays
credential-free (§7.3a). Both readings are logged on every run, so a change on GitHub's side would
show up in the logs. How the first measurement failed: [`HISTORY.md`](HISTORY.md) §1.

The one question left (whether to cache manifests anyway, against GitHub outages or bandwidth) is
not blocking, and is parked in §7.9.

### 7.3 Credentials in a country workspace — **low priority**, and no longer blocks the checker

Two questions, and the one that gated phase 1 is answered.

**7.3a — does *reading* a pipeline version need the `oh` token? No. Closed 2026-09-22.** A run's own
`HEXA_TOKEN` returns `currentVersion.zipfile` in full. The checker is therefore **credential-free**:
it can run unattended in a country workspace that holds no connection at all, which also keeps the
daily-check story in §7.6 alive. Evidence and method: [`HISTORY.md`](HISTORY.md) §2.4.

**7.3b — where the `oh` token comes from in a country workspace. Open, deliberately low priority.**
Only `snt_workspace_deployer` needs it. What it is, is now known: the **workspace access token** that
OpenHEXA shows under *Pipelines → Create → "From OpenHEXA CLI"*, the same string
`openhexa workspaces add <workspace>` asks for. It is workspace-scoped rather than personal, and the
UI reveals it only to members with the **Editor** or **Admin** role. Today it is copy-pasted by hand
into a CUSTOM connection named `oh` — which works, but means a manual step per country workspace and
no rotation story.

Deliberately **not** being solved now: the goal is a working checker and deployer to demonstrate, and
this is the kind of thing to take to the OpenHEXA devs once there is something to show. Revisit
before real production rollout, not before.

### 7.4 Enriching `.snt_release`

The marker should arguably record the repository, a timestamp, and whether the run completed
cleanly, so a checker can tell "deployed to T" from "attempted T, partially". Changing it means
changing `snt_workspace_deployer` and handling markers written by older versions.

### 7.5 ~~Pipelines removed in the target release~~ — **closed 2026-09-29**

**Report-and-leave**, confirmed by Giulia. A pipeline cannot delete another pipeline in OpenHEXA,
and agents are forbidden destructive actions anyway. The report says plainly that the pipeline is
not part of the target (`pipelines[].in_target: false`), and deleting it is a manual UI action.

Two consequences to keep in mind, **not to fix here** and worth raising with the OpenHEXA devs
(Giulia, 2026-09-30):

* **No "factory reset".** Because no pipeline can delete any other, the deployer cannot make a
  workspace hold exactly one release. It can only add and update.
* **Pipelines dropped by a release stay as strays.** When a new release no longer contains a pipeline,
  the workspace keeps it, at the code of whichever release last deployed it. Nothing in the deployer
  says so, and in the OpenHEXA pipeline list it looks like part of the current release. This is
  confusing for operators. Today only the checker surfaces it (`in_target: false`, or
  `in_any_release` for one no release describes). Files on the filesystem that a release drops behave
  the same way (§6.5, the `fixture_removed.r` leftovers).

The question for the OpenHEXA devs: could a pipeline, or a documented API call, delete or archive a
pipeline? Until then this is a permanent limit of the design, not a bug.

### 7.6 Scheduling

A daily unattended check was the original motivation for splitting check from fix. Whether SNT
pipelines can be scheduled in OpenHEXA in practice — and whether a daily run is wanted per
workspace — is unconfirmed. The repo's pipelines are all launched by hand today.

### 7.7 ~~R5~~ and the Template mechanism — **closed 2026-09-30 (D19, D20)**

`CLAUDE.md` **R5** ("always publish from `snt-development`") was written about *template*
publication. This work, `snt_workspace_deployer` above all, exists to **get rid of pipeline
templates altogether** (D20). The mechanism is still in place and must be understood, but nothing
new is built around it, and R5 is **not** being reworded for this work: it stays as it is for as
long as the templates it protects exist. Pushing a pipeline version into the workspace that runs it
creates no template and is a different operation; see `pipeline_deployment_mechanism.md`.

### 7.8 ~~How to represent attribution without flooding the report~~ — **closed 2026-09-29 (D14)**

The problem: most files do not change in most releases, so a naive `matching_releases` list would
make nearly every entry carry nearly every tag. The report would then grow with release history
rather than with what is wrong. Giulia's answers, 2026-09-29:

| Question | Answer |
|---|---|
| Per-file attribution on `match` entries? | **No.** In verification mode, `matching_releases` is carried only on entries that are not `match`, which bounds the representation to the files that are off. |
| Spans or the full list? | **Spans**: `{"from", "to", "count"}` in `published_at` order (§5.1.2). |
| A file matching one release? | **A span of one** (`from == to`, `count: 1`), so a consumer handles a single shape. |
| A release with no readable manifest inside a span? | **It breaks the span.** Nothing is known about its content, so it is not assumed to match. |

Built into the verification report straight away (§6.3). Still for phase 3: in **attribution mode**
there is no target and so no `match`, so every file carries spans. That is the mode's purpose, and
its distribution summary is what keeps the report readable at a glance.

### 7.9 Deferred to a later version

Country-specific variant override detection (D5), web app verification (D8), and report history
retention/pruning. Also parked until one of them blocks something (Giulia, 2026-09-29):

* **A manifest cache**, keyed by tag. Safe, because tags never move. It is no longer needed for the
  rate limit (§7.2); it would matter only for GitHub outages, bandwidth, or runs with no internet.
* ~~**The deployer's `DUPLICATE_PIPELINE_VERSION_NAME` defect** on re-deploy.~~ **Written 2026-09-30
  (D22), untested** — §6.7. No longer blocks phase 5 once the test plan passes.
* **Delete the deployer's legacy-manifest fallback** (§2.1, D26: decided 2026-10-01), in a PR of its own.
* **The 15 `not_in_repo` notebooks** from the D9 run, one real country workspace, 2026-09-29. Their
  names would show whether they are country variants, renamed notebooks or scratch work, which is
  input for D5. Not collected.

### 7.10 ~~What an attribution percentage means~~ — **closed 2026-09-29 (D15)**

**Answer: D, coverage per release plus a best fit**, as recommended below, **revised the same day
after sandbox run 1** (§6.5). Each release is judged **only on the paths it ships**:

| Field | Meaning |
|---|---|
| `present` | Files found here at paths the release ships. The pool is every entry for a present file except `untracked`, both sources together. |
| `agreeing` | Of those, how many have exactly the release's bytes. |
| `extra` | Files found here at paths the release does not ship. They count neither for nor against it. |
| `agreement` | `agreeing / present` |
| `completeness` | `present / shipped` |

Best fit: highest agreement, then highest completeness, then newest. Files matching no release and
unreadable files are also reported on their own.

**Why it was revised.** As first built, every release was scored over the whole pool, so a file at a
path the release does not ship counted as disagreement. The leftovers of removed files then dragged
the newest release down, and run 1 named `v0.2.0-test` as the best fit for a workspace just upgraded
to `v0.3.0-test`. Judging on shipped paths alone would let a near-empty workspace agree 100% with
everything, which is why completeness was added beside agreement. §4.1 has been reworded to match.
What was weighed originally:

Found while writing the phase-3 handover, 2026-09-29. §4.1 promised *"97% of files are v0.1.0-test,
2.7% are v0.2.1-test"*, figures that add up to 100%, so each file is counted once. But
attribution is a **set** (§5.1.2). `code/fixture_stable.r` matches all four releases with a
manifest, and most files in a real workspace will be like it. Which single release does such a file
count towards? The spec never says, and every answer changes what the headline number means:

| Option | A file matching v0.1–v0.3 counts as | Headline reads | Weakness |
|---|---|---|---|
| **A. Newest match** | v0.3 | "how far forward each file has got" | An unchanged file looks up to date, whatever else is in the workspace |
| **B. Oldest match** | v0.1 | "since when unchanged" | A fully up-to-date workspace reads as mostly v0.1 |
| **C. Split evenly** | ⅓ to each | adds to 100% | Hard to explain; says nothing actionable |
| **D. Coverage per release** | 1 to *every* release it matches | "98% of files are consistent with v0.2.1-test" | Does **not** add to 100%: each release gets its own score |

**Recommended: D, plus a best fit.** For each release, report the share of classified files whose
bytes match that release. The release with the highest share is the workspace's **best fit**, with
ties going to the newest. Reading one row of the table then says "if you called this workspace
v0.2.1, 98% of it would agree", which is the question an operator is actually asking. The
`unknown_content` share is reported beside it. This replaces the 100%-summing example in §4.1, so
the §4.1 wording has to change if D is chosen.

### 7.11 ~~Two small attribution-mode choices~~ — **closed 2026-09-29 (D16, D17)**

**Answers:** the status is **`attributed`** (D16), and attribution mode is forced with the reserved
value **`release_tag=none`** (D17), both as recommended below.

* **The status name for "matches ≥1 release".** §5.1 says attribution-mode files "resolve to
  `matching_releases`", but a status is a stable enum and needs a value. Recommended:
  **`attributed`**, a new value. None of the verification statuses fits, because each is defined
  relative to a target.
* **How to force attribution mode in a workspace that has a marker.** The §4.2 order is parameter,
  then marker, then attribution, so once `snt_workspace_deployer` has run, attribution mode is
  unreachable without deleting `.snt_release` by hand. Options: accept that, and test Run 3 by
  renaming the marker by hand; or add a reserved `release_tag` value such as `none`. Recommended:
  **a reserved value**. A daily attribution check must not require an operator to delete a file
  every time.

## 8. Decisions taken

Recorded so they are not re-litigated. Taken by Giulia in review, 2026-09-18 (D1–D9, D11, D12) and
2026-09-21 (D10), 2026-09-29 (D9 resolved, D13–D17) and 2026-09-30 (D18–D22).

| # | Decision |
|---|---|
| D1 | This spec covers the whole suite, phased: existing `snt_workspace_deployer`, new checker, web app deferred. |
| D2 | Check and fix are **separate pipelines**. The checker is read-only and schedulable. |
| D3 | Report is a workspace file, timestamped plus a stable `status_latest.json`. No dataset, no DB table. |
| D4 | Release ordering uses GitHub `published_at`. |
| D5 | Country-specific variant override detection is **out of scope for v1**. |
| D6 | Untracked files are **reported** in their own bucket, never acted on. |
| D7 | Both sources — filesystem and pipeline version zips — are read from v1. |
| D8 | Web apps are out of scope for v1, and named as a blind spot in the report. |
| D9 | Notebook hash normalisation: **decide after measuring** real drift in phase 2, not up front. **Decided 2026-09-29: do not normalise** — hash raw bytes. Measured on one real country workspace: of 31 notebooks present in both workspace and repo, 15 identical, 2 metadata-only, 0 outputs or formatting, 14 real source differences. Normalisation would change the verdict for 2 of 31, and would need a second hash definition kept in lock-step between generator and checker (§6.3). |
| D10 | The manifest gap is closed **before** the checker is built (phase 0). |
| D11 | Two modes, driven by whether a target release is given: attribution (factual inventory) and verification (qualitative verdicts). |
| D12 | The status formerly called `mismatch_unknown` is renamed **`unknown_content`**, to stop it reading as a synonym of `untracked`: one is about the bytes at a known path, the other about a path we never shipped (§5.1.1). |
| D13 | New status **`added_after_target`** (2026-09-29): a present path that only releases newer than the target ship. Without it, a workspace ahead of its target would read as `removed_in_target` (§5.1). |
| D14 | Attribution representation (2026-09-29, §7.8): spans `{from, to, count}` in `published_at` order; a single match is a span of one; any release outside the set breaks a span, including one whose manifest is unreadable; in verification mode, carried only on entries that are not `match`. |
| D15 | Attribution scores (2026-09-29, §7.10), **revised the same day after sandbox run 1** (§6.5). Each release is judged **only on the paths it ships**. **Agreement** = files here with exactly its bytes ÷ its files found here. **Completeness** = its files found here ÷ its files shipped. Files at paths it does not ship are **extra**, neither for nor against it. A file counts towards every release it matches, so nothing sums to 100%. Best fit = highest agreement, then completeness, then newest. The pool is every entry for a present file except `untracked`, both sources together, and the rule is stated in the report. Files matching no release and unreadable files are also shown on their own. *Superseded form:* one share per release over the whole pool, which counted leftovers of removed files against the release that removed them. |
| D16 | New status **`attributed`** (2026-09-29, §7.11): attribution mode's "known path, bytes match ≥1 release", with spans over all matching releases and `position: null`. |
| D17 | The reserved `release_tag` value **`none`** (any case) forces attribution mode even when `.snt_release` exists (2026-09-29, §7.11), so a scheduled attribution check never requires deleting the marker. |
| D18 | Phase 4 (2026-09-30): `schema_version: 1` is **frozen** as a JSON Schema in `docs/wip/docs/status_report.schema.json`, and **`docs/wip/` is treated as a future standalone repository**, so contracts live inside it. Free-text fields (`remediation`, `errors[].message`, `blind_spots[]`, `summary.attribution.rule`) are frozen as keys and types but **display-only**. `checker_version` is renamed **`snt_workspace_check_version`**: the version of the `snt_workspace_checker` pipeline that generated the report; documenting it is enough, and it is `null` today (§5.5, §6.6). |
| D19 | **No deployment workflow** (`push_snt_*.yaml`) for `snt_workspace_checker` or `snt_workspace_deployer` (2026-09-30). They are deployed by hand or by `snt_workspace_deployer` itself; nobody adds a workflow. Supersedes the open item in `pipeline_deployment_mechanism.md`. |
| D20 | **The pipeline Template mechanism is being retired** (2026-09-30). This work, especially `snt_workspace_deployer`, aims to remove templates altogether. It stays documented because it is still in place and affects `pipeline.py` delivery, but new work is not designed around it, and `CLAUDE.md` R5 is not reworded for it (§7.7). |
| D21 | **The deployer always deploys the whole release** (2026-09-30). The parameters `sync_analytics`, `deploy_pipelines`, `only_pipelines` and `create_missing` are removed: the point is that R and Python code move together on one tag, and a partial deploy invites the mixed workspaces this product exists to prevent. Recovery from a partial run is a plain re-run, which now skips what is done. `dry_run` and `backup_existing` stay. |
| D22 | **Version naming on deploy** (2026-09-30, §6.7). A pipeline whose current version has the release's exact files *and* the tag as its name is skipped (success). Identical files under another name are **registered again under the tag**, so the workspace reads as being at the release, at the cost of one extra version per unchanged pipeline per release (reverses an earlier decision not to redeploy identical versions). A taken tag gets `<tag>+redeploy-<YYYYMMDD>`. `+` in a version name is accepted by OpenHEXA. |
| D23 | **An empty `release_tag` deploys the latest release** (2026-10-01, §6.7). The deployer resolves it through GitHub's `/releases/latest`, i.e. the newest published release that is neither a draft nor a pre-release, so a pre-release must be typed. The resolved tag, never "latest", names the pipeline versions, `archive/<tag>/` and `.snt_release`, so a workspace always records which release it actually got. |
| D24 | **Every deployer failure reaches the run's Messages** (2026-10-01, §6.7). A raised exception alone appears only in the logs, so each anticipated failure is logged as `[ERROR] Cannot deploy: <reason and fix>` before raising, and anything else is logged by a catch-all in the pipeline function. |
| D25 | **One workspace per release is the expected usage** (2026-10-01, §1.3). A country keeps a main workspace on the latest release, updated forward, and uses a dedicated workspace to run an older release. Rolling one workspace back and forth only has to be safe, not clean; do not polish it before there is feedback from real users. |
| D26 | **The release manifest is a cross-repo contract, frozen as a JSON Schema** (2026-10-01, §2.1) in `docs/wip/docs/release_manifest.schema.json`. It is derived from the generator, `split_manifest()`/`download_manifest()` in the deployer and `load_manifests()`/`build_index()` in the checker, not from prose. It is repository-agnostic (no `pipelines/` or `code/` paths), because the bootstrap's other release sources will publish the same shape. Strict for the producer (`additionalProperties: false`), tolerant for consumers, which ignore unknown keys. `pipelines` is required, so pre-phase-0 manifests do not validate. **They are not supported** (decided 2026-10-01): they only ever existed during development, so the deployer's fallback for them in `split_manifest()` is to be deleted in a PR of its own (§2.1). Two invariants JSON Schema cannot express (every `<dir>/<zip_file>` is a key of `files`; every `zip_files` holds `pipeline.py`) are stated in the schema's description and must be checked by a validator separately. |
