"""T001 RED tests: `provider-summary` read-only text mode.

Traces: FR-001, FR-003, FR-004, FR-006, FR-007 / AC-001, AC-003, AC-004,
AC-005, AC-007, AC-008, AC-010, AC-011 (specs/001-provider-summary/tasks.md).
"""
import json
from pathlib import Path
import re
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


def _invoke(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["orchestrator", *argv])
    try:
        ret = cli.main()
        assert ret in (0, None), f"provider-summary returned non-zero code {ret!r}"
    except SystemExit as exc:
        assert exc.code in (0, None), f"provider-summary must exit 0, got {exc.code!r}"


def _line_for(output, name):
    pattern = re.compile(rf"^\s*{re.escape(name)}\b", re.IGNORECASE)
    lines = [line for line in output.splitlines() if pattern.search(line)]
    assert lines, f"provider '{name}' line missing from stdout:\n{output}"
    return lines[0]


def _assert_count(line, count):
    pattern = rf"\b{count}\s*(?:discovered|descobertos|models?|modelos?)\b"
    assert re.search(pattern, line, re.IGNORECASE), f"expected count {count} in line: {line!r}"


def _assert_status(line, expected_status):
    assert expected_status in ("OK", "UNAVAILABLE")
    if expected_status == "OK":
        assert re.search(r"\bOK\b", line), f"expected OK in line: {line!r}"
        assert not re.search(r"\bUNAVAILABLE\b", line), f"contradictory UNAVAILABLE in line: {line!r}"
    else:
        assert re.search(r"\bUNAVAILABLE\b", line), f"expected UNAVAILABLE in line: {line!r}"
        assert not re.search(r"\bOK\b", line), f"contradictory OK in line: {line!r}"


def _snapshot(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def test_text_summary_lists_all_registered_providers(tmp_path, monkeypatch, capsys):
    """FR-001 / AC-001: exit 0 and every registry provider shows name, OK/UNAVAILABLE, N discovered."""
    monkeypatch.chdir(tmp_path)
    _write_cache(tmp_path, {
        "agy": _cap_entry("agy", True, ["m1", "m2"]),
        "codex": _cap_entry("codex", False, ["c1"]),
    })
    _invoke(monkeypatch, "provider-summary")
    output = capsys.readouterr().out
    for name in cli.PROVIDERS:
        line = _line_for(output, name)
        if name == "agy":
            _assert_status(line, "OK")
            _assert_count(line, 2)
        elif name == "codex":
            _assert_status(line, "UNAVAILABLE")
            _assert_count(line, 1)
        else:
            _assert_status(line, "UNAVAILABLE")
            _assert_count(line, 0)


def test_text_summary_unknown_availability_reports_unavailable(tmp_path, monkeypatch, capsys):
    """AC-010: false, absent-cache, and non-true availability are all UNAVAILABLE (never OK)."""
    monkeypatch.chdir(tmp_path)
    _write_cache(tmp_path, {
        "agy": _cap_entry("agy", False, ["m1"]),
        "codex": {"provider": "codex", "cli_available": "yes", "models": ["c1"]},
    })
    _invoke(monkeypatch, "provider-summary")
    output = capsys.readouterr().out
    for name in cli.PROVIDERS:
        line = _line_for(output, name)
        if name == "agy":
            _assert_status(line, "UNAVAILABLE")
            _assert_count(line, 1)
        else:
            _assert_status(line, "UNAVAILABLE")


def test_text_summary_absent_catalog_reports_zero_discovered(tmp_path, monkeypatch, capsys):
    """AC-011: empty models list, missing models key, and absent cache all report 0 discovered."""
    monkeypatch.chdir(tmp_path)
    # Case 1: When capabilities.json file is absent entirely -> all providers UNAVAILABLE and 0 discovered
    _invoke(monkeypatch, "provider-summary")
    output = capsys.readouterr().out
    for name in cli.PROVIDERS:
        line = _line_for(output, name)
        _assert_status(line, "UNAVAILABLE")
        _assert_count(line, 0)

    # Case 2: When capabilities.json has cli_available=True with empty models or missing models key -> OK and 0 discovered
    cache_dir = tmp_path / ".orchestrator"
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "capabilities.json").write_text(json.dumps({
        "agy": {"provider": "agy", "cli_available": True, "models": []},
        "codex": {"provider": "codex", "cli_available": True},
    }), encoding="utf-8")
    _invoke(monkeypatch, "provider-summary")
    output = capsys.readouterr().out
    agy_line = _line_for(output, "agy")
    _assert_status(agy_line, "OK")
    _assert_count(agy_line, 0)
    codex_line = _line_for(output, "codex")
    _assert_status(codex_line, "OK")
    _assert_count(codex_line, 0)
    for name in cli.PROVIDERS:
        line = _line_for(output, name)
        _assert_count(line, 0)
        if name not in ("agy", "codex"):
            _assert_status(line, "UNAVAILABLE")


def test_text_summary_names_derive_from_registry_not_hardcoded(tmp_path, monkeypatch, capsys):
    """FR-004 / AC-003: a provider added to PROVIDERS beyond known examples appears with cached data."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "PROVIDERS", {**cli.PROVIDERS, "customx": type("CustomProvider", (), {})})
    _write_cache(tmp_path, {"customx": _cap_entry("customx", True, ["x1", "x2", "x3"])})
    _invoke(monkeypatch, "provider-summary")
    output = capsys.readouterr().out
    custom_line = _line_for(output, "customx")
    _assert_status(custom_line, "OK")
    _assert_count(custom_line, 3)
    for name in ("agy", "opencode", "codex"):

        assert name in output


def test_text_summary_does_not_discover_or_spawn_subprocess(tmp_path, monkeypatch, capsys):
    """FR-003 / AC-004, AC-005 / FR-007, AC-008: text mode uses cache only; no discovery, probes, subprocesses, writes."""
    monkeypatch.chdir(tmp_path)
    entries = {"agy": _cap_entry("agy", True, ["m1", "m2"]), "opencode": _cap_entry("opencode", True, ["o1"])}
    _write_cache(tmp_path, entries)

    def boom(*args, **kwargs):
        raise AssertionError(f"forbidden call during provider-summary: {args!r} {kwargs!r}")

    monkeypatch.setattr(cli, "discover_all", boom)
    monkeypatch.setattr(cli, "write_selection_report", boom)
    for provider_class in cli.PROVIDERS.values():
        monkeypatch.setattr(provider_class, "discover", boom)
    for name in ("run", "Popen", "call", "check_call", "check_output"):
        if hasattr(subprocess, name):
            monkeypatch.setattr(subprocess, name, boom)
    for mod_name in ("orchestrator.doctor.subprocess",):
        try:
            m = sys.modules.get(mod_name)
            if m:
                for name in ("run", "Popen", "call", "check_call", "check_output"):
                    if hasattr(m, name):
                        monkeypatch.setattr(m, name, boom)
        except Exception:
            pass
    import os
    if hasattr(os, "system"):
        monkeypatch.setattr(os, "system", boom)
    if hasattr(os, "popen"):
        monkeypatch.setattr(os, "popen", boom)

    def check_safe_path(target):
        try:
            p = Path(target).resolve()
            if not str(p).startswith(str(tmp_path.resolve())):
                raise AssertionError(f"forbidden write outside tmp_path: {target}")
        except AssertionError:
            raise
        except Exception as exc:
            raise AssertionError(f"forbidden path access: {target}") from exc

    orig_open = open

    def safe_open(file, mode="r", *args, **kwargs):
        if any(m in mode for m in ("w", "a", "x", "+")):
            check_safe_path(file)
        return orig_open(file, mode, *args, **kwargs)

    monkeypatch.setattr("builtins.open", safe_open)

    orig_path_open = Path.open

    def safe_path_open(self, mode="r", *args, **kwargs):
        if any(m in mode for m in ("w", "a", "x", "+")):
            check_safe_path(self)
        return orig_path_open(self, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", safe_path_open)

    orig_write_text = Path.write_text

    def safe_write_text(self, *args, **kwargs):
        check_safe_path(self)
        return orig_write_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", safe_write_text)

    orig_write_bytes = Path.write_bytes

    def safe_write_bytes(self, *args, **kwargs):
        check_safe_path(self)
        return orig_write_bytes(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_bytes", safe_write_bytes)

    orig_mkdir = Path.mkdir

    def safe_mkdir(self, *args, **kwargs):
        check_safe_path(self)
        return orig_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", safe_mkdir)

    for op in ("remove", "unlink", "mkdir", "makedirs", "rename", "replace"):
        if hasattr(os, op):
            def make_safe_os(orig_fn):
                def safe_os(path, *args, **kwargs):
                    check_safe_path(path)
                    return orig_fn(path, *args, **kwargs)
                return safe_os
            monkeypatch.setattr(os, op, make_safe_os(getattr(os, op)))

    before = _snapshot(tmp_path)
    _invoke(monkeypatch, "provider-summary")
    output = capsys.readouterr().out
    agy_line = _line_for(output, "agy")
    _assert_status(agy_line, "OK")
    _assert_count(agy_line, 2)
    assert _snapshot(tmp_path) == before
    assert json.loads((tmp_path / ".orchestrator" / "capabilities.json").read_text()) == entries


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
