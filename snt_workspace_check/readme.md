# SNT Workspace Check Pipeline

Reports what is actually deployed in this OpenHEXA workspace, compared against the GitHub releases
of the SNT codebase. It is **read-only**: it writes a status report and changes nothing else.
Installing and updating is `snt_workspace_manager`'s job. The report is a JSON file on the
workspace filesystem under **`snt_status/`**, not an OpenHEXA dataset (decision D3).

It runs in one of two modes (decision D11):

* **Verification**: a target release is given. Every file gets a verdict relative to it (up to
  date, behind, ahead, edited, missing…), judged against every other release too.
* **Attribution**: no target. Every file is attributed to the releases whose bytes it holds, with no
  verdicts. Each release gets two scores (decision D15). **Agreement** answers "of the files v0.3.0
  ships that are here, 100% have its exact bytes". **Completeness** answers "98.2% of v0.3.0's files
  are here".

Both modes report the per-release scores.

> **Phase 4 of [`docs/wip/PRODUCT_SPEC.md`](../docs/wip/PRODUCT_SPEC.md) §6.** The report shape is
> **frozen at `schema_version: 1`**. The contract is the JSON Schema
> [`docs/wip/docs/status_report.schema.json`](../docs/wip/docs/status_report.schema.json); this
> readme explains it, and the schema wins if the two disagree.

## Parameters

* **`github_repo`** (String, Required):
  * **Name:** GitHub repository
  * **Description:** The `owner/repo` whose releases this workspace is checked against. A parameter
    while the product is in testing; it will be hard-coded before production, so a user cannot point
    the checker at an arbitrary repository (`PRODUCT_SPEC.md` §5.4).
  * **Default:** `BLSQ/snt_development_sandbox`.
* **`release_tag`** (String, Optional):
  * **Name:** Target release tag
  * **Description:** The release to check this workspace against, e.g. `v0.2.1-test`. Left empty, the
    pipeline falls back to the tag recorded in `.snt_release` by the last `snt_workspace_manager`
    run. With neither, it runs in **attribution mode**. The reserved value **`none`** (any case)
    forces attribution mode even when `.snt_release` exists (decision D17), so a scheduled
    attribution check never needs the marker deleted. A release literally tagged `none` could
    therefore never be targeted.
  * **Default:** none.

This pipeline takes **no credential parameter and needs no connection.** A run's own `HEXA_TOKEN`
reads pipeline version contents in full ([`HISTORY.md`](../docs/wip/HISTORY.md) §2.4), so it can run
unattended in a country workspace that holds no connection at all.

## Functionality Overview

1. Read `.snt_release` at the workspace root, if present, for the **declared** release. Its absence
   is the normal starting state for every country workspace today and is never an error.
2. Resolve the **target** release: `release_tag` = `none` means no target; otherwise the parameter,
   else the declared release, else no target. No target means attribution mode. The report records
   the outcome under `target_release.resolved_from`: `parameter`, `marker` or `nothing_given`.
3. **List every published release** of the repository (one GitHub API request per 100 releases) and
   download each one's **`release_manifest.json`** asset. A release whose manifest is absent, cannot
   be downloaded, or has no `pipelines` block cannot be used for comparison. It is listed with
   `manifest_available: false`, added to `errors`, and the report is marked `incomplete`. The run
   stops (raises) only if the **target** is not among the published releases or has no usable
   manifest.
4. **Filesystem source.** Walk `workspace.files_path`, minus the exclusions below. A file at a path
   no release has ever shipped is `untracked` and is **not read**. A file at a known path is hashed
   and classified. A file inside a pipeline directory is listed in `inert_filesystem_copies` and
   not classified. In verification mode, every filesystem path the target ships that the walk did
   not find is then checked directly, and reported `missing` if it is absent. In attribution mode
   absence means nothing, so this step is skipped.
5. **Pipeline-version source.** For each pipeline directory described by **any** release, read that
   pipeline's **current registered version** through the OpenHEXA API, decode the stored zip, and
   classify every file inside it, plus every file the target expects there. Older versions are not
   read, because they are not what would run. A pipeline the target ships but the workspace has not
   deployed yields `missing` for all its files. An API failure yields `unreadable` for that pipeline
   and does not stop the others.
6. **List the workspace's pipelines** to name any that no release describes. Their contents are not
   read.
7. Score every release on agreement and completeness, judged only on the paths it ships
   (`summary.attribution`).
8. Write the report twice, and log a summary: the scores per release and the best fit, one line per
   actionable file, and a count plus sample for untracked filesystem files.

Each tracked path is read from **exactly one** source. Anything inside a pipeline directory comes
from the version zip. A copy of the same file on the workspace filesystem is **not** used as
evidence. OpenHEXA runs pipelines from the registered version and never from the bucket, so such a
copy is inert. It is listed, but it cannot make a file look fine.

### Excluded from the filesystem walk

`archive/`, `data/`, `configuration/` and `snt_status/` at the root; `papermill_outputs/` and any
dot-directory (`.ipynb_checkpoints/`, `.git/`, …) at any depth; `reporting/outputs/`; and the
`.snt_release` marker. The report repeats this list under `scan_exclusions`.

### Statuses

Every entry has exactly one status. A **path** question comes before a **content** question: a path
no release has ever shipped is `untracked`, and its bytes are never compared.

| Status | Meaning | Mode |
|---|---|---|
| `match` | Hash equals the target manifest's. | verification |
| `mismatch_known` | The target ships this path. The hash differs from the target's but equals another release's (see `matching_releases`, `position`). | verification |
| `attributed` | The path is tracked and the bytes match at least one release. `matching_releases` lists **all** of them, as spans (decision D16). | attribution |
| `unknown_content` | A tracked path whose bytes match **no** release. Edited in place, or corrupt. | both |
| `missing` | In the target; absent from its source. | verification |
| `removed_in_target` | An older release shipped this path; the target does not. | verification |
| `added_after_target` | Only releases **newer** than the target ship this path. The workspace is ahead here. | verification |
| `untracked` | No release has ever shipped this path. On the filesystem: reported only. In a pipeline zip (`source: pipeline_version`): the §5.3 sharper case, because the file **is deployed code**. | both |
| `not_covered` | In a pipeline zip, at a path of a type the manifest generator does not hash. It should never appear. It is the alarm that the generator has fallen behind the OpenHEXA SDK's zip rule. | both |
| `unreadable` | Present but could not be hashed (permissions, I/O, API error). Always sets `incomplete: true`. | both |

`position`, on verification-mode entries that carry `matching_releases`, places those releases
relative to the target by GitHub `published_at` (decision D4). In attribution mode it is `null`.

| `position` | Meaning |
|---|---|
| `older` | Every matching release is older than the target. The file is **behind**. |
| `newer` | Every matching release is newer. The file is **ahead**. |
| `both` | Matches on both sides, none in the target: the content was changed in the target and later reverted. |
| `unordered` | A matching release has no usable timestamp, or one identical to the target's. |

## Inputs

| Input | Source | Required |
|---|---|---|
| Release list | GitHub API, `repos/<repo>/releases` | Yes |
| `release_manifest.json` of every release | GitHub release assets, via `browser_download_url` | The target's, yes. Others: each missing one makes the report `incomplete` |
| `.snt_release` | Workspace root, written by `snt_workspace_manager` | No. Only used when `release_tag` is empty |
| Workspace files | Workspace filesystem | No. In verification mode, absence is the `missing` finding |
| Current pipeline version zips, and the pipeline list | OpenHEXA GraphQL API, with the run's own token | No. In verification mode, absence is the `missing` finding; failure is an `errors` entry |

This pipeline does **not** read `configuration/SNT_config.json` and takes no country code. It checks
code, not data, so nothing about it is country-specific.

## Outputs

Written to the **workspace filesystem** (no dataset, no database table):

* **`snt_status/status_<UTC timestamp>.json`**: the run's report, kept as history. The colons of the
  time are written as `-` in the filename (`status_2026-09-29T13-39-32Z.json`); `generated_at`
  inside keeps the ISO form.
* **`snt_status/status_latest.json`**: a byte-identical copy at a stable path. This is the one the
  future status web app reads.

Nothing is published to an OpenHEXA dataset.

**The report's top-level keys** (all always present; types, enums and nullability are in the
[schema](../docs/wip/docs/status_report.schema.json)): `schema_version`,
`snt_workspace_check_version`, `generated_at`, `workspace`, `repo`, `mode`, `target_release`,
`declared_release`, `releases_considered`, `incomplete`, `summary` (`by_status`, `attribution`),
`pipelines`, `entries`, `inert_filesystem_copies`, `scan_exclusions`, `errors`, `blind_spots`.

> **Notes for the Data Analyst:**
>
> - **`status`**, **`position`**, **`source`** and **`target_release.resolved_from`** are stable
>   enums. A value may be added but is never renamed, so a consumer can switch on them. A consumer
>   should also tolerate a value it does not know yet.
> - **Display-only text.** `remediation`, `errors[].message`, `blind_spots[]` and
>   `summary.attribution.rule` are for a human. Never parse or compare them; their wording can
>   change without a version bump. `remediation`'s wording depends on the source as well as the
>   status: an `untracked` file in a zip is deployed code, one on the filesystem is a stray.
> - **`snt_workspace_check_version`** is meant to record which version of this pipeline generated
>   the report. It is **`null` today**: a run cannot read the name of the pipeline version executing
>   it, and no source for the value has been found. The key is frozen and nullable.
> - **`errors[].scope`** is `manifest:<tag>`, `pipeline_version:<code>` or `pipeline_list`.
> - **`matching_releases`**: the releases whose manifest holds exactly the observed bytes at that
>   path, collapsed into spans in `published_at` order, e.g.
>   `[{"from": "v0.1.0-test", "to": "v0.2.0-test", "count": 2}]`. In verification mode the target
>   is left out and the field is `null` on `match` entries (decision D14). In attribution mode it
>   lists **every** matching release. A single release is a span of one. Any release outside the set
>   breaks a span, including the target and a release with no readable manifest. **Two spans** for
>   one file means its content was changed and later reverted.
> - **`summary.attribution`**, in both modes (decision D15). Each release is judged **only on the
>   paths it ships**. Per release, `by_release` gives:
>   * `shipped`: the number of files the release ships.
>   * `present`: how many of those paths are found here.
>   * `agreeing`: how many of those have exactly the release's bytes.
>   * `extra`: files found here at paths the release does not ship. Typically these are leftovers of
>     files it removed. They count neither for nor against it.
>   * **`agreement`** = `agreeing / present`: "apart from files it has no opinion about, how far does
>     this workspace agree with the release?"
>   * **`completeness`** = `present / shipped`: "how much of the release is here at all?"
>
>   A file unchanged across releases agrees with each of them, so no column adds up to 1. `best_fit`
>   is the highest agreement, then the highest completeness, then the newest release. `files_scored`
>   counts every entry for a file present in the workspace, both sources together, except
>   `untracked` (no release has an opinion about the path) and `missing` (no bytes). A `not_covered`
>   file therefore counts, and counts as matching no release. `rule` restates all of this. Files matching no release (`matches_no_release`) and files that could not
>   be read (`unreadable`) are also reported on their own. An unreadable file is present but never
>   agrees. A release with no usable manifest has `null` in every count.
> - **`incomplete`**: `true` means at least one source or release manifest could not be read. The
>   report is then a partial account, not a pass.
> - **`mode`** is `verification` or `attribution`. **`target_release`** is always an object: in
>   attribution mode its `tag` and `published_at` are `null`, and `resolved_from` says why, either
>   `parameter` (`release_tag` was `none`) or `nothing_given`.
> - **`declared_release`** vs **`target_release`**: the declared one is what `.snt_release` says was
>   last deployed. It is written even after a partial run, so it records **intent, not verified
>   fact**. When the two disagree, the run logs a warning and checks against the target.
> - **`pipelines[]`** has one block per pipeline that any release describes and that is either
>   deployed or in the target, plus one per deployed pipeline that no release describes
>   (`in_any_release: false`, contents not read). `in_target: false` on a deployed pipeline means it
>   is **not part of the target release and is left in place**. A pipeline cannot delete another
>   pipeline in OpenHEXA (`PRODUCT_SPEC.md` §7.5). In attribution mode `in_target` is `null`, because
>   there is no target to be part of.
> - **`pipelines[].version_name_matches_content`**: a version's *name* is free text somebody typed;
>   its hash is evidence. It is `true` when the zip holds exactly the files, byte for byte, that the
>   named release ships for that pipeline, and `false` otherwise. That includes a zip with an extra
>   file, or a name that points at a release that does not ship the pipeline. It is `null` when the
>   name is not a release tag with a usable manifest (e.g. `v3` from a plain CLI push).
> - **`current_version_name`** is the raw API value and **`current_version_claims_tag`** is the
>   release tag parsed out of it. They differ because OpenHEXA appends the version number:
>   `v0.1.0-test` reads back as `v0.1.0-test [v1]`.
> - **`inert_filesystem_copies`**: files on the filesystem inside a pipeline directory. They never
>   execute and are not classified.
> - Every field that could be absent is present as `null` rather than omitted.
