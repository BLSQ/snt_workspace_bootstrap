# Deploying an OpenHEXA pipeline from inside a pipeline run

Companion to [`release_strategy.md`](release_strategy.md), which explains *why* the Workspace
Deployer deploys `pipeline.py` instead of copying it. This document is the *how*.

> Implemented in [`snt_workspace_deployer/`](../snt_workspace_deployer/), running in the
> `snt-development-sandbox` workspace. Verified end to end for 2 of 20 pipelines.
>
> This document describes the **current** mechanism. Past verifications against deleted fixtures,
> dead ends and closed problems are in [`HISTORY.md`](HISTORY.md).

## Why a file copy is not a deployment

Writing `snt_dhis2_extract/pipeline.py` into `workspace.files_path` produces a *file*, not a
runnable pipeline. OpenHEXA never reads pipelines from the filesystem at run time: each pipeline is
a registered object in the OpenHEXA database with versions, and **each version stores its own
zipped copy of the code** (`type: "zipFile"`). The runner downloads that zip — you can see
`Downloading pipeline...` in any run log — and ignores the workspace bucket entirely.

So deployment needs a real API call. No CLI and no Docker are required; it is three steps.

## The mechanism

Reference implementation: `openhexa/cli/api.py` → `upload_pipeline()` (~line 690) and
`_build_pipeline_version_input()` (~line 298), readable in any installed SDK under
`site-packages/openhexa/`.

### Step 1 — parse the pipeline's parameters

```python
from openhexa.sdk.pipelines.runtime import get_pipeline
parsed = get_pipeline(Path("snt_dhis2_extract"))   # directory, not file
parsed.parameters   # list[Parameter]; .to_dict() matches the GraphQL ParameterInput exactly
parsed.timeout
```

**This is AST-based, not import-based** (`openhexa/sdk/pipelines/runtime.py:225` uses `ast.parse`).
That is what makes the whole approach viable: the Workspace Deployer parses all 20 `pipeline.py`
files **without importing them**, so none of their dependencies (`snt_lib`, `openhexa.toolbox`, …)
need to be installed in the deployer's own environment.

Use `Parameter.to_dict()` (`openhexa/sdk/pipelines/parameter/decorator.py:118`) — it emits exactly
the keys `ParameterInput` accepts. Do not hand-build that dict: the backend rejects the entire
mutation on any unknown input field (the SDK source cites HEXA-1687 about precisely this).

### Step 2 — zip the pipeline directory

Only these suffixes are included; everything else is skipped:

```
.py  .ipynb  .txt  .md  .r  .sql
```

Paths inside the zip are relative to the pipeline directory, so `pipeline.py` sits at the zip root.
The zip is base64-encoded into the `zipfile` input field.

Note the zip carries the **whole directory**, not just `pipeline.py` — `snt_map_extracts` deploys
with `utils.py`, `worldpopclient.py`, the `malariaAtlasProject/` package, `readme.md` and
`requirements.txt`. The release manifest mirrors this rule so that everything deployed is also
verifiable — see [`release_manifest.md`](contracts/release_manifest.md).

### Step 3 — call the GraphQL mutation

Endpoint `{HEXA_SERVER_URL}/graphql/`, header `Authorization: Bearer <token>` (which token matters
— see below).

| Mutation | Purpose | Input type |
|---|---|---|
| `createPipeline` | create a pipeline that does not exist yet | `CreatePipelineInput` |
| `uploadPipeline` | push a **new version** of an existing pipeline | `UploadPipelineInput` |

`UploadPipelineInput` (verified against `openhexa/graphql/graphql_client/input_types.py:919`):

```python
{
    "workspaceSlug": workspace.slug,   # required
    "code": "snt-dhis2-extract",       # kebab-case pipeline code, the deploy target
    "name": "v1.2.0",                  # version name
    "description": "...",              # shown in the version list
    "externalLink": None,              # link to the commit; what CI uses for traceability
    "zipfile": "<base64>",
    "parameters": [p.to_dict() for p in parsed.parameters],
    "timeout": parsed.timeout,
}
```

`CreatePipelineInput` takes `{workspaceSlug, name}` and optionally a nested `version`
(`CreatePipelineVersionInput` — same fields as above minus `workspaceSlug` / `code`). The nested
form creates a pipeline **and** its first version atomically, so bootstrapping an empty workspace
needs no manual UI step.

The **pipeline code** is the directory name with `_` → `-`. Cross-checked against all 20
`push_snt_*.yaml` workflows: 20/20 match the `--code` slug CI already uses.

## Authentication: use the `oh` connection token, not the run's token

`HEXA_SERVER_URL` and `HEXA_TOKEN` are injected into every cloud pipeline run
(`openhexa/sdk/utils.py:90`, `openhexa/sdk/workspaces/current_workspace.py:45`), so it is tempting
to call the API as the run itself. **That does not work for deployment.**

| Credential | `uploadPipeline` | `createPipeline` |
|---|---|---|
| The run's own `HEXA_TOKEN` | `PERMISSION_DENIED` | opaque HTTP 500 |
| Workspace API token from the `oh` connection | allowed | allowed (bare and nested) |

It is a credential-*scope* problem, not a mechanism problem: the identical payload succeeds or
fails purely on the header. Both tokens are 95 characters, so they are indistinguishable by shape.

In practice, a **CUSTOM connection named `oh`** (slug `oh`, one secret field `token`):

```python
token = workspace.custom_connection("oh").token
headers = {"Authorization": f"Bearer {token}"}
```

This is the same class of credential CI already uses for `openhexa pipelines push` — not a new
trust assumption, just an explicit one. It is literally the **workspace access token** OpenHEXA
displays under *Pipelines → Create → "From OpenHEXA CLI"*, the string that
`openhexa workspaces add <workspace>` prompts for. Two properties follow, both good:

* it is **workspace-scoped**, not personal — so it is not one person's credential spread across 20
  workspaces;
* the UI reveals it only to members holding the **Editor** or **Admin** role.

What is still manual is getting it there: someone copies it by hand into a CUSTOM connection named
`oh`, per workspace, with no rotation story. That is the one genuinely new operational requirement
this design adds. It is **deliberately parked as low priority** — see §7.3b.

### Reading is not deploying — reads need no connection

Verified 2026-09-22 in `snt-development-sandbox`: a run's own `HEXA_TOKEN` **can** read
`pipelineByCode.currentVersion.zipfile` in full, for pipelines it did not deploy. So the asymmetry is
specifically about *writing*:

| Operation | Run's `HEXA_TOKEN` | `oh` connection token |
|---|---|---|
| Read `currentVersion.zipfile` | **full zip** | full zip |
| `uploadPipeline` | `PERMISSION_DENIED` | allowed |
| `createPipeline` | opaque HTTP 500 | allowed |

The checker therefore needs no credential at all. Method and raw result:
[`HISTORY.md`](HISTORY.md) §2.4.

## Gotchas

1. **`pipelines` is a root-level query, not a field on `workspace`.** `query { workspace(slug:) {
   pipelines } }` returns a bare HTTP 400 with no message. Correct form:
   `pipelines(workspaceSlug: $slug, page: 1, perPage: 1) { totalItems }`.
2. **The SDK's `graphql()` helper hides the reason for failures.** `openhexa.sdk.utils.graphql`
   calls `raise_for_status()` and discards the response body — which is exactly where GraphQL puts
   the error. POST manually and log `response.text` before raising.
3. **Do not pass `code` to `createPipeline`.** It is declared `Optional[str]` in the generated
   schema types, but the CLI never sends it and the resolver appears not to handle it.
4. **`createPipeline` returns an opaque 500 on a permission failure**, where `uploadPipeline`
   returns a clean `PERMISSION_DENIED`. If you get `{'message': 'An unknown error occurred.'}`,
   suspect the token before anything else. (Worth reporting to the OpenHEXA devs.)
5. **`default=""` is rejected by the SDK.** A `str` parameter with an empty-string default raises
   `ParameterValueError("Empty values are not accepted.")` at parse time — meaning the *deploy*
   fails, not the run. Use `default=None`. Catch it locally by running `get_pipeline(Path(...))`
   before pushing; that is the same AST parse the backend performs, so it is worth doing after any
   parameter edit.
6. **A pipeline run reporting `success` does not mean the work happened.** Verify the effect
   independently — re-read the target pipeline's `currentVersion.versionNumber`.
7. **`@task` is not used anywhere in this repo.** The MCP `create_pipeline` tool's generic
   cheat-sheet suggests `@<pipeline_name>.task`; this codebase uses plain helper functions called
   from the `@pipeline` function. Follow the repo, not the tool hint (`snt_development`'s CLAUDE.md **R3**).
8. **A version's `files` list is the ground truth for what was deployed.** `get_pipeline` returns
   the full zip contents, so you can hash the deployed `pipeline.py` and compare it to the release
   manifest — that is how byte-identity was confirmed, and it is the basis for the verification
   pipeline.

9. **Version names must be unique within a pipeline.** `uploadPipeline` refuses a name that any
   version of that pipeline already holds, current or not, with `DUPLICATE_PIPELINE_VERSION_NAME`. The
   deployer therefore reads the current version first and chooses skip / relabel / `+redeploy-` name
   [`deployer.md`](deployer.md) (§ Workspace Deployer). The `+` is accepted by OpenHEXA.
10. **Nothing can be deleted through a pipeline.** There is no way for a pipeline to delete another
    pipeline or a version, so a release that drops a pipeline leaves a stray behind
    ([`deployer.md`](deployer.md) §7.5). Whether a rename or delete API exists for versions was not established;
    the SDK schema was not inspected.

## What has been proven, and what has not

**Proven:** the three-step sequence above bootstrapped `snt_dhis2_extract` and `snt_map_extracts`
into `snt-development-sandbox`, with correct pipeline codes, parameters round-tripped through
`Parameter.to_dict()` (including the `dhis2_connection` connection-typed parameter), and the deployed
`pipeline.py` byte-identical to the release manifest's sha256. Run details and the fixture it used:
[`HISTORY.md`](HISTORY.md) §4.2.

**Not verified:**

* **The 2026-09-30 version-naming logic** (skip / relabel / `+redeploy-`), in a real workspace.
  [`deployer.md`](deployer.md) §6.7 has the test plan.
* **The remaining 18 pipelines.** Only 2 of 20 have been through the deployer.
* **Whether `externalLink` is stored.** It is sent in the payload, but the MCP `get_pipeline` query
  does not select that field, so its absence from the response proves nothing either way. Check the
  OpenHEXA UI's version list.

## Open items

1. **Run the full 20-pipeline bootstrap** once, to confirm nothing in the other 18 trips the
   deployer.
2. **Automate getting the `oh` token into a country workspace** (minting, storage, rotation). What
   the token *is* is now known (see Authentication above); placing it is still a manual copy-paste
   per workspace. **Low priority** by the user's decision — a question for the OpenHEXA devs once
   there is a working checker and deployer to demonstrate. §7.3b.
3. ~~**Establish whether *reading* a pipeline version needs the `oh` token too.**~~ **Closed
   2026-09-22: it does not.** See Authentication above and [`HISTORY.md`](HISTORY.md) §2.4.
4. ~~**Add a `push_snt_workspace_deployer.yaml` workflow.**~~ **Closed 2026-09-30 (D19): no deployment
   workflow for the deployer or the checker.**

### R5 and the Template mechanism

This approach pushes pipeline **versions directly into each country workspace**, bypassing
OpenHEXA's Template system. Retiring templates altogether is the aim of this work (D20,
§7.7), so the mechanism is documented here because it is still in place, not
because anything new should be built around it.

`snt_development`'s CLAUDE.md **R5** ("Always publish from `snt-development`") exists because pushing *a template* from
the wrong workspace creates a competing duplicate template. Pushing a *pipeline version into the
workspace that will run it* is a different operation and creates no template. R5 is **left as it
is** and is not being reworded for this work: it stays true for as long as templates exist.

## Python deployment — done

`pipeline.py` is **not** copied into the workspace filesystem. OpenHEXA does not run pipelines from
the filesystem: each pipeline is a registered object and each of its versions stores its own zipped
copy of the code, which is what the runner downloads.

The full mechanism — the three-step API sequence, exact GraphQL payloads, token handling and
gotchas — is in **[`openhexa_deployment.md`](openhexa_deployment.md)**. In short:

* No CLI and no Docker needed. `openhexa.sdk.pipelines.runtime.get_pipeline()` parses a pipeline's
  parameters by **AST**, not by importing it, so the deployer handles all 20 pipelines without their
  dependencies installed.
* Authentication must use a **workspace API token read from the `oh` CUSTOM connection**. A run's
  own `HEXA_TOKEN` is refused.
* `createPipeline` accepts the nested form that creates a pipeline and its first version atomically,
  so **bootstrapping an empty workspace needs no manual UI step.**

Proven for **2 of 20** pipelines in the original 2026-09-16 run, byte-identical to the manifest hash;
the phase 1–4 sandbox runs since deployed the whole fixture release (21 pipelines) through it.

## 7.3 Credentials in a country workspace — **low priority**, and no longer blocks the checker

Two questions, and the one that gated phase 1 is answered.

**7.3a — does *reading* a pipeline version need the `oh` token? No. Closed 2026-09-22.** A run's own
`HEXA_TOKEN` returns `currentVersion.zipfile` in full. The checker is therefore **credential-free**:
it can run unattended in a country workspace that holds no connection at all, which also keeps the
daily-check story in [`checker.md`](checker.md) §7.6 alive. Evidence and method: [`HISTORY.md`](HISTORY.md) §2.4.

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

## 7.7 ~~R5~~ and the Template mechanism — **closed 2026-09-30 (D19, D20)**

`snt_development`'s `CLAUDE.md` **R5** ("always publish from `snt-development`") was written about *template*
publication. This work, `snt_workspace_deployer` above all, exists to **get rid of pipeline
templates altogether** (D20). The mechanism is still in place and must be understood, but nothing
new is built around it, and R5 is **not** being reworded for this work: it stays as it is for as
long as the templates it protects exist. Pushing a pipeline version into the workspace that runs it
creates no template and is a different operation; see `openhexa_deployment.md`.
