import json

import pytest

from orchestrator.tdd_contract import parse_test_design, hash_test_files, changed_test_hashes
from orchestrator.verification.harness import VerificationHarness


def design():
    return {"task_id":"T018","requirement_ids":["FR-018"],"acceptance_criteria_ids":["AC-018-01"],
        "created_tests":["tests/test_queue.py::test_latest_window_wins"],
        "test_commands":[["python","-m","pytest","-q","tests/test_queue.py::test_latest_window_wins"]]}


def test_selects_only_designer_declared_task_test(tmp_path):
    path=tmp_path/"tests"/"test_queue.py"; path.parent.mkdir(); path.write_text("def test_latest_window_wins(): assert False\n")
    parsed=parse_test_design(json.dumps(design()),"T018",["FR-018"],["AC-018-01"],tmp_path,["tests/test_queue.py"])
    assert parsed.test_commands==[["python","-m","pytest","-q","tests/test_queue.py::test_latest_window_wins"]]
    envelope={"status":"SUCCESS","response":json.dumps(design())}
    assert parse_test_design(envelope,"T018",["FR-018"],["AC-018-01"],tmp_path,["tests/test_queue.py"]).task_id=="T018"


def test_rejects_full_suite_or_uncreated_test(tmp_path):
    path=tmp_path/"tests"/"test_queue.py"; path.parent.mkdir(); path.write_text("def test_latest_window_wins(): assert False\n")
    data=design(); data["test_commands"]=[["python","-m","pytest","-q"]]
    with pytest.raises(ValueError,match="task-specific"): parse_test_design(json.dumps(data),"T018",["FR-018"],["AC-018-01"],tmp_path,["tests/test_queue.py"])
    with pytest.raises(ValueError,match="modified test"): parse_test_design(json.dumps(design()),"T018",["FR-018"],["AC-018-01"],tmp_path,[])
    data=design(); data["test_commands"]=[["python","-c","print('unsafe')","tests/test_queue.py::test_latest_window_wins"]]
    with pytest.raises(ValueError,match="approved test runner"): parse_test_design(json.dumps(data),"T018",["FR-018"],["AC-018-01"],tmp_path,["tests/test_queue.py"])


def test_red_classifies_expected_invalid_infrastructure_and_unexpected(tmp_path):
    harness=VerificationHarness(tmp_path)
    expected=harness.run_red(["python","-c","print('collected 1 item'); print('FAILED tests/test_x.py::test_x - AssertionError: expected value'); raise SystemExit(1)"],expected_test_ids=["tests/test_x.py::test_x"])
    invalid=harness.run_red(["python","-c","print('collected 1 item'); print('SyntaxError: broken'); raise SystemExit(1)"])
    unexpected=harness.run_red(["python","-c","print('collected 1 item'); print('1 passed'); raise SystemExit(0)"])
    infrastructure=harness.run_red(["missing-test-runner-binary"])
    assert expected.classification=="EXPECTED_FAILURE" and expected.cause
    assert invalid.classification=="INVALID_TEST"
    assert unexpected.classification=="UNEXPECTED_FAILURE"
    assert infrastructure.classification=="INFRASTRUCTURE_FAILURE"


def test_hash_snapshot_detects_test_and_fixture_tampering(tmp_path):
    tests=tmp_path/"tests"; tests.mkdir()
    test=tests/"test_x.py"; fixture=tests/"conftest.py"
    test.write_text("def test_x(): assert False\n"); fixture.write_text("VALUE=1\n")
    before=hash_test_files(tmp_path)
    fixture.write_text("VALUE=2\n")
    assert changed_test_hashes(before,tmp_path)==["tests/conftest.py"]
