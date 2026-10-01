"""T003 RED tests: strictly local registry/cache-only data access for `provider-summary`.

Traces: FR-003, FR-004, FR-006, FR-007 / AC-003, AC-004, AC-005, AC-007,
AC-008 (specs/001-provider-summary/tasks.md, User Story 3).

RED expectation: the provider-summary command path currently delegates to
`orchestrator.cli._router`, which reads the SQLite state store through
`StateStore.load_provider_metrics` (a read-only connection that rewrites no
file bytes), opens and re-initializes the state database through
`StateStore(db_path).routing_history()`, and constructs a `ModelRouter`.
File-write snapshots alone cannot prove the state store is never read, so
these tests guard the real access boundaries themselves: the `StateStore`
class, the `sqlite3.connect` entry point used by every module, the
`ModelRouter` constructor, and any Python-level open of files under
`.orchestrator/state/` during invocation. The guards patch those protected
boundaries (never the `cli.StateStore` / `cli.ModelRouter` aliases, which a
valid GREEN may drop from `orchestrator/cli.py`; every optional alias patch
uses `raising=False`), so the tests stay meaningful across implementation
refactors while failing now for the missing registry/cache-only behavior.
"""
import builtins
import contextlib
import io
import json
import os
import re
import socket
import sqlite3
import subprocess
import sys
import urllib.request
from pathlib import Path

import pytest

from orchestrator import cli
from orchestrator.agents.router import ModelRouter
from orchestrator.providers import PROVIDERS
from orchestrator.storage.sqlite import StateStore


def _violation(calls, reason):
    def boom(*args, **kwargs):
        calls.append((reason, args, kwargs))
        raise AssertionError(f"forbidden during provider-summary ({reason}): {args!r} {kwargs!r}")

    return boom


def _cap_entry(name, cli_available, models):
    return {"provider": name, "cli_available": cli_available, "models": list(models)}


def _write_cache(root, entries):
    cache_dir = root / ".orchestrator"
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "capabilities.json").write_text(json.dumps(entries), encoding="utf-8")


def _seed_state_db(root):
    db_path = root / ".orchestrator" / "state" / "orchestrator.sqlite3"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    StateStore(db_path)
    return db_path


def _snapshot(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def _invoke(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["orchestrator", *argv])
    try:
        ret = cli.main()
        assert ret in (0, None), f"provider-summary returned non-zero code {ret!r}"
    except SystemExit as exc:
        assert exc.code in (0, None), f"provider-summary must exit 0, got {exc.code!r}"


def _invoke_json(monkeypatch, capsys, *argv):
    _invoke(monkeypatch, *argv)
    captured = capsys.readouterr().out
    assert captured.strip(), "expected the JSON payload on stdout for --json"
    return json.loads(captured)


def _line_for(output, name):
    pattern = re.compile(rf"^\s*{re.escape(name)}\b", re.IGNORECASE)
    lines = [line for line in output.splitlines() if pattern.search(line)]
    assert lines, f"provider '{name}' line missing from stdout:\n{output}"
    return lines[0]


def _assert_count(line, count):
    pattern = rf"\b{count}\s*(?:discovered|descobertos|models?|modelos?)\b"
    assert re.search(pattern, line, re.IGNORECASE), f"expected count {count} in line: {line!r}"


def _assert_status(line, expected_status):
    if expected_status == "OK":
        assert re.search(r"\bOK\b", line), f"expected OK in line: {line!r}"
        assert not re.search(r"\bUNAVAILABLE\b", line), f"contradictory UNAVAILABLE in line: {line!r}"
    else:
        assert re.search(r"\bUNAVAILABLE\b", line), f"expected UNAVAILABLE in line: {line!r}"
        assert not re.search(r"\bOK\b", line), f"contradictory OK in line: {line!r}"


def _forbid_external_boundaries(monkeypatch, calls):
    """FR-003 / AC-004, AC-005: no adapter construction, discovery, probe, subprocess,
    socket, or HTTP call may run. `cli`-alias patches use `raising=False` so a GREEN
    that prunes unused imports from `orchestrator/cli.py` cannot break the guard; the
    real adapter classes and provider-module functions are patched directly."""
    boom = _violation(calls, "external discovery/probe/subprocess boundary")
    import orchestrator.doctor as doctor_module
    import orchestrator.providers.common as provider_common

    monkeypatch.setattr(cli, "discover_all", boom, raising=False)
    monkeypatch.setattr(cli, "write_selection_report", boom, raising=False)
    monkeypatch.setattr(doctor_module, "discover_all", boom, raising=False)
    monkeypatch.setattr(doctor_module, "live_smoke_tests", boom, raising=False)
    for fn in ("probe", "resolve_binary", "execute"):
        monkeypatch.setattr(provider_common, fn, boom, raising=False)
    for cls in PROVIDERS.values():
        monkeypatch.setattr(cls, "discover", boom, raising=False)
        monkeypatch.setattr(cls, "__init__", boom, raising=False)
    for fn in ("run", "Popen", "call", "check_call", "check_output"):
        monkeypatch.setattr(subprocess, fn, boom, raising=False)
    monkeypatch.setattr(os, "system", boom, raising=False)
    monkeypatch.setattr(os, "popen", boom, raising=False)
    monkeypatch.setattr(socket, "create_connection", boom, raising=False)
    monkeypatch.setattr(urllib.request, "urlopen", boom, raising=False)


def _forbid_state_and_router(monkeypatch, calls):
    """FR-003 / FR-004 / AC-003: guard the actual state-store and router boundaries at
    their source modules, so reads are detected no matter which module performs them
    and no `cli.StateStore` / `cli.ModelRouter` alias needs to keep existing."""
    boom = _violation(calls, "state store or ModelRouter access")
    monkeypatch.setattr(StateStore, "__init__", boom)
    monkeypatch.setattr(StateStore, "connect", boom)
    monkeypatch.setattr(StateStore, "load_provider_metrics", boom)
    monkeypatch.setattr(StateStore, "load_model_metrics", boom)
    monkeypatch.setattr(sqlite3, "connect", boom)
    monkeypatch.setattr(ModelRouter, "__init__", boom)


@contextlib.contextmanager
def _forbid_state_file_reads(monkeypatch, calls):
    """FR-003 / AC-003: during the command invocation, any Python-level open/read of a
    file under `.orchestrator/state/` (direct SQLite-file access bypassing StateStore)
    is a violation. Scoped to the invocation so test-side snapshotting still works."""
    real_open = io.open

    def guarded_open(file, *args, **kwargs):
        try:
            parts = Path(os.fspath(file)).parts
        except TypeError:
            parts = ()
        if ".orchestrator" in parts and "state" in parts:
            calls.append(("state store file read", (str(file),), {}))
            raise AssertionError(f"provider-summary must not read state-store files directly: {file!r}")
        return real_open(file, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(builtins, "open", guarded_open)
        patch.setattr(io, "open", guarded_open)
        yield


def _fixture_registry(monkeypatch, calls):
    """FR-004 / AC-003: add a provider beyond any assumed examples to the shared
    PROVIDERS registry dict itself (visible through every module binding), with its
    constructor and discovery treated as forbidden calls."""
    monkeypatch.setitem(
        PROVIDERS,
        "customx",
        type(
            "CustomProvider",
            (),
            {
                "__init__": _violation(calls, "provider adapter construction"),
                "discover": _violation(calls, "provider adapter discovery"),
            },
        ),
    )


def test_text_summary_does_not_rewrite_sqlite_state(tmp_path, monkeypatch, capsys):
    """FR-003 / AC-004, AC-005 / AC-007: text mode is read-only; the state boundary is
    never touched and seeded workspace files stay byte-identical."""
    monkeypatch.chdir(tmp_path)
    _write_cache(tmp_path, {
        "agy": _cap_entry("agy", True, ["m1", "m2"]),
        "codex": _cap_entry("codex", False, ["c1"]),
        "opencode": _cap_entry("opencode", True, ["o1"]),
    })
    _seed_state_db(tmp_path)
    calls = []
    _forbid_external_boundaries(monkeypatch, calls)
    _forbid_state_and_router(monkeypatch, calls)
    before = _snapshot(tmp_path)
    with _forbid_state_file_reads(monkeypatch, calls):
        _invoke(monkeypatch, "provider-summary")
    output = capsys.readouterr().out
    agy_line = _line_for(output, "agy")
    _assert_status(agy_line, "OK")
    _assert_count(agy_line, 2)
    assert calls == [], f"forbidden boundaries invoked during provider-summary: {calls!r}"
    assert _snapshot(tmp_path) == before, (
        "provider-summary text mode must not rewrite the SQLite state store or any workspace file (read-only invariant)"
    )


def test_json_summary_does_not_rewrite_sqlite_state(tmp_path, monkeypatch, capsys):
    """FR-003 / AC-004, AC-005 / AC-007: JSON mode is read-only; the state boundary is
    never touched and seeded workspace files stay byte-identical."""
    monkeypatch.chdir(tmp_path)
    _write_cache(tmp_path, {
        "agy": _cap_entry("agy", True, ["m1", "m2"]),
        "codex": _cap_entry("codex", False, ["c1"]),
        "opencode": _cap_entry("opencode", True, ["o1"]),
    })
    _seed_state_db(tmp_path)
    calls = []
    _forbid_external_boundaries(monkeypatch, calls)
    _forbid_state_and_router(monkeypatch, calls)
    before = _snapshot(tmp_path)
    with _forbid_state_file_reads(monkeypatch, calls):
        report = _invoke_json(monkeypatch, capsys, "provider-summary", "--json")
    items = {item["name"]: item for item in report["providers"]}
    assert items["agy"]["available"] is True and items["agy"]["model_count"] == 2
    assert items["codex"]["available"] is False and items["codex"]["model_count"] == 1
    assert items["opencode"]["model_count"] == 1
    assert calls == [], f"forbidden boundaries invoked during provider-summary --json: {calls!r}"
    assert _snapshot(tmp_path) == before, (
        "provider-summary --json mode must not rewrite the SQLite state store or any workspace file (read-only invariant)"
    )


def test_text_summary_reads_registry_and_cache_only(tmp_path, monkeypatch, capsys):
    """FR-004 / AC-003 + FR-003 / AC-008: names come from the shared PROVIDERS registry
    and values from capabilities.json; the state store is never read through any path."""
    monkeypatch.chdir(tmp_path)
    calls = []
    _fixture_registry(monkeypatch, calls)
    _write_cache(tmp_path, {
        "customx": _cap_entry("customx", True, ["x1", "x2", "x3"]),
        "agy": _cap_entry("agy", False, ["m1"]),
    })
    _seed_state_db(tmp_path)
    _forbid_external_boundaries(monkeypatch, calls)
    _forbid_state_and_router(monkeypatch, calls)
    with _forbid_state_file_reads(monkeypatch, calls):
        _invoke(monkeypatch, "provider-summary")
    output = capsys.readouterr().out
    custom_line = _line_for(output, "customx")
    _assert_status(custom_line, "OK")
    _assert_count(custom_line, 3)
    agy_line = _line_for(output, "agy")
    _assert_status(agy_line, "UNAVAILABLE")
    _assert_count(agy_line, 1)
    _assert_status(_line_for(output, "opencode"), "UNAVAILABLE")
    assert calls == [], f"forbidden boundaries invoked during provider-summary: {calls!r}"


def test_json_summary_reads_registry_and_cache_only(tmp_path, monkeypatch, capsys):
    """FR-004 / AC-003 + FR-003 / AC-008: the JSON payload derives from PROVIDERS order
    and the capability cache without any state-store or router access."""
    monkeypatch.chdir(tmp_path)
    calls = []
    _fixture_registry(monkeypatch, calls)
    _write_cache(tmp_path, {
        "customx": _cap_entry("customx", True, ["x1", "x2"]),
        "agy": _cap_entry("agy", True, []),
    })
    _seed_state_db(tmp_path)
    _forbid_external_boundaries(monkeypatch, calls)
    _forbid_state_and_router(monkeypatch, calls)
    with _forbid_state_file_reads(monkeypatch, calls):
        report = _invoke_json(monkeypatch, capsys, "provider-summary", "--json")
    assert [item["name"] for item in report["providers"]] == list(PROVIDERS)
    items = {item["name"]: item for item in report["providers"]}
    assert items["customx"]["available"] is True and items["customx"]["model_count"] == 2
    assert items["agy"]["available"] is True and items["agy"]["model_count"] == 0
    assert items["opencode"]["available"] is False and items["opencode"]["model_count"] == 0
    assert calls == [], f"forbidden boundaries invoked during provider-summary --json: {calls!r}"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
