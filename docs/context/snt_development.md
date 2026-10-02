# `snt_development` — what these tools depend on

[`BLSQ/snt_development`](https://github.com/BLSQ/snt_development) is the repository whose releases
the deployer installs and the checker verifies. This file holds only the facts about it that the
tools here rely on. Everything else (country data rules, naming, schemas, lineage, the domain) stays
in that repo; its [`CLAUDE.md`](https://github.com/BLSQ/snt_development/blob/main/CLAUDE.md) and
[`docs/DATA_ARCHITECTURE.md`](https://github.com/BLSQ/snt_development/blob/main/docs/DATA_ARCHITECTURE.md)
are the authority. Condensed from both on 2026-10-01 (branch `SNT25-670` @ `d9acafc`).

## What it is

About 20 standalone OpenHEXA pipelines that turn DHIS2 routine data plus external sources into a
one-row-per-ADM2 malaria subnational tailoring table. Each pipeline is a top-level directory
`<name>/` holding `pipeline.py` (orchestration only, `snt_development` R3), `requirements.txt` and
`readme.md`. The analytics are **R notebooks run by papermill**, under
`pipelines/<name>/{code,reporting}/*.ipynb`, with helpers in `pipelines/<name>/utils/*.r` and a shared
R library in `code/*.r`. Pipelines are launched by hand and pass data through OpenHEXA datasets.

## The two delivery routes — the product's why

OpenHEXA supports Python pipelines but not R, so the two halves of one pipeline reach a workspace by
different routes, on different cadences (`snt_development` CLAUDE.md rule 2, DATA_ARCHITECTURE §2.1–§2.2.1):

| Half | Route today | Trigger |
|---|---|---|
| `pipeline.py`, `requirements.txt` | CI (`push_snt_*.yaml`) pushes to the `snt-development` workspace, publishing a new **template** version; subscribed country workspaces auto-update | merge to `main` |
| Notebooks, `.r` helpers | `pull_scripts_from_repository()` (in `snt_lib`) fetches them from the repo at run time | an operator runs the pipeline with **`Pull scripts` = ON** |

So a workspace can run today's `pipeline.py` against months-old R analytics, and nothing reports the
mismatch. A notebook-only PR triggers no CI at all. The deployer exists to replace both routes with one
release tag ([`../release_strategy.md`](../release_strategy.md)).

## The Template mechanism and R5 — still live, being retired

All 20 deployment workflows push to the one workspace `snt-development`. Pushing there publishes a new
version of the existing SNT **template pipeline**; pushing the same pipeline from any other workspace
creates a separate, competing template nobody subscribes to. Hence `snt_development` **R5**: publish
only from `snt-development`, never edit `workspace:` in a `push_snt_*.yaml`.

Each workflow runs `openhexa pipelines push <dir> --code "<slug>" --description "<commit message>"
--link "<commit URL>"`, where the slug is the directory name with `_` → `-`. That is the same pipeline
code the manifest's `pipelines` block carries.

This work aims to retire templates altogether (D20). Until then the mechanism stays in place, R5
stays as written, and the deployer's direct push into the workspace that runs a pipeline is a different
operation that creates no template ([`../openhexa_deployment.md`](../openhexa_deployment.md) §7.7).

## Country-specific notebook variants

A notebook named `<generic>_<CC>.ipynb` beside its generic one runs **instead of** it, but only in the
workspace whose `SNT_CONFIG.COUNTRY_CODE` is `CC`. `pipeline.py` names only the generic notebook and
passes `country_code=`; the swap happens inside `snt_lib`. Variants are deliberately left out of
`pull_scripts_from_repository(code_scripts=[...])`, so `Pull scripts` neither overwrites nor delivers
them: they are created and edited in the workspace, and a committed copy is archival only. Variants
parked in a `country_specific/` folder are on no execution path.

Consequence here: the checker reports a variant in a workspace as `untracked` (or, if a copy was
committed and has drifted, `unknown_content`). Recognising variants as deliberate overrides is out of
scope for v1 (D5), and until it is done a workspace with one looks alarming.

## Where the manifest is generated, and which paths it tracks

`.github/workflows/generate_manifest.yaml` lives in `snt_development` and stays there (M3): it runs on
`release: published` (and `workflow_dispatch`) and attaches `release_manifest.json` to the release.
It is also called as a reusable workflow (`workflow_call`) by this repository's own
`generate_manifest.yaml`, so a change to it on `main` reaches both repositories (D27).
Its shape is the contract in [`../contracts/release_manifest.schema.json`](../contracts/release_manifest.schema.json);
how it is generated is in [`../contracts/release_manifest.md`](../contracts/release_manifest.md).
What `snt_development` ships, which the schema deliberately does not fix:

* **Filesystem half:** `pipelines/**/code/*.ipynb`, `pipelines/**/reporting/*.ipynb`,
  `pipelines/**/utils/*.r`, `code/**/*.r`.
* **Deployment half:** every top-level directory holding a `pipeline.py`, walked recursively for the
  SDK's zip suffixes (`.py .ipynb .txt .md .r .sql`), minus `workspace/`. `dev/` and `deprecated/` fall
  out without being named.
* **Not shipped:** `data/` (bootstrapped by the pipelines), `configuration/` (managed by the
  SNT_config editor web app), the web apps themselves (D8).

As of 2026-10-01 the workflow sits on branch `SNT25-670` only; Session 4 of the move merges it to
`main`, so links to it on `main` resolve after that.

## The sandbox — two things with one name

* `BLSQ/snt_development_sandbox` — a **GitHub repo**, independent (not a fork), seeded from
  `snt_development` @ `551ddd8` minus the 20 `push_snt_*.yaml`. Fixture releases are cut from it.
* `snt-development-sandbox` — the **OpenHEXA workspace** the deployer and checker run in.

Both pipelines still default `github_repo` to the sandbox repo. Details: [`../sandbox.md`](../sandbox.md).

## Dependencies outside either repo

* **`openhexa-sdk`.** The deployer leans on it heavily: `get_pipeline()` parses a pipeline's
  parameters by AST without importing it, and the manifest generator mirrors its
  `generate_zip_file()` rule. A change to the SDK's zip suffixes silently stales the generator; the
  checker's `not_covered` status is the tripwire.
* **`openhexa.toolbox` and `snt_lib`** (`BLSQ/snt_utils`). Every `snt_development` `requirements.txt`
  installs both from a **moving branch**, unpinned: `openhexa.toolbox @ ...@main` and `snt_lib` with no
  ref at all. The installed commit is never recorded, so redeploying an unchanged `pipeline.py` can
  change behaviour. The deployer cannot pin what a release's pipelines install.
* **The runtime image** `blsq/openhexa-blsq-r-environment:latest` (DATA_ARCHITECTURE §7.4): R 4.5,
  Python 3.13, `openhexa.sdk=2.22.6`, `openhexa.toolbox=2.11.3` as read on 2026-08-27. A notebook run
  interactively sees the image's pinned toolbox; a pipeline run sees whatever `@main` was at deploy time.

## Conventions these tools inherit

* Pipeline README structure: link to `snt_development`'s
  [`docs/PIPELINE_README_STANDARD.md`](https://github.com/BLSQ/snt_development/blob/main/docs/PIPELINE_README_STANDARD.md)
  rather than copying it.
* Rule IDs cited here as "`snt_development` R3/R5" are that repo's register, not this one's.
