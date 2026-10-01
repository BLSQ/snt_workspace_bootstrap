#!/usr/bin/env python3
"""PreToolUse(Bash) guardrail: hard-deny destructive / history-rewriting git & gh commands.

Reads the Claude Code hook payload (JSON) on stdin, inspects ``tool_input.command``
and, on a match, prints a PreToolUse "deny" decision so the command never runs.

Why Python and not node/jq: this repo already requires Python >= 3.11 (pyproject.toml)
and every contributor has it; node and jq are not part of the toolchain and would make
the guardrail fail *open* on machines that lack them. Only the standard library is used.

Wired up in ``.claude/settings.json`` -> ``hooks.PreToolUse``. A second, coarser backstop
lives in the same file under ``permissions.deny`` — it needs no interpreter, so it still
applies if this script cannot run at all.

To add or remove a blocked pattern, edit RULES below. A non-match exits 0 silently and the
command proceeds to the normal permission flow.
"""

import json
import re
import sys

# Global git options that may sit between `git` and the subcommand, so that
# `git -C /some/path reset --hard` is caught just like `git reset --hard`.
GIT = r"\bgit\s+(?:-[cC]\s+\S+\s+|--git-dir=\S+\s+|--work-tree=\S+\s+|-P\s+|--no-pager\s+)*"
GH = r"\bgh\s+"

# `[^;&|]*` keeps each rule inside a single command, so chained commands
# (`foo && git reset --hard`) are matched on their own rather than across the `&&`.
RULES = [
    ("git reset --hard", GIT + r"reset\b[^;&|]*\s--hard\b"),
    (
        "git push --force / -f / --force-with-lease",
        GIT + r"push\b[^;&|]*(?:--force(?:-with-lease|-if-includes)?\b|\s-f\b)",
    ),
    ("git push <remote> +<ref> (force refspec)", GIT + r"push\b[^;&|]*\s\+[A-Za-z0-9_./*-]+"),
    ("git push --delete / -d (remote branch/tag deletion)", GIT + r"push\b[^;&|]*(?:--delete\b|\s-d\b)"),
    (
        "git rebase (history rewrite)",
        GIT + r"rebase\b(?![^;&|]*--(?:abort|continue|skip|quit|edit-todo|show-current-patch))",
    ),
    # The lookahead also matches clustered dry-run flags such as `-nd` / `-ndx`.
    ("git clean (deletes untracked files)", GIT + r"clean\b(?![^;&|]*(?:--dry-run\b|\s-[A-Za-z]*n))"),
    ("git branch -D / -d / --delete", GIT + r"branch\b[^;&|]*(?:\s-D\b|\s-d\b|\s--delete\b)"),
    ("git tag -d / --delete", GIT + r"tag\b[^;&|]*(?:\s-d\b|\s--delete\b)"),
    ("git stash drop / clear", GIT + r"stash\s+(?:drop|clear)\b"),
    ("git checkout -- <file> (discards working-tree changes)", GIT + r"checkout\b[^;&|]*\s--(?:\s|$)"),
    ("git checkout . (discards working-tree changes)", GIT + r"checkout\s+\.(?:\s|$)"),
    (
        "git checkout -f / --force (discards working-tree changes)",
        GIT + r"checkout\b[^;&|]*(?:\s-f\b|\s--force\b)",
    ),
    # `git restore --staged` alone only unstages, which is recoverable; anything that
    # also touches the worktree (the default, or an explicit --worktree) discards edits.
    (
        "git restore (discards working-tree changes)",
        GIT + r"restore\b(?![^;&|]*--staged\b(?![^;&|]*--worktree\b))",
    ),
    ("git reflog delete / expire", GIT + r"reflog\s+(?:delete|expire)\b"),
    ("git update-ref -d / --delete (ref deletion)", GIT + r"update-ref\b[^;&|]*(?:\s-d\b|\s--delete\b)"),
    ("git filter-branch / filter-repo (history rewrite)", GIT + r"filter-(?:branch|repo)\b"),
    ("gh pr close", GH + r"pr\s+close\b"),
    ("gh repo delete", GH + r"repo\s+delete\b"),
    ("gh release delete", GH + r"release\s+delete\b"),
    ("gh api ... -X DELETE / --method DELETE", GH + r"api\b[^;&|]*(?:-X\s+DELETE|--method\s+DELETE)"),
]

COMPILED = [(label, re.compile(pattern)) for label, pattern in RULES]

DENY_TEMPLATE = (
    "BLOCKED by project guardrail (.claude/hooks/block-destructive-git.py): this is a "
    "destructive / history-rewriting command ({label}). Per CLAUDE.md the agent must NEVER run "
    "this — even on explicit request. Do not retry and do not work around it. Instead give the "
    "user the exact command to run by hand, explain what it does and the risk, and let them "
    "execute it themselves."
)


def find_violation(command: str) -> str | None:
    """Return the label of the first rule the command violates.

    Returns
    -------
    str or None
        The matching rule's label, or None if the command is not blocked.
    """
    for label, pattern in COMPILED:
        if pattern.search(command):
            return label
    return None


def main() -> None:
    """Read the hook payload from stdin and deny the command if it matches a rule.

    Returns
    -------
    None
        Always exits 0; a deny decision, when there is one, is written to stdout as JSON.
    """
    try:
        command = str(json.load(sys.stdin).get("tool_input", {}).get("command", ""))
    except Exception:  # unparseable payload -> stay out of the way
        sys.exit(0)

    label = find_violation(command) if command else None
    if label:
        json.dump(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": DENY_TEMPLATE.format(label=label),
                }
            },
            sys.stdout,
        )
    sys.exit(0)


if __name__ == "__main__":
    main()
