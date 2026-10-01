"""Policy-level TDD enforcement helpers used by workflow drivers."""
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from orchestrator.agents.roles import ROLES
from orchestrator.config.models import ValidationResult
from orchestrator.workflow.tdd import TDDTask
from orchestrator.workflow.transitions import TDDPhase
from orchestrator.tdd_contract import parse_test_design, hash_test_files, changed_test_hashes
from orchestrator.agents.runner import load_prompt


class TDDGate:
    def __init__(self, task: TDDTask, max_attempts=3): self.task=task; self.max_attempts=max_attempts
    def red(self, test_validation_status: str, red_result: str):
        if test_validation_status != "PASS":
            self.task.attempts["red"] += 1
            if self.task.attempts["red"] >= self.max_attempts: self.task.advance(TDDPhase.BLOCKED)
            return False
        if self.task.phase == TDDPhase.ANALYZE: self.task.advance(TDDPhase.RED_GENERATE)
        if self.task.phase == TDDPhase.RED_GENERATE: self.task.advance(TDDPhase.RED_VERIFY)
        self.task.evidence["test_validated"] = True
        self.task.evidence["red_result"] = red_result
        if red_result != "EXPECTED_FAILURE": return False
        self.task.advance(TDDPhase.GREEN_IMPLEMENT, {"red_result":"EXPECTED_FAILURE"})
        return True
    def green(self, passed: bool, tampered=False):
        if tampered:
            self.task.evidence["test_tampering_detected"] = True
            self.task.attempts["green"] += 1
            return "TEST_TAMPERING"
        if self.task.phase != TDDPhase.GREEN_IMPLEMENT: raise ValueError("Cannot verify GREEN before valid RED")
        if passed:
            self.task.evidence["green_pass"] = True
            self.task.advance(TDDPhase.GREEN_VERIFY, {"green_pass":True})
            return "PASS"
        self.task.attempts["green"] += 1
        if self.task.attempts["green"] >= self.max_attempts: self.task.advance(TDDPhase.BLOCKED)
        return "GREEN_FAIL"
    def refactor(self, performed: bool, regression_pass: bool):
        if self.task.phase == TDDPhase.GREEN_VERIFY:
            self.task.advance(TDDPhase.REFACTOR, {"refactor_accounted": True, "refactor_performed":performed})
        if self.task.phase != TDDPhase.REFACTOR: raise ValueError("Refactor requires GREEN")
        self.task.advance(TDDPhase.REGRESSION_VERIFY)
        self.task.evidence["regression_pass"] = regression_pass
        if not regression_pass: return False
        self.task.advance(TDDPhase.REVIEW, {"regression_pass":True})
        return True
    def review(self, verification_pass: bool, traceability_recorded: bool, review_status="PASS"):
        if self.task.phase != TDDPhase.REVIEW: raise ValueError("Review requires regression verification")
        if review_status != "PASS" or not verification_pass: return False
        self.task.advance(TDDPhase.COMPLETE, {"verification_pass":True,"traceability_recorded":traceability_recorded,"refactor_accounted":self.task.evidence.get("refactor_accounted",False),"test_validated":self.task.evidence.get("test_validated",False)})
        return self.task.complete()


def test_files_snapshot(root):
    return hash_test_files(root)


def test_files_changed(before, root):
    return changed_test_hashes(before,root)


def workspace_snapshot(root):
    root=Path(root)
    ignored={".git", ".venv", ".orchestrator", "build", "dist", "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache"}
    return {str(p.relative_to(root)): str(p.readlink()).encode() if p.is_symlink() else p.read_bytes() for p in root.rglob("*") if (p.is_file() or p.is_symlink()) and not any(part in ignored for part in p.parts)}


def _test_or_fixture(path):
    p=Path(path)
    return "tests" in p.parts or "fixtures" in p.parts or p.name.startswith("test_") or p.name in {"conftest.py", "pytest.ini", "tox.ini"}


def _record_validation(runner, result, validation, stage=None, evidence=None):
    if not hasattr(runner, "record_validation"):
        return
    try:
        runner.record_validation(result, validation, stage=stage, evidence=evidence)
    except TypeError:
        runner.record_validation(result, validation)


def execute_tdd_task(task: TDDTask, runner, harness, workspace, task_test_command=None, regression_commands=None, max_attempts=3, workflow_dir=None, gate_callback=None, checkpoint_callback=None, task_data=None, resume_stage=None, artifact_paths=None, snapshot_gate_registered: bool = False):
    """Run a bounded TDD cycle. Python alone advances phases and evaluates checks."""
    import json
    import time
    from pathlib import Path
    from orchestrator.validation.parser import parse_validation
    from orchestrator.workflow.transitions import TDDPhase
    gate=TDDGate(task,max_attempts)
    root=Path(workspace)
    # TDD reports are operational data, while the protected SDD artifacts are
    # the canonical SpecKit files.  Keep the old locations only for direct
    # callers that have not supplied a layout yet.
    artifact_root=Path(workflow_dir) if workflow_dir else root
    artifact_paths=artifact_paths or {"constitution":str(root/"constitution.md"),"specification":str(artifact_root/"spec.md"),"plan":str(artifact_root/"plan.md"),"tasks":str(artifact_root/"tasks.md")}
    def artifact_hashes():
        import hashlib
        return {name:hashlib.sha256(str(Path(path).readlink()).encode() if Path(path).is_symlink() else Path(path).read_bytes()).hexdigest() for name,path in artifact_paths.items() if Path(path).is_file() or Path(path).is_symlink()}
    protected_artifacts=artifact_hashes()
    ctx=f"Task: {task.task}\nRequirements: {task.requirements}\nAcceptance criteria: {task.acceptance_criteria}\nArtifact paths: {artifact_paths}"
    numeric_sensitive = bool(task_data.get("numeric_sensitive", False)) if task_data else False
    snapshot_gate = snapshot_gate_registered or (bool(task_data.get("snapshot_gate_registered", False)) if task_data else False)
    def _fixture_hashes(files):
        import hashlib
        hashes = {}
        for f in (files or ()):
            p = (root / f).resolve()
            if p.is_file():
                hashes[str(p.relative_to(root))] = hashlib.sha256(p.read_bytes()).hexdigest()
        return hashes
    def invoke(role, extra="", author_provider=None, artifacts=None, attempt=1, author_model=None, author_models=None, author_role=None):
        options={"task":task_data,"task_id":task.task} if task_data else {}
        if task_data and role in {"coder","refactorer"}: options["allowed_paths"]=task_data.get("allowed_files") or task_data.get("production_files") or []
        if role in {"test_validator", "code_reviewer", "debugger"}: options["allowed_paths"]=[]
        if artifacts is not None: options["artifacts"]=artifacts
        options["attempt"]=attempt
        options["author_model"]=author_model
        options["author_models"]=author_models
        options["author_role"]=author_role
        options["numeric_sensitive"]=numeric_sensitive
        if role == "coder":
            # Python's task-level TDD machine is the only implementation
            # authority. SpecKit implement is dispatched as the worker skill
            # for this GREEN task, with an explicit one-task boundary.
            skill_name=ROLES[role].skill_name
            options["skill_name"]=skill_name
            extra=(f"Use the installed {skill_name} skill for task {task.task} only. "
                   "Do not process any other task, edit tasks.md, mark checklist items, "
                   "or advance the TDD phase; Python owns task selection and phase gates.\n"+extra)
        result=runner.run(role, ctx+"\n"+extra, cwd=root, author_provider=author_provider,**options)
        if result.usage.get("execution_id"):
            task.evidence.setdefault("executions",[]).append({"role":role,"id":result.usage["execution_id"]})
        if not result.success: return result, None
        return result, result.stdout
    def save():
        if workflow_dir: task.save(Path(workflow_dir)/task.task/"tdd.json")
        if getattr(runner,"store",None):
            values={"red_valid":task.evidence.get("red_expected_failure_confirmed"),"red_attempts":task.evidence.get("red_attempts",0),
                "green_attempts":task.evidence.get("green_attempts",0),"first_pass_green":task.evidence.get("green_attempts")==1,
                "attempts":max(1,task.evidence.get("green_attempts",0)),
                "test_tampering":task.evidence.get("test_tampering_detected",False),"review_accepted":task.evidence.get("review_status")=="PASS" if "review_status" in task.evidence else None,
                "regression_passed":task.evidence.get("regression",{}).get("status")=="PASS" if task.evidence.get("regression") else None,
                "blocked":task.phase==TDDPhase.BLOCKED}
            for execution in task.evidence.get("executions",[]):
                specific=dict(values)
                if task.phase in {TDDPhase.COMPLETE,TDDPhase.BLOCKED}:
                    if execution["role"] in {"coder","refactorer"}:
                        specific.update(success=task.complete(),first_pass_success=task.complete() and task.evidence.get("green_attempts")==1)
                    elif execution["role"]=="test_designer":
                        specific.update(success=bool(task.evidence.get("red_expected_failure_confirmed")),first_pass_success=bool(task.evidence.get("red_expected_failure_confirmed")) and task.evidence.get("red_attempts")==1)
                runner.store.update_execution_outcome(execution["id"],**specific)
    def checkpoint(stage):
        save()
        if checkpoint_callback: checkpoint_callback(stage,task.task,task.attempts.get("green",0)+1)
    def gate_phase(phase, role, files=(), commands=(), artifacts=(), attempt=1, extra_context=None):
        if hasattr(runner, "set_stage_context"):
            runner.set_stage_context(phase, role, artifacts)
        if not gate_callback:
            return True
        try:
            ok = gate_callback(phase, role, list(files), list(commands), attempt=attempt, artifacts=list(artifacts), extra_context=extra_context)
        except TypeError:
            try:
                ok = gate_callback(phase, role, list(files), list(commands), attempt=attempt, artifacts=list(artifacts))
            except TypeError:
                try:
                    ok = gate_callback(phase, role, list(files), list(commands), artifacts=list(artifacts))
                except TypeError:
                    try:
                        ok = gate_callback(phase, role, list(files), list(commands))
                    except TypeError:
                        ok = True
        if not ok:
            task.evidence["gate_abort"]=phase
            task.advance(TDDPhase.BLOCKED); save(); return False
        return True



    if resume_stage in {"RED_VALIDATED","GREEN_VALIDATED","REFACTOR_VALIDATED"}:
        from types import SimpleNamespace
        tdd_report_path=Path(workflow_dir)/task.task/"tdd.json"
        payload=json.loads(tdd_report_path.read_text())
        task.evidence={k:v for k,v in payload.items() if k not in {"task","requirement","acceptance_criteria","test_type","phase","attempts"}}
        task.attempts=payload.get("attempts",task.attempts)
        design: Any = SimpleNamespace(**task.evidence["test_design"])
        task.phase={"RED_VALIDATED":TDDPhase.GREEN_IMPLEMENT,"GREEN_VALIDATED":TDDPhase.GREEN_VERIFY,"REFACTOR_VALIDATED":TDDPhase.REVIEW}[resume_stage]
    else:
        production_before=workspace_snapshot(root)
        before_red=test_files_snapshot(root)
        if not gate_phase("ANALYZE","test_designer"): return task
        analysis,_=invoke("test_designer", "ANALYZE only: inspect linked requirements, acceptance criteria, plan, task, and existing code. Propose observable deterministic tests. Do not edit any file.")
        task.evidence["analysis"]={"status":"PASS" if analysis.success else "BLOCKED","provider":analysis.provider,"model":analysis.model,"summary":analysis.stdout[:2000]}
        if not analysis.success:
            task.advance(TDDPhase.BLOCKED); save(); return task
        after_analysis=workspace_snapshot(root)
        if production_before != after_analysis or artifact_hashes()!=protected_artifacts:
            task.evidence["analyze_files_changed"]=sorted(k for k in set(production_before)|set(after_analysis) if production_before.get(k)!=after_analysis.get(k))
            task.advance(TDDPhase.BLOCKED); save(); return task
        task.advance(TDDPhase.RED_GENERATE)
        from orchestrator.workflow.stagnation import StagnationDetector
        stagnation_limit = max(1, max_attempts - 1) if max_attempts > 1 else 1
        red_detector = StagnationDetector(stagnation_limit=stagnation_limit)
        validator_feedback = ""
        while task.phase == TDDPhase.RED_GENERATE and task.attempts["red"] < max_attempts:
            red_attempt = task.attempts["red"] + 1
            task.evidence["red_attempts"] = red_attempt
            if not gate_phase("RED_GENERATE", "test_designer", ["tests/"], attempt=red_attempt): return task
            designer_prompt = "RED: create only task-specific tests from the approved analysis. Production files must not change. Return the required TestDesign JSON contract."
            if validator_feedback:
                designer_prompt += f"\n\nAddress previous validation issues:\n{validator_feedback}"
            designer, _ = invoke("test_designer", designer_prompt, attempt=red_attempt)
            if not designer.success:
                task.advance(TDDPhase.BLOCKED); save(); return task
            after_design = workspace_snapshot(root)
            changed_design = {k for k in set(production_before) | set(after_design) if production_before.get(k) != after_design.get(k)}
            if any(not _test_or_fixture(path) for path in changed_design) or artifact_hashes() != protected_artifacts:
                task.evidence["red_production_files_changed"] = sorted(k for k in changed_design if not _test_or_fixture(k))
                task.advance(TDDPhase.BLOCKED); save(); return task
            task.evidence["test_designer_provider"] = designer.provider
            task.evidence["test_designer_model"] = designer.model
            task.evidence["red_test_created"] = bool(test_files_changed(before_red, root))
            task.evidence["test_files_changed"] = test_files_changed(before_red, root)
            try:
                design = parse_test_design(
                    designer.structured_output if isinstance(designer.structured_output, dict) else designer.stdout,
                    task.task, task.requirements, task.acceptance_criteria, root, task.evidence["test_files_changed"]
                )
            except ValueError as exc:
                task.evidence["test_design_error"] = str(exc)
                if getattr(runner, "store", None):
                    runner.store.record_metric(designer.provider, designer.model, designer.role, "structured_output_failure")
                    if designer.usage.get("execution_id"): runner.store.update_execution_outcome(designer.usage["execution_id"], structured_output_valid=False)
                task.attempts["red"] += 1
                if task.attempts["red"] < max_attempts:
                    validator_feedback = f"TestDesign contract error: {exc}. Return only task-specific test runner commands referencing modified test files (do not include full test suite commands without paths)."
                    continue
                task.advance(TDDPhase.BLOCKED); save(); return task
            task.evidence["fixture_files"] = list(getattr(design, "fixture_files", ()))
            task.evidence["test_design"] = {"task_id": design.task_id, "requirement_ids": design.requirement_ids, "acceptance_criteria_ids": design.acceptance_criteria_ids, "created_tests": design.created_tests, "test_commands": design.test_commands, "fixture_files": list(getattr(design, "fixture_files", ()))}
            test_sources = {path: (root / path).read_text(errors="replace") for path in task.evidence["test_files_changed"] if (root / path).is_file()}
            test_files = list(task.evidence["test_files_changed"])
            if not gate_phase("RED_VALIDATE", "test_validator", [], [], artifacts=test_files, attempt=red_attempt): return task
            validator, raw = invoke("test_validator", load_prompt("test_validator", task=json.dumps(task.evidence['test_design']), artifact=json.dumps(test_sources, indent=2)), author_provider=designer.provider, author_model=designer.model, author_models=[designer.model] if designer.model else None, author_role="test_designer", artifacts=test_files, attempt=red_attempt)
            if validator.success:
                vr = parse_validation(raw or "", "test_validator", validator.model, provider=getattr(validator, "provider", None))
            else:
                vr = ValidationResult("PARSE_ERROR" if not validator.error else "BLOCKED", [], validator.error or validator.stderr or "test_validator failed", "test_validator", validator.model, datetime.now(timezone.utc).isoformat(), raw or validator.stderr or validator.error or "")
            _record_validation(runner, validator, vr, stage="RED_VALIDATE", evidence={"task_id": task.task, "attempt": red_attempt})
            task.evidence["test_validated"] = bool(vr and vr.status == "PASS")
            if not task.evidence["test_validated"]:
                task.evidence["test_validation"] = vr.status
                task.evidence["test_validation_reason"] = vr.summary
                task.evidence["test_validation_issues"] = vr.issues
                if vr.status in {"REVISE", "BLOCKED"}:
                    task.attempts["red"] += 1
                    validator_feedback = f"{vr.summary}\nIssues: {vr.issues}"
                    combined_tests = "\n".join(test_sources.values())
                    stag_report = red_detector.evaluate(
                        status=vr.status,
                        issues=vr.issues,
                        artifact_text=combined_tests,
                        summary=vr.summary,
                    )
                    if stag_report.requires_human_intervention:
                        warn_context = {
                            "reason": stag_report.reason,
                            "stagnant_streak": stag_report.stagnant_streak,
                            "total_attempts": stag_report.total_attempts,
                            "issues": list(vr.issues),
                            "summary": vr.summary,
                        }
                        if gate_phase("STAGNATION_WARNING", "test_designer", ["tests/"], artifacts=test_files, attempt=red_attempt, extra_context=warn_context):
                            red_detector.stagnant_streak = 0
                        else:
                            task.advance(TDDPhase.BLOCKED); save(); return task
                    if task.attempts["red"] >= max_attempts:
                        task.advance(TDDPhase.BLOCKED); save(); return task
                    continue
                elif vr.status != "PASS":
                    task.advance(TDDPhase.BLOCKED); save(); return task
            if not gate_phase("RED_VERIFY", "python", task.evidence["test_files_changed"], design.test_commands, attempt=red_attempt): return task
            red_files = (task_data.get("allowed_files") or task_data.get("production_files") or []) if task_data else task.evidence.get("allowed_files", ())
            red_start = time.monotonic()
            red_results = [harness.run_red(command, expected_test_ids=[test_id for test_id in design.created_tests if test_id in command], expected_failure=getattr(design, "expected_failure", None), allowed_files=red_files, expected_markers=[design.expected_failure] if getattr(design, "expected_failure", None) else ()) for command in design.test_commands]
            task.evidence["task_test_duration"] = time.monotonic() - red_start
            red_status = "EXPECTED_FAILURE" if red_results and all(result.classification == "EXPECTED_FAILURE" for result in red_results) else next((result.classification for result in red_results if result.classification != "EXPECTED_FAILURE"), "INVALID_TEST")
            task.evidence.update({"red_result": red_status, "red_test_files": test_files_changed(before_red, root), "red": {"classification": red_status, "results": [{"command": result.command, "exit_code": result.exit_code, "stdout": result.stdout, "stderr": result.stderr, "cause": result.cause, "classification": result.classification} for result in red_results]}})
            task.evidence["red_expected_failure_confirmed"] = red_status == "EXPECTED_FAILURE"
            if red_status == "EXPECTED_FAILURE":
                red_validator, red_raw = invoke("test_validator", load_prompt("test_validator", task=f"Validate observed RED failures for {task.task}", artifact=json.dumps(task.evidence["red"], indent=2)), author_provider=designer.provider, author_model=designer.model, author_models=[designer.model] if designer.model else None, author_role="test_designer", artifacts=test_files, attempt=red_attempt)
                if red_validator.success:
                    red_validation = parse_validation(red_raw or "", "test_validator", red_validator.model, provider=getattr(red_validator, "provider", None))
                else:
                    red_validation = ValidationResult("PARSE_ERROR" if not red_validator.error else "BLOCKED", [], red_validator.error or red_validator.stderr or "test_validator failed", "test_validator", red_validator.model, datetime.now(timezone.utc).isoformat(), red_raw or red_validator.stderr or red_validator.error or "")
                _record_validation(runner, red_validator, red_validation, stage="RED_SEMANTIC_VALIDATE", evidence={"task_id": task.task, "attempt": red_attempt})
                task.evidence["red"]["semantic_validation"] = red_validation.status
                if red_validation.status != "PASS":
                    task.evidence["red_semantic_validation"] = red_validation.status
                    task.evidence["red_semantic_validation_reason"] = red_validation.summary
                    task.evidence["red_semantic_validation_issues"] = red_validation.issues
                    if red_validation.status in {"REVISE", "BLOCKED"} and task.attempts["red"] < max_attempts:
                        task.attempts["red"] += 1
                        validator_feedback = f"RED SEMANTIC VALIDATION: {red_validation.summary}\nIssues: {red_validation.issues}"
                        stag_report = red_detector.evaluate(
                            status=red_validation.status,
                            issues=red_validation.issues,
                            artifact_text="\n".join(test_sources.values()),
                            summary=red_validation.summary,
                        )
                        if stag_report.requires_human_intervention:
                            warn_context = {
                                "reason": stag_report.reason,
                                "stagnant_streak": stag_report.stagnant_streak,
                                "total_attempts": stag_report.total_attempts,
                                "issues": list(red_validation.issues),
                                "summary": red_validation.summary,
                            }
                            if gate_phase("STAGNATION_WARNING", "test_designer", ["tests/"], artifacts=test_files, attempt=red_attempt, extra_context=warn_context):
                                red_detector.stagnant_streak = 0
                            else:
                                task.advance(TDDPhase.BLOCKED); save(); return task
                        continue
                    red_status = "INVALID_TEST"
                    task.evidence["red_result"] = red_status
                    task.evidence["red"]["classification"] = red_status
                    task.evidence["red_expected_failure_confirmed"] = False
                    task.advance(TDDPhase.BLOCKED); save(); return task
            if not gate.red("PASS" if red_status == "EXPECTED_FAILURE" else "BLOCKED", red_status):
                if task.phase != TDDPhase.BLOCKED: task.advance(TDDPhase.BLOCKED)
                save(); return task
            checkpoint("RED_VALIDATED")
            break
        if task.phase == TDDPhase.RED_GENERATE:
            task.advance(TDDPhase.BLOCKED); save(); return task
    while task.phase==TDDPhase.GREEN_IMPLEMENT and task.attempts["green"]<max_attempts:
        protected=test_files_snapshot(root)
        task.evidence["green_test_hashes_before"]=protected
        fixture_paths = task.evidence.get("fixture_files", [])
        protected_fixtures = _fixture_hashes(fixture_paths)
        task.evidence["green_fixture_hashes_before"] = protected_fixtures
        code_before=workspace_snapshot(root)
        if not gate_phase("GREEN_IMPLEMENT","coder",["production files"],design.test_commands): return task
        designer_model = task.evidence.get("test_designer_model")
        validator_model = task.evidence.get("test_validator_model")
        coder_authors = []
        if designer_model:
            coder_authors.append((designer_model, "test_designer"))
        if validator_model:
            coder_authors.append((validator_model, "test_validator"))
        coder,_=invoke(
            "coder",
            f"Validated task-specific tests: {json.dumps(task.evidence['test_design'])}\nValidated RED results: {json.dumps(task.evidence['red'])}\nDo not modify tests or fixtures.",
            author_provider=task.evidence.get("test_designer_provider"),
            author_model=designer_model,
            author_models=coder_authors if coder_authors else ([designer_model] if designer_model else None),
            author_role="test_designer",
        )
        if not coder.success:
            task.attempts["green"]+=1
            task.evidence["green_attempts"]=task.attempts["green"]
            continue
        task.evidence["coder_provider"]=coder.provider
        task.evidence["coder_model"]=coder.model
        task.evidence["coder_independent_from_test_designer"]=coder.usage.get("validation_independence")
        changed=test_files_changed(protected,root)
        fixture_changes = {}
        for fpath, orig_hash in protected_fixtures.items():
            curr_path = (root / fpath).resolve()
            if not curr_path.is_file() or hashlib.sha256(curr_path.read_bytes()).hexdigest() != orig_hash:
                fixture_changes[fpath] = "modified"
        if changed or fixture_changes:
            task.evidence["test_tampering_detected"]=True
            if changed: task.evidence["test_tampering_files"]=changed
            if fixture_changes: task.evidence["fixture_tampering_files"]=fixture_changes
            gate.green(False,tampered=True)
            task.evidence["green_attempts"]=task.attempts["green"]
            tamper_prompt = (
                "TEST_TAMPERING: inspect unauthorized GREEN changes. Return REVISE with 'RETURN_TO_RED' in issues or summary only if a legitimate test correction is needed; otherwise BLOCKED.\n"
                "Do not attempt to read files via shell or execute any commands.\n"
                f"Files: {json.dumps(list(changed.keys()) if isinstance(changed, dict) else list(changed))}\n"
                f"Fixtures: {json.dumps(list(fixture_changes.keys()))}\n"
                'Return strict JSON: {"status":"PASS|REVISE|BLOCKED","summary":"...","issues":[]}'
            )
            tamper_files = list(changed.keys()) if isinstance(changed, dict) else list(changed)
            tamper_files.extend(list(fixture_changes.keys()))
            tamper_validator,tamper_raw=invoke(
                "test_validator",
                tamper_prompt,
                author_provider=coder.provider,
                author_model=coder.model,
                author_models=[coder.model] if coder.model else None,
                author_role="coder",
                artifacts=tamper_files,
            )

            if tamper_validator.success:
                tamper_review=parse_validation(tamper_raw or "","test_validator",tamper_validator.model, provider=getattr(tamper_validator,"provider",None))
            else:
                tamper_review=ValidationResult("PARSE_ERROR" if not tamper_validator.error else "BLOCKED", [], tamper_validator.error or tamper_validator.stderr or "test_validator failed", "test_validator", tamper_validator.model, datetime.now(timezone.utc).isoformat(), tamper_raw or tamper_validator.stderr or tamper_validator.error or "")
            _record_validation(runner, tamper_validator, tamper_review, stage="TEST_TAMPERING_VALIDATE", evidence={"task_id": task.task, "changed_files": changed, "changed_fixtures": fixture_changes})
            if tamper_review and tamper_review.status=="REVISE" and (
                any("RETURN_TO_RED" in str(issue) for issue in tamper_review.issues)
                or "RETURN_TO_RED" in tamper_review.summary
            ):
                task.evidence["test_change_approved"]=True
                task.evidence["red_restart_required"]=True
                task.advance(TDDPhase.RED_GENERATE,{"test_change_approved":True})
                save(); return task
            task.advance(TDDPhase.BLOCKED); save(); return task

        if artifact_hashes()!=protected_artifacts:
            task.evidence["artifact_tampering_detected"]=True
            invoke(
                "code_reviewer",
                "ARTIFACT_TAMPERING: Coder modified a protected SDD artifact. Review and require restoration.",
                author_provider=coder.provider,
                author_model=coder.model,
                author_models=[coder.model] if coder.model else None,
                author_role="coder",
            )
            task.advance(TDDPhase.BLOCKED); save(); return task
        code_after=workspace_snapshot(root)
        changed_production={k for k in set(code_before)|set(code_after) if code_before.get(k)!=code_after.get(k) and not _test_or_fixture(k)}
        task.evidence["production_files_changed"]=sorted(set(task.evidence.get("production_files_changed",[]))|changed_production)
        if not gate_phase("GREEN_VERIFY","python",commands=design.test_commands): return task
        green_results=[harness.run_command(command,category="task_tests") for command in design.test_commands]
        task.evidence.setdefault("green",{}).setdefault("attempts",[]).append({"task_tests":[{"command":result.command,"status":result.status,"exit_code":result.exit_code} for result in green_results]})
        green_pass=all(result.success for result in green_results)
        if green_pass:
            early_regression=[harness.run_command(c["command"],c.get("name","regression"),"regression_tests") if isinstance(c,dict) else harness.run_command(c,category="regression_tests") for c in (regression_commands or [])]
            task.evidence["green"]["attempts"][-1]["regression"]=[{"command":result.command,"status":result.status} for result in early_regression]
            if early_regression and not all(result.success for result in early_regression): task.evidence["green_regression_failed"]=True
            green_pass=bool(early_regression) and all(result.success for result in early_regression)
        if not green_pass and any(result.status=="FAIL" for result in green_results) and hasattr(runner,"record_model_feedback"):
            runner.record_model_feedback(coder,"GREEN_IMPLEMENTATION_FAILURE","Validated task tests still fail after implementation")
        if green_pass:
            gate.green(True); task.evidence["green_attempts"]=task.attempts["green"]+1; checkpoint("GREEN_VALIDATED"); break
        task.attempts["green"]+=1
        task.evidence["green_attempts"]=task.attempts["green"]
        if task.attempts["green"]>=max_attempts:
            if gate_callback and gate_phase("STAGNATION_WARNING","coder",["production files"],design.test_commands):
                max_attempts += max_attempts
            else:
                task.advance(TDDPhase.BLOCKED); save(); return task
    if task.phase != TDDPhase.GREEN_VERIFY and resume_stage!="REFACTOR_VALIDATED":
        if task.phase != TDDPhase.BLOCKED: task.advance(TDDPhase.BLOCKED)
        save(); return task
    if resume_stage!="REFACTOR_VALIDATED":
        task.evidence["green_attempts"]=task.attempts["green"]+1
        task.evidence["refactor_attempts"]=1
        tests_before_refactor=test_files_snapshot(root)
        fixture_paths = task.evidence.get("fixture_files", [])
        fixtures_before_refactor = _fixture_hashes(fixture_paths)
        if numeric_sensitive and not snapshot_gate:
            task.evidence["refactor_noop_numeric"] = True
            task.evidence["refactor_result"] = "REFACTOR_NOOP_NUMERIC"
        else:
            if not gate_phase("REFACTOR","refactorer",["production files"]): return task
            coder_model = task.evidence.get("coder_model")
            refactor,_=invoke(
                "refactorer",
                "Refactor only; preserve validated test files and behavior.",
                author_provider=task.evidence.get("coder_provider"),
                author_model=coder_model,
                author_models=[coder_model] if coder_model else None,
                author_role="coder",
            )
            if not refactor.success:
                task.advance(TDDPhase.BLOCKED); save(); return task
            refactor_test_changes=test_files_changed(tests_before_refactor,root)
            fixture_changes = {}
            for fpath, orig_hash in fixtures_before_refactor.items():
                curr_path = (root / fpath).resolve()
                if not curr_path.is_file() or hashlib.sha256(curr_path.read_bytes()).hexdigest() != orig_hash:
                    fixture_changes[fpath] = "modified"
            if refactor_test_changes or fixture_changes:
                task.evidence["test_tampering_detected"]=True
                if refactor_test_changes: task.evidence["refactor_test_files_changed"]=refactor_test_changes
                if fixture_changes: task.evidence["refactor_fixture_tampering_files"]=fixture_changes
                task.advance(TDDPhase.BLOCKED); save(); return task
            if artifact_hashes()!=protected_artifacts:
                task.evidence["artifact_tampering_detected"]=True
                task.advance(TDDPhase.BLOCKED); save(); return task
        if not gate_phase("REGRESSION_VERIFY","python",commands=design.test_commands+list(regression_commands or [])): return task
        regression_start=time.monotonic()
        task_results=[harness.run_command(command,category="task_tests") for command in design.test_commands]
        regression=[harness.run_command(c["command"],c.get("name","regression"),"regression_tests") if isinstance(c,dict) else harness.run_command(c,category="regression_tests") for c in (regression_commands or [])]
        regression=task_results+regression
        task.evidence["regression_duration"]=time.monotonic()-regression_start
        regression_ok=bool(regression_commands) and all(r.success for r in regression)
        task.evidence["verification_results"]=[{"command":r.command,"status":r.classification,"exit_code":r.exit_code} for r in regression]
        task.evidence["regression"]={"status":"PASS" if regression_ok else "FAIL","results":task.evidence["verification_results"]}
        if not gate.refactor(True,regression_ok):
            task.advance(TDDPhase.BLOCKED); save(); return task
        checkpoint("REFACTOR_VALIDATED")
    prod_files = list(task.evidence.get("production_files_changed", []))

    if not gate_phase("REVIEW","code_reviewer",[],[],artifacts=prod_files): return task
    coder_model = task.evidence.get("coder_model")
    designer_model = task.evidence.get("test_designer_model")
    validator_model = task.evidence.get("test_validator_model")
    reviewer_authors = []
    if coder_model:
        reviewer_authors.append((coder_model, "coder"))
    if designer_model:
        reviewer_authors.append((designer_model, "test_designer"))
    if validator_model:
        reviewer_authors.append((validator_model, "test_validator"))
    review,raw=invoke(
        "code_reviewer",
        load_prompt("code_reviewer", task=task.task, artifact=json.dumps(task.evidence, indent=2)),
        author_provider=task.evidence.get("coder_provider"),
        author_model=coder_model,
        author_models=reviewer_authors if reviewer_authors else ([coder_model] if coder_model else None),
        author_role="coder",
        artifacts=prod_files,
    )




    if review.success:
        review_result=parse_validation(raw or "", "code_reviewer", review.model, provider=getattr(review,"provider",None))
    else:
        review_result=ValidationResult("PARSE_ERROR" if not review.error else "BLOCKED", [], review.error or review.stderr or "code_reviewer failed", "code_reviewer", review.model, datetime.now(timezone.utc).isoformat(), raw or review.stderr or review.error or "")
    _record_validation(runner, review, review_result, stage="CODE_REVIEW", evidence={"task_id": task.task})
    task.evidence["review_status"]=review_result.status
    task.evidence["reviewer_provider"]=review.provider
    task.evidence["review_independent_from_coder"]=review.usage.get("validation_independence")
    if review_result and review_result.status=="PASS":
        gate.review(True, bool(task.requirements and task.evidence.get("red_test_files")), "PASS")
    else:
        if review_result and "coder" in locals() and hasattr(runner,"record_model_feedback"):
            runner.record_model_feedback(coder,"REVIEW_REJECTION",review_result.summary)
        task.advance(TDDPhase.BLOCKED)
    save()
    task.evidence["final_status"]=task.phase.value
    if getattr(runner,"store",None):
        outcomes={"red_valid":task.evidence.get("red_expected_failure_confirmed",False),"red_attempts":task.evidence.get("red_attempts",0),
            "green_attempts":task.evidence.get("green_attempts",0),"first_pass_green":task.evidence.get("green_attempts")==1,
            "refactor_regression":task.evidence.get("regression",{}).get("status")=="PASS","test_tampering":task.evidence.get("test_tampering_detected",False),
            "review_accepted":task.evidence.get("review_status")=="PASS","regression_passed":task.evidence.get("regression",{}).get("status")=="PASS","blocked":not task.complete()}
        for execution in task.evidence.get("executions",[]): runner.store.update_execution_outcome(execution["id"],**outcomes)
    save()
    return task
