"""Check what is actually deployed in this OpenHEXA workspace against the SNT release manifests.

Read-only. This pipeline writes its own report and nothing else - it never deletes, moves,
overwrites, deploys or archives. `snt_workspace_deployer` is the only component that changes
workspace state (docs/wip/PRODUCT_SPEC.md section 5.4).

Phase 4 of the build plan (PRODUCT_SPEC.md section 6). Two modes (decision D11):

    verification  a target release is given: every file gets a verdict relative to it, judged
                  in the light of EVERY release's manifest, with the full status taxonomy
    attribution   no target: every file is attributed to the releases its bytes match, and the
                  workspace gets a per-release coverage score (decision D15)

Both modes carry the coverage summary; attribution mode is verification without the verdicts.

Every release's manifest is fetched on each run. The release list is ONE GitHub API request
(per_page=100), and the manifests are downloaded from `browser_download_url` on github.com,
not from api.github.com - measured 2026-09-29 not to be charged per download, so a run costs
about one API request whatever the release count (PRODUCT_SPEC.md section 7.2, closed).

Two sources are hashed, and each tracked path is routed to exactly ONE of them:

    filesystem        files under workspace.files_path, at their repository-relative paths
    pipeline_version  the files inside each pipeline's CURRENT registered version zip

Anything under a pipeline directory is read from the zip only. A copy of such a file on the
workspace filesystem is inert - OpenHEXA runs pipelines from the registered version, never
from the bucket - so it is listed in `inert_filesystem_copies` and not classified: treating
it as evidence would report a file as fine on the strength of bytes that never execute.

Credentials: none. A run's own HEXA_TOKEN reads `currentVersion.zipfile` in full, established
by the snt-token-probe run of 2026-09-22 (docs/wip/HISTORY.md section 2.4). The checker can
therefore run unattended in a country workspace holding no connection at all.
"""

import base64
import hashlib
import io
import json
import os
import re
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

import requests
from openhexa.sdk import current_run, parameter, pipeline, workspace

# Bumped only when the report shape changes in a way a consumer must notice. v1 was frozen at
# phase 4; the contract is docs/wip/docs/status_report.schema.json.
SCHEMA_VERSION = 1

REPORT_DIR_NAME = "snt_status"
LATEST_REPORT_NAME = "status_latest.json"
RELEASE_MARKER_NAME = ".snt_release"
MANIFEST_ASSET_NAME = "release_manifest.json"

# The reserved `release_tag` value that forces attribution mode even when .snt_release exists
# (decision D17). Without it, attribution mode would be unreachable once snt_workspace_deployer
# has run, short of deleting the marker by hand. Compared case-insensitively.
NO_TARGET_TAG = "none"

GITHUB_HEADERS = {"User-Agent": "snt-workspace-checker"}
GITHUB_PAGE_SIZE = 100

# The OpenHEXA SDK's zip rule, mirrored from generate_zip_file() in openhexa/cli/api.py - the
# same constants the manifest generator mirrors (release_strategy.md, "Manifest generation").
# Used to tell the two kinds of undescribed zip member apart: one the generator WOULD have
# hashed had it ever been in the repository is `untracked`; one it could never see is
# `not_covered`, the tripwire for the generator falling behind the SDK.
ZIPPED_SUFFIXES = {".py", ".ipynb", ".txt", ".md", ".r", ".sql"}
ZIP_EXCLUDED_SUBTREE = "workspace"

# Left out of the filesystem walk so the `untracked` bucket stays readable (PRODUCT_SPEC.md
# section 5.3). Recorded verbatim in the report, so a reader can see what was never looked at.
SCAN_EXCLUDED_TOP_LEVEL = {"archive", "data", "configuration", REPORT_DIR_NAME}
SCAN_EXCLUDED_ANY_DEPTH = {"papermill_outputs"}
SCAN_EXCLUDED_SUBPATHS = {("reporting", "outputs")}
SCAN_EXCLUDED_ROOT_FILES = {RELEASE_MARKER_NAME}
# Dot-directories (.ipynb_checkpoints, .git, .cache, ...) are editor and tool state. Jupyter
# alone drops a checkpoint copy of every notebook it saves, which would double the bucket.
SCAN_EXCLUDES_DOT_DIRECTORIES = True

# OpenHEXA does not return a version's name as it was submitted: it appends the version
# number, so a version deployed as "v0.1.0-test" reads back as "v0.1.0-test [v1]". Observed
# in the first real run, 2026-09-22, where an exact comparison silently disabled the whole
# name-versus-content check (HISTORY.md section 1). The tag is recovered by stripping the
# suffix; a version whose name never carried a tag (a plain CLI push reads back as "v3")
# simply fails to match any tag, which is the right answer.
VERSION_NUMBER_SUFFIX = re.compile(r"\s*\[v\d+\]\s*$")

# Human-readable display text, never parsed by a consumer (PRODUCT_SPEC.md section 5.5).
REMEDIATION = {
    "match": None,
    "attributed": None,
    "mismatch_known": (
        "This copy is another release's version of the file - see matching_releases and position. "
        "Run snt_workspace_deployer at the target release to bring it into line (the current copy is "
        "archived first), or keep it deliberately."
    ),
    "unknown_content": (
        "This copy is not any version a release ever shipped - it was edited in place, or it is "
        "corrupt. Run snt_workspace_deployer at the target release (or, with no target, at the release "
        "you want) to restore it; the current copy is archived first."
    ),
    "missing": (
        "The target release ships this file and the workspace does not have it. Run "
        "snt_workspace_deployer at the target release to install it."
    ),
    "removed_in_target": (
        "An earlier release shipped this file and the target release no longer does. "
        "snt_workspace_deployer does not remove files, so it stays where it is; move it to archive/ "
        "by hand if it is no longer wanted."
    ),
    "added_after_target": (
        "Only releases newer than the target ship this file, so the workspace is ahead of the "
        "target here. Nothing to do unless you meant to go back to the target; "
        "snt_workspace_deployer does not remove files."
    ),
    "untracked": (
        "No release has ever shipped a file at this path. Reported for completeness only; the "
        "release process has no opinion about it."
    ),
    "not_covered": (
        "This file is inside a deployed pipeline zip, but its type is one the release manifest "
        "generator does not hash. The generator has fallen behind the OpenHEXA SDK's zip rule - "
        "fix the generator (release_strategy.md, 'Manifest generation')."
    ),
    "unreadable": (
        "The file could not be read, so nothing is known about its contents. Check permissions "
        "on the filesystem, or the API error recorded in this report's `errors` list."
    ),
}

# Where the source changes what the reader should do. A file inside a deployed zip is code
# that runs, not an inert stray - the sharper case of PRODUCT_SPEC.md section 5.3.
REMEDIATION_IN_ZIP = {
    "untracked": (
        "This file ships inside the deployed pipeline version, yet no release has ever contained "
        "it: the version was pushed from a tree carrying extra files. It is deployed code, not an "
        "inert stray. Redeploy the pipeline from a release with snt_workspace_deployer to replace it."
    ),
    "removed_in_target": (
        "The deployed pipeline version still carries a file the target release no longer ships. "
        "Redeploy the pipeline at the target release with snt_workspace_deployer to drop it."
    ),
}

PIPELINE_REMEDIATION = {
    "not_deployed": (
        "The target release ships this pipeline and the workspace has no version of it. Run "
        "snt_workspace_deployer at the target release to deploy it."
    ),
    "not_in_target": (
        "This pipeline is not part of the target release. A pipeline cannot delete another "
        "pipeline in OpenHEXA, so it is left in place (PRODUCT_SPEC.md section 7.5); delete it by "
        "hand in the OpenHEXA UI if it is no longer wanted."
    ),
    "unreadable": (
        "This pipeline's current version could not be read; see this report's `errors` list. "
        "Nothing is known about what it contains."
    ),
    "not_in_any_release": (
        "No release describes this pipeline. Reported for completeness only; its contents are not read."
    ),
}


@pipeline("snt_workspace_checker")
@parameter(
    "github_repo",
    name="GitHub repository",
    help="owner/repo holding the releases to check against (e.g. BLSQ/snt_development_sandbox)",
    type=str,
    default="BLSQ/snt_development_sandbox",
    required=True,
)
@parameter(
    "release_tag",
    name="Target release tag",
    help=(
        "Release to check this workspace against (e.g. v0.2.1-test). Leave empty to use the tag "
        "recorded in .snt_release by the last snt_workspace_deployer run; with neither, the "
        "workspace is attributed to releases without a target. Enter 'none' to force that "
        "attribution mode even when .snt_release exists."
    ),
    type=str,
    default=None,
    required=False,
)
def snt_workspace_checker(github_repo: str, release_tag: str | None) -> None:
    """Hash this workspace against every release manifest and write a status report.

    Orchestration only: resolves the target release, loads every manifest, delegates the
    hashing of each source to a helper, and writes the report.
    """
    snt_root_path = Path(workspace.files_path)

    declared_tag = read_release_marker(snt_root_path)
    target_tag, resolved_from = resolve_target(release_tag, declared_tag)
    if target_tag is None:
        current_run.log_info(
            f"Attribution mode: assessing workspace '{workspace.slug}' against every release of "
            f"{github_repo}, with no target ({describe_no_target(resolved_from)})."
        )
        if declared_tag:
            current_run.log_info(
                f"The workspace declares release '{declared_tag}'; it is not used as a target in "
                "this mode, and the coverage summary shows how far the files bear it out."
            )
    else:
        current_run.log_info(
            f"Verification mode: checking workspace '{workspace.slug}' against "
            f"{github_repo}@{target_tag} (target resolved from the {resolved_from})."
        )
        if declared_tag and declared_tag != target_tag:
            current_run.log_warning(
                f"The workspace declares release '{declared_tag}' but is being checked against "
                f"'{target_tag}'. The declaration records intent, not verified fact."
            )

    releases = list_releases(github_repo)
    manifests, releases_considered, errors = load_manifests(releases)
    target_release = require_target(target_tag, releases, manifests, errors) if target_tag else None
    index = build_index(manifests, releases, target_tag)
    target_part = (
        f"Release {target_tag} tracks {len(index.target_files)} file(s) across "
        f"{len(index.target_pipelines)} pipeline(s); "
        if target_tag
        else ""
    )
    current_run.log_info(
        f"{target_part}{len(manifests)} of {len(releases)} release(s) have a usable manifest, "
        f"together tracking {len(index.paths)} distinct path(s)."
    )

    fs_entries, inert_copies = check_filesystem(snt_root_path, index)

    token = get_run_token()
    zip_entries, pipeline_reports, zip_errors = check_pipeline_versions(index, token)
    errors.extend(zip_errors)

    report = build_report(
        entries=fs_entries + zip_entries,
        index=index,
        pipeline_reports=pipeline_reports,
        inert_copies=inert_copies,
        errors=errors,
        github_repo=github_repo,
        release=target_release,
        releases_considered=releases_considered,
        resolved_from=resolved_from,
        declared_tag=declared_tag,
    )
    report_path = write_report(snt_root_path, report)

    log_summary(report, report_path)


# --------------------------------------------------------------------------------------------
# Target resolution
# --------------------------------------------------------------------------------------------


def resolve_target(release_tag: str | None, declared_tag: str | None) -> tuple[str | None, str]:
    """Decide which release this run checks against - if any - and record where that came from.

    The order is fixed by PRODUCT_SPEC.md section 4.2: the parameter, then the .snt_release
    marker, then no target at all, which is attribution mode. The reserved parameter value
    `none` (decision D17) selects attribution mode outright, ahead of the marker, so a
    scheduled attribution check never needs the marker deleted first.

    Returns
    -------
    tuple[str | None, str]
        (the target release tag, or None in attribution mode; where that was resolved from:
        "parameter", "marker", or "nothing_given" when neither named a target).
    """
    if release_tag and release_tag.strip():
        if release_tag.strip().lower() == NO_TARGET_TAG:
            return None, "parameter"
        return release_tag.strip(), "parameter"
    if declared_tag:
        return declared_tag, "marker"
    return None, "nothing_given"


def describe_no_target(resolved_from: str) -> str:
    """Say in words why a run has no target release, for the log.

    Returns
    -------
    str
        A short explanation.
    """
    if resolved_from == "parameter":
        return f"the 'Target release tag' parameter is '{NO_TARGET_TAG}'"
    return f"the 'Target release tag' parameter is empty and there is no {RELEASE_MARKER_NAME} marker"


def read_release_marker(snt_root_path: Path) -> str | None:
    """Read the release tag the last snt_workspace_deployer run claims to have deployed.

    A workspace with no marker is the normal starting state for every country workspace
    today, so its absence is reported, never treated as an error.

    Returns
    -------
    str | None
        The declared release tag, or None if there is no readable marker.
    """
    marker_path = snt_root_path / RELEASE_MARKER_NAME
    if not marker_path.exists():
        current_run.log_info(f"No {RELEASE_MARKER_NAME} marker - this workspace declares no release.")
        return None

    try:
        declared = json.loads(marker_path.read_text())["snt_release"]
    except (OSError, ValueError, KeyError) as exception:
        current_run.log_warning(
            f"[WARNING] {RELEASE_MARKER_NAME} exists but could not be read: {exception}. "
            "Treating the workspace as declaring no release."
        )
        return None

    current_run.log_info(f"Workspace declares release '{declared}' in {RELEASE_MARKER_NAME}.")
    return declared


def get_run_token() -> str:
    """Read this run's own OpenHEXA API token from the environment.

    Deployment needs a workspace-scoped token from a connection, but reading does not: a
    run's own HEXA_TOKEN returns `currentVersion.zipfile` in full (HISTORY.md section 2.4).
    Keeping the checker credential-free is what lets it run unattended anywhere.

    Returns
    -------
    str
        The bearer token for the OpenHEXA GraphQL API.
    """
    token = os.environ.get("HEXA_TOKEN")
    if not token:
        raise RuntimeError(
            "HEXA_TOKEN is not set in this run's environment, so the pipeline version zips "
            "cannot be read. This variable is provided by the OpenHEXA runner."
        )
    return token


# --------------------------------------------------------------------------------------------
# Releases and manifests
# --------------------------------------------------------------------------------------------


def parse_timestamp(value: str | None) -> datetime | None:
    """Parse a GitHub ISO-8601 timestamp, returning None rather than guessing on bad input.

    A release with no usable timestamp is reported `unordered`, never placed by a guess
    (PRODUCT_SPEC.md section 5.2).

    Returns
    -------
    datetime | None
        The timezone-aware timestamp, or None if it is absent or malformed.
    """
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def release_sort_key(release: dict) -> tuple:
    """Order releases by `published_at` (decision D4), untimestamped ones last, tag as tie-break.

    Returns
    -------
    tuple
        A sort key: (has no timestamp, timestamp, tag).
    """
    published = parse_timestamp(release.get("published_at"))
    return (published is None, published or datetime.min.replace(tzinfo=UTC), release["tag_name"])


def list_releases(github_repo: str) -> list[dict]:
    """List every published release of the repository, oldest first.

    One page of 100 is one GitHub API request, so for any realistic release count this is a
    single call - the same cost as phase 1's single `releases/tags/<tag>` lookup. Drafts are
    skipped: they are not releases anyone can deploy.

    Returns
    -------
    list[dict]
        The GitHub API release objects, sorted by `published_at`.
    """
    releases: list[dict] = []
    page = 0
    remaining = None
    while True:
        page += 1
        response = requests.get(
            f"https://api.github.com/repos/{github_repo}/releases",
            params={"per_page": GITHUB_PAGE_SIZE, "page": page},
            headers=GITHUB_HEADERS,
            timeout=30,
        )
        if response.status_code == 404:
            raise ValueError(f"Repository '{github_repo}' not found, or it is not public.")
        response.raise_for_status()
        remaining = response.headers.get("X-RateLimit-Remaining")
        batch = response.json()
        releases.extend(batch)
        if len(batch) < GITHUB_PAGE_SIZE:
            break

    published = [release for release in releases if not release.get("draft")]
    # Logged because it is the live evidence for PRODUCT_SPEC.md section 7.2.
    current_run.log_info(
        f"Listed {len(published)} release(s) of {github_repo} in {page} GitHub API request(s); "
        f"unauthenticated rate limit remaining this hour: {remaining}."
    )
    return sorted(published, key=release_sort_key)


def download_manifest(release: dict) -> dict | None:
    """Download and parse release_manifest.json from a release's assets.

    Adapted from snt_workspace_deployer: pipelines are deployed as independent zips and cannot
    share a module, so the two carry the same helper by design. Unlike the deployer's, this
    one returns None for a release with no manifest asset, because here that is a finding
    about one release among many rather than a reason to stop.

    Returns
    -------
    dict | None
        The parsed manifest, or None if the release carries no manifest asset.
    """
    asset = next((a for a in release["assets"] if a["name"] == MANIFEST_ASSET_NAME), None)
    if asset is None:
        return None
    response = requests.get(asset["browser_download_url"], headers=GITHUB_HEADERS, timeout=30)
    response.raise_for_status()
    return response.json()


def load_manifests(releases: list[dict]) -> tuple[dict, list[dict], list[dict]]:
    """Download every release's manifest, recording which ones could not be used and why.

    A release whose manifest is absent or unusable makes the report incomplete: a file that
    only that release shipped would otherwise be mislabelled `untracked` or `unknown_content`
    without anything saying so (PRODUCT_SPEC.md sections 5.4 and 5.5).

    A manifest with no `pipelines` block predates the phase-0 generator. snt_workspace_deployer
    carries a fallback for that; this checker deliberately does not (PRODUCT_SPEC.md section
    2.1), so such a manifest is treated as unusable rather than half-read.

    Returns
    -------
    tuple[dict, list[dict], list[dict]]
        ({tag: manifest} for the usable ones, the `releases_considered` list, errors).
    """
    manifests: dict[str, dict] = {}
    considered: list[dict] = []
    errors: list[dict] = []

    for release in releases:
        tag = release["tag_name"]
        problem = None
        try:
            manifest = download_manifest(release)
        except (requests.RequestException, ValueError) as exception:
            manifest = None
            problem = f"its {MANIFEST_ASSET_NAME} asset could not be downloaded: {exception}"
        else:
            if manifest is None:
                problem = f"it has no {MANIFEST_ASSET_NAME} asset"
            elif not manifest.get("pipelines"):
                manifest = None
                problem = "its manifest has no 'pipelines' block (it predates the phase-0 generator)"

        if manifest is not None:
            manifests[tag] = manifest
        else:
            message = (
                f"Release {tag}: {problem}, so it cannot be compared against. Files only it shipped "
                "may be reported as untracked or unknown_content."
            )
            current_run.log_warning(f"[WARNING] {message}")
            errors.append({"scope": f"manifest:{tag}", "message": message})

        considered.append(
            {
                "tag": tag,
                "published_at": release.get("published_at"),
                "manifest_available": manifest is not None,
            }
        )

    # Logged because it is the live evidence for PRODUCT_SPEC.md section 7.2: equal to the
    # figure logged after the release list means asset downloads cost no API budget.
    current_run.log_info(
        f"After downloading {len(manifests)} manifest(s): unauthenticated rate limit remaining this "
        f"hour: {github_rate_limit_remaining()}. Equal to the figure after the release list means "
        "manifest downloads are outside the GitHub API budget."
    )
    return manifests, considered, errors


def github_rate_limit_remaining() -> str | None:
    """Read this runner's remaining GitHub core API budget, without spending any of it.

    GitHub documents that calling `/rate_limit` does not count against the limit, so this
    measures what the calls before it cost.

    Returns
    -------
    str | None
        The remaining core requests this hour, or None if it could not be read.
    """
    try:
        response = requests.get("https://api.github.com/rate_limit", headers=GITHUB_HEADERS, timeout=30)
        response.raise_for_status()
        return str(response.json()["resources"]["core"]["remaining"])
    except (requests.RequestException, ValueError, KeyError):
        return None


def require_target(target_tag: str, releases: list[dict], manifests: dict, errors: list[dict]) -> dict:
    """Return the target release, stopping if it does not exist or cannot be checked against.

    Returns
    -------
    dict
        The GitHub API release object of the target.
    """
    release = next((r for r in releases if r["tag_name"] == target_tag), None)
    if release is None:
        known = ", ".join(r["tag_name"] for r in releases) or "none"
        raise ValueError(f"Release '{target_tag}' not found. Published releases: {known}.")
    if target_tag not in manifests:
        reason = next((e["message"] for e in errors if e["scope"] == f"manifest:{target_tag}"), "")
        raise ValueError(
            f"The target release {target_tag} has no usable manifest, so there is nothing to check "
            f"against. {reason} Pick another tag."
        )
    return release


@dataclass
class ReleaseIndex:
    """Every usable manifest, re-keyed by path, plus what is needed to order releases.

    Attributes
    ----------
    target_tag : str | None
        The release being checked against, or None in attribution mode.
    published_at : dict
        {tag: timezone-aware datetime or None}, for every release with a usable manifest.
    ordered_tags : list
        The tags of the usable manifests, in `published_at` order.
    all_tags : list
        Every release's tag in `published_at` order, including releases with no usable
        manifest - which is what lets such a release break an attribution span.
    paths : dict
        {repository path: {tag: sha256}} across every usable manifest.
    pipeline_codes : dict
        {pipeline directory: OpenHEXA code} across every usable manifest.
    manifests : dict
        {tag: manifest}.
    """

    target_tag: str | None
    published_at: dict
    ordered_tags: list
    all_tags: list
    paths: dict
    pipeline_codes: dict
    manifests: dict

    @property
    def target_files(self) -> dict:
        """The target manifest's `files`, {repository path: sha256}; empty with no target.

        Returns
        -------
        dict
            The target's tracked files.
        """
        return self.manifests[self.target_tag]["files"] if self.target_tag else {}

    @property
    def target_pipelines(self) -> dict:
        """The target manifest's `pipelines` block; empty with no target.

        Returns
        -------
        dict
            {pipeline directory: {"code": ..., "zip_files": [...]}}.
        """
        return self.manifests[self.target_tag]["pipelines"] if self.target_tag else {}

    def releases_holding(self, rel_path: str, observed: str | None) -> list[str]:
        """List the releases whose manifest holds exactly these bytes at this path.

        Returns
        -------
        list[str]
            Tags in `published_at` order; empty for an absent file or an unknown path.
        """
        by_tag = self.paths.get(rel_path)
        if observed is None or by_tag is None:
            return []
        return [tag for tag in self.ordered_tags if by_tag.get(tag) == observed]


def build_index(manifests: dict, releases: list[dict], target_tag: str | None) -> ReleaseIndex:
    """Re-key the usable manifests by path, so each observed file costs one lookup.

    Returns
    -------
    ReleaseIndex
        The index every classification in this run is made against.
    """
    ordered_tags = [r["tag_name"] for r in releases if r["tag_name"] in manifests]
    published_at = {
        r["tag_name"]: parse_timestamp(r.get("published_at")) for r in releases if r["tag_name"] in manifests
    }

    paths: dict[str, dict[str, str]] = {}
    pipeline_codes: dict[str, str] = {}
    for tag in ordered_tags:
        for rel_path, checksum in manifests[tag]["files"].items():
            paths.setdefault(rel_path, {})[tag] = checksum
        for dir_name, spec in manifests[tag]["pipelines"].items():
            pipeline_codes[dir_name] = spec["code"]

    return ReleaseIndex(
        target_tag=target_tag,
        published_at=published_at,
        ordered_tags=ordered_tags,
        all_tags=[r["tag_name"] for r in releases],
        paths=paths,
        pipeline_codes=pipeline_codes,
        manifests=manifests,
    )


# --------------------------------------------------------------------------------------------
# Classification
# --------------------------------------------------------------------------------------------


def position_of(tags: list[str], index: ReleaseIndex) -> str:
    """Place a set of releases relative to the target, by `published_at` (PRODUCT_SPEC.md 5.2).

    `both` is the changed-then-reverted case: the content matches releases on either side of
    the target and not the target itself. A release with no usable timestamp - or one
    published at the very same instant as the target - makes the answer `unordered` rather
    than a guess.

    Returns
    -------
    str
        One of "older", "newer", "both", "unordered".
    """
    target_time = index.published_at.get(index.target_tag)
    times = [index.published_at.get(tag) for tag in tags]
    if target_time is None or any(time is None or time == target_time for time in times):
        return "unordered"

    older = any(time < target_time for time in times)
    newer = any(time > target_time for time in times)
    if older and newer:
        return "both"
    return "older" if older else "newer"


def classify(rel_path: str, observed: str | None, index: ReleaseIndex) -> dict:
    """Assign one status to a path, given the hash observed for it (None when it is absent).

    The path is asked about before the bytes (PRODUCT_SPEC.md section 5.1.1): a path no
    release ever shipped is `untracked` and its content is never compared at all.

    For a known path, `matching_releases` holds the releases other than the target whose
    manifest holds exactly the observed bytes at this path, as spans (see `to_spans`). It is
    carried only on entries that are not `match` - decision D14, section 7.8.

    With no target (attribution mode) there is nothing to be behind, ahead of, missing from
    or removed in, so a known path whose bytes match any release is `attributed` (decision
    D16), with spans over ALL its matching releases and no position.

    Returns
    -------
    dict
        {"status", "target_sha256", "matching_releases", "position"}.
    """
    by_tag = index.paths.get(rel_path)
    if by_tag is None:
        return verdict("untracked")

    if index.target_tag is None:
        matching = index.releases_holding(rel_path, observed)
        if matching:
            return verdict("attributed", None, to_spans(matching, index))
        return verdict("unknown_content")

    target_sha = by_tag.get(index.target_tag)
    if observed is None:
        return verdict("missing", target_sha)
    if observed == target_sha:
        return verdict("match", target_sha)

    matching = [tag for tag in index.releases_holding(rel_path, observed) if tag != index.target_tag]
    matching_position = position_of(matching, index) if matching else None
    spans = to_spans(matching, index) if matching else None

    if target_sha is not None:
        if matching:
            return verdict("mismatch_known", target_sha, spans, matching_position)
        return verdict("unknown_content", target_sha)

    # A path some release shipped and the target does not. Which side of the target the
    # shipping releases sit on decides removed vs ahead; the bytes are reported alongside,
    # and may match none of them if the copy was also edited.
    shipped_in = [tag for tag in index.ordered_tags if tag in by_tag]
    status = "added_after_target" if position_of(shipped_in, index) == "newer" else "removed_in_target"
    return verdict(status, None, spans, matching_position)


def to_spans(tags: list[str], index: ReleaseIndex) -> list[dict]:
    """Collapse a set of releases into contiguous spans, in `published_at` order.

    Decision D14 (PRODUCT_SPEC.md section 7.8): a file unchanged across many releases
    renders as one span rather than a list that grows with release history. A span is
    broken by any release outside the set - including the target itself, and including a
    release whose manifest could not be read, since nothing is known about its content. A
    single release is a span of one, so a consumer handles one shape only. Two spans for one
    file is the changed-then-reverted signal.

    Returns
    -------
    list[dict]
        [{"from": tag, "to": tag, "count": int}, ...], oldest span first.
    """
    wanted = set(tags)
    spans: list[dict] = []
    current: list[str] = []
    for tag in index.all_tags:
        if tag in wanted:
            current.append(tag)
            continue
        if current:
            spans.append({"from": current[0], "to": current[-1], "count": len(current)})
            current = []
    if current:
        spans.append({"from": current[0], "to": current[-1], "count": len(current)})
    return spans


def verdict(
    status: str,
    target_sha256: str | None = None,
    matching_releases: list[str] | None = None,
    position: str | None = None,
) -> dict:
    """Bundle the classification fields of one entry.

    Returns
    -------
    dict
        {"status", "target_sha256", "matching_releases", "position"}.
    """
    return {
        "status": status,
        "target_sha256": target_sha256,
        "matching_releases": matching_releases,
        "position": position,
    }


def make_entry(path: str, source: str, pipeline_name: str | None, observed: str | None, result: dict) -> dict:
    """Build one report entry. Fields are never omitted (PRODUCT_SPEC.md section 5.5).

    Returns
    -------
    dict
        One entry of the report's `entries` list.
    """
    status = result["status"]
    remediation = REMEDIATION[status]
    if source == "pipeline_version":
        remediation = REMEDIATION_IN_ZIP.get(status, remediation)
    return {
        "path": path,
        "source": source,
        "pipeline": pipeline_name,
        "status": status,
        "observed_sha256": observed,
        "target_sha256": result["target_sha256"],
        "matching_releases": result["matching_releases"],
        "position": result["position"],
        "remediation": remediation,
    }


def sha256_bytes(payload: bytes) -> str:
    """Hash raw bytes, with no normalisation of any kind.

    Notebooks are hashed as they are on purpose - decision D9, taken 2026-09-29 after
    measuring real drift: metadata-only noise was 2 of 31 notebooks, too little to justify a
    second hash definition kept in lock-step with the manifest generator.

    Returns
    -------
    str
        The lowercase hex sha256 digest.
    """
    return hashlib.sha256(payload).hexdigest()


# --------------------------------------------------------------------------------------------
# Filesystem source
# --------------------------------------------------------------------------------------------


def scan_exclusions() -> dict:
    """Describe what the filesystem walk skips, for the report.

    Returns
    -------
    dict
        The exclusion rules, as lists of strings.
    """
    return {
        "top_level_directories": sorted(SCAN_EXCLUDED_TOP_LEVEL),
        "directories_at_any_depth": sorted(SCAN_EXCLUDED_ANY_DEPTH)
        + (["any directory whose name starts with '.'"] if SCAN_EXCLUDES_DOT_DIRECTORIES else []),
        "subpaths": sorted("/".join(parts) for parts in SCAN_EXCLUDED_SUBPATHS),
        "root_files": sorted(SCAN_EXCLUDED_ROOT_FILES),
    }


def is_excluded_directory(rel_dir: PurePosixPath, name: str) -> bool:
    """Say whether the walk should skip a directory, given its parent's relative path.

    Returns
    -------
    bool
        True if the directory is excluded from the scan.
    """
    if not rel_dir.parts and name in SCAN_EXCLUDED_TOP_LEVEL:
        return True
    if name in SCAN_EXCLUDED_ANY_DEPTH:
        return True
    if SCAN_EXCLUDES_DOT_DIRECTORIES and name.startswith("."):
        return True
    return bool(rel_dir.parts) and (rel_dir.name, name) in SCAN_EXCLUDED_SUBPATHS


def walk_workspace(snt_root_path: Path) -> list[str]:
    """List every file under the workspace root that the scan covers, as repository paths.

    Returns
    -------
    list[str]
        Posix-style paths relative to the workspace root, sorted.
    """
    found = []
    for dir_path, dir_names, file_names in os.walk(snt_root_path):
        rel_dir = PurePosixPath(Path(dir_path).relative_to(snt_root_path).as_posix())
        if str(rel_dir) == ".":
            rel_dir = PurePosixPath()
        dir_names[:] = [name for name in dir_names if not is_excluded_directory(rel_dir, name)]
        for name in file_names:
            if not rel_dir.parts and name in SCAN_EXCLUDED_ROOT_FILES:
                continue
            found.append(str(rel_dir / name))
    return sorted(found)


def check_filesystem(snt_root_path: Path, index: ReleaseIndex) -> tuple[list[dict], list[str]]:
    """Walk the workspace filesystem and classify every file outside a pipeline directory.

    Files at paths no release ever shipped are reported `untracked` without being read -
    the content question is never asked for them. Files inside a pipeline directory never
    execute, so they are returned separately as inert copies and not classified. Tracked
    target paths the walk did not see are then checked directly, so a file the exclusions
    happened to hide is still found rather than reported `missing`. With no target there are
    no target paths, and absence means nothing, so that second pass is skipped.

    Returns
    -------
    tuple[list[dict], list[str]]
        (report entries, inert copies found inside pipeline directories).
    """
    pipeline_dirs = set(index.pipeline_codes)
    entries: list[dict] = []
    inert_copies: list[str] = []
    seen: set[str] = set()

    for rel_path in walk_workspace(snt_root_path):
        if PurePosixPath(rel_path).parts[0] in pipeline_dirs:
            inert_copies.append(rel_path)
            continue
        seen.add(rel_path)
        entries.append(classify_file(rel_path, snt_root_path / rel_path, index))

    if index.target_tag is None:
        return entries, inert_copies

    for rel_path in sorted(index.target_files):
        if rel_path in seen or PurePosixPath(rel_path).parts[0] in pipeline_dirs:
            continue
        file_path = snt_root_path / rel_path
        if file_path.is_file():
            entries.append(classify_file(rel_path, file_path, index))
        else:
            entries.append(make_entry(rel_path, "filesystem", None, None, classify(rel_path, None, index)))

    return entries, inert_copies


def classify_file(rel_path: str, file_path: Path, index: ReleaseIndex) -> dict:
    """Hash and classify one file found on the filesystem.

    Returns
    -------
    dict
        One report entry, sourced from `filesystem`.
    """
    if rel_path not in index.paths:
        return make_entry(rel_path, "filesystem", None, None, verdict("untracked"))

    try:
        observed = sha256_bytes(file_path.read_bytes())
    except OSError as exception:
        current_run.log_warning(f"[WARNING] Could not read {rel_path}: {exception}")
        target_sha = index.target_files.get(rel_path)
        return make_entry(rel_path, "filesystem", None, None, verdict("unreadable", target_sha))

    return make_entry(rel_path, "filesystem", None, observed, classify(rel_path, observed, index))


# --------------------------------------------------------------------------------------------
# Pipeline-version source
# --------------------------------------------------------------------------------------------


def check_pipeline_versions(index: ReleaseIndex, token: str) -> tuple[list[dict], list[dict], list[dict]]:
    """Hash the current registered version of every pipeline any release describes.

    Every pipeline directory in every usable manifest is probed, not only the target's: that
    is how a pipeline removed in the target, but still deployed, is found. The workspace's
    pipeline list is then read only to name the pipelines no release describes at all.

    Only the current version is read. Older versions are history - they are not what would
    run, and reporting on them would drown the report in files nobody can act on
    (PRODUCT_SPEC.md section 3.1).

    Returns
    -------
    tuple[list[dict], list[dict], list[dict]]
        (report entries, one per-pipeline block each, errors encountered).
    """
    entries: list[dict] = []
    pipeline_reports: list[dict] = []
    errors: list[dict] = []

    has_target = index.target_tag is not None
    for dir_name, code in sorted(index.pipeline_codes.items()):
        # None, not False, with no target: "not in the target" would be a verdict about a target
        # that does not exist.
        in_target = (dir_name in index.target_pipelines) if has_target else None
        expected_members = index.target_pipelines[dir_name]["zip_files"] if in_target else []

        try:
            version = fetch_current_version(token, code)
            members = read_zip_members(version["zipfile"]) if version else None
        except Exception as exception:  # one unreadable pipeline must not hide the others
            message = f"Could not read the current version of pipeline '{code}': {exception}"
            current_run.log_error(f"[ERROR] {message}")
            errors.append({"scope": f"pipeline_version:{code}", "message": message})
            for member in sorted(expected_members):
                target_sha = index.target_files.get(f"{dir_name}/{member}")
                entries.append(
                    make_entry(
                        f"{dir_name}/{member}",
                        "pipeline_version",
                        dir_name,
                        None,
                        verdict("unreadable", target_sha),
                    )
                )
            pipeline_reports.append(pipeline_report(dir_name, code, in_target, None, None, "unreadable"))
            continue

        if members is None:
            if in_target:
                current_run.log_warning(
                    f"[WARNING] {code}: the target release defines this pipeline but the workspace has "
                    "no deployed version of it. All of its files are reported missing."
                )
                for member in sorted(expected_members):
                    entries.append(classify_zip_member(dir_name, member, None, index))
                pipeline_reports.append(pipeline_report(dir_name, code, True, None, None, "not_deployed"))
            else:
                current_run.log_debug(f"{code}: not deployed, and no target expects it - nothing to do.")
            continue

        for member in sorted(set(expected_members) | set(members)):
            entries.append(classify_zip_member(dir_name, member, members.get(member), index))

        name = version["versionName"]
        if in_target is False:
            current_run.log_warning(
                f"[WARNING] {code}: deployed (current version '{name}') but not part of the target "
                "release. Left in place - a pipeline cannot delete another pipeline."
            )
        pipeline_reports.append(
            pipeline_report(
                dir_name,
                code,
                in_target,
                name,
                name_matches_content(dir_name, name, members, index),
                "not_in_target" if in_target is False else None,
            )
        )

    unknown, listing_error = list_unknown_pipelines(token, set(index.pipeline_codes.values()), has_target)
    if listing_error:
        errors.append(listing_error)
    pipeline_reports.extend(unknown)

    return entries, pipeline_reports, errors


def classify_zip_member(dir_name: str, member: str, observed: str | None, index: ReleaseIndex) -> dict:
    """Classify one file of a deployed pipeline zip, or one the target expects there.

    A member at a path no release ever shipped is split by the SDK's own zip rule: one the
    manifest generator would have hashed had it ever been in the repository is `untracked`
    - the sharper case of PRODUCT_SPEC.md section 5.3, since it ships and runs - while one
    the generator could never have seen is `not_covered`, the alarm for the generator's rule
    having fallen behind the SDK's.

    Returns
    -------
    dict
        One report entry, sourced from `pipeline_version`.
    """
    rel_path = f"{dir_name}/{member}"
    result = classify(rel_path, observed, index)
    if result["status"] == "untracked" and not zip_rule_covers(member):
        result = verdict("not_covered")
    return make_entry(rel_path, "pipeline_version", dir_name, observed, result)


def zip_rule_covers(member: str) -> bool:
    """Say whether the SDK's zip rule - and so the manifest generator - includes a zip member.

    Returns
    -------
    bool
        True if the generator would hash a file at this path inside a pipeline directory.
    """
    parts = PurePosixPath(member).parts
    return PurePosixPath(member).suffix.lower() in ZIPPED_SUFFIXES and parts[0] != ZIP_EXCLUDED_SUBTREE


def fetch_current_version(token: str, code: str) -> dict | None:
    """Read a pipeline's current registered version, including its zipped source.

    Returns
    -------
    dict | None
        {"versionNumber", "versionName", "zipfile"} for the current version, or None if the
        workspace has no pipeline with that code or no version registered against it.
    """
    data = call_graphql(
        token,
        "query ($slug: String!, $code: String!) { pipelineByCode(workspaceSlug: $slug, code: $code)"
        " { id code currentVersion { versionNumber versionName zipfile } } }",
        {"slug": workspace.slug, "code": code},
    )["pipelineByCode"]

    if data is None:
        return None
    return data["currentVersion"]


def list_unknown_pipelines(
    token: str, known_codes: set[str], has_target: bool
) -> tuple[list[dict], dict | None]:
    """Name the pipelines deployed in this workspace that no release describes.

    Their contents are not read: they are not the release's business. Before the checker
    ships in a release, it appears here itself (PRODUCT_SPEC.md section 6.2). A failure is an
    error that makes the report incomplete, but does not stop the run - every pipeline a
    release describes has already been checked by code. `in_target` is False when there is a
    target (no release describes them, so the target does not either) and None when there is
    none.

    Returns
    -------
    tuple[list[dict], dict | None]
        (one pipeline block per unknown pipeline, an error entry or None).
    """
    query = (
        "query ($slug: String!, $page: Int!, $perPage: Int!) {"
        " pipelines(workspaceSlug: $slug, page: $page, perPage: $perPage)"
        " { totalPages items { code currentVersion { versionName } } } }"
    )
    items: list[dict] = []
    page, total_pages = 0, 1
    while page < total_pages:
        page += 1
        variables = {"slug": workspace.slug, "page": page, "perPage": 100}
        try:
            data = call_graphql(token, query, variables)["pipelines"]
        except Exception as exception:
            message = f"Could not list this workspace's pipelines: {exception}"
            current_run.log_error(f"[ERROR] {message}")
            return [], {"scope": "pipeline_list", "message": message}
        items.extend(data["items"])
        total_pages = data["totalPages"]

    unknown = []
    for item in sorted(items, key=lambda i: i["code"]):
        if item["code"] in known_codes:
            continue
        name = (item.get("currentVersion") or {}).get("versionName")
        in_target = False if has_target else None
        unknown.append(pipeline_report(None, item["code"], in_target, name, None, "not_in_any_release"))
    return unknown, None


def call_graphql(token: str, operation: str, variables: dict) -> dict:
    """Call the OpenHEXA GraphQL API with an explicit bearer token, reporting errors usefully.

    Copied from snt_workspace_deployer - see download_manifest for why. The SDK's own
    `graphql()` helper raises a bare HTTPError on a 4xx and discards the response body, which
    is where GraphQL puts the actual reason.

    Returns
    -------
    dict
        The `data` object of the GraphQL response.
    """
    response = requests.post(
        f"{os.environ['HEXA_SERVER_URL'].rstrip('/')}/graphql/",
        headers={"Authorization": f"Bearer {token}"},
        json={"query": operation, "variables": variables},
        timeout=120,
    )
    if response.status_code != 200:
        current_run.log_error(f"HTTP {response.status_code} from the OpenHEXA API: {response.text[:2000]}")
        response.raise_for_status()

    body = response.json()
    if body.get("errors"):
        raise RuntimeError(f"GraphQL errors: {body['errors']}")
    return body["data"]


def read_zip_members(encoded_zipfile: str) -> dict:
    """Decode a pipeline version's base64 zip and hash every file inside it.

    A 200 OK is not evidence on its own - GraphQL field-level denial returns a null field
    inside a successful response - so a zip that does not decode is an error, loudly.

    Returns
    -------
    dict
        {path inside the zip: sha256 of its contents}.
    """
    if not encoded_zipfile:
        raise ValueError(
            "The API returned an empty zipfile field for this version. That is how field-level "
            "permission denial presents itself; check the token before anything else."
        )

    archive = zipfile.ZipFile(io.BytesIO(base64.b64decode(encoded_zipfile)))
    return {
        info.filename: sha256_bytes(archive.read(info.filename))
        for info in archive.infolist()
        if not info.is_dir()
    }


def claimed_release_tag(version_name: str) -> str:
    """Recover the release tag a pipeline version's name claims, ignoring OpenHEXA's suffix.

    Returns
    -------
    str
        The version name with a trailing " [v<number>]" removed, if it had one.
    """
    return VERSION_NUMBER_SUFFIX.sub("", version_name).strip()


def name_matches_content(dir_name: str, version_name: str, members: dict, index: ReleaseIndex) -> bool | None:
    """Say whether a version's name is borne out by the bytes it actually contains.

    A version name is free text somebody typed; the hash is evidence. When they disagree the
    hash wins and the name is reported as misleading - a workspace whose version labels have
    stopped meaning anything looks perfectly healthy in the OpenHEXA UI, which shows only the
    names (PRODUCT_SPEC.md section 3.1).

    With every manifest loaded, a version named after ANY release can be judged, not only
    one named after the target. The zip must hold exactly that release's files for this
    pipeline, byte for byte - an extra file is a disagreement too. A name that is not a
    release tag at all ("v3" from a plain CLI push) claims nothing and is reported as null.

    Returns
    -------
    bool | None
        True or False when the name claims a release with a usable manifest, None otherwise.
    """
    claimed = claimed_release_tag(version_name)
    manifest = index.manifests.get(claimed)
    if manifest is None:
        return None
    spec = manifest["pipelines"].get(dir_name)
    if spec is None:
        return False  # named after a release that does not ship this pipeline at all
    expected = {member: manifest["files"].get(f"{dir_name}/{member}") for member in spec["zip_files"]}
    return members == expected


def pipeline_report(
    dir_name: str | None,
    code: str,
    in_target: bool | None,
    version_name: str | None,
    matches_content: bool | None,
    remediation_key: str | None,
) -> dict:
    """Build the per-pipeline block that sits alongside the per-file entries.

    The name/content disagreement, and a pipeline's presence or absence from the target, are
    properties of the deployment rather than of any one file inside the zip, so they are
    reported here rather than as per-file statuses.

    Returns
    -------
    dict
        One entry of the report's `pipelines` list.
    """
    return {
        "pipeline": dir_name,
        "code": code,
        "in_target": in_target,
        "in_any_release": dir_name is not None,
        # The raw API value, kept verbatim as the evidence it is, alongside the tag parsed
        # out of it - the two differ because OpenHEXA appends the version number.
        "current_version_name": version_name,
        "current_version_claims_tag": claimed_release_tag(version_name) if version_name else None,
        "version_name_matches_content": matches_content,
        "remediation": PIPELINE_REMEDIATION.get(remediation_key),
    }


# --------------------------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------------------------


def attribution_summary(entries: list[dict], index: ReleaseIndex, releases_considered: list[dict]) -> dict:
    """Score every release on how far the workspace agrees with it, and how much of it is there.

    Decision D15, revised (PRODUCT_SPEC.md section 7.10). Each release is judged only on the
    paths it ships. A present file at a path the release does not ship is `extra` for it -
    neither agreement nor disagreement. Counting such files as disagreement made every
    leftover of a removed file (the deployer removes nothing, and a pipeline cannot delete
    another) drag the newest release down, so a freshly upgraded workspace read as an older
    release (sandbox run of 2026-09-29).

    Judging only on shipped paths would let a near-empty workspace agree 100% with
    everything, so completeness is reported beside agreement:

        agreement     agreeing / present   of the release's paths found here, the share whose
                                           bytes are exactly that release's
        completeness  present / shipped    the share of the release's paths found here at all

    A file unchanged across releases agrees with each of them, so no column sums to 1. The
    best fit is the highest agreement, then the highest completeness, then the newest
    release - identical content across releases is the normal case, and the newest is the
    least surprising label for it.

    "Present" means an entry for a file in the workspace, both sources together, except
    `untracked` (no release has an opinion about the path) and `missing` (no bytes). An
    unreadable file is present but never agrees; it and the files matching no release at
    all are also reported on their own. Scores come from the observed bytes, not from each
    entry's status, so they mean the same thing in both modes.

    Returns
    -------
    dict
        The `summary.attribution` block.
    """
    counted = [entry for entry in entries if entry["status"] not in ("untracked", "missing")]
    total = len(counted)

    def ratio(part: int, whole: int) -> float | None:
        return round(part / whole, 4) if whole else None

    matches_none = sum(
        1
        for entry in counted
        if entry["observed_sha256"] is not None
        and not index.releases_holding(entry["path"], entry["observed_sha256"])
    )
    unreadable = sum(1 for entry in counted if entry["observed_sha256"] is None)

    by_release = []
    for release in releases_considered:
        row = {
            "tag": release["tag"],
            "published_at": release["published_at"],
            "manifest_available": release["manifest_available"],
            "shipped": None,
            "present": None,
            "agreeing": None,
            "extra": None,
            "agreement": None,
            "completeness": None,
        }
        if release["manifest_available"]:
            files = index.manifests[release["tag"]]["files"]
            at_shipped = [entry for entry in counted if entry["path"] in files]
            agreeing = sum(1 for entry in at_shipped if entry["observed_sha256"] == files[entry["path"]])
            row.update(
                shipped=len(files),
                present=len(at_shipped),
                agreeing=agreeing,
                extra=total - len(at_shipped),
                agreement=ratio(agreeing, len(at_shipped)),
                completeness=ratio(len(at_shipped), len(files)),
            )
        by_release.append(row)

    best = None
    for row in by_release:  # oldest first, so `>=` hands a full tie to the newest
        if not row["agreeing"]:
            continue
        key = (row["agreement"], row["completeness"])
        if best is None or key >= (best["agreement"], best["completeness"]):
            best = row

    return {
        "files_scored": total,
        "rule": (
            "Each release is judged only on the paths it ships. present = files found here at those "
            "paths; agreeing = those whose bytes are exactly the release's; extra = files found here "
            "at paths it does not ship. agreement = agreeing / present; completeness = present / "
            "shipped. Files are every entry for a file present in the workspace, filesystem and "
            "pipeline versions together, except `untracked` and `missing`. A file unchanged across "
            "releases agrees with each of them, so no column adds up to 1. Best fit: highest "
            "agreement, then highest completeness, then newest."
        ),
        "by_release": by_release,
        "best_fit": (
            {"tag": best["tag"], "agreement": best["agreement"], "completeness": best["completeness"]}
            if best
            else None
        ),
        "matches_no_release": {"count": matches_none, "share": ratio(matches_none, total)},
        "unreadable": {"count": unreadable, "share": ratio(unreadable, total)},
    }


def build_report(
    entries: list[dict],
    index: ReleaseIndex,
    pipeline_reports: list[dict],
    inert_copies: list[str],
    errors: list[dict],
    github_repo: str,
    release: dict | None,
    releases_considered: list[dict],
    resolved_from: str,
    declared_tag: str | None,
) -> dict:
    """Assemble the report document from what each source produced.

    Returns
    -------
    dict
        The full report, in the shape frozen by docs/wip/docs/status_report.schema.json.
    """
    by_status: dict[str, int] = {}
    for entry in entries:
        by_status[entry["status"]] = by_status.get(entry["status"], 0) + 1

    incomplete = (
        bool(errors)
        or any(entry["status"] == "unreadable" for entry in entries)
        or not all(r["manifest_available"] for r in releases_considered)
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        # The version of THIS pipeline that produced the report. A run cannot read the name of
        # the pipeline version executing it, so this is null until a source for it is found.
        # The key is part of the frozen v1 schema and stays, nullable.
        "snt_workspace_check_version": None,
        "workspace": workspace.slug,
        "repo": github_repo,
        "mode": "verification" if release else "attribution",
        # Always an object, so a consumer reads `resolved_from` the same way in both modes. In
        # attribution mode `tag` is null and `resolved_from` says why: "parameter" (the reserved
        # value 'none') or "nothing_given" (empty parameter, no marker).
        "target_release": {
            "tag": release["tag_name"] if release else None,
            "resolved_from": resolved_from,
            "published_at": release.get("published_at") if release else None,
        },
        "declared_release": ({"tag": declared_tag, "source": RELEASE_MARKER_NAME} if declared_tag else None),
        "releases_considered": releases_considered,
        "incomplete": incomplete,
        "summary": {
            "by_status": dict(sorted(by_status.items())),
            "attribution": attribution_summary(entries, index, releases_considered),
        },
        "pipelines": pipeline_reports,
        "entries": entries,
        "inert_filesystem_copies": inert_copies,
        "scan_exclusions": scan_exclusions(),
        "errors": errors,
        # Named so the report never reads as a clean bill of health for things it never
        # looked at (PRODUCT_SPEC.md sections 1.2 and 5.4).
        "blind_spots": [
            "In verification mode, matching_releases is carried only on entries that are not `match` "
            "(decision D14); summary.attribution still counts every file.",
            "Files on the filesystem inside a pipeline directory never execute; they are listed in "
            "inert_filesystem_copies, not classified.",
            "Pipelines no release describes are named in `pipelines`, but their contents are not read.",
            "The directories and files in scan_exclusions were not scanned.",
            "Notebooks are compared as raw bytes (decision D9): a metadata-only change, such as a "
            "different kernel name, reads as unknown_content.",
            "OpenHEXA web apps, configuration/ and data/ are out of scope by decision.",
            "Country-specific notebook variants are not recognised as deliberate overrides.",
        ],
    }


def write_report(snt_root_path: Path, report: dict) -> Path:
    """Write the report twice: timestamped for history, and at the stable latest path.

    Returns
    -------
    Path
        The path of the timestamped copy.
    """
    report_dir = snt_root_path / REPORT_DIR_NAME
    report_dir.mkdir(parents=True, exist_ok=True)

    # The colons of an ISO timestamp are legal in an object key but hostile in a filename on
    # a machine that later syncs this bucket, so they are replaced here and nowhere else -
    # `generated_at` inside the document keeps the exact ISO form.
    stamp = report["generated_at"].replace(":", "-")
    report_path = report_dir / f"status_{stamp}.json"

    payload = json.dumps(report, indent=2)
    report_path.write_text(payload)
    (report_dir / LATEST_REPORT_NAME).write_text(payload)

    return report_path


def percent(value: float | None) -> str:
    """Format a 0-1 ratio for the log, or 'n/a' when there was nothing to divide by.

    Returns
    -------
    str
        e.g. "97.6%".
    """
    return f"{value:.1%}" if value is not None else "n/a"


def log_summary(report: dict, report_path: Path) -> None:
    """Log the shape of the result, leaving the per-file detail to the JSON.

    Every actionable entry gets one line. Untracked files on the filesystem are not
    actionable (decision D6) and can number in the hundreds in a real workspace, so they are
    counted with a short sample rather than listed (PRODUCT_SPEC.md section 5.4). Neither are
    `match` and `attributed` entries - they are the normal case - so the coverage scores
    stand in for them: one line per release.
    """
    by_status = report["summary"]["by_status"]
    total = sum(by_status.values())
    against = report["target_release"]["tag"] or "no target (attribution mode)"
    current_run.log_info(
        f"Checked {total} file(s) against {against}: "
        + ", ".join(f"{count} {status}" for status, count in by_status.items())
    )

    attribution = report["summary"]["attribution"]
    current_run.log_info(
        f"Per release, over {attribution['files_scored']} file(s) present and tracked. Agreement: of "
        "the release's files found here, the share with exactly its bytes. Completeness: the share of "
        "its files found here at all. Extra: files here it does not ship."
    )
    for row in attribution["by_release"]:
        if not row["manifest_available"]:
            current_run.log_info(f"  {row['tag']}: no usable manifest, not scored")
            continue
        current_run.log_info(
            f"  {row['tag']}: agreement {percent(row['agreement'])} ({row['agreeing']}/{row['present']}), "
            f"completeness {percent(row['completeness'])} ({row['present']}/{row['shipped']}), "
            f"{row['extra']} extra"
        )
    no_match, unreadable = attribution["matches_no_release"], attribution["unreadable"]
    best = attribution["best_fit"]
    headline = (
        f"Best fit: {best['tag']} (agreement {percent(best['agreement'])}, completeness "
        f"{percent(best['completeness'])})."
        if best
        else "No best fit: no file matches any release."
    )
    current_run.log_info(
        f"{headline} Matching no release: {no_match['count']}; unreadable: {unreadable['count']}."
    )

    stray = []
    for entry in report["entries"]:
        if entry["status"] in ("match", "attributed"):
            continue
        if entry["status"] == "untracked" and entry["source"] == "filesystem":
            stray.append(entry["path"])
            continue
        detail = ""
        if entry["matching_releases"]:
            spans = ", ".join(
                span["from"] if span["count"] == 1 else f"{span['from']}..{span['to']}"
                for span in entry["matching_releases"]
            )
            detail = f", matches {spans}" + (f" ({entry['position']})" if entry["position"] else "")
        current_run.log_warning(f"[WARNING] {entry['status']}: {entry['path']} ({entry['source']}{detail})")

    if stray:
        sample = ", ".join(stray[:10]) + (f", ... and {len(stray) - 10} more" if len(stray) > 10 else "")
        current_run.log_info(f"{len(stray)} untracked file(s) on the filesystem, reported only: {sample}")
    if report["inert_filesystem_copies"]:
        current_run.log_info(
            f"{len(report['inert_filesystem_copies'])} file(s) on the filesystem inside pipeline "
            "directories - inert, never executed, not classified."
        )

    for block in report["pipelines"]:
        if block["version_name_matches_content"] is False:
            current_run.log_warning(
                f"[WARNING] {block['code']}: its current version is named "
                f"'{block['current_version_name']}' but its contents are not that release's. "
                "The name is metadata someone typed; the hash is evidence."
            )
        if not block["in_any_release"]:
            current_run.log_info(f"{block['code']}: deployed here, described by no release - reported only.")

    if report["incomplete"]:
        current_run.log_warning(
            "[WARNING] This report is INCOMPLETE - at least one source or release manifest could not "
            "be read. It is not a clean bill of health; see the 'errors' list."
        )

    current_run.log_info(f"Report written: {report_path} (and {REPORT_DIR_NAME}/{LATEST_REPORT_NAME})")


if __name__ == "__main__":
    snt_workspace_checker()
