from orchestrator.verification.harness import VerificationHarness


def test_red_requires_discovered_behavior_failure(tmp_path):
    harness=VerificationHarness(tmp_path)
    invalid=harness.run_red(["python","-c","raise SystemExit(2)"])
    assert invalid.classification=="INVALID_TEST"
    expected=harness.run_red(["python","-c","print('collected 1 item'); print('FAILED test_x - AssertionError: expected value'); raise SystemExit(1)"])
    assert expected.classification=="EXPECTED_FAILURE"


def test_red_rejects_syntax_or_import_failure(tmp_path):
    harness=VerificationHarness(tmp_path)
    result=harness.run_red(["python","-c","print('collected 1 item'); print('ModuleNotFoundError: missing') ; raise SystemExit(1)"])
    assert result.classification=="INVALID_TEST"
