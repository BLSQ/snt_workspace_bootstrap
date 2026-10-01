"""Measure how far real workspace notebooks drift from the repository, and of what kind (decision D9).

docs/decisions.md D9: whether the checker should normalise notebooks before hashing is to
be decided after measuring real drift, not up front. This script produces that measurement.

Usage (standard library only, any Python >= 3.11):

    python3 d9_notebook_drift.py <workspace_copy> [<repo_root>] [--json out.json]

<workspace_copy> is a local copy of a real workspace's files - at least its `pipelines/` folder
(e.g. downloaded from JupyterLab). <repo_root> defaults to a sibling snt_development clone. Every
notebook under pipelines/*/code/ and pipelines/*/reporting/ in the copy is compared against the
same path in the repo, and assigned the FIRST level at which the two become equal:

    identical      byte-identical - no normalisation needed
    formatting     same JSON, different serialisation (indentation, key order, trailing newline)
    outputs        differ only in cell outputs and execution counts
    metadata       differ also in notebook- or cell-level metadata (kernel, widgets, ids, ...)
    source         the code or markdown itself differs - a real change, never noise
    not_in_repo    no notebook at that path in the repo (a variant, a scratch notebook, ...)

`formatting` + `outputs` + `metadata` is the drift a normalising hash would absorb. `source` is
drift no normalisation should absorb. Note the repo copy is whatever is checked out, so a workspace
that is several months behind shows `source` for notebooks that were changed since, which is
version lag, not noise - the point is how much sits in the three middle levels.
"""

import argparse
import copy
import json
import sys
from collections import Counter
from pathlib import Path

LEVELS = ["identical", "formatting", "outputs", "metadata", "source", "not_in_repo", "unparseable"]


def strip_outputs(nb: dict) -> dict:
    """Return a copy with every cell's outputs and execution count removed.

    Returns
    -------
    dict
        The stripped notebook.
    """
    nb = copy.deepcopy(nb)
    for cell in nb.get("cells", []):
        cell.pop("outputs", None)
        cell.pop("execution_count", None)
    return nb


def strip_metadata(nb: dict) -> dict:
    """Return a copy with outputs and all metadata removed, plus cell ids and nbformat_minor.

    Returns
    -------
    dict
        The stripped notebook.
    """
    nb = strip_outputs(nb)
    nb.pop("metadata", None)
    nb.pop("nbformat_minor", None)
    for cell in nb.get("cells", []):
        cell.pop("metadata", None)
        cell.pop("id", None)
        cell.pop("attachments", None)
    return nb


def sources(nb: dict) -> list:
    """Reduce a notebook to its cells' type and source text.

    Returns
    -------
    list
        [(cell_type, source), ...].
    """
    out = []
    for cell in nb.get("cells", []):
        src = cell.get("source", "")
        out.append((cell.get("cell_type"), "".join(src) if isinstance(src, list) else src))
    return out


def differing_keys(a: dict, b: dict) -> list[str]:
    """Name what differs between two notebooks, coarsely, for the per-file detail.

    Returns
    -------
    list[str]
        Short labels such as "metadata.kernelspec" or "cell.outputs".
    """
    keys = set()
    for key in set(a.get("metadata", {})) | set(b.get("metadata", {})):
        if a.get("metadata", {}).get(key) != b.get("metadata", {}).get(key):
            keys.add(f"metadata.{key}")
    if a.get("nbformat_minor") != b.get("nbformat_minor"):
        keys.add("nbformat_minor")
    for ca, cb in zip(a.get("cells", []), b.get("cells", []), strict=False):
        for key in set(ca) | set(cb):
            if ca.get(key) != cb.get(key):
                keys.add(f"cell.{key}")
    if len(a.get("cells", [])) != len(b.get("cells", [])):
        keys.add("cell count")
    return sorted(keys)


def compare(workspace_file: Path, repo_file: Path) -> tuple[str, list[str]]:
    """Find the first normalisation level at which two notebooks become equal.

    Returns
    -------
    tuple[str, list[str]]
        (level, differing keys).
    """
    if not repo_file.is_file():
        return "not_in_repo", []
    raw_w, raw_r = workspace_file.read_bytes(), repo_file.read_bytes()
    if raw_w == raw_r:
        return "identical", []
    try:
        nb_w, nb_r = json.loads(raw_w), json.loads(raw_r)
    except ValueError:
        return "unparseable", []
    diff = differing_keys(nb_w, nb_r)
    if nb_w == nb_r:
        return "formatting", diff
    if strip_outputs(nb_w) == strip_outputs(nb_r):
        return "outputs", diff
    if strip_metadata(nb_w) == strip_metadata(nb_r) or sources(nb_w) == sources(nb_r):
        return "metadata", diff
    return "source", diff


def main() -> int:
    """Run the comparison and print a summary.

    Returns
    -------
    int
        The process exit code.
    """
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("workspace_copy", type=Path)
    parser.add_argument(
        "repo_root", type=Path, nargs="?", default=Path(__file__).resolve().parents[2] / "snt_development"
    )
    parser.add_argument("--json", type=Path, help="also write per-file results here")
    args = parser.parse_args()

    notebooks = sorted(
        p
        for p in args.workspace_copy.glob("pipelines/*/*/*.ipynb")
        if p.parent.name in {"code", "reporting"} and ".ipynb_checkpoints" not in p.parts
    )
    if not notebooks:
        print(f"No notebooks under {args.workspace_copy}/pipelines/*/{{code,reporting}}/", file=sys.stderr)
        return 1

    results = []
    for nb in notebooks:
        rel = nb.relative_to(args.workspace_copy).as_posix()
        level, keys = compare(nb, args.repo_root / rel)
        results.append({"path": rel, "level": level, "differs_in": keys})

    counts = Counter(r["level"] for r in results)
    width = max(len(r["path"]) for r in results)
    for r in results:
        print(f"{r['path']:<{width}}  {r['level']:<12} {', '.join(r['differs_in'])}")
    print()
    print(f"{len(results)} notebook(s) compared against {args.repo_root}")
    for level in LEVELS:
        if counts[level]:
            print(f"  {level:<12} {counts[level]:>4}  ({counts[level] / len(results):.0%})")
    absorbable = counts["formatting"] + counts["outputs"] + counts["metadata"]
    share = absorbable / len(results)
    print(f"\nA normalising hash would absorb {absorbable} of {len(results)} ({share:.0%}).")

    if args.json:
        args.json.write_text(json.dumps({"counts": dict(counts), "files": results}, indent=2))
        print(f"Per-file results written to {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
