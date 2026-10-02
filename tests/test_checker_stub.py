"""Stubbed test of snt_workspace_checker phase-2 classification, mirroring the sandbox fixtures."""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "snt_workspace_checker"))
import pipeline as chk


class Log:
    def __getattr__(self, name):
        return lambda msg: print(f"  [{name}] {msg}")


chk.current_run = Log()

H = {
    k: chk.sha256_bytes(k.encode())
    for k in ["A", "B", "C", "S", "R", "F", "P", "X", "Y", "E", "E2", "Ar", "Br", "N"]
}


def manifest(files, pipelines):
    return {"files": {p: H[v] for p, v in files.items()}, "pipelines": pipelines}


inc = {"code": "snt-dhis2-incidence", "zip_files": ["pipeline.py", "fixture_reverted.py"]}
rem = {"code": "snt-fixture-pipeline-removed", "zip_files": ["pipeline.py"]}
add = {"code": "snt-fixture-pipeline-added", "zip_files": ["pipeline.py"]}
base = {"code/edited.r": "E", "code/fixture_stable.r": "S", "snt_dhis2_incidence/pipeline.py": "P"}

v010 = manifest(
    {
        **base,
        "code/fixture_changing.r": "A",
        "code/fixture_removed.r": "R",
        "snt_dhis2_incidence/fixture_reverted.py": "Ar",
        "snt_fixture_pipeline_removed/pipeline.py": "X",
    },
    {"snt_dhis2_incidence": inc, "snt_fixture_pipeline_removed": rem},
)
v021 = manifest(
    {
        **base,
        "code/fixture_changing.r": "B",
        "pipelines/snt_dhis2_incidence/utils/fixture_added.r": "F",
        "snt_dhis2_incidence/fixture_reverted.py": "Br",
        "snt_fixture_pipeline_added/pipeline.py": "Y",
    },
    {"snt_dhis2_incidence": inc, "snt_fixture_pipeline_added": add},
)
v030 = manifest(
    {
        **base,
        "code/fixture_changing.r": "C",
        "pipelines/snt_dhis2_incidence/utils/fixture_added.r": "F",
        "snt_dhis2_incidence/fixture_reverted.py": "Ar",
        "snt_fixture_pipeline_added/pipeline.py": "Y",
        "code/fixture_future.r": "N",
    },
    {"snt_dhis2_incidence": inc, "snt_fixture_pipeline_added": add},
)
releases = [
    {"tag_name": "v0.1.0-test", "published_at": "2026-09-21T15:20:00Z"},
    {"tag_name": "v0.2.0-test", "published_at": "2026-09-21T15:40:00Z"},
    {"tag_name": "v0.2.1-test", "published_at": "2026-09-21T16:00:00Z"},
    {"tag_name": "v0.3.0-test", "published_at": "2026-09-21T16:04:00Z"},
]
manifests = {"v0.1.0-test": v010, "v0.2.0-test": v010, "v0.2.1-test": v021, "v0.3.0-test": v030}
index = chk.build_index(manifests, releases, "v0.2.1-test")

# ---- filesystem: workspace deployed at v0.1.0-test, plus hand-made cases
root = Path(tempfile.mkdtemp())
files = {
    "code/edited.r": "E2",  # hand edit -> unknown_content
    "code/fixture_stable.r": "S",
    "code/fixture_changing.r": "A",
    "code/fixture_removed.r": "R",
    "code/fixture_future.r": "N",  # only in v0.3.0 -> added_after_target
    "scratch_notes.ipynb": "junk",
    "pipelines/x/code/.ipynb_checkpoints/n-checkpoint.ipynb": "junk",
    "pipelines/x/reporting/outputs/r.html": "junk",
    "pipelines/x/papermill_outputs/o.ipynb": "junk",
    "data/x.csv": "junk",
    "snt_status/status_latest.json": "{}",
    ".snt_release": "{}",
    "snt_dhis2_incidence/pipeline.py": "P",  # inert copy
}
for rel, content in files.items():
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(content.encode())

fs_entries, inert = chk.check_filesystem(root, index)
got = {e["path"]: (e["status"], e["matching_releases"], e["position"]) for e in fs_entries}
expected = {
    "code/edited.r": ("unknown_content", None, None),
    "code/fixture_stable.r": ("match", None, None),
    "code/fixture_changing.r": (
        "mismatch_known",
        [{"from": "v0.1.0-test", "to": "v0.2.0-test", "count": 2}],
        "older",
    ),
    "code/fixture_removed.r": (
        "removed_in_target",
        [{"from": "v0.1.0-test", "to": "v0.2.0-test", "count": 2}],
        "older",
    ),
    "code/fixture_future.r": (
        "added_after_target",
        [{"from": "v0.3.0-test", "to": "v0.3.0-test", "count": 1}],
        "newer",
    ),
    "scratch_notes.ipynb": ("untracked", None, None),
    "pipelines/snt_dhis2_incidence/utils/fixture_added.r": ("missing", None, None),
}
assert got == expected, got
assert inert == ["snt_dhis2_incidence/pipeline.py"], inert
print("filesystem OK")

# ---- zip side
e = chk.classify_zip_member("snt_dhis2_incidence", "fixture_reverted.py", H["Ar"], index)
assert (e["status"], e["position"]) == ("mismatch_known", "both"), e
assert e["matching_releases"] == [
    {"from": "v0.1.0-test", "to": "v0.2.0-test", "count": 2},
    {"from": "v0.3.0-test", "to": "v0.3.0-test", "count": 1},
], e
e = chk.classify_zip_member("snt_dhis2_incidence", "extra_helper.py", H["N"], index)
assert e["status"] == "untracked" and "deployed code" in e["remediation"], e
e = chk.classify_zip_member("snt_dhis2_incidence", "notes.json", H["N"], index)
assert e["status"] == "not_covered", e
e = chk.classify_zip_member("snt_dhis2_incidence", "workspace/x.py", H["N"], index)
assert e["status"] == "not_covered", e
e = chk.classify_zip_member("snt_fixture_pipeline_removed", "pipeline.py", H["X"], index)
assert e["status"] == "removed_in_target" and "Redeploy" in e["remediation"], e
e = chk.classify_zip_member("snt_fixture_pipeline_added", "pipeline.py", None, index)
assert e["status"] == "missing", e
print("zip OK")

# ---- version name vs content
members_010 = {"pipeline.py": H["P"], "fixture_reverted.py": H["Ar"]}
assert chk.name_matches_content("snt_dhis2_incidence", "v0.1.0-test [v1]", members_010, index) is True
assert chk.name_matches_content("snt_dhis2_incidence", "v0.2.1-test [v2]", members_010, index) is False
assert chk.name_matches_content("snt_dhis2_incidence", "v3", members_010, index) is None
extra = {**members_010, "stray.py": H["N"]}
assert chk.name_matches_content("snt_dhis2_incidence", "v0.1.0-test [v1]", extra, index) is False
assert (
    chk.name_matches_content("snt_fixture_pipeline_removed", "v0.2.1-test", {"pipeline.py": H["X"]}, index)
    is False
)
print("names OK")

# ---- unordered: a release with no timestamp
releases_u = [*releases, {"tag_name": "v9-nodate", "published_at": None}]
manifests_u = {**manifests, "v9-nodate": manifest({**base, "code/fixture_changing.r": "C"}, {})}
index_u = chk.build_index(manifests_u, sorted(releases_u, key=chk.release_sort_key), "v0.2.1-test")
assert index_u.ordered_tags[-1] == "v9-nodate"
r = chk.classify("code/fixture_changing.r", H["C"], index_u)
assert (r["status"], r["position"]) == ("mismatch_known", "unordered"), r
r = chk.classify("code/fixture_changing.r", H["A"], index_u)
assert r["position"] == "older", r
print("unordered OK")
# a manifest-less release breaks a span (D14)
rel_hole = [
    releases[0],
    {"tag_name": "v0.1.5-nomanifest", "published_at": "2026-09-21T15:30:00Z"},
    *releases[1:],
]
idx_h = chk.build_index(manifests, rel_hole, "v0.2.1-test")
r = chk.classify("code/fixture_changing.r", H["A"], idx_h)
assert [s["count"] for s in r["matching_releases"]] == [1, 1], r
print("spans OK")


# ============================================================================================
# Phase 3
# ============================================================================================


def considered(rels, mans):
    return [
        {"tag": r["tag_name"], "published_at": r["published_at"], "manifest_available": r["tag_name"] in mans}
        for r in rels
    ]


# ---- target resolution (D17)
assert chk.resolve_target("none", "v0.1.0-test") == (None, "parameter")
assert chk.resolve_target(" NONE ", None) == (None, "parameter")
assert chk.resolve_target(None, None) == (None, "nothing_given")
assert chk.resolve_target("", "v0.1.0-test") == ("v0.1.0-test", "marker")
assert chk.resolve_target("v0.2.1-test", "v0.1.0-test") == ("v0.2.1-test", "parameter")
print("resolve OK")


# ---- verification mode also carries the per-release scores (section 4.2), from the phase-2 fs run.
# 5 files scored: edited(E2), stable, changing(A), removed(R), future(N); untracked scratch_notes
# and missing fixture_added are left out. Columns: shipped, present, agreeing, extra, agreement,
# completeness.
def scores(summary):
    return [
        (r["tag"], r["shipped"], r["present"], r["agreeing"], r["extra"], r["agreement"], r["completeness"])
        for r in summary["by_release"]
    ]


s = chk.attribution_summary(fs_entries, index, considered(releases, manifests))
assert s["files_scored"] == 5, s
assert scores(s) == [
    ("v0.1.0-test", 7, 4, 3, 1, 0.75, 0.5714),  # future.r is extra; edited.r disagrees
    ("v0.2.0-test", 7, 4, 3, 1, 0.75, 0.5714),
    ("v0.2.1-test", 7, 3, 1, 2, 0.3333, 0.4286),  # removed.r and future.r are extra
    ("v0.3.0-test", 8, 4, 2, 1, 0.5, 0.5),
], scores(s)
# v0.1.0 and v0.2.0 tie on agreement AND completeness -> the newest
assert s["best_fit"] == {"tag": "v0.2.0-test", "agreement": 0.75, "completeness": 0.5714}, s
assert s["matches_no_release"] == {"count": 1, "share": 0.2}, s
print("verification summary OK")

# ---- attribution mode. Mirrors the sandbox after snt_workspace_deployer ran at v0.3.0-test with
# pipeline deployment off: analytics at v0.3.0 (plus the leftover fixture_removed.r), pipeline
# zips still at v0.1.0, one hand edit. v0.4.0-test is the newest release and has no manifest.
releases_a = [*releases, {"tag_name": "v0.4.0-test", "published_at": "2026-09-21T16:10:00Z"}]
index_a = chk.build_index(manifests, releases_a, None)
assert index_a.target_files == {} and index_a.target_pipelines == {}

root_a = Path(tempfile.mkdtemp())
files_a = {
    "code/edited.r": "E2",
    "code/fixture_stable.r": "S",
    "code/fixture_changing.r": "C",
    "code/fixture_removed.r": "R",
    "pipelines/snt_dhis2_incidence/utils/fixture_added.r": "F",
    "scratch_notes.ipynb": "junk",
    "snt_dhis2_incidence/pipeline.py": "P",  # inert copy
}
for rel, content in files_a.items():
    p = root_a / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(content.encode())

fs_a, inert_a = chk.check_filesystem(root_a, index_a)
got = {e["path"]: (e["status"], e["matching_releases"], e["position"], e["target_sha256"]) for e in fs_a}


def span(a, b, n):
    return {"from": a, "to": b, "count": n}


expected = {
    "code/edited.r": ("unknown_content", None, None, None),
    # one span of 4: v0.4.0 has no manifest, but it is last, so it ends the span without breaking it
    "code/fixture_stable.r": ("attributed", [span("v0.1.0-test", "v0.3.0-test", 4)], None, None),
    "code/fixture_changing.r": ("attributed", [span("v0.3.0-test", "v0.3.0-test", 1)], None, None),
    "code/fixture_removed.r": ("attributed", [span("v0.1.0-test", "v0.2.0-test", 2)], None, None),
    "pipelines/snt_dhis2_incidence/utils/fixture_added.r": (
        "attributed",
        [span("v0.2.1-test", "v0.3.0-test", 2)],
        None,
        None,
    ),
    "scratch_notes.ipynb": ("untracked", None, None, None),
}
assert got == expected, got  # and no `missing` pass: nothing else appears
assert inert_a == ["snt_dhis2_incidence/pipeline.py"], inert_a
assert all(e["remediation"] is None for e in fs_a if e["status"] == "attributed")
print("attribution filesystem OK")

# ---- attribution, pipeline side: stub the API with zips as deployed at v0.1.0-test
import base64, io, zipfile  # ruff: ignore[multiple-imports-on-one-line, module-import-not-at-top-of-file]


def zipped(members):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, content in members.items():
            z.writestr(name, content)
    return base64.b64encode(buf.getvalue()).decode()


deployed = {
    "snt-dhis2-incidence": {
        "versionName": "v0.1.0-test [v1]",
        "zipfile": zipped({"pipeline.py": "P", "fixture_reverted.py": "Ar"}),
    },
    "snt-fixture-pipeline-removed": {
        "versionName": "v0.1.0-test [v1]",
        "zipfile": zipped({"pipeline.py": "X"}),
    },
}
chk.fetch_current_version = lambda token, code: deployed.get(code)
unknown_calls = []
chk.list_unknown_pipelines = lambda token, codes, has_target: (
    unknown_calls.append(has_target)
    or [
        chk.pipeline_report(
            None, "snt-workspace-checker", False if has_target else None, "v1", None, "not_in_any_release"
        )
    ],
    None,
)
zip_a, pipes_a, errs_a = chk.check_pipeline_versions(index_a, "token")
assert errs_a == [] and unknown_calls == [False], (errs_a, unknown_calls)
got = {e["path"]: (e["status"], e["matching_releases"]) for e in zip_a}
assert got == {
    "snt_dhis2_incidence/pipeline.py": ("attributed", [span("v0.1.0-test", "v0.3.0-test", 4)]),
    "snt_dhis2_incidence/fixture_reverted.py": (
        "attributed",
        [span("v0.1.0-test", "v0.2.0-test", 2), span("v0.3.0-test", "v0.3.0-test", 1)],
    ),
    "snt_fixture_pipeline_removed/pipeline.py": ("attributed", [span("v0.1.0-test", "v0.2.0-test", 2)]),
}, got
blocks = {b["code"]: b for b in pipes_a}
assert set(blocks) == {"snt-dhis2-incidence", "snt-fixture-pipeline-removed", "snt-workspace-checker"}, blocks
assert blocks["snt-dhis2-incidence"]["in_target"] is None
assert blocks["snt-dhis2-incidence"]["version_name_matches_content"] is True
assert blocks["snt-fixture-pipeline-removed"]["in_target"] is None
assert blocks["snt-fixture-pipeline-removed"]["remediation"] is None  # not "not in target"
print("attribution pipelines OK")


# ---- the exit criterion: a mixed workspace gives the right per-release breakdown (D15, revised).
# 8 files scored: edited(E2), stable, changing(C), removed(R), added(F), incidence pipeline.py,
# reverted(A), removed-pipeline(X). The leftovers removed.r and X are what the first D15 counted
# against v0.3.0, producing a three-way tie; judged on shipped paths only, they are extra for it,
# and v0.3.0 wins outright - the sandbox finding of 2026-09-29, in miniature.
s = chk.attribution_summary(fs_a + zip_a, index_a, considered(releases_a, manifests))
assert s["files_scored"] == 8, s
assert scores(s) == [
    ("v0.1.0-test", 7, 7, 5, 1, 0.7143, 1.0),  # added.r extra; edited, changing(C) disagree
    ("v0.2.0-test", 7, 7, 5, 1, 0.7143, 1.0),
    ("v0.2.1-test", 7, 6, 3, 2, 0.5, 0.8571),  # removed.r, X extra; added pipeline not deployed
    ("v0.3.0-test", 8, 6, 5, 2, 0.8333, 0.75),  # only edited.r disagrees
    ("v0.4.0-test", None, None, None, None, None, None),
], scores(s)
assert s["best_fit"] == {"tag": "v0.3.0-test", "agreement": 0.8333, "completeness": 0.75}, s
assert s["matches_no_release"] == {"count": 1, "share": 0.125}, s
assert s["unreadable"] == {"count": 0, "share": 0.0}, s
# an empty workspace: nothing to score, no best fit, no division by zero
e = chk.attribution_summary([], index_a, considered(releases_a, manifests))
assert e["files_scored"] == 0 and e["best_fit"] is None, e
assert scores(e)[0] == ("v0.1.0-test", 7, 0, 0, 0, None, 0.0), scores(e)
assert e["matches_no_release"]["share"] is None, e
# agreement ties are broken by completeness before recency: a release that agrees just as well
# but is less present here loses to an older, complete one
row_old = {"tag": "old", "published_at": None, "manifest_available": True}
row_new = {"tag": "new", "published_at": None, "manifest_available": True}
idx_t = chk.ReleaseIndex(
    None,
    {},
    ["old", "new"],
    ["old", "new"],
    {"a": {"old": H["A"], "new": H["A"]}, "b": {"old": H["B"]}},
    {},
    {
        "old": {"files": {"a": H["A"], "b": H["B"]}, "pipelines": {}},
        "new": {"files": {"a": H["A"], "c": H["C"]}, "pipelines": {}},
    },
)
ents = [
    {"path": "a", "status": "attributed", "observed_sha256": H["A"]},
    {"path": "b", "status": "attributed", "observed_sha256": H["B"]},
]
t = chk.attribution_summary(ents, idx_t, [row_old, row_new])
assert t["best_fit"] == {"tag": "old", "agreement": 1.0, "completeness": 1.0}, t  # new: 1.0 / 0.5
print("attribution summary OK")
print("ALL PASS")


# ============================================================================================
# Phase 4: reports built by build_report() conform to the frozen schema
# ============================================================================================
import json  # ruff: ignore[module-import-not-at-top-of-file]
from types import SimpleNamespace  # ruff: ignore[module-import-not-at-top-of-file]

from jsonschema import Draft202012Validator, FormatChecker  # ruff: ignore[module-import-not-at-top-of-file]

schema = json.loads(
    (Path(__file__).resolve().parents[1] / "docs/contracts/status_report.schema.json").read_text()
)
Draft202012Validator.check_schema(schema)
validator = Draft202012Validator(schema, format_checker=FormatChecker())
chk.workspace = SimpleNamespace(slug="stub-workspace")


def assert_conforms(report, label):
    errs = list(validator.iter_errors(report))
    assert not errs, (label, [(list(e.path), e.message[:100]) for e in errs[:5]])
    # every key written must be declared, and every declared key written (no drift either way)
    assert set(report) == set(schema["properties"]), (label, set(report) ^ set(schema["properties"]))


# attribution mode, from the objects built above
rep_a = chk.build_report(
    fs_a + zip_a,
    index_a,
    pipes_a,
    inert_a,
    errs_a,
    "o/r",
    None,
    considered(releases_a, manifests),
    "parameter",
    "v0.3.0-test",
)
assert rep_a["mode"] == "attribution" and rep_a["snt_workspace_checker_version"] is None
assert_conforms(rep_a, "attribution")

# verification mode: target v0.2.1-test, one pipeline unreadable, so `unreadable` and `errors` appear
index_v = chk.build_index(manifests, releases, "v0.2.1-test")


def flaky(token, code):
    if code == "snt-fixture-pipeline-added":
        raise RuntimeError("stub API failure")
    return deployed.get(code)


chk.fetch_current_version = flaky
zip_v, pipes_v, errs_v = chk.check_pipeline_versions(index_v, "token")
assert any(e["status"] == "unreadable" for e in zip_v) and errs_v, (zip_v, errs_v)
rep_v = chk.build_report(
    fs_entries + zip_v,
    index_v,
    pipes_v,
    inert,
    errs_v,
    "o/r",
    {"tag_name": "v0.2.1-test", "published_at": "2026-09-21T16:00:00Z"},
    considered(releases, manifests),
    "marker",
    None,
)
assert rep_v["mode"] == "verification" and rep_v["incomplete"] is True
assert {e["position"] for e in rep_v["entries"]} >= {"older", "newer"}
assert_conforms(rep_v, "verification")

# a JSON round trip (what is written to disk) must conform too
assert_conforms(json.loads(json.dumps(rep_v)), "verification, round-tripped")
print("schema conformance OK")
print("ALL PASS (phase 4)")


def test_stub_assertions_hold() -> None:
    """Give pytest a test to collect: every assertion above runs at import, so reaching here means all held."""
