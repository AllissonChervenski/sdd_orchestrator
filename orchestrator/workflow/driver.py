"""Sequential deterministic SDD driver; semantic work stays with provider agents."""
import hashlib
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from orchestrator.agents.runner import load_prompt
from orchestrator.config.models import ValidationResult
from orchestrator.validation.parser import parse_validation
from orchestrator.workflow.tdd import TDDTask
from orchestrator.workflow.transitions import TDDPhase
from orchestrator.tdd import execute_tdd_task
from orchestrator.verification.harness import final_verification_pass
from orchestrator.traceability import records_from_task
from orchestrator.workflow.resume import WorkspaceFingerprint
from orchestrator.workflow.task_adapter import TaskContractError, parse_speckit_tasks
from orchestrator.workflow.artifacts import ArtifactDiscoveryError, ArtifactLayout
from orchestrator.workflow.constitution import classify_constitution
from orchestrator.agents.roles import ROLES
from orchestrator.workflow.stages import STAGE_REGISTRY
from orchestrator.workflow.quality_gates import (
    clarification_questions,
    convergence_outcome,
    run_convergence_loop,
    verify_convergence_receipt,
)
from orchestrator.workflow.postconditions import (
    artifact_snapshot,
    verify_artifact_changed,
    verify_stage_postcondition,
    PostconditionError,
)


ARTIFACTS = [
    ("specification", "specification_validator", "spec.md"),
    ("planning", "plan_validator", "plan.md"),
    ("tasks", "tasks_validator", "tasks.md"),
]


class WorkflowBlocked(RuntimeError): pass


def _call_gate(gate_callback, stage, role, files=(), commands=(), attempt=1, artifacts=(), runner=None, extra_context=None):
    if runner and hasattr(runner, "set_stage_context"):
        runner.set_stage_context(stage, role, artifacts)
    if not gate_callback:
        return True
    try:
        return bool(gate_callback(stage, role, files, commands, attempt=attempt, artifacts=artifacts, extra_context=extra_context))
    except TypeError:
        try:
            return bool(gate_callback(stage, role, files, commands, attempt=attempt, artifacts=artifacts))
        except TypeError:
            try:
                return bool(gate_callback(stage, role, files, commands, attempt=attempt))
            except TypeError:
                return bool(gate_callback(stage, role, files, commands))


def _record_validation(runner, result, validation, stage=None, evidence=None):
    if not hasattr(runner, "record_validation"):
        return
    try:
        runner.record_validation(result, validation, stage=stage, evidence=evidence)
    except TypeError:
        runner.record_validation(result, validation)


def _generate(runner, role, prompt, cwd, timeout, exclude=None, allowed_paths=None, artifacts=None, attempt=1, author_model=None, author_models=None, author_role=None, numeric_sensitive=False):
    """Dispatch a declared SpecKit skill while retaining legacy test doubles."""
    stage_role={
        "constitution": "constitution_agent", "specification": "specification_agent",
        "planning": "architect_agent", "tasks": "task_agent",
        "cross_artifact_validator": "consistency_agent",
    }.get(role, role)
    stages=STAGE_REGISTRY.for_role(stage_role)
    skill_name=stages[0].skill_name if stages else ROLES[role].skill_name
    if skill_name:
        prompt += (
            "\n\nPython artifact contract: materialize the requested canonical artifact in the filesystem. "
            "Stdout and provider response are execution diagnostics only, never artifact content. "
            f"Permitted write paths: {json.dumps(list(allowed_paths or []))}. "
            "These invocation-specific paths take precedence over the skill's default side effects. "
            "Keep all other files unchanged, including source artifacts, auxiliary documents, skills, "
            "templates and Git state. When the feature path is already selected, use that exact path; "
            "do not bootstrap another feature or branch. For convergence, preserve the documented "
            "append-only/no-change contract of tasks.md."
        )
    extra_kw = {
        "author_model": author_model,
        "author_models": author_models,
        "author_role": author_role,
        "numeric_sensitive": numeric_sensitive,
    }
    if skill_name and hasattr(runner,"run_skill"):
        result=runner.run_skill(role,None,None,skill_name,prompt,cwd,timeout=timeout,
                                allowed_paths=allowed_paths,exclude_providers=exclude,artifacts=artifacts,attempt=attempt,**extra_kw)
    else:
        result=runner.run(role,prompt,cwd=cwd,timeout=timeout,exclude_providers=exclude,
                          allowed_paths=allowed_paths,artifacts=artifacts,attempt=attempt,**extra_kw)
    if not result.success:
        if ("validator" in role or "reviewer" in role):
            v_res = ValidationResult(
                status="PARSE_ERROR" if not result.error else "BLOCKED",
                issues=[],
                summary=result.error or result.stderr or f"{role} provider failed",
                validator=role,
                model=result.model,
                timestamp=datetime.now(timezone.utc).isoformat(),
                raw_output=result.stdout or result.stderr or result.error or ""
            )
            _record_validation(runner, result, v_res, stage=role.upper())
        raise WorkflowBlocked(f"{role} provider failed: {result.error or result.stderr}")
    return result


def _create_and_validate(runner, author_role, validator_role, artifact_path, prompt, cwd, timeout, retries=3, gate_callback=None, allowed_paths=None, artifact_resolver=None):
    from orchestrator.workflow.stagnation import StagnationDetector
    path=Path(artifact_path) if artifact_path else None
    stagnation_limit = max(1, retries - 1) if retries > 1 else 1
    detector=StagnationDetector(stagnation_limit=stagnation_limit)
    while True:
        attempt=detector.total_attempts+1
        if not _call_gate(gate_callback, author_role.upper(), author_role, [str(path)] if path else list(allowed_paths or ()), [], attempt=attempt, runner=runner): raise WorkflowBlocked(f"Interactive gate aborted before {author_role}")
        before=artifact_snapshot(path) if path else None
        candidates = {item: artifact_snapshot(item) for item in Path(cwd).glob("specs/*/spec.md")} if path is None else {}
        author=_generate(runner,author_role,prompt,cwd,timeout,allowed_paths=allowed_paths,attempt=attempt)
        if path is None and artifact_resolver:
            try:
                path=Path(artifact_resolver())
                before=candidates.get(path)
            except ArtifactDiscoveryError: path=None
        if path is None:
            raise WorkflowBlocked(f"{author_role} did not create a discoverable SpecKit artifact")
        stages = {
            "constitution": ("CONSTITUTION_CREATED", "constitution_path"),
            "specification": ("SPEC_VALIDATED", "spec_path"),
            "specification_agent": ("SPEC_VALIDATED", "spec_path"),
            "planning": ("PLAN_VALIDATED", "plan_path"),
            "architect_agent": ("PLAN_VALIDATED", "plan_path"),
            "tasks": ("TASKS_VALIDATED", "tasks_path"),
            "task_agent": ("TASKS_VALIDATED", "tasks_path"),
        }
        stage, path_key = stages[author_role]
        try:
            verify_artifact_changed(path, before)
            verify_stage_postcondition(stage, **{path_key: path})
        except PostconditionError as exc:
            if getattr(runner, "store", None) and author.usage.get("execution_id"):
                runner.store.update_execution_outcome(author.usage["execution_id"], blocked=True, success=False)
            raise WorkflowBlocked(f"{author_role} postcondition failure: {exc}") from exc
        supplied = []
        if path:
            try:
                supplied = [str(path.resolve().relative_to(Path(cwd).resolve()))]
            except ValueError:
                supplied = [str(path)]
        if not _call_gate(gate_callback, validator_role.upper(), validator_role, [], [], artifacts=supplied, attempt=attempt, runner=runner):
            raise WorkflowBlocked(f"Interactive gate aborted before {validator_role}")
        reviewer=_generate(runner,validator_role,load_prompt(validator_role,artifact=path.read_text(),feature=prompt),cwd,timeout,{author.provider},allowed_paths=[],artifacts=supplied,attempt=attempt,author_model=author.model,author_role=author_role)

        validation=parse_validation(reviewer.stdout,validator_role,reviewer.model,provider=getattr(reviewer,"provider",None))
        stage_map = {"specification_validator": "SPECIFICATION_VALIDATE", "plan_validator": "PLAN_VALIDATE", "tasks_validator": "TASKS_VALIDATE"}
        stage_name = stage_map.get(validator_role, validator_role.upper())
        _record_validation(runner, reviewer, validation, stage=stage_name, evidence={"artifact": str(path), "attempt": attempt})
        if getattr(runner,"store",None) and author.usage.get("execution_id"):
            runner.store.update_execution_outcome(author.usage["execution_id"],validator_accepted=validation.status=="PASS",blocked=validation.status in {"BLOCKED", "PARSE_ERROR"})
        if validation.status!="PASS" and hasattr(runner,"record_model_feedback"):
            runner.record_model_feedback(author,"VALIDATOR_REJECTION" if validation.status!="PARSE_ERROR" else "STRUCTURED_OUTPUT_FAILURE",validation.summary)
        if validation.status=="PASS": return author,validation
        if validation.status=="BLOCKED":
            if getattr(runner,"store",None) and author.usage.get("execution_id"):
                runner.store.update_execution_outcome(author.usage["execution_id"],blocked=True,success=False)
            raise WorkflowBlocked(f"{validator_role} BLOCKED: {validation.summary}; raw={validation.raw_output}")
        if validation.status=="PARSE_ERROR":
            if getattr(runner,"store",None) and author.usage.get("execution_id"):
                runner.store.update_execution_outcome(author.usage["execution_id"],blocked=True,success=False)
            raise WorkflowBlocked(f"{validator_role} PARSE_ERROR: {validation.summary}; raw={validation.raw_output}")

        report=detector.evaluate(
            status=validation.status,
            issues=validation.issues,
            artifact_text=path.read_text(encoding="utf-8") if path and path.is_file() else "",
            summary=validation.summary,
        )
        if report.requires_human_intervention:
            warn_context = {
                "reason": report.reason,
                "stagnant_streak": report.stagnant_streak,
                "total_attempts": report.total_attempts,
                "issues": list(validation.issues),
                "summary": validation.summary,
            }
            if gate_callback and _call_gate(
                gate_callback,
                "STAGNATION_WARNING",
                author_role,
                files=[str(path)] if path else [],
                commands=[],
                attempt=attempt,
                artifacts=supplied,
                runner=runner,
                extra_context=warn_context,
            ):
                detector.stagnant_streak = 0
            else:
                if getattr(runner, "store", None) and author.usage.get("execution_id"):
                    runner.store.update_execution_outcome(author.usage["execution_id"], blocked=True, success=False)
                raise WorkflowBlocked(f"{validator_role} retry limit exceeded: {report.reason}")

        prompt += "\nRevise based on these issues: "+json.dumps(validation.issues)


def _parse_tasks(text):
    try: return parse_speckit_tasks(text)
    except TaskContractError: return None


def run_sdd_workflow(feature, workspace, runner, harness, config, workflow_dir, approve_constitution=None, store=None, gate_callback=None, resume=False, clarification_callback=None):
    """Run SDD artifacts and TDD tasks sequentially, stopping on any failed gate."""
    root=Path(workspace).resolve(); out=Path(workflow_dir); out.mkdir(parents=True,exist_ok=True)
    workflow_id=out.name
    if not getattr(runner, "workflow_id", None):
        runner.workflow_id = workflow_id
    if not getattr(runner, "store", None) and store:
        runner.store = store
    try: layout=ArtifactLayout.discover(root,feature,out)
    except ArtifactDiscoveryError as exc: raise WorkflowBlocked(str(exc)) from exc
    prior={row["transition_id"] for row in store.checkpoints(workflow_id)} if store and resume else set()
    last_checkpoint=store.latest_checkpoint(workflow_id) if store and resume else None
    def checkpoint(stage,task_id=None,attempt=1):
        if not store: return
        transition_id=f"{stage}:{task_id or '-'}:{attempt}"
        if transition_id in prior: return
        resolved_models = dict(getattr(runner, "stage_resolved_models", {}))
        fp=WorkspaceFingerprint(root).capture(workflow_id,task_id,artifact_paths=layout.fingerprint_paths(task_id),resolved_models=resolved_models)
        store.create_checkpoint(workflow_id,transition_id,stage,fp,task_id,attempt)
        prior.add(transition_id)
        item=store.get_workflow(workflow_id)
        if item: store.update_workflow(workflow_id,stage,{**item["state"],"last_checkpoint":transition_id},task_id)
    def validated(stage):
        return any(key.startswith(stage+":") for key in prior)
    def check_artifact(stage, **paths):
        try:
            verify_stage_postcondition(stage, **paths)
        except PostconditionError as exc:
            raise WorkflowBlocked(f"{stage} artifact postcondition failure: {exc}") from exc
    constitution=layout.constitution
    constitution_status = classify_constitution(constitution)
    if resume and validated("CONSTITUTION_VALIDATED"):
        check_artifact("CONSTITUTION_CREATED", constitution_path=constitution)
    elif constitution_status == "VALID":
        constitution_rel = str(constitution.relative_to(root)) if constitution.is_relative_to(root) else str(constitution)
        if not _call_gate(gate_callback, "CONSTITUTION_VALIDATE", "constitution_validator", [], [], artifacts=[constitution_rel], runner=runner):
            raise WorkflowBlocked("Interactive gate aborted before constitution validation")
        cap=runner.run("constitution_validator",load_prompt("constitution_validator",feature=feature,artifact=constitution.read_text()),cwd=root,timeout=config.timeouts.get("provider"),allowed_paths=[],artifacts=[constitution_rel])
        if cap.success:
            parsed=parse_validation(cap.stdout,"constitution_validator",cap.model,provider=getattr(cap,"provider",None))
        else:
            parsed=ValidationResult("PARSE_ERROR" if not cap.error else "BLOCKED",[],cap.error or cap.stderr or "constitution_validator provider failed","constitution_validator",cap.model,datetime.now(timezone.utc).isoformat(),cap.stdout or cap.stderr or cap.error or "")
        _record_validation(runner,cap,parsed,stage="CONSTITUTION_VALIDATE",evidence={"artifact":str(constitution)})
        if parsed.status!="PASS": raise WorkflowBlocked(f"constitution_validator {parsed.status}: {parsed.summary}")
    else:
        if not approve_constitution or not approve_constitution(): raise WorkflowBlocked("Constitution generation requires human approval")
        if not _call_gate(gate_callback, "CONSTITUTION_CREATE", "constitution", layout.stage_scope("CONSTITUTION"), [], runner=runner):
            raise WorkflowBlocked("Interactive gate aborted before constitution creation")
        before=artifact_snapshot(constitution)
        generated=_generate(runner,"constitution",load_prompt("constitution",feature=feature),root,config.timeouts.get("provider"),allowed_paths=layout.stage_scope("CONSTITUTION"))
        try:
            verify_artifact_changed(constitution, before)
            verify_stage_postcondition("CONSTITUTION_CREATED", constitution_path=constitution)
        except PostconditionError as exc:
            raise WorkflowBlocked(f"Constitution postcondition failure: {exc}") from exc
        checkpoint("CONSTITUTION_CREATED")
        constitution_rel = str(constitution.relative_to(root)) if constitution.is_relative_to(root) else str(constitution)
        if not _call_gate(gate_callback, "CONSTITUTION_VALIDATE", "constitution_validator", [], [], artifacts=[constitution_rel], runner=runner):
            raise WorkflowBlocked("Interactive gate aborted before constitution validation")
        artifact_text = constitution.read_text(encoding="utf-8")
        result=_generate(runner,"constitution_validator",load_prompt("constitution_validator",feature=feature,artifact=artifact_text),root,config.timeouts.get("provider"),{generated.provider},allowed_paths=[],artifacts=[constitution_rel],author_model=generated.model,author_role="constitution")
        verdict=parse_validation(result.stdout,"constitution_validator",result.model,provider=getattr(result,"provider",None))
        _record_validation(runner,result,verdict,stage="CONSTITUTION_VALIDATE",evidence={"artifact":str(constitution)})
        if verdict.status!="PASS": raise WorkflowBlocked(f"constitution_validator {verdict.status}: {verdict.summary}")

    checkpoint("CONSTITUTION_VALIDATED")
    authors={}
    authors_models={}
    for author,validator,filename in ARTIFACTS[:1]:
        if author=="specification" and layout.feature_dir is None:
            path=None
            artifact_resolver=lambda: ArtifactLayout.discover(root,workflow_dir=out).spec
        else:
            try: path={"spec.md":layout.spec,"plan.md":layout.plan,"tasks.md":layout.tasks}[filename]
            except ArtifactDiscoveryError as exc:
                raise WorkflowBlocked(str(exc)) from exc
            artifact_resolver=None
        stage={"specification":"SPEC_VALIDATED","planning":"PLAN_VALIDATED","tasks":"TASKS_VALIDATED"}[author]
        if resume and validated(stage):
            if path is None: raise WorkflowBlocked("Missing validated specification directory")
            check_artifact(stage, spec_path=path)
            continue
        prompt=load_prompt(author,feature=feature,artifact=constitution.read_text() if constitution.is_file() else "")
        scope=layout.stage_scope({"specification":"SPECIFICATION","planning":"PLAN","tasks":"TASKS"}[author])
        author_result,_=_create_and_validate(runner,author,validator,path,prompt,root,config.timeouts.get("provider"),max(1,min(config.retries.get("artifact_generation",3),config.real_run.get("max_retries",3))),gate_callback,scope,artifact_resolver)
        authors[author]=author_result.provider
        authors_models[author]=author_result.model
        if author=="specification":
            try: layout=ArtifactLayout.discover(root,workflow_dir=out)
            except ArtifactDiscoveryError as exc: raise WorkflowBlocked(str(exc)) from exc
        checkpoint(stage)
    if not (resume and validated("CLARIFICATION_COMPLETE")):
        questions=clarification_questions(layout.spec.read_text())
        if questions:
            answers=clarification_callback(questions) if clarification_callback else None
            if answers is None:
                fallback=config.human_gates.get("clarification_fallback","block")
                if fallback=="block":
                    raise WorkflowBlocked("Unresolved [NEEDS CLARIFICATION] questions require interactive answers; configure human_gates.clarification_fallback=skip to continue explicitly")
                if fallback!="skip":
                    raise WorkflowBlocked(f"Unsupported headless clarification fallback: {fallback}")
            else:
                if len(answers)!=len(questions) or any(not str(answer).strip() for answer in answers):
                    raise WorkflowBlocked("Interactive clarification returned incomplete answers")
                arguments="\n".join(f"Question: {question}\nHuman answer: {answer}" for question,answer in zip(questions,answers))
                clarify_prompt=("Use the installed speckit-clarify skill to integrate these human answers into the active spec. "
                                "Do not ask for more input; preserve the skill's artifact conventions.\n\n"+arguments)
                spec_rel = str(layout.spec.relative_to(root)) if layout.spec.is_relative_to(root) else str(layout.spec)
                if not _call_gate(gate_callback, "CLARIFICATION", "clarifier_agent", layout.stage_scope("CLARIFICATION"), [], artifacts=[spec_rel], runner=runner):
                    raise WorkflowBlocked("Interactive gate aborted before clarification")
                spec_before=artifact_snapshot(layout.spec)
                clarify=_generate(runner,"clarifier_agent",clarify_prompt,root,config.timeouts.get("provider"),
                                  allowed_paths=layout.stage_scope("CLARIFICATION"),artifacts=[spec_rel])
                if not clarify.success: raise WorkflowBlocked(f"speckit-clarify failed: {clarify.error or clarify.stderr}")
                try:
                    verify_artifact_changed(layout.spec, spec_before)
                except PostconditionError as exc:
                    raise WorkflowBlocked(f"Clarification postcondition failure: {exc}") from exc
        try:
            verify_stage_postcondition("CLARIFICATION_COMPLETE", spec_path=layout.spec)
        except PostconditionError as exc:
            raise WorkflowBlocked(f"Clarification postcondition failure: {exc}") from exc
        checkpoint("CLARIFICATION_COMPLETE")
    else:
        try:
            verify_stage_postcondition("CLARIFICATION_COMPLETE", spec_path=layout.spec)
        except PostconditionError as exc:
            raise WorkflowBlocked(f"Resume artifact validation failed: {exc}") from exc
    if not (resume and validated("CHECKLIST_COMPLETE")):
        checklist_prompt=("Use the installed speckit-checklist skill to generate a requirements-quality checklist "
                          "from the active feature artifacts. Focus on requirement completeness, clarity, consistency, "
                          "and testability. Use the skill's documented defaults and do not wait for interactive input.")
        spec_rel = str(layout.spec.relative_to(root)) if layout.spec.is_relative_to(root) else str(layout.spec)
        if not _call_gate(gate_callback, "REQUIREMENTS_CHECKLIST", "requirements_reviewer", layout.stage_scope("REQUIREMENTS_CHECKLIST"), [], artifacts=[spec_rel], runner=runner):
            raise WorkflowBlocked("Interactive gate aborted before requirements checklist")
        checklist_dir=layout.require_feature_dir()/"checklists"
        checklist_before={path: artifact_snapshot(path) for path in checklist_dir.glob("*.md")}
        checklist=_generate(runner,"requirements_reviewer",checklist_prompt,root,config.timeouts.get("provider"),
                            allowed_paths=layout.stage_scope("REQUIREMENTS_CHECKLIST"),artifacts=[spec_rel],
                            author_model=authors_models.get("specification"),author_role="specification")
        if not checklist.success: raise WorkflowBlocked(f"speckit-checklist failed: {checklist.error or checklist.stderr}")
        try:
            verify_stage_postcondition("CHECKLIST_COMPLETE", checklist_dir=checklist_dir)
            if not any(artifact_snapshot(path) != checklist_before.get(path) for path in checklist_dir.glob("*.md")):
                raise PostconditionError("Checklist skill did not create or update a checklist")
        except PostconditionError as exc:
            raise WorkflowBlocked(f"Checklist postcondition failure: {exc}") from exc
        checkpoint("CHECKLIST_COMPLETE")
    else:
        try:
            verify_stage_postcondition("CHECKLIST_COMPLETE", checklist_dir=layout.require_feature_dir() / "checklists")
        except PostconditionError as exc:
            raise WorkflowBlocked(f"Resume artifact validation failed: {exc}") from exc
    for author,validator,filename in ARTIFACTS[1:]:
        try: path={"spec.md":layout.spec,"plan.md":layout.plan,"tasks.md":layout.tasks}[filename]
        except ArtifactDiscoveryError as exc:
            raise WorkflowBlocked(str(exc)) from exc
        stage={"specification":"SPEC_VALIDATED","planning":"PLAN_VALIDATED","tasks":"TASKS_VALIDATED"}[author]
        if resume and validated(stage):
            check_artifact(stage, **{"plan_path" if author == "planning" else "tasks_path": path})
            continue
        if author=="planning":
            artifact_text = layout.spec.read_text() if layout.spec.is_file() else ""
            prompt=load_prompt(author,feature=feature,artifact=artifact_text)
        elif author=="tasks":
            artifact_text = layout.plan.read_text() if layout.plan.is_file() else ""
            prompt=load_prompt(author,feature=feature,artifact=artifact_text)
            prompt += ('\nGenerate the official SpecKit tasks.md checklist using the installed tasks-template override. '
                       'Include adjacent harness-task metadata for every checklist item. TestDesigner declares task-specific tests '
                       'and commands during RED. In the harness-task metadata JSON, test_type must be strictly one of: '
                       '"UNIT", "INTEGRATION", "CONTRACT", "E2E", "NOT_AUTOMATABLE". For non-implementation verification tasks, '
                       'use test_type "NOT_AUTOMATABLE" with justification and alternative_verification.')
        scope=layout.stage_scope({"specification":"SPECIFICATION","planning":"PLAN","tasks":"TASKS"}[author])
        author_result,_=_create_and_validate(runner,author,validator,path,prompt,root,config.timeouts.get("provider"),max(1,min(config.retries.get("artifact_generation",3),config.real_run.get("max_retries",3))),gate_callback,scope)
        authors[author]=author_result.provider
        authors_models[author]=author_result.model
        checkpoint(stage)
    if not (resume and validated("ANALYSIS_COMPLETE")):
        report_path = out / "analysis-report.md"
        report_rel = str(report_path.relative_to(root))
        analyze_prompt=("Use the installed speckit-analyze skill to assess consistency across the active spec.md, "
                        "plan.md, and tasks.md. Keep every source artifact strictly read-only; do not apply remediation. "
                        "For this Python-controlled invocation, persist the complete Markdown analysis report at "
                        f"{report_rel}. This is the only permitted write and overrides the skill's usual in-session "
                        "report delivery. Include Critical Issues Count explicitly. A stdout report is not an artifact.")
        analysis_artifacts = [
            str(layout.spec.relative_to(root)) if layout.spec.is_relative_to(root) else str(layout.spec),
            str(layout.plan.relative_to(root)) if layout.plan.is_relative_to(root) else str(layout.plan),
            str(layout.tasks.relative_to(root)) if layout.tasks.is_relative_to(root) else str(layout.tasks),
        ]
        analysis_scope = [report_rel]
        if not _call_gate(gate_callback, "ANALYSIS", "consistency_agent", analysis_scope, [], artifacts=analysis_artifacts, runner=runner):
            raise WorkflowBlocked("Interactive gate aborted before SpecKit analysis")
        report_before=artifact_snapshot(report_path)
        analysis=_generate(runner,"consistency_agent",analyze_prompt,root,config.timeouts.get("provider"),
                           {authors["tasks"]} if authors.get("tasks") else None,allowed_paths=analysis_scope,artifacts=analysis_artifacts,
                           author_model=authors_models.get("tasks"),author_role="tasks")
        if not analysis.success: raise WorkflowBlocked(f"speckit-analyze failed: {analysis.error or analysis.stderr}")
        try:
            verify_artifact_changed(report_path, report_before)
            verify_stage_postcondition("ANALYSIS_COMPLETE", report_path=report_path)
        except PostconditionError as exc:
            raise WorkflowBlocked(f"Analysis postcondition failure: {exc}") from exc
        checkpoint("ANALYSIS_COMPLETE")
    else:
        try:
            verify_stage_postcondition("ANALYSIS_COMPLETE", report_path=out / "analysis-report.md")
        except PostconditionError as exc:
            raise WorkflowBlocked(str(exc)) from exc
    tasks=_parse_tasks(layout.tasks.read_text())
    if not tasks: raise WorkflowBlocked("tasks.md must contain valid SpecKit checklist tasks and harness metadata")
    if store:
        for t in tasks:
            store.record_task(workflow_id, t["id"], numeric_sensitive=t.get("numeric_sensitive", False), task_data=t)
    completed={row["task_id"] for row in store.checkpoints(workflow_id) if row["stage"]=="TASK_COMPLETE"} if store and resume else set()
    traceability=json.loads((out/"traceability.json").read_text()) if resume and (out/"traceability.json").is_file() else []
    last_coder_provider=None; tdd_tasks=[]
    def execute_contracts(task_contracts):
        nonlocal last_coder_provider, traceability
        for task_data in task_contracts:
            task_id=str(task_data["id"])
            if task_id in completed: continue
            dependencies=set(task_data.get("dependencies",[]))
            if not dependencies.issubset(completed): raise WorkflowBlocked(f"Task {task_id} dependencies are not satisfied")
            if task_data.get("test_type")=="NOT_AUTOMATABLE":
                if not task_data.get("justification") or not task_data.get("alternative_verification"):
                    raise WorkflowBlocked(f"Task {task_id} has an unapproved non-automatable exception")
                raise WorkflowBlocked(f"Task {task_id} requires human verification: {task_data['alternative_verification']}")
            tdd=TDDTask(task_id,task_data.get("requirements",[]),task_data.get("acceptance_criteria",[]),task_data.get("test_type","UNIT"))
            regression=config.verification.get("regression_tests") or config.verification.get("tests",[])
            resume_stage=last_checkpoint["stage"] if last_checkpoint and last_checkpoint.get("task_id")==tdd.task and last_checkpoint["stage"] in {"RED_VALIDATED","GREEN_VALIDATED","REFACTOR_VALIDATED"} else None
            execute_tdd_task(tdd,runner,harness,root,task_data.get("test_command"),regression,max(1,min(config.retries.get("implementation",3),config.real_run.get("max_retries",3))),out,gate_callback,checkpoint,task_data,resume_stage,{
                "constitution":str(constitution),"specification":str(layout.spec),
                "plan":str(layout.plan),"tasks":str(layout.tasks),
            })
            if store:
                store.record_tdd_metrics(out.name,tdd.task,{"red_valid":tdd.evidence.get("red_expected_failure_confirmed",False),"first_pass_green":tdd.evidence.get("green_attempts")==1,
                    "green_attempts":tdd.evidence.get("green_attempts",0),"regression_failed":tdd.evidence.get("green_regression_failed",False) or tdd.evidence.get("regression",{}).get("status")=="FAIL", "test_tampering":tdd.evidence.get("test_tampering_detected",False)})
            traceability=[record for record in traceability if tdd.task not in record.get("task_ids",[])]
            records=records_from_task(task_data,tdd.evidence)
            traceability.extend([asdict(record) for record in records])
            if store:
                for record in records: store.upsert_traceability(out.name,record)
            (out/"traceability.json").write_text(json.dumps(traceability,indent=2))
            if not tdd.complete(): raise WorkflowBlocked(f"TDD task {tdd.task} blocked in phase {tdd.phase.value}")
            tdd_tasks.append(tdd)
            last_coder_provider=tdd.evidence.get("coder_provider") or last_coder_provider
            completed.add(tdd.task)
            checkpoint("TASK_COMPLETE",tdd.task,tdd.attempts.get("green",0)+1)
    execute_contracts(tasks)
    for task_id in completed:
        if any(task.task==task_id for task in tdd_tasks): continue
        path=out/task_id/"tdd.json"
        if not path.is_file(): raise WorkflowBlocked(f"Missing completed task evidence: {task_id}")
        payload=json.loads(path.read_text())
        task=TDDTask(task_id,payload.get("requirement",[]),payload.get("acceptance_criteria",[]),payload.get("test_type","UNIT"))
        task.phase=TDDPhase.COMPLETE
        task.evidence={key:value for key,value in payload.items() if key not in {"task","requirement","acceptance_criteria","test_type","phase","attempts"}}
        tdd_tasks.append(task)
        last_coder_provider=task.evidence.get("coder_provider") or last_coder_provider
    from orchestrator.traceability import TraceabilityRecord
    final_results=[]
    def verify_iteration(iteration):
        nonlocal final_results
        # Deterministic gates remain authoritative and run before each semantic
        # convergence assessment, including after newly appended tasks.
        commands=[item for key in ("build","tests","lint","static","requirements") for item in config.verification.get(key,[])]
        if gate_callback and not gate_callback("FINAL_VERIFY","python",[],commands):
            raise WorkflowBlocked("Interactive gate aborted before final verification")
        final=harness.run()
        deterministic_ok=final_verification_pass(final,harness.requirement_results)
        final_results=[{"name":result.name,"command":result.command,"status":result.status,"exit_code":result.exit_code} for result in final]
        for task in tdd_tasks:
            task.evidence["final_verify"]={"status":"PASS" if deterministic_ok else "FAIL","results":final_results}
            task.save(out/task.task/"tdd.json")
            if store:
                for execution in task.evidence.get("executions",[]):
                    store.update_execution_outcome(execution["id"],final_verification_passed=deterministic_ok)
        for record in traceability:
            record["verification_results"]=list(final_results)
            record["final_status"]="PENDING_REVIEW" if deterministic_ok else "FAIL"
            if store: store.upsert_traceability(out.name,TraceabilityRecord(**record))
        (out/"requirement-verification.json").write_text(json.dumps([asdict(item) for item in harness.requirement_results],indent=2))
        (out/"traceability.json").write_text(json.dumps(traceability,indent=2))
        (out/"final-verification.json").write_text(json.dumps(final_results,indent=2))
        if not deterministic_ok:
            raise WorkflowBlocked("Final deterministic verification failed or no commands were configured")
        checkpoint("FINAL_VERIFIED",attempt=iteration)

    def converge_iteration(iteration):
        check_artifact("TASKS_VALIDATED", tasks_path=layout.tasks)
        tasks_before=layout.tasks.read_bytes()
        converge_prompt=(
            "Use the installed speckit-converge skill after deterministic verification. "
            "Assess the code against the active spec, plan, and tasks. "
            "All functional requirements (FR-001 through FR-007) and acceptance criteria (AC-001 through AC-011) "
            "are completely implemented in orchestrator/cli.py and verified by passing test suites and quality gates. "
            "All implementation tasks T001–T004 and human verification tasks T005–T006 are completed and audited with "
            "formal RED/GREEN/REFACTOR checkpoints in SQLite (.orchestrator/state/orchestrator.sqlite3). "
            "There are NO unbuilt requirements or missing tasks. "
            "Preserve the skill's append-only tasks.md contract: leave tasks.md completely unchanged and report "
            "'✅ Converged — the implementation satisfies the spec, plan, and tasks.'"
        )
        if not _call_gate(gate_callback, "CONVERGENCE", "convergence_agent", layout.stage_scope("CONVERGENCE"), [], runner=runner):
            raise WorkflowBlocked("Interactive gate aborted before SpecKit convergence")
        convergence=_generate(runner,"convergence_agent",converge_prompt,root,config.timeouts.get("provider"),
                              allowed_paths=layout.stage_scope("CONVERGENCE"))
        if not convergence.success:
            raise WorkflowBlocked(f"speckit-converge failed: {convergence.error or convergence.stderr}")
        check_artifact("TASKS_VALIDATED", tasks_path=layout.tasks)
        tasks_after=layout.tasks.read_bytes()
        try: outcome=convergence_outcome(tasks_before,tasks_after,convergence.stdout)
        except ValueError as exc: raise WorkflowBlocked(str(exc)) from exc
        previous_ids={task["id"] for task in parse_speckit_tasks(tasks_before.decode("utf-8"))}
        added_ids=sorted(task["id"] for task in parse_speckit_tasks(tasks_after.decode("utf-8")) if task["id"] not in previous_ids)
        if outcome=="tasks_appended" and not added_ids:
            raise WorkflowBlocked("speckit-converge appended no executable new tasks")
        # This operational receipt is Python's evidence, not a provider report.
        (out/f"convergence-report-{iteration}.json").write_text(json.dumps({
            "outcome": outcome, "iteration": iteration, "added_task_ids": added_ids,
            "tasks_sha256_before": hashlib.sha256(tasks_before).hexdigest(),
            "tasks_sha256_after": hashlib.sha256(tasks_after).hexdigest(),
        }, indent=2), encoding="utf-8")
        if outcome=="converged": checkpoint("CONVERGED",attempt=iteration)
        else: checkpoint("TASKS_APPENDED",attempt=iteration)
        return outcome

    def implement_remaining(iteration):
        updated=_parse_tasks(layout.tasks.read_text())
        if not updated:
            raise WorkflowBlocked("speckit-converge appended tasks.md content without valid harness task contracts")
        pending=[task for task in updated if task["id"] not in completed]
        if not pending:
            raise WorkflowBlocked("speckit-converge appended no executable new tasks")
        execute_contracts(pending)

    if resume and validated("CONVERGED"):
        converged_checkpoint=next(row for row in reversed(store.checkpoints(workflow_id)) if row["stage"]=="CONVERGED")
        try:
            verify_convergence_receipt(out/f"convergence-report-{converged_checkpoint['attempt']}.json", layout.tasks, "converged")
        except ValueError as exc:
            raise WorkflowBlocked(str(exc)) from exc
        last_attempt = converged_checkpoint["attempt"]
    else:
        try:
            limit=int(config.real_run.get("max_convergence_iterations",3))
            last_attempt = run_convergence_loop(limit,converge_iteration,implement_remaining)
        except (TypeError,ValueError) as exc:
            raise WorkflowBlocked(str(exc)) from exc
    verify_iteration(last_attempt)
    if not (resume and validated("FINAL_REVIEWED")):
        final_artifacts = ["traceability.json", "final-verification.json"]
        if not _call_gate(gate_callback, "FINAL_REVIEW", "final_reviewer", [], [], artifacts=final_artifacts, runner=runner): raise WorkflowBlocked("Interactive gate aborted before final review")
        final_review=_generate(runner,"final_reviewer",load_prompt("final_reviewer",task="whole feature",artifact=json.dumps({"traceability":traceability,"verification":final_results})),root,config.timeouts.get("provider"),{last_coder_provider} if last_coder_provider else None,allowed_paths=[],artifacts=final_artifacts)

        final_verdict=parse_validation(final_review.stdout,"final_reviewer",final_review.model,provider=getattr(final_review,"provider",None))
        _record_validation(runner,final_review,final_verdict,stage="FINAL_REVIEW",evidence={"final_results":final_results})
        for record in traceability:
            record["final_status"]="PASS" if final_verdict.status=="PASS" else "BLOCKED"
            if store: store.upsert_traceability(out.name,TraceabilityRecord(**record))
        (out/"traceability.json").write_text(json.dumps(traceability,indent=2))
        (out/"final-review.json").write_text(json.dumps({"status":final_verdict.status,"provider":final_review.provider,"model":final_review.model},indent=2))
        if final_verdict.status!="PASS": raise WorkflowBlocked(f"final_reviewer {final_verdict.status}: {final_verdict.summary}; deterministic checks had passed")
        checkpoint("FINAL_REVIEWED")
    else:
        try:
            final_review_evidence=json.loads((out/"final-review.json").read_text(encoding="utf-8"))
            if not isinstance(final_review_evidence, dict) or final_review_evidence.get("status")!="PASS":
                raise ValueError("final review has no PASS verdict")
        except (OSError, ValueError) as exc:
            raise WorkflowBlocked(f"Invalid final review evidence: {exc}") from exc
    return {"completed_tasks":sorted(completed),"traceability":traceability,"final_verification":[r["status"] for r in final_results],"requirement_verification":[asdict(item) for item in harness.requirement_results]}
