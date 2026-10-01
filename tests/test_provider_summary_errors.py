"""T004 RED tests: controlled handling of expected provider-summary failures.

Traces: FR-005, FR-006, FR-007 / AC-007, AC-008, AC-009
(specs/001-provider-summary/tasks.md, User Story 4; plan.md D-001, D-005).

RED expectation: the command boundary currently does not contain expected
local cache failures. Malformed cache JSON is silently tolerated (the command
exits 0 and prints a bogus all-UNAVAILABLE/0 summary), an unreadable cache
path or malformed cache shape escapes `cli.main` as a raw traceback from
`ProviderCapabilities(**item)`/`dict(...)`/`read_text`, and `--json` mode
emits a full payload for unreadable-shaped data. Per plan D-005, GREEN must
catch expected local read/shape errors at the command boundary, print a
concise stderr diagnostic, and exit non-zero without a traceback, while
argparse keeps rejecting unsupported flags and valid cache reads stay exit 0.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _cap_entry(name, cli_available, models):
    return {"provider": name, "cli_available": cli_available, "models": list(models)}


def _write_cache_raw(root, content):
    cache_dir = root / ".orchestrator"
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "capabilities.json").write_text(content, encoding="utf-8")


def _run_cli(tmp_path, *argv):
    env = dict(os.environ, PYTHONPATH=str(REPO_ROOT))
    result = subprocess.run(
        [sys.executable, "-m", "orchestrator", *argv],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        stdin=subprocess.DEVNULL,
    )
    return result


def _assert_contained_failure(result):
    """FR-005 / AC-009: non-zero status, concise readable stderr diagnostic, no raw traceback."""
    assert result.returncode != 0, (
        f"expected failure must exit non-zero, got {result.returncode} "
        f"(stdout={result.stdout!r} stderr={result.stderr!r})"
    )
    assert "Traceback (most recent call last)" not in result.stderr, (
        f"expected failure must not expose an unhandled traceback: {result.stderr!r}"
    )
    assert result.stderr.strip(), f"expected failure must write a diagnostic to stderr: {result.stderr!r}"
    assert re.search(r"capabilities|provider-summary", result.stderr, re.IGNORECASE), (
        f"stderr diagnostic must identify the failing command or local data: {result.stderr!r}"
    )


def test_unsupported_flag_exits_nonzero_with_usage_and_no_traceback(tmp_path):
    """FR-005 / AC-009 + D-001/D-005: argparse keeps rejecting unknown flags (non-zero, usage on stderr)."""
    result = _run_cli(tmp_path, "provider-summary", "--flag-invalida")
    _assert_contained_failure(result)
    assert "usage" in result.stderr.lower(), f"expected argparse usage on stderr: {result.stderr!r}"
    assert result.stdout == "", f"stdout must stay empty for a rejected invocation: {result.stdout!r}"


def test_malformed_cache_json_text_mode_exits_nonzero_with_diagnostic(tmp_path):
    """FR-005 / AC-009 / spec edge 'dados ilegíveis': unreadable cache JSON fails instead of masquerading as empty."""
    _write_cache_raw(tmp_path, "not json {{{")
    result = _run_cli(tmp_path, "provider-summary")
    _assert_contained_failure(result)
    assert "discovered" not in result.stdout.lower(), (
        f"failed text invocation must not print a bogus summary: {result.stdout!r}"
    )


def test_unreadable_cache_path_text_mode_exits_nonzero_without_traceback(tmp_path):
    """FR-005 / AC-009: local read I/O failure (directory where the cache file must be) is contained."""
    cache_dir = tmp_path / ".orchestrator"
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "capabilities.json").mkdir()
    result = _run_cli(tmp_path, "provider-summary")
    _assert_contained_failure(result)
    assert "discovered" not in result.stdout.lower(), (
        f"failed text invocation must not print a bogus summary: {result.stdout!r}"
    )


def test_cache_top_level_shape_error_is_contained(tmp_path):
    """FR-005 / AC-009: a non-mapping cache document (list instead of object) is a contained shape failure."""
    _write_cache_raw(tmp_path, json.dumps(["agy"]))
    result = _run_cli(tmp_path, "provider-summary")
    _assert_contained_failure(result)
    assert "discovered" not in result.stdout.lower(), (
        f"failed text invocation must not print a bogus summary: {result.stdout!r}"
    )


def test_cache_entry_unexpected_fields_shape_error_is_contained(tmp_path):
    """FR-005 / AC-009: an entry with unknown fields (incompletos/inconsistentes) is a contained shape failure."""
    _write_cache_raw(tmp_path, json.dumps({"agy": {"provider": "agy", "cli_available": True, "bogus_key": 1}}))
    result = _run_cli(tmp_path, "provider-summary")
    _assert_contained_failure(result)
    assert "discovered" not in result.stdout.lower(), (
        f"failed text invocation must not print a bogus summary: {result.stdout!r}"
    )


def test_malformed_cache_json_mode_exits_nonzero_without_payload(tmp_path):
    """FR-005 / AC-009 + plan contract: --json reserves stdout for success; failures print no payload."""
    _write_cache_raw(tmp_path, "not json {{{")
    result = _run_cli(tmp_path, "provider-summary", "--json")
    _assert_contained_failure(result)
    assert result.stdout.strip() == "", (
        f"stdout must stay reserved for the JSON payload on success; failure emitted {result.stdout!r}"
    )


def test_unsupported_flag_json_mode_exits_nonzero_without_payload(tmp_path):
    """FR-005 / AC-009: unknown flags are rejected before any payload, even with --json."""
    result = _run_cli(tmp_path, "provider-summary", "--json", "--flag-invalida")
    _assert_contained_failure(result)
    assert "usage" in result.stderr.lower(), f"expected argparse usage on stderr: {result.stderr!r}"
    assert result.stdout.strip() == "", f"stdout must stay free of payloads on failure: {result.stdout!r}"


def test_valid_cache_success_path_stays_green(tmp_path):
    """FR-005 guard (D-005): containment must not misfire — nominal local data still exits 0 with output."""
    _write_cache_raw(tmp_path, json.dumps({"agy": _cap_entry("agy", True, ["m1", "m2"])}))
    text = _run_cli(tmp_path, "provider-summary")
    assert text.returncode == 0, f"valid cache must exit 0: {text.stderr!r}"
    assert "discovered" in text.stdout.lower()
    report = json.loads(_run_cli(tmp_path, "provider-summary", "--json").stdout)
    assert report["providers"], "valid cache must still serialize the registry summary"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
