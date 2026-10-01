import hashlib
import json
import socket
import sqlite3
import subprocess
from types import SimpleNamespace

import pytest

from orchestrator.storage.sqlite import StateStore
from orchestrator.workflow.resume import WorkspaceFingerprint, compare_fingerprints, resume_workflow


def _repo(tmp_path):
    subprocess.run(["git","init","-q",str(tmp_path)],check=True)
    subprocess.run(["git","config","user.name","Test"],cwd=tmp_path,check=True)
    subprocess.run(["git","config","user.email","test@example.invalid"],cwd=tmp_path,check=True)
    (tmp_path/"README.md").write_text("base\n")
    subprocess.run(["git","add","README.md"],cwd=tmp_path,check=True)
    subprocess.run(["git","commit","-qm","base"],cwd=tmp_path,check=True)
    return tmp_path


def _store(root): return StateStore(root/".orchestrator"/"state"/"state.sqlite3")


def test_fingerprint_classifies_safe_artifact_test_branch_and_head(tmp_path):
    root=_repo(tmp_path)
    (root/"spec.md").write_text("approved")
    (root/"tests").mkdir(); (root/"tests"/"test_x.py").write_text("assert True")
    sensor=WorkspaceFingerprint(root)
    old=sensor.capture(artifact_paths=["spec.md"],test_paths=["tests/test_x.py"])
    assert compare_fingerprints(old,sensor.capture(artifact_paths=["spec.md"],test_paths=["tests/test_x.py"]))[0]=="CLEAN_MATCH"
    (root/".orchestrator"/"logs").mkdir(parents=True)
    (root/".orchestrator"/"logs"/"event.log").write_text("log")
    assert compare_fingerprints(old,sensor.capture(artifact_paths=["spec.md"],test_paths=["tests/test_x.py"]))[0]=="SAFE_DIVERGENCE"
    (root/".orchestrator"/"runs"/"demo").mkdir(parents=True)
    (root/".orchestrator"/"runs"/"demo"/"report.md").write_text("generated report")
    assert compare_fingerprints(old,sensor.capture(artifact_paths=["spec.md"],test_paths=["tests/test_x.py"]))[0]=="SAFE_DIVERGENCE"
    (root/"spec.md").write_text("external edit")
    assert compare_fingerprints(old,sensor.capture(artifact_paths=["spec.md"],test_paths=["tests/test_x.py"]))[0]=="UNSAFE_DIVERGENCE"
    (root/"spec.md").unlink()
    assert compare_fingerprints(old,sensor.capture(artifact_paths=["spec.md"],test_paths=["tests/test_x.py"]))[0]=="MISSING_FILES"
    (root/"spec.md").write_text("approved")
    (root/"tests"/"test_x.py").write_text("assert False")
    status,diff=compare_fingerprints(old,sensor.capture(artifact_paths=["spec.md"],test_paths=["tests/test_x.py"]))
    assert status=="UNSAFE_DIVERGENCE" and any(item["field"]=="test_hashes" for item in diff)
    subprocess.run(["git","switch","-qc","other"],cwd=root,check=True)
    assert compare_fingerprints(old,sensor.capture())[0]=="BRANCH_CHANGED"
    subprocess.run(["git","switch","-q","-"],cwd=root,check=True)
    (root/"README.md").write_text("new")
    subprocess.run(["git","commit","-qam","new"],cwd=root,check=True)
    assert compare_fingerprints(old,sensor.capture())[0]=="BASE_CHANGED"


def test_checkpoint_idempotency_lock_orphan_and_abort(tmp_path):
    root=_repo(tmp_path); store=_store(root); wid=store.create_workflow("feature")
    fp=WorkspaceFingerprint(root).capture(wid)
    first=store.create_checkpoint(wid,"SPEC_VALIDATED:-:1","SPEC_VALIDATED",fp)
    assert store.create_checkpoint(wid,"SPEC_VALIDATED:-:1","SPEC_VALIDATED",fp)==first
    assert len(store.checkpoints(wid))==1
    with store.workflow_lock(wid):
        with pytest.raises(RuntimeError,match="WORKFLOW_ALREADY_RUNNING"):
            with store.workflow_lock(wid): pass
    with store.connect() as db:
        db.execute("INSERT INTO workflow_locks VALUES(?,?,?,?,?)",(wid,999999,socket.gethostname(),"dead","now"))
    with store.workflow_lock(wid): pass
    store.abort_workflow(wid)
    assert store.get_workflow(wid)["stage"]=="ABORTED"
    assert len(store.checkpoints(wid))==1


class Harness:
    commands={"regression_tests":[["pytest","-q"]]}
    def run_red(self,command,expected_test_ids=()):
        return SimpleNamespace(classification="EXPECTED_FAILURE")
    def run_command(self,command,name="",category=""):
        return SimpleNamespace(success=True,status="PASS")


@pytest.mark.parametrize("stage,phase",[("RED_VALIDATED","GREEN_IMPLEMENT"),("GREEN_VALIDATED","GREEN_VERIFY"),("REFACTOR_VALIDATED","REVIEW")])
def test_resume_revalidates_tdd_checkpoint_without_duplicate_transition(tmp_path,stage,phase):
    root=_repo(tmp_path); store=_store(root); wid=store.create_workflow("feature")
    task_dir=root/".orchestrator"/"runs"/wid/"T1"; task_dir.mkdir(parents=True)
    (task_dir/"tdd.json").write_text(json.dumps({"phase":phase,"test_design":{"created_tests":["tests/test_x.py::test_x"],"test_commands":[["pytest","-q","tests/test_x.py::test_x"]]}}))
    fp=WorkspaceFingerprint(root).capture(wid,"T1")
    store.create_checkpoint(wid,f"{stage}:T1:1",stage,fp,"T1")
    store.update_workflow(wid,"RUNNING_VERIFICATION",{"workspace":str(root),"stage":"RUNNING_VERIFICATION"},"T1")
    continued=[]
    report=resume_workflow(store,wid,root,Harness(),continue_fn=lambda:continued.append(True))
    assert report["workspace_status"]=="CLEAN_MATCH"
    assert report["revalidation_result"]["pass"] is True
    assert report["interrupted_stage"]=="RUNNING_VERIFICATION"
    assert report["next_transition"]=="READY" and continued==[True]
    assert len(store.checkpoints(wid))==1
    assert len(store.list_resume_reports(wid))==1


def test_resume_blocks_unsafe_divergence_and_records_it(tmp_path):
    root=_repo(tmp_path); store=_store(root); wid=store.create_workflow("feature")
    (root/"spec.md").write_text("approved")
    fp=WorkspaceFingerprint(root).capture(wid,artifact_paths=["spec.md"])
    store.create_checkpoint(wid,"SPEC_VALIDATED:-:1","SPEC_VALIDATED",fp)
    (root/"spec.md").write_text("changed")
    report=resume_workflow(store,wid,root,Harness())
    assert report["workspace_status"]=="UNSAFE_DIVERGENCE"
    assert not report["revalidation_performed"]
    assert report["next_transition"] is None
    assert store.get_workflow(wid)["stage"]=="PLANNED"


def test_tdd_evidence_change_after_checkpoint_is_unsafe(tmp_path):
    root=_repo(tmp_path); store=_store(root); wid=store.create_workflow("feature")
    report=root/".orchestrator"/"runs"/wid/"T1"/"tdd.json"
    report.parent.mkdir(parents=True)
    report.write_text(json.dumps({"phase":"GREEN_IMPLEMENT","test_design":{"test_commands":[["pytest","-q","tests/test_x.py::test_x"]]}}))
    old=WorkspaceFingerprint(root).capture(wid,"T1")
    report.write_text(json.dumps({"phase":"COMPLETE","test_design":{"test_commands":[]}}))
    status,differences=compare_fingerprints(old,WorkspaceFingerprint(root).capture(wid,"T1"))
    assert status=="UNSAFE_DIVERGENCE"
    assert any(change.get("path","").endswith("tdd.json") for change in differences)


def test_resume_reconciles_interrupted_coder_only_after_scope_and_tests_pass(tmp_path):
    root=_repo(tmp_path); store=_store(root); wid=store.create_workflow("feature")
    folder=root/".orchestrator"/"runs"/wid; task_dir=folder/"T1"; task_dir.mkdir(parents=True)
    (folder/"tasks.md").write_text(json.dumps({"tasks":[{"id":"T1","allowed_files":["src/feature.py"]}]}))
    (task_dir/"tdd.json").write_text(json.dumps({"phase":"GREEN_IMPLEMENT","test_design":{"created_tests":["tests/test_x.py::test_x"],"test_commands":[["pytest","-q","tests/test_x.py::test_x"]]}}))
    store.create_checkpoint(wid,"RED_VALIDATED:T1:1","RED_VALIDATED",WorkspaceFingerprint(root).capture(wid,"T1"),"T1")
    store.update_workflow(wid,"RUNNING_AGENT",{"workspace":str(root),"running_role":"coder","running_provider":"opencode"},"T1")
    (root/"src").mkdir(); (root/"src"/"feature.py").write_text("implementation")
    gate=SimpleNamespace(input_fn=lambda prompt:"reconcile")
    continued=[]
    report=resume_workflow(store,wid,root,Harness(),interactive=gate,continue_fn=lambda:continued.append(True))
    assert report["workspace_status"]=="UNSAFE_DIVERGENCE"
    assert report["reconciliation"]["accepted"] is True
    assert report["reconciled_checkpoint"]=="GREEN_VALIDATED"
    assert report["revalidation_result"]["pass"] is True and continued==[True]
    assert store.latest_checkpoint(wid)["stage"]=="GREEN_VALIDATED"
    assert json.loads((task_dir/"tdd.json").read_text())["coder_provider"]=="opencode"


def test_resume_reconciles_speckit_markdown_task_contract(tmp_path):
    root=_repo(tmp_path); store=_store(root); wid=store.create_workflow("feature")
    folder=root/".orchestrator"/"runs"/wid; task_dir=folder/"T001"; task_dir.mkdir(parents=True)
    feature_dir=root/"specs"/"feature"; feature_dir.mkdir(parents=True)
    (root/".specify").mkdir()
    (root/".specify"/"feature.json").write_text(json.dumps({"feature_directory":"specs/feature"}))
    metadata={"requirements":["FR-001"],"acceptance_criteria":["AC-001"],"plan_decisions":["D-001"],
              "dependencies":[],"test_type":"UNIT","allowed_files":["src/feature.py"],
              "tdd_phases":["RED","GREEN","REFACTOR"]}
    (feature_dir/"tasks.md").write_text("# Tasks: Feature\n- [ ] T001 [US1] Implement in src/feature.py\n"
                                   f"  <!-- harness-task {json.dumps(metadata)} -->\n")
    (task_dir/"tdd.json").write_text(json.dumps({"phase":"GREEN_IMPLEMENT","test_design":{
        "created_tests":["tests/test_x.py::test_x"],"test_commands":[["pytest","-q","tests/test_x.py::test_x"]]}}))
    store.create_checkpoint(wid,"RED_VALIDATED:T001:1","RED_VALIDATED",WorkspaceFingerprint(root).capture(wid,"T001"),"T001")
    store.update_workflow(wid,"RUNNING_AGENT",{"workspace":str(root),"running_role":"coder","running_provider":"codex"},"T001")
    (root/"src").mkdir(); (root/"src"/"feature.py").write_text("implementation")
    report=resume_workflow(store,wid,root,Harness(),interactive=SimpleNamespace(input_fn=lambda prompt:"reconcile"))
    assert report["reconciliation"]["accepted"] is True
    assert report["reconciled_checkpoint"]=="GREEN_VALIDATED"


def test_resume_rejects_failed_regression_and_never_marks_task_complete(tmp_path):
    root=_repo(tmp_path); store=_store(root); wid=store.create_workflow("feature")
    task_dir=root/".orchestrator"/"runs"/wid/"T1"; task_dir.mkdir(parents=True)
    (task_dir/"tdd.json").write_text(json.dumps({"phase":"COMPLETE","test_design":{"created_tests":["tests/test_x.py::test_x"],"test_commands":[["pytest","-q","tests/test_x.py::test_x"]]}}))
    store.create_checkpoint(wid,"TASK_COMPLETE:T1:1","TASK_COMPLETE",WorkspaceFingerprint(root).capture(wid,"T1"),"T1")
    class FailingHarness(Harness):
        def run_command(self,command,name="",category=""):
            return SimpleNamespace(success=category!="regression_tests",status="FAIL" if category=="regression_tests" else "PASS")
    report=resume_workflow(store,wid,root,FailingHarness())
    assert report["next_transition"]=="BLOCKED_REVALIDATION"
    assert report["revalidation_result"]["pass"] is False
    assert store.get_workflow(wid)["stage"]=="BLOCKED"


def test_legacy_sqlite_workflow_migrates_with_integrity_warning(tmp_path):
    db_path=tmp_path/"legacy.db"
    with sqlite3.connect(db_path) as db:
        db.execute("CREATE TABLE workflows(id TEXT PRIMARY KEY, feature TEXT, stage TEXT, current_task TEXT, state_json TEXT, created_at TEXT, updated_at TEXT)")
        db.execute("INSERT INTO workflows VALUES(?,?,?,?,?,?,?)",("old","feature","BLOCKED",None,"{}","2025-01-01T00:00:00+00:00","2025-01-01T00:00:00+00:00"))
    store=StateStore(db_path)
    state=store.get_workflow("old")["state"]
    assert state["state_schema_version"]==StateStore.SCHEMA_VERSION
    assert state["migrated_from_state_version"]==1
    assert state["resume_integrity"]=="LEGACY_NO_FINGERPRINT"
    assert store.checkpoints("old")==[]


def test_newer_state_schema_is_rejected_before_migration(tmp_path):
    path=tmp_path/"future.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE schema_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
        db.execute("INSERT INTO schema_meta VALUES('state_schema_version','99')")
    with pytest.raises(RuntimeError,match="newer than supported"):
        StateStore(path)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT name FROM sqlite_master WHERE name='checkpoints'").fetchone() is None


def test_execution_history_persists_identity_outcomes_and_filters(tmp_path):
    from orchestrator.config.models import AgentResult
    store=StateStore(tmp_path/"history.db")
    wid=store.create_workflow("feature")
    eid=store.record_provider_execution(AgentResult("codex","model","coder",True,duration=2),"secret prompt",workflow_id=wid,task_id="T1",task_type="CODING",task_complexity="HIGH")
    store.update_execution_outcome(eid,success=False,green_attempts=2,regression_passed=False)
    rows=store.routing_history(role="coder",provider="codex",model="model")
    assert len(rows)==1
    assert {"provider","model","role","task_type","task_complexity","workflow_id","task_id","green_attempts","regression_passed"}<=rows[0].keys()
    assert rows[0]["workflow_id"]==wid and rows[0]["task_id"]=="T1"
    assert rows[0]["success"] is False and rows[0]["green_attempts"]==2
    assert store.routing_history(provider="agy")==[]


def test_execution_and_command_logs_redact_raw_secrets(tmp_path):
    from orchestrator.config.models import AgentResult
    store=StateStore(tmp_path/"secure.db")
    result=AgentResult("codex","model","coder",False,stdout="TOKEN_VALUE",stderr="TOKEN_VALUE",error="PROVIDER_FAILURE: TOKEN_VALUE")
    store.record_provider_execution(result,"prompt TOKEN_VALUE")
    store.record_verification("wid",SimpleNamespace(command=["tool","--token","TOKEN_VALUE","api_key=TOKEN_VALUE"],category="test",name="check",status="FAIL",exit_code=1,duration=1))
    with store.connect() as db:
        records=[row[0] for row in db.execute("SELECT result_json FROM provider_executions")]
        commands=[row[0] for row in db.execute("SELECT result_json FROM verifications")]
    assert all("TOKEN_VALUE" not in row for row in records+commands)
    assert "[REDACTED]" in commands[0]


def test_resume_driver_skips_validated_artifacts_completed_tasks_and_review(tmp_path):
    from orchestrator.config.models import Config
    from orchestrator.workflow.driver import run_sdd_workflow
    root=_repo(tmp_path); store=_store(root); wid=store.create_workflow("feature")
    folder=root/".orchestrator"/"runs"/wid; (folder/"T001").mkdir(parents=True)
    feature_dir=root/"specs"/"feature"; feature_dir.mkdir(parents=True)
    memory=root/".specify"/"memory"; memory.mkdir(parents=True)
    (root/".specify"/"feature.json").write_text(json.dumps({"feature_directory":"specs/feature"}))
    (memory/"constitution.md").write_text("# Constitution\n\nPython governs deterministic stage gates.\n")
    (feature_dir/"spec.md").write_text("# Specification\n\nFR-1: List providers. AC-1: Return configured names.\n")
    (feature_dir/"plan.md").write_text("# Plan\n\nD-1: Use the configured provider registry.\n")
    (feature_dir/"checklists").mkdir()
    (feature_dir/"checklists"/"requirements.md").write_text("# Requirements\n\n- [x] Requirements are testable.\n")
    (folder/"analysis-report.md").write_text("# Analysis Report\n\nCritical Issues Count: 0\n")
    metadata={"requirements":["FR-1"],"acceptance_criteria":["AC-1"],"plan_decisions":["D-1"],
              "dependencies":[],"allowed_files":["src/feature.py"],"test_type":"UNIT",
              "tdd_phases":["RED","GREEN","REFACTOR"]}
    (feature_dir/"tasks.md").write_text("# Tasks\n\n- [x] T001 List providers in src/feature.py\n"
                                      f"  <!-- harness-task {json.dumps(metadata)} -->\n")
    (folder/"T001"/"tdd.json").write_text(json.dumps({"phase":"COMPLETE","requirement":["FR-1"],"acceptance_criteria":["AC-1"],"coder_provider":"opencode"}))
    (folder/"traceability.json").write_text("[]")
    (folder/"final-verification.json").write_text(json.dumps([{"status":"PASS","command":["pytest"]}]))
    (folder/"final-review.json").write_text(json.dumps({"status":"PASS"}))
    digest=hashlib.sha256((feature_dir/"tasks.md").read_bytes()).hexdigest()
    (folder/"convergence-report-1.json").write_text(json.dumps({
        "iteration":1,"outcome":"converged","added_task_ids":[],
        "tasks_sha256_before":digest,"tasks_sha256_after":digest,
    }))
    fp=WorkspaceFingerprint(root).capture(wid,"T001")
    for stage in ("CONSTITUTION_VALIDATED","SPEC_VALIDATED","CLARIFICATION_COMPLETE","CHECKLIST_COMPLETE","PLAN_VALIDATED","TASKS_VALIDATED","ANALYSIS_COMPLETE","TASK_COMPLETE","FINAL_VERIFIED","CONVERGED","FINAL_REVIEWED"):
        task_id="T001" if stage=="TASK_COMPLETE" else None
        store.create_checkpoint(wid,f"{stage}:{task_id or '-'}:1",stage,fp,task_id)
    class NoAgent:
        def run(self,*args,**kwargs): raise AssertionError("resume duplicated a validated agent call")
    class FinalHarness:
        calls=0
        requirement_results=[]
        def run(self):
            self.calls+=1
            return [SimpleNamespace(name="tests",command=["pytest"],status="PASS",exit_code=0,success=True)]
    harness=FinalHarness()
    result=run_sdd_workflow("feature",root,NoAgent(),harness,Config(),folder,store=store,resume=True)
    assert harness.calls==1
    assert result["completed_tasks"]==["T001"]
    assert result["final_verification"]==["PASS"]
    assert len(store.checkpoints(wid))==11


def test_driver_skips_constitution_generation_when_canonical_constitution_exists_and_valid(tmp_path):
    from orchestrator.config.models import AgentResult, Config
    from orchestrator.workflow.driver import run_sdd_workflow

    root = _repo(tmp_path)
    store = _store(root)
    wid = store.create_workflow("feature")
    folder = root / ".orchestrator" / "runs" / wid
    memory = root / ".specify" / "memory"
    memory.mkdir(parents=True)
    (memory / "constitution.md").write_text("# Existing Canonical Constitution\n\nPython alone governs gates.\n")

    roles_called = []
    class StopWorkflow(Exception): pass

    class RunnerMock:
        def run(self, role, prompt, *args, **kwargs):
            roles_called.append(role)
            if role == "constitution_validator":
                return AgentResult(
                    "codex",
                    "gpt-6-luna",
                    role,
                    True,
                    stdout=json.dumps({"status": "PASS", "issues": [], "summary": "Valid"}),
                )
            if role == "specification":
                raise StopWorkflow()
            raise AssertionError(f"Unexpected run call: {role}")

        def record_validation(self, *args, **kwargs):
            pass

    approve_called = []

    def approve():
        approve_called.append(True)
        return True

    with pytest.raises(StopWorkflow):
        run_sdd_workflow(
            "feature",
            root,
            RunnerMock(),
            SimpleNamespace(requirement_results=[]),
            Config(),
            folder,
            approve_constitution=approve,
            store=store,
        )

    assert approve_called == []
    assert "constitution" not in roles_called
    assert "constitution_validator" in roles_called
    assert any(c["stage"] == "CONSTITUTION_VALIDATED" for c in store.checkpoints(wid))
