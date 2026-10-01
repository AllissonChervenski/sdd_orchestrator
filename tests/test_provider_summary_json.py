"""T002 RED tests: `provider-summary --json` canonical typed JSON contract.

Traces: FR-002, FR-003, FR-004, FR-006, FR-007 / AC-002, AC-003, AC-004,
AC-005, AC-007, AC-008, AC-010, AC-011 (specs/001-provider-summary/tasks.md).

RED expectation: the `--json` flag is not yet registered on the
provider-summary subparser, so argparse exits non-zero (usage error) and each
test fails with EXPECTED_FAILURE until GREEN implements the contract.
"""
import json
import subprocess
import sys

import pytest

from orchestrator import cli


def _cap_entry(name, cli_available, models):
    return {"provider": name, "cli_available": cli_available, "models": list(models)}


def _write_cache(root, entries):
    cache_dir = root / ".orchestrator"
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "capabilities.json").write_text(json.dumps(entries), encoding="utf-8")


def _invoke_json(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["orchestrator", "provider-summary", "--json"])
    try:
        ret = cli.main()
        assert ret in (0, None), f"provider-summary --json returned non-zero code {ret!r}"
    except SystemExit as exc:
        assert exc.code in (0, None), f"provider-summary --json must exit 0, got {exc.code!r}"
    captured = capsys.readouterr()
    assert captured.out, "expected the JSON payload on stdout for --json"
    return json.loads(captured.out)


def _item_map(report):
    assert isinstance(report, dict), f"JSON payload must be an object, got {type(report).__name__}"
    assert set(report) == {"providers"}, f"top-level keys must be exactly ['providers'], got {sorted(report)}"
    items = report["providers"]
    assert isinstance(items, list), "providers must be a list"
    return {item["name"]: item for item in items}


def test_json_summary_emits_pure_parseable_json(tmp_path, monkeypatch, capsys):
    """FR-002 / AC-002 / AC-007: --json exits 0 and stdout is only a parseable JSON document."""
    monkeypatch.chdir(tmp_path)
    _write_cache(tmp_path, {
        "agy": _cap_entry("agy", True, ["m1", "m2"]),
        "codex": _cap_entry("codex", False, ["c1"]),
    })
    report = _invoke_json(monkeypatch, capsys)
    assert set(report) == {"providers"}
    assert isinstance(report["providers"], list) and report["providers"]


def test_json_summary_canonical_schema_and_strict_types(tmp_path, monkeypatch, capsys):
    """FR-002 / AC-002: exact item keys and strict str/bool/int field types per cache values."""
    monkeypatch.chdir(tmp_path)
    _write_cache(tmp_path, {
        "agy": _cap_entry("agy", True, ["m1", "m2", "m3"]),
        "opencode": _cap_entry("opencode", False, []),
    })
    items = _item_map(_invoke_json(monkeypatch, capsys))
    assert items["agy"]["name"] == "agy"
    assert type(items["agy"]["name"]) is str
    assert items["agy"]["available"] is True and type(items["agy"]["available"]) is bool
    assert items["agy"]["model_count"] == 3 and type(items["agy"]["model_count"]) is int
    for item in items.values():
        assert set(item) == {"name", "available", "model_count"}, f"unexpected item keys: {sorted(item)}"
        assert type(item["name"]) is str
        assert type(item["available"]) is bool
        assert type(item["model_count"]) is int
        assert item["model_count"] >= 0


def test_json_summary_unknown_availability_is_false(tmp_path, monkeypatch, capsys):
    """AC-010: false, non-bool truthy, and absent-cache statuses are all strictly boolean false."""
    monkeypatch.chdir(tmp_path)
    _write_cache(tmp_path, {
        "agy": _cap_entry("agy", False, ["m1"]),
        "codex": {"provider": "codex", "cli_available": "yes", "models": ["c1"]},
    })
    items = _item_map(_invoke_json(monkeypatch, capsys))
    assert items, "JSON providers list must contain the registered providers, not be empty"
    assert {"agy", "codex"} <= set(items), (
        f"expected cached providers missing from JSON payload: {sorted(items)}"
    )
    assert set(items) == set(cli.PROVIDERS), (
        f"JSON providers must cover exactly the registry {sorted(cli.PROVIDERS)}, got {sorted(items)}"
    )
    for name in cli.PROVIDERS:
        assert items[name]["available"] is False and type(items[name]["available"]) is bool, (
            f"'{name}' must report strict JSON false when availability is unconfirmed"
        )


def test_json_summary_absent_catalog_is_zero_int(tmp_path, monkeypatch, capsys):
    """AC-011: empty models, missing models key, and absent cache all report strict integer 0."""
    monkeypatch.chdir(tmp_path)
    _write_cache(tmp_path, {
        "agy": {"provider": "agy", "cli_available": True, "models": []},
        "codex": {"provider": "codex", "cli_available": True},
    })
    items = _item_map(_invoke_json(monkeypatch, capsys))
    assert items, "JSON providers list must contain the registered providers, not be empty"
    assert {"agy", "codex"} <= set(items), (
        f"expected cached providers missing from JSON payload: {sorted(items)}"
    )
    assert set(items) == set(cli.PROVIDERS), (
        f"JSON providers must cover exactly the registry {sorted(cli.PROVIDERS)}, got {sorted(items)}"
    )
    for name in cli.PROVIDERS:
        assert items[name]["model_count"] == 0 and type(items[name]["model_count"]) is int, (
            f"'{name}' must report model_count as strict integer 0 without a usable catalog"
        )


def test_json_summary_names_derive_from_registry_not_hardcoded(tmp_path, monkeypatch, capsys):
    """FR-004 / AC-003: provider names and order come from the PROVIDERS registry iteration order."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "PROVIDERS", {**cli.PROVIDERS, "customx": type("CustomProvider", (), {})})
    _write_cache(tmp_path, {"customx": _cap_entry("customx", True, ["x1", "x2"])})
    report = _invoke_json(monkeypatch, capsys)
    assert [item["name"] for item in report["providers"]] == list(cli.PROVIDERS)
    items = _item_map(report)
    assert items["customx"]["available"] is True
    assert items["customx"]["model_count"] == 2


def test_json_summary_empty_registry_yields_empty_providers(tmp_path, monkeypatch, capsys):
    """FR-002 / AC-002 contract edge: empty registry serializes as {'providers': []}."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "PROVIDERS", {})
    report = _invoke_json(monkeypatch, capsys)
    assert set(report) == {"providers"}
    assert report["providers"] == []


def test_json_summary_does_not_discover_or_spawn_subprocess(tmp_path, monkeypatch, capsys):
    """FR-003 / AC-004, AC-005 / FR-007, AC-008: JSON mode reads cache only; no discovery, probes, subprocesses, writes."""
    monkeypatch.chdir(tmp_path)
    entries = {"agy": _cap_entry("agy", True, ["m1", "m2"]), "opencode": _cap_entry("opencode", True, ["o1"])}
    _write_cache(tmp_path, entries)

    def boom(*args, **kwargs):
        raise AssertionError(f"forbidden call during provider-summary --json: {args!r} {kwargs!r}")

    monkeypatch.setattr(cli, "discover_all", boom)
    monkeypatch.setattr(cli, "write_selection_report", boom)
    for provider_class in cli.PROVIDERS.values():
        monkeypatch.setattr(provider_class, "discover", boom)
    for name in ("run", "Popen", "call", "check_call", "check_output"):
        if hasattr(subprocess, name):
            monkeypatch.setattr(subprocess, name, boom)
    import os
    if hasattr(os, "system"):
        monkeypatch.setattr(os, "system", boom)
    if hasattr(os, "popen"):
        monkeypatch.setattr(os, "popen", boom)

    def _snapshot():
        return {str(p.relative_to(tmp_path)): p.read_bytes() for p in sorted(tmp_path.rglob("*")) if p.is_file()}

    before = _snapshot()
    items = _item_map(_invoke_json(monkeypatch, capsys))
    assert items["agy"]["available"] is True
    assert items["agy"]["model_count"] == 2
    assert items["opencode"]["model_count"] == 1
    assert _snapshot() == before, "provider-summary --json must not write any workspace file"
    assert json.loads((tmp_path / ".orchestrator" / "capabilities.json").read_text()) == entries


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
