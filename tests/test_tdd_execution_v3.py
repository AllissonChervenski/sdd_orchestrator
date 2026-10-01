import json

from orchestrator.config.models import AgentResult
from orchestrator.tdd import execute_tdd_task
from orchestrator.verification.harness import VerificationResult
from orchestrator.workflow.tdd import TDDTask
from orchestrator.workflow.transitions import TDDPhase


TASK_COMMAND=["python","-m","pytest","-q","tests/test_feature.py::test_feature"]
REGRESSION=["python","-m","pytest","-q"]
VALIDATION=json.dumps({"status":"PASS","issues":[],"summary":"verified"})


class Runner:
    def __init__(self,root,tamper=False,approve_red=False): self.root=root; self.tamper=tamper; self.approve_red=approve_red; self.roles=[]; self.calls=[]
    def run(self,role,prompt,**kwargs):
        self.roles.append(role)
        self.calls.append((role,prompt,kwargs))
        if role=="test_designer" and "RED:" in prompt:
            path=self.root/"tests"/"test_feature.py"; path.parent.mkdir(exist_ok=True)
            path.write_text("def test_feature(): assert False\n")
            raw=json.dumps({"task_id":"T1","requirement_ids":["FR1"],"acceptance_criteria_ids":["AC1"],
                "created_tests":["tests/test_feature.py::test_feature"],"test_commands":[TASK_COMMAND]})
        elif role=="coder":
            if self.tamper: (self.root/"tests"/"test_feature.py").write_text("def test_feature(): assert True\n")
            else: (self.root/"feature.py").write_text("VALUE=1\n")
            raw="done"
        elif role=="test_validator" and self.approve_red and "TEST_TAMPERING" in prompt:
            raw=json.dumps({"status":"REVISE","summary":"test correction required","issues":["RETURN_TO_RED: test needs correction"]})

        elif role in ("test_validator","code_reviewer"): raw=VALIDATION
        else: raw="analysis" if role=="test_designer" else "done"
        return AgentResult("fake",None,role,True,stdout=raw)


class Harness:
    def __init__(self,regression_pass=True): self.commands=[]; self.regression_pass=regression_pass
    def run_red(self,command,**kwargs):
        self.commands.append(("RED",command))
        return VerificationResult(command,False,1,"collected 1 item; FAILED tests/test_feature.py::test_feature - AssertionError","",0.1,"EXPECTED_FAILURE",cause="linked assertion")
    def run_command(self,command,**kwargs):
        self.commands.append((kwargs.get("category"),command))
        success=command!=REGRESSION or self.regression_pass
        return VerificationResult(command,success,0 if success else 1,"","",0.1,"PASS" if success else "FAIL")


def test_executor_runs_only_declared_task_test_before_regression(tmp_path):
    runner=Runner(tmp_path); harness=Harness(); task=TDDTask("T1",["FR1"],["AC1"])
    execute_tdd_task(task,runner,harness,tmp_path,regression_commands=[REGRESSION],workflow_dir=tmp_path/"runs")
    assert task.phase==TDDPhase.COMPLETE
    assert harness.commands[0]==("RED",TASK_COMMAND)
    assert harness.commands[1]==("task_tests",TASK_COMMAND)
    assert harness.commands[2]==("regression_tests",REGRESSION)
    assert (tmp_path/"runs"/"T1"/"tdd.md").exists()
    coder_calls=[call for call in runner.calls if call[0]=="coder"]
    assert len(coder_calls)==1
    assert coder_calls[0][2]["skill_name"]=="speckit-implement"
    assert "task T1 only" in coder_calls[0][1]
    assert "Do not process any other task" in coder_calls[0][1]


def test_executor_blocks_and_escalates_test_tampering(tmp_path):
    runner=Runner(tmp_path,tamper=True); harness=Harness(); task=TDDTask("T1",["FR1"],["AC1"])
    execute_tdd_task(task,runner,harness,tmp_path,regression_commands=[REGRESSION],workflow_dir=tmp_path/"runs")
    assert task.phase==TDDPhase.BLOCKED and task.evidence["test_tampering_detected"]
    assert runner.roles.count("test_validator")==3


def test_regression_failure_blocks_complete(tmp_path):
    runner=Runner(tmp_path); harness=Harness(regression_pass=False); task=TDDTask("T1",["FR1"],["AC1"])
    execute_tdd_task(task,runner,harness,tmp_path,regression_commands=[REGRESSION],max_attempts=1)
    assert task.phase==TDDPhase.BLOCKED


def test_approved_test_correction_returns_formally_to_red(tmp_path):
    runner=Runner(tmp_path,tamper=True,approve_red=True); harness=Harness(); task=TDDTask("T1",["FR1"],["AC1"])
    execute_tdd_task(task,runner,harness,tmp_path,regression_commands=[REGRESSION])
    assert task.phase==TDDPhase.RED_GENERATE and task.evidence["red_restart_required"]


def test_invalid_red_never_reaches_green(tmp_path):
    class InvalidRedHarness(Harness):
        def run_red(self, command, **kwargs):
            return VerificationResult(command,False,1,"collected 1 item; ImportError: missing module","",0.1,"INVALID_TEST")

    runner=Runner(tmp_path); task=TDDTask("T1",["FR1"],["AC1"])
    execute_tdd_task(task,runner,InvalidRedHarness(),tmp_path,regression_commands=[REGRESSION])
    assert task.phase==TDDPhase.BLOCKED
    assert "coder" not in runner.roles


def test_refactor_test_tampering_blocks_regression_and_review(tmp_path):
    class TamperingRefactor(Runner):
        def run(self,role,prompt,**kwargs):
            if role=="refactorer":
                (self.root/"tests"/"test_feature.py").write_text("def test_feature(): assert True\n")
            return super().run(role,prompt,**kwargs)

    runner=TamperingRefactor(tmp_path); task=TDDTask("T1",["FR1"],["AC1"]); harness=Harness()
    execute_tdd_task(task,runner,harness,tmp_path,regression_commands=[REGRESSION])
    assert task.phase==TDDPhase.BLOCKED
    assert task.evidence["test_tampering_detected"]
    assert "code_reviewer" not in runner.roles


def test_red_validation_revise_retries_and_succeeds(tmp_path):
    class RevisingRunner(Runner):
        def __init__(self, root):
            super().__init__(root)
            self.val_count = 0
        def run(self, role, prompt, **kwargs):
            if role == "test_validator" and "TEST_TAMPERING" not in prompt and "Validate observed RED" not in prompt:
                self.val_count += 1
                if self.val_count == 1:
                    return AgentResult("fake", None, role, True, stdout=json.dumps({"status": "REVISE", "summary": "fix assertions", "issues": ["assertion too weak"]}))
            return super().run(role, prompt, **kwargs)

    runner = RevisingRunner(tmp_path); task = TDDTask("T1", ["FR1"], ["AC1"]); harness = Harness()
    execute_tdd_task(task, runner, harness, tmp_path, regression_commands=[REGRESSION])
    assert task.phase == TDDPhase.COMPLETE
    assert runner.val_count == 2
    assert task.attempts["red"] == 1

