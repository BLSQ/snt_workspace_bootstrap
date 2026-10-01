# `.snt_release` — the workspace release marker

> Written by the deployer at the workspace root, read by the checker to pick its target (see
> [`checker.md`](../checker.md) §4.2).

## 3.2 `.snt_release`

`snt_workspace_deployer` writes `{"snt_release": "<tag>"}` at the workspace root at the end of
every run. Two honest limitations, both of which the report must reflect rather than paper over:

* It records only the tag, **not the repository it came from**, while `github_repo` is still a
  parameter.
* It is written after a partial run too, so it states **intent**, not verified fact.

A workspace with no marker is the normal starting state today — every existing country workspace.
That must never be an error.

## 7.4 Enriching `.snt_release`

The marker should arguably record the repository, a timestamp, and whether the run completed
cleanly, so a checker can tell "deployed to T" from "attempted T, partially". Changing it means
changing `snt_workspace_deployer` and handling markers written by older versions.
