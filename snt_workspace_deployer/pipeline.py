"""Deploy a pinned GitHub release of the SNT codebase into this OpenHEXA workspace.

Both halves of the codebase move together, on one release tag, replacing the two
uncoordinated update paths described in docs/release_strategy.md:

    R analytics   pipelines/**/*.ipynb, pipelines/**/utils/*.r, code/**/*.r
                  -> copied into the workspace filesystem, where notebooks source() them.
                  Replaces the per-pipeline `Pull scripts` toggle.

    Python half   <name>/pipeline.py
                  -> registered as a new pipeline VERSION through the OpenHEXA API.
                  Replaces the Template auto-update subscription.

The Python half is deliberately NOT copied into the workspace filesystem. OpenHEXA runs
each pipeline from the zip stored on its registered version, never from the bucket, so a
pipeline.py sitting in workspace/files would look authoritative and never execute.

Credentials: a pipeline run's own HEXA_TOKEN can read the API but is refused
(PERMISSION_DENIED) on uploadPipeline, so deployment reads a workspace API token from a
CUSTOM connection instead - see the `api_connection` parameter.
"""

import base64
import hashlib
import io
import json
import os
import re
import shutil
import tarfile
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import NoReturn

import requests
from openhexa.sdk import current_run, parameter, pipeline, workspace
from openhexa.sdk.pipelines.pipeline import Pipeline
from openhexa.sdk.pipelines.runtime import get_pipeline

# The suffixes `openhexa pipelines push` puts in a pipeline version's zip. Kept identical
# so a version deployed from here is byte-comparable with one pushed by CI.
ZIPPED_SUFFIXES = (".py", ".ipynb", ".txt", ".md", ".r", ".sql")

GITHUB_HEADERS = {"User-Agent": "snt-workspace-deployer"}

# OpenHEXA appends " [v<number>]" to a version name when it is read back.
VERSION_NUMBER_SUFFIX = re.compile(r"\s*\[v\d+\]\s*$")

# The error code OpenHEXA returns when a pipeline already has a version with that name.
DUPLICATE_VERSION_NAME_ERROR = "DUPLICATE_PIPELINE_VERSION_NAME"


class DuplicateVersionNameError(RuntimeError):
    """The pipeline already has a version with the requested name."""


class DeploymentAbortedError(RuntimeError):
    """The run was stopped on purpose, and the reason is already in the run's Messages."""


@pipeline("snt_workspace_deployer")
@parameter(
    "github_repo",
    name="GitHub repository",
    help="owner/repo to pull the release from (e.g. BLSQ/snt_development_sandbox)",
    type=str,
    default="BLSQ/snt_development_sandbox",
    required=True,
)
@parameter(
    "release_tag",
    name="Release tag",
    help=(
        "GitHub release tag to deploy (e.g. v0.3.0-test). Leave empty to deploy the repository's "
        "latest release. The resolved tag becomes the pipeline version name."
    ),
    type=str,
    default=None,
    required=False,
)
@parameter(
    "api_connection",
    name="OpenHEXA API connection",
    help=(
        "CUSTOM connection holding a workspace API token in a secret field named 'token'. "
        "A run's own credentials cannot deploy pipelines."
    ),
    type=str,
    default="oh",
    required=True,
)
@parameter(
    "backup_existing",
    name="Backup existing files",
    help="Move any existing tracked file to workspace/archive/<release_tag>/ before overwriting it",
    type=bool,
    default=True,
    required=False,
)
@parameter(
    "dry_run",
    name="Dry run",
    help="Report what would change without writing any file or registering any version",
    type=bool,
    default=False,
    required=False,
)
def snt_workspace_deployer(
    github_repo: str,
    release_tag: str | None,
    api_connection: str,
    backup_existing: bool,
    dry_run: bool,
) -> None:
    """Deploy one GitHub release's R analytics and Python orchestration into this workspace.

    Orchestration only: resolves the release, downloads its manifest and source tarball,
    then delegates the file sync and the pipeline deployment to plain helper functions.

    A raised exception reaches only the run's logs, not its Messages, so any failure that was
    not already reported through `abort_run()` is logged here before the run is stopped.
    """
    try:
        deploy_release(github_repo, release_tag, api_connection, backup_existing, dry_run)
    except DeploymentAbortedError:
        raise
    except Exception as exception:
        current_run.log_error(
            f"[ERROR] Deployment stopped by an unexpected error - {type(exception).__name__}: {exception}"
        )
        raise


def deploy_release(
    github_repo: str,
    release_tag: str | None,
    api_connection: str,
    backup_existing: bool,
    dry_run: bool,
) -> None:
    """Run the deployment steps for `snt_workspace_deployer`, in order."""
    snt_root_path = Path(workspace.files_path)
    if dry_run:
        current_run.log_info("DRY RUN - nothing will be written or registered.")

    token = get_api_token(api_connection)

    release = get_release(github_repo, release_tag)
    release_tag = release["tag_name"]
    manifest = download_manifest(release)
    analytics_files, pipeline_dirs = split_manifest(manifest["files"], manifest["pipelines"])
    current_run.log_info(
        f"Release {github_repo}@{release_tag} tracks {len(manifest['files'])} files: "
        f"{len(analytics_files)} analytics file(s) and {len(pipeline_dirs)} pipeline(s)."
    )

    with tempfile.TemporaryDirectory() as tmp_dir:
        tarball_root = download_and_extract_tarball(release["tarball_url"], Path(tmp_dir))

        archive_dir = snt_root_path / "archive" / release_tag if backup_existing else None
        copied, missing = sync_files(analytics_files, tarball_root, snt_root_path, archive_dir, dry_run)
        verb = "would be synced" if dry_run else "synced"
        current_run.log_info(f"Analytics: {len(copied)}/{len(analytics_files)} file(s) {verb}.")
        if missing:
            current_run.log_warning(
                f"{len(missing)} manifest entries were not found in the release tarball: {missing}"
            )

        failures = deploy_all(tarball_root, pipeline_dirs, release, token, dry_run)

    if not dry_run:
        write_release_marker(snt_root_path, release_tag)

    if failures:
        abort_run(
            f"{len(failures)} pipeline(s) failed to deploy: {failures}. The workspace is now "
            "partially updated - fix the cause and re-run to converge on the release."
        )


def get_api_token(connection_slug: str) -> str:
    """Read the workspace API token used to deploy pipelines.

    A run's own HEXA_TOKEN is refused with PERMISSION_DENIED on uploadPipeline, so
    deployment needs a workspace API key supplied through a CUSTOM connection.

    Returns
    -------
    str
        The bearer token held in the connection's `token` field.
    """
    try:
        token = workspace.custom_connection(connection_slug).token
        reason = None if token else "the 'token' field is empty"
    except Exception as exception:
        token = None
        reason = f"{type(exception).__name__}: {exception}"

    if not token:
        message = (
            f"[ERROR] Cannot deploy: the OpenHEXA API connection '{connection_slug}' is missing or "
            f"unusable ({reason}). Create a CUSTOM connection with the slug '{connection_slug}' and a "
            "secret field named 'token' holding a workspace API token (Settings > Connections), "
            "or set the 'OpenHEXA API connection' parameter to an existing connection slug."
        )
        current_run.log_error(message)
        raise DeploymentAbortedError(message)

    current_run.log_info(f"Using the API token from connection '{connection_slug}' to deploy.")
    return token


def get_release(github_repo: str, release_tag: str | None) -> dict:
    """Fetch a GitHub release's metadata, by tag or the repository's latest.

    A blank `release_tag` resolves to GitHub's "latest" release, which is the most recent
    published one that is neither a draft nor flagged as a pre-release. A pre-release must
    therefore be requested by its tag, and deploying one is logged as a warning (D27).

    Returns
    -------
    dict
        The GitHub API release object (tag_name, tarball_url, html_url, assets, ...).
    """
    release_tag = (release_tag or "").strip()
    base_url = f"https://api.github.com/repos/{github_repo}/releases"
    url = f"{base_url}/tags/{release_tag}" if release_tag else f"{base_url}/latest"
    response = github_get(url)
    if response.status_code == 404:
        abort_run(explain_missing_release(github_repo, release_tag))
    if not response.ok:
        abort_run(f"GitHub answered {response.status_code} on {url}: {response.text[:200]}")
    release = response.json()
    if not release_tag:
        current_run.log_info(f"No release tag given: deploying the latest release, {release['tag_name']}.")
    if release.get("prerelease"):
        current_run.log_warning(
            f"[WARNING] Release {release['tag_name']} is a pre-release, for development and testing "
            "only, not for a country workspace. snt_workspace_checker ignores pre-releases unless "
            "one is its target; run with an empty tag, it targets the release in .snt_release, i.e. "
            "this one."
        )
    return release


def github_get(url: str) -> requests.Response:
    """GET a GitHub API URL, turning a network failure into a readable run error.

    Returns
    -------
    requests.Response
        The response, whatever its status code.
    """
    try:
        return requests.get(url, headers=GITHUB_HEADERS, timeout=30)
    except requests.RequestException as exception:
        abort_run(f"Could not reach GitHub ({type(exception).__name__}: {exception}).")


def explain_missing_release(github_repo: str, release_tag: str) -> str:
    """Work out why GitHub returned 404 for a release, for the operator to act on.

    A 404 covers several causes - the repository is missing or private, the tag does not exist,
    the repository has no release at all, or it only has pre-releases (which GitHub never
    treats as "latest") - so the repository and its release list are queried to tell them apart.

    Returns
    -------
    str
        A message naming the cause and what to do about it.
    """
    repo_response = github_get(f"https://api.github.com/repos/{github_repo}")
    if repo_response.status_code == 404:
        return (
            f"The GitHub repository '{github_repo}' does not exist or is private (it is read without "
            "credentials, so it must be public). Check the 'GitHub repository' parameter."
        )

    releases_response = github_get(f"https://api.github.com/repos/{github_repo}/releases?per_page=10")
    if not releases_response.ok:
        what = f"a release tagged '{release_tag}'" if release_tag else "a latest release"
        return (
            f"GitHub found no {what} in '{github_repo}', and listing its releases failed "
            f"({releases_response.status_code}: {releases_response.text[:200]})."
        )
    releases = releases_response.json()
    available = ", ".join(r["tag_name"] + (" (pre-release)" if r["prerelease"] else "") for r in releases)

    if not releases:
        return (
            f"The repository '{github_repo}' has no published GitHub release, so there is nothing to "
            "deploy. Publish a release (with its release_manifest.json asset) on GitHub, or point the "
            "'GitHub repository' parameter at a repository that has one."
        )
    if release_tag:
        return (
            f"The repository '{github_repo}' has no release tagged '{release_tag}'. Check the spelling "
            f"of the 'Release tag' parameter. Most recent releases: {available}."
        )
    return (
        f"The repository '{github_repo}' has no release GitHub counts as 'latest': only pre-releases "
        f"are published, and GitHub never treats a pre-release as latest. Type the tag to deploy in "
        f"the 'Release tag' parameter, or mark a release as a full release on GitHub. Most recent "
        f"releases: {available}."
    )


def abort_run(reason: str) -> NoReturn:
    """Log a failure to the run's Messages and stop the run.

    A raised exception alone shows only in the run's logs, so the reason is logged first.

    Raises
    ------
    DeploymentAbortedError
        Always, carrying the same message that was logged.
    """
    message = f"[ERROR] Cannot deploy: {reason}"
    current_run.log_error(message)
    raise DeploymentAbortedError(message)


def download_manifest(release: dict) -> dict:
    """Download and parse release_manifest.json from a release's assets.

    A manifest without a `pipelines` block is refused before anything is written (D26).

    Returns
    -------
    dict
        The parsed manifest: {"version": ..., "files": {path: sha256}, "pipelines": {dir: spec}}.
    """
    asset = next((a for a in release["assets"] if a["name"] == "release_manifest.json"), None)
    if asset is None:
        abort_run(
            f"release {release['tag_name']} has no release_manifest.json asset, so the files to deploy "
            "are unknown. Run the 'Generate Release Manifest' workflow for this release on GitHub (or "
            f"wait for it to finish), then re-run. Release page: {release['html_url']}"
        )
    response = github_get(asset["browser_download_url"])
    if not response.ok:
        abort_run(
            f"downloading release_manifest.json of release {release['tag_name']} failed "
            f"({response.status_code}: {response.text[:200]})."
        )
    try:
        manifest = response.json()
    except ValueError:
        abort_run(f"release_manifest.json of release {release['tag_name']} is not valid JSON.")
    if "pipelines" not in manifest:
        abort_run(
            f"release_manifest.json of release {release['tag_name']} has no 'pipelines' block (it predates "
            "the phase-0 generator), so which files ship inside pipeline zips is unknown. Manifests without "
            "it are not supported (D26): re-run the 'Generate Release Manifest' workflow for this release."
        )
    return manifest


def split_manifest(tracked_files: dict, pipelines: dict) -> tuple[dict, list[str]]:
    """Separate the manifest into filesystem-synced analytics and API-deployed pipelines.

    Everything under a pipeline directory is deliberately excluded from the filesystem
    sync, because OpenHEXA runs pipelines from their registered version's zip and never
    from the workspace bucket. A copy in the bucket is inert and actively misleading.

    Parameters
    ----------
    tracked_files : dict
        The manifest's `files` map, `{repository path: sha256}`.
    pipelines : dict
        The manifest's `pipelines` block, keyed by pipeline directory. May be empty.

    Returns
    -------
    tuple[dict, list[str]]
        (the analytics files to copy, keyed by path; the pipeline directory names to deploy).
    """
    pipeline_dirs = set(pipelines)

    analytics = {
        rel_path: checksum
        for rel_path, checksum in tracked_files.items()
        if Path(rel_path).parts[0] not in pipeline_dirs
    }
    return analytics, sorted(pipeline_dirs)


def download_and_extract_tarball(tarball_url: str, extract_to: Path) -> Path:
    """Download a GitHub source tarball and extract it.

    One request for the whole repository: a per-file Contents API fetch would need one
    call per tracked file, against an unauthenticated limit of 60 per hour.

    Returns
    -------
    Path
        The path to the extracted repository root (GitHub tarballs contain one top-level
        directory named "<owner>-<repo>-<short_sha>").
    """
    try:
        response = requests.get(tarball_url, headers=GITHUB_HEADERS, timeout=120, stream=True)
    except requests.RequestException as exception:
        abort_run(f"could not download the release source tarball ({type(exception).__name__}: {exception}).")
    if not response.ok:
        abort_run(f"downloading the release source tarball failed ({response.status_code}): {tarball_url}")

    tarball_path = extract_to / "release.tar.gz"
    with tarball_path.open("wb") as f:
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            f.write(chunk)

    with tarfile.open(tarball_path) as tar:
        tar.extractall(path=extract_to)

    subdirs = [p for p in extract_to.iterdir() if p.is_dir()]
    if len(subdirs) != 1:
        abort_run(f"the release source tarball should hold exactly one directory, found: {subdirs}")
    return subdirs[0]


def sync_files(
    tracked_files: dict, source_root: Path, dest_root: Path, archive_dir: Path | None, dry_run: bool
) -> tuple[list[str], list[str]]:
    """Copy every tracked analytics file from the extracted tarball into the workspace.

    Existing files are moved under `archive_dir` before being overwritten, if one is given.

    Returns
    -------
    tuple[list[str], list[str]]
        (paths successfully copied, paths listed in the manifest but missing from the tarball).
    """
    copied, missing = [], []
    for rel_path in tracked_files:
        source_path = source_root / rel_path
        if not source_path.exists():
            missing.append(rel_path)
            current_run.log_warning(f"Manifest entry not found in tarball, skipping: {rel_path}")
            continue

        dest_path = dest_root / rel_path
        if dry_run:
            current_run.log_info(f"DRY RUN would sync: {rel_path}")
            copied.append(rel_path)
            continue

        if dest_path.exists() and archive_dir is not None:
            backup_path = archive_dir / rel_path
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(dest_path), str(backup_path))

        dest_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, dest_path)
        copied.append(rel_path)

    return copied, missing


def deploy_all(
    tarball_root: Path,
    pipeline_dirs: list[str],
    release: dict,
    token: str,
    dry_run: bool,
) -> list[str]:
    """Register every release pipeline as a version of the matching OpenHEXA pipeline.

    One pipeline's failure does not stop the others: a partial deployment is reported in
    full and re-running converges, whereas aborting halfway hides which ones still need it.

    Returns
    -------
    list[str]
        The directory names of the pipelines that could not be deployed.
    """
    failures = []
    for dir_name in pipeline_dirs:
        try:
            deploy_one(tarball_root / dir_name, dir_name, release, token, dry_run)
        except Exception as exception:  # one bad pipeline must not stop the other nineteen
            current_run.log_error(f"[ERROR] {dir_name}: deployment failed: {exception}")
            failures.append(dir_name)

    if dry_run:
        current_run.log_info(f"Pipelines: {len(pipeline_dirs)} inspected, none registered (dry run).")
    else:
        current_run.log_info(
            f"Pipelines: {len(pipeline_dirs) - len(failures)}/{len(pipeline_dirs)} deployed."
        )
    return failures


def deploy_one(pipeline_dir: Path, dir_name: str, release: dict, token: str, dry_run: bool) -> None:
    """Deploy a single pipeline directory as a new version, creating the pipeline if it is missing.

    The OpenHEXA pipeline code is the directory name with underscores replaced by hyphens -
    the same slug this repo's CI passes to `openhexa pipelines push --code`.
    """
    if not (pipeline_dir / "pipeline.py").exists():
        raise ValueError(f"No pipeline.py in the release tarball at {dir_name}/.")

    code = dir_name.replace("_", "-")
    parsed = get_pipeline(pipeline_dir)
    existing = get_pipeline_by_code(token, code)

    version_input = build_version_input(pipeline_dir, parsed, release)

    if existing:
        deploy_new_version(token, code, existing["currentVersion"], version_input, dry_run)
        return

    if dry_run:
        current_run.log_info(f"DRY RUN would create: {code} with {len(parsed.parameters)} parameter(s).")
        return

    created = create_pipeline_with_version(token, parsed.name, version_input)
    if created["code"] != code:
        raise ValueError(
            f"Created pipeline got code '{created['code']}', not the expected '{code}'. "
            f"OpenHEXA derives the code from the name '{parsed.name}'; delete the pipeline it "
            "just made and create it by hand in the UI with the right code."
        )
    current_run.log_info(f"{code}: created and seeded with version {version_input['name']}.")


def deploy_new_version(
    token: str, code: str, current_version: dict | None, version_input: dict, dry_run: bool
) -> None:
    """Make the pipeline's current version carry the release tag, registering a version if needed.

    Re-running the deployer on the same tag must converge, so the pipeline's current version is
    read first and compared with the release by content, not by name (a name is free text):

    * same files and already named with this tag: nothing to do, logged as a skip and counted
      as a success;
    * same files under another name: registered again under the tag, so the workspace reads as
      being at this release (most pipelines do not change between releases);
    * different files: registered under the tag, or under `<tag>+redeploy-<date>` when the
      current version already carries the tag, since it then holds something that is not the
      release.

    A plain tag can also be taken by an older, non-current version, which cannot be seen from
    the current one. OpenHEXA then refuses the name, and the next candidate name is tried.
    """
    tag = version_input["name"]
    same_files = False
    tag_is_current = False

    if current_version is None:
        current_run.log_info(f"{code}: has no registered version yet.")
    else:
        current_name = current_version["versionName"]
        same_files = hash_zip_members(current_version["zipfile"]) == hash_zip_members(
            version_input["zipfile"]
        )
        tag_is_current = VERSION_NUMBER_SUFFIX.sub("", current_name).strip() == tag
        if same_files and tag_is_current:
            current_run.log_info(
                f"{code}: already up to date, skipped. Version {current_version['versionNumber']} "
                f"('{current_name}') holds exactly the files of release {tag}."
            )
            return

    candidates = redeploy_name_candidates(tag, tag_is_current)
    if dry_run:
        what = "relabel (same files)" if same_files else "register"
        current_run.log_info(f"DRY RUN would {what} {code} as '{candidates[0]}'.")
        return

    for candidate in candidates:
        try:
            registered = upload_version(token, code, {**version_input, "name": candidate})
        except DuplicateVersionNameError:
            current_run.log_info(f"{code}: the name '{candidate}' is already taken, trying another.")
            continue

        if candidate != tag:
            current_run.log_warning(
                f"{code}: the name '{tag}' is already held by another version of this pipeline "
                f"(edited by hand, or an older deployment of this tag). Registered the release's "
                f"files as '{registered['versionName']}' instead."
            )
        previous = current_version["versionNumber"] if current_version else None
        relabel = " (same files, relabelled)" if same_files else ""
        current_run.log_info(
            f"{code}: updated from version {previous} to {registered['versionNumber']} "
            f"('{registered['versionName']}'){relabel}."
        )
        return

    raise RuntimeError(f"Every candidate version name for '{code}' is already taken: {candidates}.")


def redeploy_name_candidates(tag: str, tag_is_current: bool) -> list[str]:
    """List the version names to try, in order, for a release tag.

    The plain tag comes first unless the current version is known to hold it. The redeploy
    names follow: by day, then by second for a second redeploy on the same day.

    Returns
    -------
    list[str]
        The candidate version names, most preferred first.
    """
    now = datetime.now(UTC)
    redeploys = [f"{tag}+redeploy-{now:%Y%m%d}", f"{tag}+redeploy-{now:%Y%m%dT%H%M%SZ}"]
    return redeploys if tag_is_current else [tag, *redeploys]


def hash_zip_members(encoded_zipfile: str | None) -> dict:
    """Hash every file inside a base64-encoded zip.

    Contents are compared rather than the zip itself, because two zips of the same files
    differ in their timestamps. An empty field is an error and never read as an empty zip: a
    field-level permission denial returns null inside a successful response.

    Returns
    -------
    dict
        {path inside the zip: sha256 of its contents}.
    """
    if not encoded_zipfile:
        raise ValueError(
            "The API returned an empty zipfile field for the pipeline's current version. That is how "
            "field-level permission denial presents itself; check the token."
        )
    archive = zipfile.ZipFile(io.BytesIO(base64.b64decode(encoded_zipfile)))
    return {
        info.filename: hashlib.sha256(archive.read(info.filename)).hexdigest()
        for info in archive.infolist()
        if not info.is_dir()
    }


def build_version_input(pipeline_dir: Path, parsed: Pipeline, release: dict) -> dict:
    """Build the GraphQL version input for a pipeline directory, the way the CLI does.

    The whole directory is zipped, so `requirements.txt` and `readme.md` travel with the
    code. Since 2026-09-21 the release manifest describes all of them, and lists them per
    pipeline in its `pipelines` block, so what ships here is verifiable after the fact.

    Returns
    -------
    dict
        The name, description, zipfile, parameters and timeout fields of the version input.
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path in sorted(pipeline_dir.glob("**/*")):
            if path.suffix.lower() in ZIPPED_SUFFIXES:
                archive.write(path, path.relative_to(pipeline_dir))
    buffer.seek(0)

    version_input = {
        "name": release["tag_name"],
        "description": f"Deployed by snt_workspace_deployer from release {release['tag_name']}.",
        "externalLink": release["html_url"],
        "zipfile": base64.b64encode(buffer.read()).decode("ascii"),
        "parameters": [p.to_dict() for p in parsed.parameters],
        "timeout": parsed.timeout,
    }
    if parsed.functional_type:
        version_input["functionalType"] = parsed.functional_type
    return version_input


def get_pipeline_by_code(token: str, code: str) -> dict | None:
    """Look up a pipeline in this workspace by its code.

    Returns
    -------
    dict | None
        The pipeline with its current version (number, name, zipfile; null if it has none),
        or None if the workspace has no pipeline with that code.
    """
    return call_graphql(
        token,
        "query ($slug: String!, $code: String!) { pipelineByCode(workspaceSlug: $slug, code: $code)"
        " { id code currentVersion { versionNumber versionName zipfile } } }",
        {"slug": workspace.slug, "code": code},
    )["pipelineByCode"]


def upload_version(token: str, code: str, version_input: dict) -> dict:
    """Register a new version of an existing pipeline.

    Returns
    -------
    dict
        The registered pipeline version (versionNumber, versionName).

    Raises
    ------
    DuplicateVersionNameError
        If the pipeline already has a version with that name.
    """
    data = call_graphql(
        token,
        "mutation ($input: UploadPipelineInput!) { uploadPipeline(input: $input)"
        " { success errors pipelineVersion { id versionName versionNumber } } }",
        {"input": {"workspaceSlug": workspace.slug, "code": code, **version_input}},
    )["uploadPipeline"]

    if not data["success"]:
        message = f"uploadPipeline refused for '{code}': {data['errors']}"
        if DUPLICATE_VERSION_NAME_ERROR in data["errors"]:
            raise DuplicateVersionNameError(message)
        raise RuntimeError(message)
    return data["pipelineVersion"]


def create_pipeline_with_version(token: str, pipeline_name: str, version_input: dict) -> dict:
    """Create a pipeline and seed it with its first version in one atomic call.

    Returns
    -------
    dict
        The created pipeline (id, code).
    """
    data = call_graphql(
        token,
        "mutation ($input: CreatePipelineInput!) { createPipeline(input: $input)"
        " { success errors pipeline { id code } } }",
        {"input": {"workspaceSlug": workspace.slug, "name": pipeline_name, "version": version_input}},
    )["createPipeline"]

    if not data["success"]:
        raise RuntimeError(f"createPipeline refused for '{pipeline_name}': {data['errors']}")
    return data["pipeline"]


def call_graphql(token: str, operation: str, variables: dict) -> dict:
    """Call the OpenHEXA GraphQL API with an explicit bearer token, reporting errors usefully.

    The SDK's own `graphql()` helper raises a bare HTTPError on a 4xx and discards the
    response body, which is where GraphQL puts the actual reason.

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


def write_release_marker(snt_root_path: Path, release_tag: str) -> None:
    """Record the currently-deployed release tag in a hidden workspace-root file."""
    marker_path = snt_root_path / ".snt_release"
    marker_path.write_text(json.dumps({"snt_release": release_tag}, indent=2))
    current_run.log_info(f"Wrote release marker: {marker_path}")


if __name__ == "__main__":
    snt_workspace_deployer()
