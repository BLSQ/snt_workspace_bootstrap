# Release manifest — the producer ↔ consumer contract

> The authority is [`release_manifest.schema.json`](release_manifest.schema.json) (D26). This file
> holds the prose: how the manifest is generated in `snt_development`, why it has a `pipelines`
> block, and the legacy fallback still in the deployer. What `snt_development` ships is in
> [`../context/snt_development.md`](../context/snt_development.md).

## Manifest generation — done

[`.github/workflows/generate_manifest.yaml`](https://github.com/BLSQ/snt_development/blob/main/.github/workflows/generate_manifest.yaml) runs on
`release: published` (and `workflow_dispatch` for manual testing). It hashes every file a release
ships and attaches `release_manifest.json` to the release. The workflow is committed in *this* repo;
the sandbox copy is applied by hand.

The generator has two halves, because a release reaches a workspace by two routes:

1. **Filesystem half** — four glob patterns: `pipelines/**/code/*.ipynb`,
   `pipelines/**/reporting/*.ipynb`, `pipelines/**/utils/*.r`, `code/**/*.r`.
2. **Deployment half** — for each top-level directory holding a `pipeline.py`, walk it recursively
   and keep every file whose suffix is in `{.py, .ipynb, .txt, .md, .r, .sql}`, minus the
   `workspace/` subtree. That is a line-for-line mirror of `generate_zip_file()` in
   `openhexa/cli/api.py`; the constants are commented with that provenance, and the suffix test is
   case-insensitive there and here.

**Why it mirrors the SDK rather than listing globs.** A glob list restates the SDK's rule in a
second, drifting dialect — one pattern per suffix *per depth* — and needs a new line for every
future nesting or suffix. Anchoring on `*/pipeline.py` also excludes `dev/` and `deprecated/`
without naming them: `deprecated/` nests its eight pipelines one level deeper, so `*/pipeline.py`
never matches inside it. Nothing is excluded by a denylist that a new top-level directory could slip
past. (The rejected alternative is recorded in [`HISTORY.md`](../HISTORY.md) §2.1.)

The manifest also carries a `pipelines` block alongside `files` — per pipeline directory, its
OpenHEXA `code` slug and the list of paths inside its zip. Shape and rationale:
§2.1. `files` is byte-compatible with the pre-phase-0 shape, so
the block is purely additive.

**Verified 2026-09-21** against `snt_development` @ `SNT25-670`, by building all 21 deployment zips
with the SDK's *real* `generate_zip_file()` and asserting every zip member resolves to a manifest
entry:

```
manifest files: 156 | zip members: 70 | zip members NOT in manifest: 0
```

The embedded script passes `ruff check` and `ruff format --check` under the repo's `pyproject.toml`.

Two consequences worth knowing:

* `snt_workspace_deployer` is itself a pipeline directory, so the manifest now describes the deployer
  as well. That is wanted — a workspace can be told its deployer is out of date — but it does mean
  the deployer can deploy a new version of itself.
* If the SDK ever changes its suffix list, this generator goes stale silently. The checker's
  `not_covered` status ([`checker.md`](../checker.md) §5.1) is the tripwire for exactly that, which is why the
  spec keeps it after phase 0 rather than deleting it.

Country-specific notebook variants appear in the manifest undistinguished from generic files. That
is intended — telling them apart is the verification pipeline's job, not the generator's.

## 2.1 The manifest's `pipelines` block

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
dead code. The contract is [`release_manifest.schema.json`](../contracts/release_manifest.schema.json),
which requires the block.

`snt_workspace_checker` deliberately does **not** carry the fallback: it refuses a manifest with no
`pipelines` block, naming the phase-0 cutover in the error. That makes the two components disagree on
purpose, and the disagreement is the argument — one of them has untested code for an impossible case
and the other does not. Resolved in the checker's favour (D26).

## 7.1 ~~The manifest under-described what is deployed~~ — **closed**

Closed as phase 0. Kept as a numbered heading only so the [`checker.md`](../checker.md) §7.x references elsewhere still line up.
What the problem was, why a wider glob list was rejected, and the consumer it broke:
[`HISTORY.md`](../HISTORY.md) §2.1–2.2. What the generator does now:
this file.

Phase 1 was left blocked only by the token question in [`openhexa_deployment.md`](../openhexa_deployment.md) §7.3, which closed on 2026-09-22. **Phase 1
is unblocked and ready to start.**
