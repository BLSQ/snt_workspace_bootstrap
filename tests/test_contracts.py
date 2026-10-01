"""Real artefacts from the sandbox validate against the two contracts in docs/contracts/."""

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "docs" / "contracts"
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def load(path: Path) -> dict:
    """Read a JSON file.

    Returns
    -------
    dict
        The parsed document.
    """
    return json.loads(path.read_text(encoding="utf-8"))


def validator(schema_name: str) -> Draft202012Validator:
    """Build a validator for one of the contract schemas, after checking the schema itself.

    Returns
    -------
    Draft202012Validator
        A validator that also checks `format` keywords.
    """
    schema = load(CONTRACTS / schema_name)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


@pytest.mark.parametrize("name", sorted(p.name for p in FIXTURES.glob("status_report_*.json")))
def test_status_report_fixture_conforms(name: str) -> None:
    """A report the checker wrote in the sandbox has exactly the frozen schema's keys and validates."""
    report = load(FIXTURES / name)
    schema = validator("status_report.schema.json")
    assert set(report) == set(schema.schema["properties"])
    errors = [e.message for e in schema.iter_errors(report)]
    assert not errors


@pytest.mark.parametrize("name", sorted(p.name for p in FIXTURES.glob("release_manifest_*.json")))
def test_release_manifest_fixture_conforms(name: str) -> None:
    """A manifest the generator attached to a sandbox release validates, invariants included."""
    manifest = load(FIXTURES / name)
    errors = [e.message for e in validator("release_manifest.schema.json").iter_errors(manifest)]
    assert not errors
    # The two invariants the schema states but JSON Schema cannot express.
    for directory, spec in manifest["pipelines"].items():
        assert "pipeline.py" in spec["zip_files"], directory
        unknown = [m for m in spec["zip_files"] if f"{directory}/{m}" not in manifest["files"]]
        assert not unknown, (directory, unknown)
