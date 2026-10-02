# Roadmap — toward the SNT workspace bootstrap

> What comes after the move: one deploy path for **several release sources** instead of one
> `github_repo`, the bootstrap's starting problem, and web apps. Nothing here is built or decided;
> each heading says which parts are proposals. A choice made from this file becomes a `D<n>` in
> [`decisions.md`](decisions.md) and its text moves to the component's spec. Written 2026-10-02
> (Session 5 of the move plan).

## The goal

Set up an empty SNT workspace by deploying, each from its own repository: every pipeline and the R
analytics of `snt_development`, the checker, and the two OpenHEXA web apps (pipeline orchestrator,
SNT_config editor). Then move the workspace to newer or older releases on request.

Today the deployer and the checker each take **one** `github_repo` and one `release_tag`, and the
workspace records one tag in `.snt_release`. Everything below is what changes when a workspace is
made of several repositories' releases at once.

## 1. Sources replace `github_repo` — proposal

A **source** is one `owner/repo@tag`. A workspace is described by a list of them:

```
BLSQ/snt_development@v1.2.0
BLSQ/snt_workspace_bootstrap@v0.1.0
```

Every source publishes a `release_manifest.json` in the shape of
[`contracts/release_manifest.schema.json`](contracts/release_manifest.schema.json). The schema is
already repository-agnostic for this reason (D26), so **the manifest itself needs no change**. What
does change, per component:

| Component | Today | With a list of sources |
|---|---|---|
| Deployer parameters | `github_repo`, `release_tag` | One list parameter of `owner/repo@tag` strings. An empty tag keeps D23's meaning (that source's latest release). |
| `.snt_release` | `{"snt_release": "<tag>"}`, no repository ([`snt_release_marker.md`](contracts/snt_release_marker.md) §3.2) | One entry per source: repository and tag at least. This is §7.4's enrichment, now required. Markers written by today's deployer must still be read. |
| `archive/<release_tag>/` | One folder per tag | Two sources can share a tag (`v0.1.0` in both). The folder needs the repository in its path. |
| Checker parameters | `github_repo`, `release_tag` | The same list. Attribution then runs per source. |
| Status report | One `repo`, one `releases[]` | **Breaking** under the report's own rules: `repo` would change meaning. Needs `schema_version: 2` (D18, DOC5). |
| Manifest | per repository | Unchanged. |

**What a single source never had to handle:**

* **Collisions.** Two sources may ship the same filesystem path or the same pipeline `code`. The
  deployer must refuse that before it changes anything, with an `[ERROR] Cannot deploy:` line (D24),
  rather than let the last source silently win.
* **Order.** With collisions refused, order does not matter for correctness. A fixed order still
  keeps the run logs comparable between runs.
* **Partial runs.** One source can succeed and the next fail. The marker must say which sources
  were deployed, or the checker reads intent as fact (§3.2's second limitation, multiplied).

**What already breaks today, before any of this is built.** Pointing the current deployer at a
second repository overwrites `.snt_release` with that repository's tag. The checker would then
compare the `snt_development` files against a tag from another repository. So until the marker
records the repository, **deploy only `snt_development` with the deployer**, and deploy this
repository's pipelines by hand (D19).

**Open:**

* Is the bootstrap a new pipeline, or the deployer with a list parameter? The deployer already does
  every step except handling several sources.
* Does the checker need the full list, or can it read the sources from the enriched marker?

## 2. This repository as a release source — proposal

Plan item: give this repository its own manifest workflow, so the bootstrap can deploy the checker
like any other pipeline.

* **What a release here contains.** The generator's deployment half finds two pipeline directories:
  `snt_workspace_checker/` *and* `snt_workspace_deployer/`. So a release of this repository ships the
  deployer as well as the checker. The filesystem half (`pipelines/**`, `code/**`) matches nothing
  here, so the deployer would copy no analytics from it.
* **One generator (D27).** [`.github/workflows/generate_manifest.yaml`](../.github/workflows/generate_manifest.yaml)
  here calls `snt_development`'s generator as a reusable workflow, rather than keeping a second copy
  of the SDK's zip rule. It needs a `workflow_call` trigger added on `snt_development` `main` first;
  until then this repository's workflow fails at the call.
* **Before the first release here:** the report key is renamed `snt_workspace_checker_version`
  (done 2026-10-02, still `schema_version: 1`; [`checker.md`](checker.md) §5.5).
* **Tag protection:** the `Protect release tags` ruleset is active on this repository (2026-10-02)
  ([`release_strategy.md`](release_strategy.md)).

### To evaluate: deliver these two as OpenHEXA template pipelines instead

**To be evaluated after checking with colleagues. Not to decide now** (Giulia, 2026-10-02).

`snt_workspace_checker` and `snt_workspace_deployer` are pure Python. Unlike the `snt_development`
pipelines, they bring no R analytics to the filesystem, so a release of them may not need the
filesystem half at all. Whether they need *other* files on the filesystem is not known yet.

That raises the option of treating them like the other OpenHEXA template pipelines, instead of as a
release source of their own. Points to evaluate:

* **How the existing template pipelines are versioned.** Colleagues keep them in a dedicated
  repository; find out how it releases and deploys them.
* **Generic or SNT-specific.** Make the two pipelines generic and company-wide, or keep them
  SNT-specific.
* **Context stays with the code.** Both are tightly coupled to other repositories: the SNT pipelines
  now, the web apps next. Whatever delivers them must keep this repository's docs and contracts next
  to the Python code.
* **D20.** The template mechanism is being retired for the `snt_development` pipelines. Weigh whether
  that applies to these two as well, or only to pipelines that ship R alongside their Python.

The proposal above (a manifest workflow here) holds until this is evaluated.

## 3. The starting problem (D19)

**The bootstrap cannot deploy itself into an empty workspace.** Nothing exists there to run it, and
by D19 there is no deployment workflow for this repository's pipelines. So every new workspace
starts with one hand-deployed pipeline, by `openhexa pipelines push`. The bootstrap then deploys
everything else, including newer versions of itself: the deployer can already deploy a new version
of itself, as noted in [`release_manifest.md`](contracts/release_manifest.md).

Two more manual steps come with that first one:

* **The `oh` connection.** Deploying needs the workspace access token in a CUSTOM connection named
  `oh` ([`openhexa_deployment.md`](openhexa_deployment.md) §7.3b). Today that is a copy-paste.
* **Which version is hand-deployed.** The hand-pushed copy should come from a release tag, not from
  a working tree. Otherwise the workspace starts on code no manifest describes, and the checker reads
  it as `unknown_content`.

## 4. Web apps (D8)

**The API can create and update a static web app from files. Read in `openhexa-sdk` 2.22.7's
GraphQL schema (`openhexa/graphql/schema.generated.graphql`) on 2026-10-02; never called.**

| Operation | Takes | Notes |
|---|---|---|
| `createWebapp(input: CreateWebappInput!)` | `workspaceSlug`, `name`, `source`, optional `allowedOperations`, `isPublic`, `description`, `icon` | `source` is one of `static: [WebappFileInput!]` (the files), `iframe`, `superset`. Only `static` concerns the bootstrap (below) |
| `updateWebapp(input: UpdateWebappInput!)` | `id`, then optionally `files`, `filesToDelete`, `allowedOperations`, `publishedVersionId`, `name`, `subdomain`, … | Incremental: files left out are kept |
| `deleteWebapp(input: DeleteWebappInput!)` | `id` | |
| `webapp(slug, workspaceSlug)`, `webapps(workspaceSlug, …)` | | A `Webapp` has `versions`, `commitDiff(ref)`, `files(ref)` and a `GitSource { repository, publishedVersion }`: OpenHEXA keeps a static app's files in a git repository of its own |

`WebappFileInput` is `{path, content, encoding}`, with `encoding` `TEXT` or `BASE64`. So a release
of a web app's repository could, in principle, be uploaded file by file, like a pipeline zip.

**Every web app the bootstrap deploys is a static app** (Giulia, 2026-10-02): the pipeline
orchestrator, the SNT_config editor, and any later one. It never deploys an `iframe` or `superset`
app, so only the `static` source and the `files` / `filesToDelete` updates matter here.

**Not known yet: test first in the sandbox, ask the OpenHEXA devs only if a test hits a wall.**

1. Which token may call `createWebapp` / `updateWebapp` from inside a pipeline run: the run's own
   `HEXA_TOKEN`, or only the `oh` workspace token? The same question for pipelines had a non-obvious
   answer ([`openhexa_deployment.md`](openhexa_deployment.md), Authentication), and was settled the
   same way, by trying both.
2. Can a web app's files be hashed as they are served (through `files(ref)`)? The checker needs that
   to tell which release an app is at, the way it reads a pipeline version's zip.
3. How should a deploy name a version? `updateWebapp` takes a `publishedVersionId`. Can a version
   carry the release tag, the way pipeline versions do (D22)?
4. `allowedOperations` grants the app's own GraphQL scopes. Who decides those per app, and are they
   part of a release?

Also for the devs, already written up elsewhere: deleting or archiving a pipeline from a run
([`deployer.md`](deployer.md) §7.5), and minting the `oh` token
([`openhexa_deployment.md`](openhexa_deployment.md) §7.3b).

## 5. Tracked elsewhere

The move plan's other Session 5 items. They are listed here so the roadmap is complete, and each
is described in its own file:

* Switch both `github_repo` defaults from `BLSQ/snt_development_sandbox` to `BLSQ/snt_development`
  **once the first real release exists**. On 2026-10-02 its only release was `v0.0.0-test`,
  published that day as "Unofficial 1st release for testing purposes".
* Finish the deployer's test plan, D21–D24 and `backup_existing` ([`deployer.md`](deployer.md)
  §6.7). Run by hand in the sandbox.
* ~~Delete the pre-phase-0 fallback in `split_manifest()`~~ Deleted 2026-10-02 (D26,
  [`release_manifest.md`](contracts/release_manifest.md) §2.1).
