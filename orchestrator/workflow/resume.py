"""Workspace integrity, checkpoint revalidation and crash recovery."""
import hashlib
import json
import subprocess
from pathlib import Path
from orchestrator.workflow.task_adapter import TaskContractError, parse_speckit_tasks
from orchestrator.workflow.artifacts import ArtifactDiscoveryError, ArtifactLayout


SAFE_PREFIXES = (".orchestrator/state/", ".orchestrator/logs/", ".orchestrator/reports/", ".orchestrator/build/", ".orchestrator/runs/")
SAFE_FILES = {".orchestrator/capabilities.json", ".orchestrator/models.json", ".orchestrator/model-selection.md"}


def _git(root, *args):
    try: process=subprocess.run(["git", *args], cwd=root, stdin=subprocess.DEVNULL, capture_output=True, text=True, check=False)
    except OSError: return None
    return process.stdout.strip() if process.returncode==0 else None


def _digest(path):
    if path.is_symlink(): return hashlib.sha256(str(path.readlink()).encode()).hexdigest()
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def _safe(path):
    return path.startswith(SAFE_PREFIXES) or path in SAFE_FILES


class WorkspaceFingerprint:
    def __init__(self, root): self.root=Path(root).resolve()

    def capture(self, workflow_id=None, task_id=None, artifact_paths=None, test_paths=None, code_paths=None, fixture_paths=None, resolved_models=None):
        branch=_git(self.root,"branch","--show-current")
        head=_git(self.root,"rev-parse","HEAD")
        tracked=(_git(self.root,"ls-files","-m") or "").splitlines()
        untracked=(_git(self.root,"ls-files","--others","--exclude-standard") or "").splitlines()
        relevant=sorted(set(path for path in tracked+untracked if path and not _safe(path)))
        safe_files=sorted(set(path for path in tracked+untracked if path and _safe(path)))
        artifacts=list(artifact_paths or [])
        if workflow_id:
            folder=self.root/".orchestrator"/"runs"/workflow_id
            # SDD artifacts live in SpecKit's canonical locations. A TDD
            # report remains operational data and is protected separately.
            if not artifact_paths:
                try:
                    artifacts.extend(ArtifactLayout.discover(self.root,workflow_dir=folder).fingerprint_paths())
                except ArtifactDiscoveryError:
                    # Old checkpoints supply their saved paths during resume;
                    # never invent a replacement feature directory.
                    pass
            if task_id:
                report=folder/task_id/"tdd.json"
                if report.is_file():
                    artifacts.append(str(report))
                    data=json.loads(report.read_text())
                    test_paths=list(test_paths or [])+data.get("red_test_files",[])
                    code_paths=list(code_paths or [])+data.get("production_files_changed",[])
                    fixture_paths=list(fixture_paths or [])+data.get("fixture_files",[])
        def hash_paths(paths):
            result={}
            for name in paths:
                path=Path(name)
                if not path.is_absolute(): path=self.root/path
                try: key=str(path.relative_to(self.root))
                except ValueError: continue
                result[key]=_digest(path)
            return result
        return {"branch":branch,"head":head,"git_commit_base":head,"tracked_modified":sorted(set(tracked)-set(safe_files)),
            "untracked_relevant":sorted(set(untracked)-set(safe_files)),"safe_files":safe_files,
            "workspace_hashes":hash_paths(relevant),"artifact_hashes":hash_paths(artifacts),
            "test_hashes":hash_paths(test_paths or []),"code_hashes":hash_paths(code_paths or []),
            "fixture_hashes":hash_paths(fixture_paths or []),"resolved_models":dict(resolved_models or {})}


def compare_fingerprints(saved, current):
    differences=[]
    if not saved.get("head") or not current.get("head"):
        return "BASE_CHANGED",[{"field":"head","before":saved.get("head"),"after":current.get("head"),"reason":"Git baseline unavailable"}]
    if saved.get("branch")!=current.get("branch"):
        return "BRANCH_CHANGED",[{"field":"branch","before":saved.get("branch"),"after":current.get("branch")}]
    if saved.get("head")!=current.get("head"):
        return "BASE_CHANGED",[{"field":"head","before":saved.get("head"),"after":current.get("head")}]
    missing=False
    for field in ("artifact_hashes","test_hashes","code_hashes","workspace_hashes","fixture_hashes"):
        old=saved.get(field,{})
        new=current.get(field,{})
        for path in sorted(set(old)|set(new)):
            if old.get(path)!=new.get(path):
                differences.append({"field":field,"path":path,"before":old.get(path),"after":new.get(path)})
                if old.get(path) and new.get(path) is None: missing=True
    for field in ("tracked_modified","untracked_relevant"):
        if saved.get(field,[])!=current.get(field,[]):
            differences.append({"field":field,"before":saved.get(field,[]),"after":current.get(field,[])})
    if missing: return "MISSING_FILES",differences
    if differences: return "UNSAFE_DIVERGENCE",differences
    if saved.get("safe_files",[])!=current.get("safe_files",[]):
        return "SAFE_DIVERGENCE",[{"field":"safe_files","before":saved.get("safe_files",[]),"after":current.get("safe_files",[])}]
    return "CLEAN_MATCH",[]


def _revalidate_artifact_chain(stage, root, folder):
    """Validate canonical bytes through the checkpoint's last SDD gate."""
    from orchestrator.workflow.postconditions import verify_stage_postcondition

    stage_order = {
        "CONSTITUTION_CREATED": 0, "CONSTITUTION_VALIDATED": 0,
        "SPEC_VALIDATED": 1, "CLARIFICATION_COMPLETE": 2,
        "CHECKLIST_COMPLETE": 3, "PLAN_VALIDATED": 4,
        "TASKS_VALIDATED": 5, "TASKS_APPENDED": 5,
        "RED_VALIDATED": 5, "GREEN_VALIDATED": 5, "REFACTOR_VALIDATED": 5,
        "CROSS_VALIDATED": 6, "ANALYSIS_COMPLETE": 6,
        "TASK_COMPLETE": 6, "FINAL_VERIFIED": 6, "CONVERGED": 6, "FINAL_REVIEWED": 6,
    }
    depth = stage_order.get(stage)
    if depth is None:
        return False
    const_path = Path(root) / ".specify" / "memory" / "constitution.md"
    if stage in {"RED_VALIDATED", "GREEN_VALIDATED", "REFACTOR_VALIDATED"} and not const_path.is_file():
        return True
    verify_stage_postcondition("CONSTITUTION_CREATED", constitution_path=const_path)
    if depth == 0:
        return True
    layout = ArtifactLayout.discover(root, workflow_dir=folder)
    verify_stage_postcondition("SPEC_VALIDATED", spec_path=layout.spec)
    if depth >= 2:
        verify_stage_postcondition("CLARIFICATION_COMPLETE", spec_path=layout.spec)
    if depth >= 3:
        verify_stage_postcondition("CHECKLIST_COMPLETE", checklist_dir=layout.require_feature_dir()/"checklists")
    if depth >= 4:
        verify_stage_postcondition("PLAN_VALIDATED", plan_path=layout.plan)
    if depth >= 5:
        verify_stage_postcondition("TASKS_VALIDATED", tasks_path=layout.tasks)
    if depth >= 6:
        verify_stage_postcondition("ANALYSIS_COMPLETE", report_path=folder/"analysis-report.md")
    return True


def revalidate_checkpoint(checkpoint, root, harness):
    """Revalidate required artifacts before rerunning the checkpoint's gate."""
    stage=checkpoint["stage"]; task_id=checkpoint.get("task_id")
    folder=Path(root)/".orchestrator"/"runs"/checkpoint["workflow_id"] if "workflow_id" in checkpoint else None
    if not folder:
        # The store returns checkpoint rows without duplicating the workflow ID.
        folder=Path(root)/".orchestrator"/"runs"/checkpoint.get("_workflow_id","")
    try:
        artifacts_validated = _revalidate_artifact_chain(stage, root, folder)
    except (ValueError, OSError) as exc:
        return False, f"ARTIFACT_POSTCONDITION_FAILED: {exc}"
    report=folder/task_id/"tdd.json" if task_id else None
    if stage in {"RED_VALIDATED","GREEN_VALIDATED","REFACTOR_VALIDATED","TASK_COMPLETE"}:
        if not report or not report.is_file(): return False,"MISSING_TDD_REPORT"
        evidence=json.loads(report.read_text())
        commands=evidence.get("test_design",{}).get("test_commands",[])
        if not commands: return False,"MISSING_TASK_TESTS"
        if stage=="RED_VALIDATED":
            results=[harness.run_red(command,expected_test_ids=[test for test in evidence["test_design"]["created_tests"] if test in command]) for command in commands]
            return all(item.classification=="EXPECTED_FAILURE" for item in results),[item.classification for item in results]
        results=[harness.run_command(command,category="task_tests") for command in commands]
        if stage in {"REFACTOR_VALIDATED","TASK_COMPLETE"}:
            regression=harness.commands.get("regression_tests") or harness.commands.get("tests",[])
            results += [harness.run_command(item["command"],item.get("name","regression"),"regression_tests") if isinstance(item,dict) else harness.run_command(item,category="regression_tests") for item in regression]
        if stage=="TASK_COMPLETE" and evidence.get("phase")!="COMPLETE": return False,"TASK_REPORT_NOT_COMPLETE"
        return bool(results) and all(item.success for item in results),[item.status for item in results]
    if stage == "CONVERGED":
        from orchestrator.workflow.quality_gates import verify_convergence_receipt
        try:
            attempt = checkpoint.get("attempt")
            if type(attempt) is not int or attempt < 1:
                raise ValueError("Invalid convergence checkpoint attempt")
            layout = ArtifactLayout.discover(root, workflow_dir=folder)
            receipt = verify_convergence_receipt(folder/f"convergence-report-{attempt}.json", layout.tasks, "converged")
            if type(receipt.get("iteration")) is not int or receipt["iteration"] != attempt:
                raise ValueError("Convergence receipt iteration does not match checkpoint attempt")
        except (ValueError, OSError) as exc:
            return False, f"ARTIFACT_POSTCONDITION_FAILED: {exc}"
        return True, "CONVERGENCE_RECEIPT_OK"
    if stage=="WORKFLOW_PLANNED": return True,"INITIAL_WORKSPACE_MATCH"
    if stage in {"FINAL_VERIFIED","FINAL_REVIEWED"}:
        from orchestrator.verification.harness import final_verification_pass
        if stage=="FINAL_REVIEWED":
            review=folder/"final-review.json"
            if not review.is_file() or json.loads(review.read_text()).get("status")!="PASS":
                return False,"FINAL_REVIEW_EVIDENCE_MISSING"
        results=harness.run()
        return final_verification_pass(results,harness.requirement_results),[item.status for item in results]
    if artifacts_validated:
        return True, "ARTIFACT_HASHES_OK"
    return False,"UNKNOWN_CHECKPOINT_STAGE"


def reconcile_interrupted_task(store, workflow_id, checkpoint, current, differences, root, harness):
    """With explicit human choice, promote only a verified partial code/refactor write."""
    if checkpoint["stage"] not in {"RED_VALIDATED","GREEN_VALIDATED"} or not checkpoint.get("task_id"):
        return None,"Only interrupted GREEN or REFACTOR can be reconciled automatically"
    old=checkpoint["workspace_fingerprint"]
    if old.get("artifact_hashes")!=current.get("artifact_hashes") or old.get("test_hashes")!=current.get("test_hashes"):
        return None,"Protected artifact or test changed"
    if any(change["field"] not in {"workspace_hashes","tracked_modified","untracked_relevant","code_hashes"} for change in differences):
        return None,"Divergence includes fields outside production code"
    folder=Path(root)/".orchestrator"/"runs"/workflow_id
    report_path=folder/checkpoint["task_id"]/"tdd.json"
    try:
        tasks_path=ArtifactLayout.discover(root,workflow_dir=folder).tasks
    except ArtifactDiscoveryError:
        # Read-only compatibility for workflow reports created before the
        # SpecKit artifact migration. New workflows never write this file.
        tasks_path=folder/"tasks.md"
    if not report_path.is_file() or not tasks_path.is_file(): return None,"Missing task report or tasks artifact"
    try:
        task=next(item for item in parse_speckit_tasks(tasks_path.read_text()) if item["id"]==checkpoint["task_id"])
        report=json.loads(report_path.read_text())
    except (TaskContractError,ValueError,KeyError,StopIteration): return None,"Task metadata cannot be read"
    allowed=task.get("allowed_files") or task.get("production_files") or []
    changed=set()
    for entry in differences:
        if "path" in entry: changed.add(entry["path"])
        elif entry["field"] in {"tracked_modified","untracked_relevant"}:
            changed.update(set(entry["before"]) ^ set(entry["after"]))
    if not changed or not allowed or any(not any(path==scope.rstrip("/") or path.startswith(scope.rstrip("/")+"/") for scope in allowed) for path in changed):
        return None,"Changed files exceed task production scope"
    commands=report.get("test_design",{}).get("test_commands",[])
    if not commands: return None,"Task tests are missing"
    results=[harness.run_command(command,category="task_tests") for command in commands]
    regression=harness.commands.get("regression_tests") or harness.commands.get("tests",[])
    results += [harness.run_command(item["command"],item.get("name","regression"),"regression_tests") if isinstance(item,dict) else harness.run_command(item,category="regression_tests") for item in regression]
    if not regression or not all(result.success for result in results):
        return None,{"reason":"Task or regression tests failed","results":[item.status for item in results]}
    stage="GREEN_VALIDATED" if checkpoint["stage"]=="RED_VALIDATED" else "REFACTOR_VALIDATED"
    report["phase"]="GREEN_VERIFY" if stage=="GREEN_VALIDATED" else "REVIEW"
    report["production_files_changed"]=sorted(set(report.get("production_files_changed",[]))|changed)
    if stage=="GREEN_VALIDATED":
        interrupted=store.get_workflow(workflow_id)["state"]
        if interrupted.get("running_role")=="coder": report["coder_provider"]=interrupted.get("running_provider")
    report["green_pass"]=True; report["green_attempts"]=max(1,report.get("green_attempts",0))
    if stage=="REFACTOR_VALIDATED":
        report["regression_pass"]=True; report["refactor_accounted"]=True
        report["regression"]={"status":"PASS","results":[{"command":r.command,"status":r.status} for r in results]}
    report_path.write_text(json.dumps(report,indent=2))
    fp=WorkspaceFingerprint(root).capture(workflow_id,checkpoint["task_id"])
    store.create_checkpoint(workflow_id,f"{stage}:{checkpoint['task_id']}:reconciled",stage,fp,checkpoint["task_id"],checkpoint["attempt"])
    return store.latest_checkpoint(workflow_id),{"results":[item.status for item in results],"changed_files":sorted(changed)}


def resume_workflow(store, workflow_id, root, harness, interactive=None, continue_fn=None):
    item=store.get_workflow(workflow_id)
    if not item: raise ValueError("Workflow not found")
    if item["stage"] in {"ABORTED","COMPLETE"}: raise ValueError(f"Workflow already {item['stage']}")
    with store.workflow_lock(workflow_id):
        checkpoint=store.latest_checkpoint(workflow_id)
        if not checkpoint: raise ValueError("No consistent checkpoint exists for this workflow")
        checkpoint["_workflow_id"]=workflow_id
        saved=checkpoint["workspace_fingerprint"]
        current=WorkspaceFingerprint(root).capture(workflow_id,checkpoint.get("task_id"),list(saved.get("artifact_hashes",{})),list(saved.get("test_hashes",{})),list(saved.get("code_hashes",{})))
        status,differences=compare_fingerprints(saved,current)
        report={"previous_checkpoint":checkpoint["stage"],"workspace_status":status,"differences":differences,
            "revalidation_performed":False,"revalidation_result":None,"next_transition":None,
            "interrupted_stage":item["stage"] if item["stage"] in {"RUNNING_AGENT","RUNNING_VERIFICATION","PARTIAL_WRITE","UNKNOWN_COMPLETION"} else item["state"].get("interrupted_stage")}
        if status not in {"CLEAN_MATCH","SAFE_DIVERGENCE"}:
            reconciled=False
            if interactive:
                print(json.dumps(report,indent=2))
                while True:
                    action=interactive.input_fn("Divergence [inspect/abort/reconcile]: ").strip().lower()
                    if action=="inspect": print(json.dumps(differences,indent=2)); continue
                    if action=="abort": store.abort_workflow(workflow_id); report["next_transition"]="ABORTED"; break
                    if action=="reconcile":
                        promoted,details=(reconcile_interrupted_task(store,workflow_id,checkpoint,current,differences,root,harness)
                            if report["interrupted_stage"] else (None,"Workflow was not interrupted mid-phase"))
                        report["reconciliation"]={"accepted":promoted is not None,"details":details}
                        if promoted:
                            checkpoint=promoted; reconciled=True; report["reconciled_checkpoint"]=checkpoint["stage"]
                        else: report["next_transition"]="RECONCILIATION_REQUIRED"
                        break
            if not reconciled: return store.record_resume(workflow_id,report)
        ok,details=revalidate_checkpoint(checkpoint,root,harness)
        report.update({"revalidation_performed":True,"revalidation_result":{"pass":ok,"details":details},
            "next_transition":"READY" if ok else "BLOCKED_REVALIDATION"})
        if ok:
            # Preserve completed task IDs and evidence; an interrupted phase is never promoted.
            state={**item["state"],"resume_checkpoint":checkpoint["transition_id"],"resume_ready":True}
            store.update_workflow(workflow_id,"READY",state,checkpoint.get("task_id"))
        else:
            store.update_workflow(workflow_id,"BLOCKED",{**item["state"],"reason":"Last checkpoint failed deterministic revalidation"},checkpoint.get("task_id"))
        report=store.record_resume(workflow_id,report)
        if ok and continue_fn:
            try: continue_fn()
            except Exception:
                report["continuation_status"]="UNKNOWN_COMPLETION"
                store.update_resume_report(report)
                current_item=store.get_workflow(workflow_id)
                store.update_workflow(workflow_id,"UNKNOWN_COMPLETION",{**current_item["state"],"interrupted_stage":"UNKNOWN_COMPLETION"},current_item["current_task"])
                raise
            report["continuation_status"]=store.get_workflow(workflow_id)["stage"]
            store.update_resume_report(report)
        return report
