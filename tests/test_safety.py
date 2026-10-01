from orchestrator.verification.policies import CommandPolicy


def test_blocks_dangerous_commands(tmp_path):
    assert not CommandPolicy(tmp_path).validate("git reset --hard")[0]
    assert not CommandPolicy(tmp_path).validate("rm -rf /")[0]


def test_blocks_outside_workspace_paths(tmp_path):
    assert not CommandPolicy(tmp_path).validate(["python","/etc/passwd"])[0]


def test_allows_safe_local_command(tmp_path):
    assert CommandPolicy(tmp_path).validate(["python","-m","pytest"])[0]
