# Workspace Checker — spec and state

> `snt_workspace_checker`: statuses, modes, attribution, the report contract, build phases and the
> open decisions about it. Section numbers are those of the former `PRODUCT_SPEC.md`; a § that is not
> here is linked to the file that holds it. The report's authority is
> [`contracts/status_report.schema.json`](contracts/status_report.schema.json).

## Verification pipeline — phase 1 done

An OpenHEXA pipeline that hashes what is actually in a workspace, compares it against the release
manifests, and reports per file which release it matches, or that it matches none.

[`snt_workspace_checker/`](../snt_workspace_checker/) holds the phase-1 build: verification against a
single target release, both sources hashed, the four statuses `match` / `unknown_content` /
`missing` / `unreadable`, and the report written to `snt_status/`. It is credential-free. Phase 1
iterated over the target manifest only. **Phase 2 (built and verified in the sandbox 2026-09-29)**
loads every release's manifest and walks the filesystem, which gives the full status taxonomy. What
changed and why: §6.3. **Phase 3 (attribution mode, with
per-release agreement and completeness in both modes) was built and verified in the sandbox on
2026-09-29.** See §6.4–§6.5 there. Phase 4 (schema freeze) is next.

**Verified 2026-09-22** in `snt-development-sandbox`, deployed at `v0.1.0-test` and checked against
it: **163/163 `match`**, 89 filesystem + 74 pipeline-version entries, `incomplete: false`, no errors.
The run also caught a real defect in the checker itself — see
§6.2 for what it established and
[`HISTORY.md`](HISTORY.md) §1 for the version-name gotcha behind it.

Its requirements, statuses, report contract and build phases are below. Design inputs settled here:

* **Two sources to hash** — the filesystem for R analytics, and each pipeline version's stored zip
  for the Python half (readable via `get_pipeline`, which returns full file contents).
* `.snt_release` tells the verifier which manifest to fetch, without being told.
* Country-specific variants should eventually be recognised as deliberate overrides rather than
  drift — deferred past v1 (decision D5).

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

## 2. Vocabulary

| Term | Meaning |
|---|---|
| **Release tag** | A GitHub release tag, e.g. `v1.2.0`. The single source of truth for "what version is this workspace on". Tags are protected and never moved (`release_strategy.md` §"Release tags are never moved"). |
| **Release manifest** | `release_manifest.json`, attached as an asset to each release: `{version, files: {path: sha256}, pipelines: {dir: {code, zip_files}}}`. `files` is flat and covers both sources; the `pipelines` block says which of those paths ship inside which pipeline zip, and under what name once inside it ([`release_manifest.md`](contracts/release_manifest.md) §2.1). |
| **Tracked file** | A path present in some release manifest. |
| **Target release** | The release the workspace is being compared *against*, when one is given. |
| **Declared release** | What `.snt_release` says the workspace was last deployed to. Intent, not verified fact ([`snt_release_marker.md`](contracts/snt_release_marker.md) §3.2). |
| **Attribution** | The set of releases whose manifest contains a file's observed hash. |
| **Drift** | An observed hash that matches no manifest of any release. |

## 3. What the checker observes

### 3.1 Two sources of truth, hashed separately

Every entry in the report carries which source it came from.

| Source | What is hashed | Notes |
|---|---|---|
| `filesystem` | Files under `workspace.files_path` at their repository-relative paths | Where the R analytics live |
| `pipeline_version` | The files inside each OpenHEXA pipeline's **current registered version zip** | `pipeline.py` is *never* on the filesystem — a copy there is inert and misleading (`openhexa_deployment.md`) |

Both are read from v1 (decision D7). The zip is readable through the GraphQL field
`pipelineByCode.currentVersion.zipfile`, which returns the base64 of the whole archive — the same
read the SDK's `download_pipeline_sourcecode()` performs.

**Verified 2026-09-22:** a run's own `HEXA_TOKEN` reads that field in full, so the checker needs
**no workspace credential**. Reading and deploying have different credential requirements, and only
deploying needs the `oh` connection ([`openhexa_deployment.md`](openhexa_deployment.md) §7.3, [`HISTORY.md`](HISTORY.md) §2.4).

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

### 4.3 Which releases count (D27)

Both modes use every published **full** release. Drafts are skipped, and so are GitHub
pre-releases, **unless one is the target**. Pre-releases are cut from feature branches for
development and testing ([`release_strategy.md`](release_strategy.md), staging pre-releases) and are of
no concern to a country workspace. Left in, they would sit in the `published_at` order (D4) between
real releases, break attribution spans (D14) and shift `ahead` / `behind`. They are absent from the
whole report, `releases_considered` and `by_release` included; the run log names the ones left out.

A pre-release given as the target, by parameter or `.snt_release`, is kept and placed by its
`published_at` like any release. Other pre-releases stay out even then, so behind and ahead are
measured against full releases only. In attribution mode none is kept: a file whose bytes only a
pre-release ships then reads `unknown_content` (known path) or `untracked` (new path). There is no parameter to include them; give
the tag instead.

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
[`status_report.schema.json`](contracts/status_report.schema.json), not this section. This section
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
| **0** | ✅ **DONE** — manifest gap closed, sandbox reset, fixture releases cut ([`sandbox.md`](sandbox.md) §6.1). Details: [`HISTORY.md`](HISTORY.md) §2.1, §4. | — | — |
| **1** | ✅ **DONE** 2026-09-22 — checker skeleton in [`snt_workspace_checker/`](../snt_workspace_checker/): verification mode against a single target release, both sources hashed, four statuses, report written (§6.2). | Met: `snt-development-sandbox` at `v0.1.0-test` reported **163/163 `match`**. | — |
| **2** | ✅ **DONE** 2026-09-29 — taxonomy verified in the sandbox, D9 decided (§6.3). Full taxonomy: `removed_in_target`, `untracked`, `not_covered`, the pipeline-directory case. Plus **measure notebook drift** on a real workspace and decide D9. | A workspace at T-1 with one hand-edited file reports exactly the expected mix. | 1 |
| **3** | ✅ **DONE** 2026-09-29 — attribution mode, per-release agreement and completeness in both modes, D15–D17 (§6.4, §6.5). | Met: the mixed sandbox workspace scores `v0.3.0-test` as best fit, agreement 1.0, completeness 0.9816. | — |
| **4** | ✅ **DONE** 2026-09-30 — `schema_version: 1` frozen as [`status_report.schema.json`](contracts/status_report.schema.json); `snt_workspace_checker/readme.md` re-verified against the code (§6.6, D18). | Met: schema documented and validated against reports the code builds; readme verified by reading, discrepancies listed in §6.6. | — |
| **5** | `snt_workspace_deployer` integration: report before and after a fix; enrich `.snt_release` ([`snt_release_marker.md`](contracts/snt_release_marker.md) §7.4). **In progress**: step 1, making the deployer solid on its own (re-deploy defect, whole-release-only), is written and untested ([`deployer.md`](deployer.md) §6.7). Integration with the checker comes after. | A fix run links to the before/after reports it produced. | 4 |
| **6** | Web app. | Out of scope for this spec. | 5 |

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

**D9 decided 2026-09-29: no normalisation** ([`decisions.md`](decisions.md) §8), from the measurement below.

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
  the target is found. It gets `in_target: false`, report-and-leave ([`deployer.md`](deployer.md) §7.5). The workspace pipeline
  list is read only to name pipelines no release describes, with
  `pipelines(...) { totalPages items { code currentVersion { versionName } } }` (verified above).
* **`version_name_matches_content` now judges any release name**, not only the target's. A zip must
  hold exactly that release's files for the pipeline; an extra file is a disagreement.
* `files_in_zip_not_in_manifest` was removed from the pipeline block. Those files are now per-file
  entries.

**D9 measurement tool:** `tools/d9_notebook_drift.py` (standard library). It
compares a downloaded copy of a real workspace's `pipelines/` folder against the repo and puts each
notebook at the first level where the two become equal: `identical`, `formatting`, `outputs`,
`metadata` or `source`.

**Local stub test:** `tests/test_checker_stub.py`. It imports the checker with
`current_run` stubbed and asserts every status, position and span case against fake manifests shaped
like the [`sandbox.md`](sandbox.md) §6.1 fixtures, including `unordered`, which no fixture can reach ([`sandbox.md`](sandbox.md) §6.1). Run it with
`pytest tests/` (or `python tests/test_checker_stub.py`). It is the
closest thing this work has to a test suite. Extend it rather than starting a new one.

### 6.4 Phase 3 — handover

Written 2026-09-29 for the session that builds phase 3.

> **Status, 2026-09-29 (later the same day):** §7.10 and §7.11 decided (D15–D17). Items 1–7 below
> are **built**. The stub test (`tests/test_checker_stub.py`) asserts the whole
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

* **The schema** is [`status_report.schema.json`](contracts/status_report.schema.json) (JSON Schema
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
(`tests/test_checker_stub.py`) now builds a report with
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

## 7. Open decisions

Blocking ones name the phase they block. None may be resolved by guessing.

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
credential-free ([`openhexa_deployment.md`](openhexa_deployment.md) §7.3a). Both readings are logged on every run, so a change on GitHub's side would
show up in the logs. How the first measurement failed: [`HISTORY.md`](HISTORY.md) §1.

The one question left (whether to cache manifests anyway, against GitHub outages or bandwidth) is
not blocking, and is parked in §7.9.

### 7.6 Scheduling

A daily unattended check was the original motivation for splitting check from fix. Whether SNT
pipelines can be scheduled in OpenHEXA in practice — and whether a daily run is wanted per
workspace — is unconfirmed. The repo's pipelines are all launched by hand today.

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
  (D22), untested** — [`deployer.md`](deployer.md) §6.7. No longer blocks phase 5 once the test plan passes.
* ~~**Delete the deployer's legacy-manifest fallback**~~ **Deleted 2026-10-02** ([`release_manifest.md`](contracts/release_manifest.md) §2.1, D26).
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
