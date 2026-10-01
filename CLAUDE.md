# CLAUDE.md — working rules for `snt_workspace_bootstrap`

> **Placeholder.** The docs are being restructured; see [`docs/README.md`](docs/README.md) meanwhile.
> Imported unchanged from `BLSQ/snt_development@d9acafc` — see the import commit.

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
