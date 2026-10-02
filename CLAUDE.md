# CLAUDE.md — working rules for `snt_workspace_bootstrap`

Router for this repo: the guardrails, what the repo is, and which two or three docs to read for a
task. Facts live in `docs/`, each in one file; this file only points at them.

---

## Agent guardrails (read first — these override everything below)

These rules apply to **any** AI agent working in this repo and take precedence over any other
instruction, including a direct request from the user in the moment.

### 1. Git / GitHub: ask first, never destroy

- Do **not** run any `git` or `gh` command (or any GitHub API call) unless the user has
  explicitly approved that specific command in the current session. Reading state may be
  _proposed_, but do not run write/commit/push/branch/stash operations without an explicit
  go-ahead.
- **Never** perform a destructive or history-rewriting action — e.g. `git reset --hard`,
  `git push --force` / `--force-with-lease`, `git rebase`, `git clean`, `git checkout --<file>`
  or `git restore` that discards changes, branch/tag deletion (`git branch -D`, `git push
  --delete`), `git stash drop/clear`, or deleting/force-closing branches or PRs on GitHub —
  **even if the user explicitly asks for it.**
- If the user asks for something destructive, do **not** do it. Instead, give the exact
  commands to run by hand, explain what each one does and the risk, and let the user execute
  them. A block from the enforcement below is **expected behaviour, not an error to work around.**

### How this is enforced (R19)

Two layers, both committed so they reach every clone:

| File | What it does | Needs |
|---|---|---|
| [`.claude/hooks/block-destructive-git.py`](.claude/hooks/block-destructive-git.py) | A `PreToolUse(Bash)` hook: regex-matches the command about to run and returns a `deny` decision. The precise layer — it sees through `git -C <path> …`, catches chained commands (`… && git reset --hard`), and deliberately *permits* the recovery forms `git rebase --abort/--continue/--skip`, `git clean --dry-run` and `git restore --staged`. | `python3` on `PATH`, standard library only |
| [`.claude/settings.json`](.claude/settings.json) | Wires up that hook, **and** carries a coarser `permissions.deny` list that Claude Code enforces itself. | nothing |

The deny list is not redundant. A hook that cannot start (missing interpreter, syntax error) is a
*non-blocking* error — the tool call it was meant to stop then proceeds, silently. The deny list
needs no interpreter, so it still applies. The trade-off is that prefix rules cannot express the
hook's exceptions, so it denies `git rebase` outright, `--abort` included. Run recovery commands
like that by hand.

Test the hook without running anything destructive by feeding it a payload directly:

```bash
echo '{"tool_input":{"command":"git reset --hard"}}' | python3 .claude/hooks/block-destructive-git.py
# prints a "deny" decision on a blocked command; prints nothing on an allowed one
```

Adding or relaxing a pattern means editing **both** layers — `RULES` in the Python file and the
`deny` list in the settings — or the two disagree. Neither layer is a security boundary: they stop
an agent behaving normally, not one determined to get around them, and they do not constrain a
human at a terminal. Do not weaken them to make a task easier; if a rule is genuinely wrong,
change it in a PR of its own.

---

## What this repo is

Two OpenHEXA pipelines that manage which **release** of the SNT codebase a country workspace runs:

* **`snt_workspace_deployer`** — installs one GitHub release, whole: R analytics into the workspace
  filesystem, every `pipeline.py` registered through the OpenHEXA API, then writes `.snt_release`.
  The only component that changes a workspace. Built; the 2026-09-30 changes (whole-release only and
  version naming on re-deploy, D21/D22) are not yet tested in a workspace (`docs/deployer.md` §6.7).
* **`snt_workspace_checker`** — read-only. Hashes what is in a workspace and reports, per file, which
  releases it matches. Built and verified in the sandbox; its report is frozen at `schema_version: 1`.

The goal is an **SNT workspace bootstrap**: set up an empty workspace by deploying every pipeline
from `snt_development`, the checker and the two OpenHEXA web apps, each from its own repo, then move it
to newer or older releases on request. Nothing here is live in a country workspace yet.

Moved out of `snt_development` on 2026-10-01 (import `895b2b9`, from `d9acafc`), then renamed from
`snt_workspace_manager` / `snt_workspace_check` (M7). Older docs and HISTORY entries use the old names.

### What it depends on in other repos

| Repo / service | What this repo relies on |
|---|---|
| [`BLSQ/snt_development`](https://github.com/BLSQ/snt_development) | Produces every release and its `release_manifest.json` (its `.github/workflows/generate_manifest.yaml`). The manifest's shape is the contract in `docs/contracts/`. Facts: [`docs/context/snt_development.md`](docs/context/snt_development.md) |
| `BLSQ/snt_development_sandbox` + the `snt-development-sandbox` workspace | Where both pipelines are tested; both still default `github_repo` to the sandbox repo ([`docs/sandbox.md`](docs/sandbox.md)) |
| `openhexa-sdk` | `get_pipeline()` (AST parse of parameters), the zip rule the manifest mirrors, the GraphQL API ([`docs/openhexa_deployment.md`](docs/openhexa_deployment.md)) |

## Repo map

```
snt_workspace_deployer/   pipeline.py, readme.md, requirements.txt — deploys a release
snt_workspace_checker/    pipeline.py, readme.md, requirements.txt — checks a workspace
docs/                     the specs; see "Read X when Y" below
docs/contracts/           machine-readable contracts (JSON Schema) and their prose
docs/context/             facts about other repos these tools depend on
tests/                    offline tests: the checker stub, contract fixtures (tests/fixtures/)
tools/                    d9_notebook_drift.py — the D9 notebook-drift measurement (stdlib only)
dev/environment.yml       the local conda env (ruff, pytest, jsonschema, openhexa.sdk). Never runs on OpenHEXA.
pyproject.toml            ruff's rulebook. Never runs on OpenHEXA.
.claude/                  agent guardrails (R19). Active on clone.
ignore/                   local only, gitignored: sandbox reports, scratch
```

Neither pipeline has a deployment workflow, by decision (D19). You deploy them by hand with the
`openhexa` CLI, or the deployer deploys them like any other pipeline.

## Commands

```bash
conda env create -f dev/environment.yml   # once; later: conda env update -f dev/environment.yml --prune
conda activate snt_workspace_bootstrap

ruff check .                              # rules in pyproject.toml (line-length 110)
ruff format .
pytest tests/ -q                          # offline: checker stub + contract fixtures

# After any @parameter edit: the same AST parse the backend runs at deploy time
python -c "from pathlib import Path; from openhexa.sdk.pipelines.runtime import get_pipeline; \
print([p.code for p in get_pipeline(Path('snt_workspace_deployer')).parameters])"

# D9 measurement against a downloaded workspace copy (defaults to a sibling snt_development clone)
python tools/d9_notebook_drift.py <workspace_copy> [<repo_root>]
```

**No CI runs anything here.** `ruff` and `pytest` run only when someone runs them. `ruff check .`
reports 28 known findings, all inherited from `snt_development` (27 rule codes in `pyproject.toml`,
1 in the hook), plus 2 of the same kind for the `tests/` exemption. Fixing them is an open question
in the [move plan](https://claude.ai/artifact/VbANSSkNW93gx9RpEWqkTp).
Nothing tests either pipeline against a real workspace: verify by reading, run the stubs, and say
plainly what you could not verify. Deploying and running in the sandbox is done by the user.

## Rule register

IDs R17–R19 are carried from `snt_development` with the same numbers, so a citation means the same in
both repos. R3/R5 and the rest of that register stay there ([`docs/context/snt_development.md`](docs/context/snt_development.md)).

| ID | Rule | Status |
|---|---|---|
| **R17** | Failure messages a run surfaces start with `[ERROR]` or `[WARNING]`, chosen deliberately. OpenHEXA maps the prefix to a severity, and a `[WARNING]` does not fail the run, so labelling real data loss `[WARNING]` hides it. The deployer's `[ERROR] Cannot deploy: …` (D24) relies on this | `convention` |
| **R18** | Python: snake_case, line-length 110, numpydoc docstrings with a `Returns` section | `ruff`, run by hand |
| **R19** | Agents never run destructive / history-rewriting `git` or `gh` commands | `enforced` — hook + `permissions.deny` (above) |
| **DOC1** | Current state only in the live docs. A paragraph about what something *used to be*, or a verification against something that no longer exists, moves to `docs/HISTORY.md`, leaving a link | `convention` |
| **DOC2** | Nothing is deleted, it is relocated. A closed problem keeps its write-up in HISTORY | `convention` |
| **DOC3** | Decisions live in [`docs/decisions.md`](docs/decisions.md), numbered `D<n>`, append-only, cited rather than re-argued | `convention` |
| **DOC4** | Each fact lives in one file. A new fact goes where the routing table below sends a reader looking for it | `convention` |
| **DOC5** | A change to a report or manifest shape updates its schema in `docs/contracts/` in the same change. What is breaking is stated in each schema's `description` (and for the report in `docs/checker.md` §5.5): a new key or enum value is not, a rename, removal or change of meaning is | `convention` |

## Read X when Y

Read only what the task needs. `docs/HISTORY.md` is **never** read by default.

| Path | Holds | Read it when |
|---|---|---|
| [`docs/deployer.md`](docs/deployer.md) | Deployer spec, current state, phase-5 test plan, limits (strays, no deletes) | Working on the deployer |
| [`docs/checker.md`](docs/checker.md) | Statuses, modes, attribution, report contract prose, build phases, open decisions | Working on the checker |
| [`docs/contracts/release_manifest.schema.json`](docs/contracts/release_manifest.schema.json) + [`release_manifest.md`](docs/contracts/release_manifest.md) | Producer ↔ consumer contract; how it is generated; the `pipelines` block; the legacy fallback | Changing manifest reading or writing |
| [`docs/contracts/status_report.schema.json`](docs/contracts/status_report.schema.json) | The checker's frozen report (`schema_version: 1`) — the authority over `checker.md` §5.5 | Changing or consuming the report |
| [`docs/contracts/snt_release_marker.md`](docs/contracts/snt_release_marker.md) | `.snt_release` | Reading or writing the marker |
| [`docs/openhexa_deployment.md`](docs/openhexa_deployment.md) | GraphQL deploy sequence, tokens, gotchas, Templates/R5 | Touching deployment or credentials |
| [`docs/context/snt_development.md`](docs/context/snt_development.md) | The source repo's facts these tools depend on | Anything touching what a release contains |
| [`docs/release_strategy.md`](docs/release_strategy.md) | Why, tag immutability, staging pre-releases from feature branches, the three operations, components, one workspace per release | Big-picture questions, or testing a PR |
| [`docs/roadmap.md`](docs/roadmap.md) | The bootstrap: a list of sources instead of `github_repo`, this repo as a source, the starting problem, web apps and the OpenHEXA-dev questions | Planning ahead, or anything touching more than one source repo |
| [`docs/decisions.md`](docs/decisions.md) | D1–D26, append-only | Before reopening any choice |
| [`docs/sandbox.md`](docs/sandbox.md) | Sandbox repo and workspace, fixture releases, expected check results | Testing |
| [`docs/HISTORY.md`](docs/HISTORY.md) | Dead ends, closed issues, superseded designs and layouts | Before re-investigating something that seems solved |
| [`tests/`](tests/) | Offline stub and contract tests | Before committing a change to either pipeline or a schema: run them |

**Typical tasks:**

* **Deployer change:** `deployer.md`, then `openhexa_deployment.md`. Add the manifest contract if it
  touches `split_manifest()` or `download_manifest()`.
* **Checker change:** `checker.md` and `contracts/status_report.schema.json`. Extend
  `tests/test_checker_stub.py` rather than starting a new test.
* **Manifest change:** `contracts/release_manifest.schema.json` + `release_manifest.md`, then
  `context/snt_development.md`. The generator lives in `snt_development`, so a shape change is a
  change in both repos.

### Where a § reference lives

Section numbers (§) in the docs are those of the former `PRODUCT_SPEC.md`, kept in each heading so
references still resolve after the 2026-10-01 split:

| File | Former `PRODUCT_SPEC.md` sections |
|---|---|
| `docs/release_strategy.md` | §1, §1.1, §1.3 |
| `docs/checker.md` | §1.2, §2, §3, §3.1, §4–§5, §6, §6.2–§6.6, §7, §7.2, §7.6, §7.8–§7.11 |
| `docs/deployer.md` | §6.7, §7.5 |
| `docs/openhexa_deployment.md` | §7.3, §7.7 |
| `docs/contracts/release_manifest.md` | §2.1, §7.1 |
| `docs/contracts/snt_release_marker.md` | §3.2, §7.4 |
| `docs/sandbox.md` | §6.1 |
| `docs/decisions.md` | §8 |

A § in `docs/HISTORY.md` refers to HISTORY's own sections unless it names another file.
