"""Interactive gates with durable, single-use approvals."""
import json
import subprocess
from pathlib import Path


class HumanGateRecoveryRequired(RuntimeError):
    """A prior approved stage may have run; re-execution needs inspection."""


class InteractiveGate:
    def __init__(self, input_fn=input, workspace=None, store=None, workflow_id=None, resume_id=None, context_fn=None):
        self.input_fn=input_fn
        self.workspace=Path(workspace or Path.cwd()).resolve()
        self.store=store
        self.workflow_id=workflow_id
        self.resume_id=resume_id
        self.context_fn=context_fn
        self.last_decision=None
        self.ready_gate_id=None
        self.ready_role=None
        self.ready_task_id=None
        self.active_gate_id=None

        self.events=[]
        self.stage_artifacts=[]
        self.stage_role=None
        self.stage_name=None
        self.stage_task_id=None


    def _event(self, name, **details):
        self.events.append({"event":name,**details})

    def _checkpoint_transition(self):
        if not self.store or not self.workflow_id: return None
        checkpoint=self.store.latest_checkpoint(self.workflow_id)
        return checkpoint["transition_id"] if checkpoint else None

    def _fingerprint(self):
        from orchestrator.workflow.resume import WorkspaceFingerprint
        return WorkspaceFingerprint(self.workspace).capture(self.workflow_id)

    def _set_workflow_stage(self, stage, pending=None):
        if not self.store or not self.workflow_id: return
        item=self.store.get_workflow(self.workflow_id)
        if not item: return
        state={**item["state"]}
        if pending is not None: state["pending_gate"]=pending
        if stage=="READY":
            state.pop("block_reason",None)
            state.pop("reason",None)
        self.store.update_workflow(self.workflow_id,stage,state,item["current_task"])

    def _summary(self, stage, role, provider, model, files, commands, task_id, artifacts=(), extra_context=None):
        from orchestrator.agents.roles import ROLES
        skill = ROLES[role].skill_name if role in ROLES else None
        reason = (
            "interactive stagnation warning: agent appears to be stalled or making insignificant changes"
            if stage == "STAGNATION_WARNING"
            else "interactive approval before stage execution"
        )
        summary={"workflow_id":self.workflow_id,"stage":stage,"role":role,"provider":provider,
                 "model":model or "CLI default","skill":skill,"task":task_id,
                 "files_allowed":list(files),"files_expected_to_change":list(files),
                 "commands_or_expected_outputs":list(commands),
                 "artifacts_supplied":list(artifacts or ()),
                 "reason_for_gate":reason}
        if extra_context:
            summary["stagnation_context"] = extra_context
        if self.context_fn:
            summary.update(self.context_fn(stage,role,provider,model,task_id) or {})
        return summary

    def ask_clarifications(self, questions):
        """Collect real SpecKit clarification answers without starting a worker."""
        answers=[]
        for index, question in enumerate(questions, 1):
            print(f"Clarification {index}/{len(questions)}: {question}")
            try:
                answer=self.input_fn("Answer (or 'abort'): ").strip()
            except (EOFError,KeyboardInterrupt):
                self.last_decision="pending"
                return None
            if answer.lower() in {"abort", "cancel"} or not answer:
                self.last_decision="abort"
                return None
            answers.append(answer)
            self._event("clarification_answered",index=index)
        self.last_decision="clarifications_answered"
        return answers

    def confirm(self, stage, role, provider, model, files, commands, task_id=None, attempt=1, artifacts=(), extra_context=None) -> bool:
        if stage != "AGENT_CALL":
            self.stage_name = stage
            self.stage_role = role
            self.stage_task_id = task_id
            self.stage_artifacts = list(artifacts or ())
        else:
            if (
                self.stage_role == role
                and (self.stage_task_id == task_id or task_id is None or self.stage_task_id is None)
                and self.stage_artifacts
                and not artifacts
            ):
                raise RuntimeError(
                    f"INVARIANCE_VIOLATION: stage '{self.stage_name}' declared artifacts_supplied "
                    f"{self.stage_artifacts}, but AGENT_CALL for role '{role}' received empty artifacts."
                )

        summary=self._summary(stage,role,provider,model,files,commands,task_id,artifacts=artifacts,extra_context=extra_context)

        checkpoint=self._checkpoint_transition()
        gate_id=None
        if self.store and self.workflow_id and checkpoint:
            gate=self.store.latest_human_gate(self.workflow_id,checkpoint,stage,role,task_id,attempt)
            if gate and gate["status"]=="APPROVED":
                from orchestrator.workflow.resume import compare_fingerprints
                status,_=compare_fingerprints(gate["workspace_fingerprint"],self._fingerprint())
                if status in {"CLEAN_MATCH","SAFE_DIVERGENCE"}:
                    self.ready_gate_id=gate["id"]; self.ready_role=role; self.ready_task_id=task_id
                    self._event("approval_reused",gate_id=gate["id"],stage=stage)
                    return True
                gate_id=self.store.create_human_gate(self.workflow_id,self.resume_id,checkpoint,stage,role,task_id,provider,model,attempt)
            elif not gate or gate["status"] in {"ABORTED","INVALIDATED","EXECUTION_FAILED","EXECUTION_STARTED","EXECUTION_COMPLETED","CHECKPOINTED"}:
                gate_id=self.store.create_human_gate(self.workflow_id,self.resume_id,checkpoint,stage,role,task_id,provider,model,attempt)
            else:
                gate_id=gate["id"]
            self._set_workflow_stage("HUMAN_APPROVAL_REQUIRED",summary)
        print(json.dumps(summary,indent=2))
        while True:
            try: decision=self.input_fn("Gate [continue/inspect/abort]: ").strip().lower()
            except (EOFError,KeyboardInterrupt):
                self.last_decision="pending"
                return False
            self._event("gate_decision_received",stage=stage,decision=decision)
            if decision=="continue":
                self.last_decision="continue"
                if gate_id:
                    if not self.store.approve_human_gate(gate_id,self.resume_id,self._fingerprint()):
                        raise HumanGateRecoveryRequired(f"APPROVAL_CONFLICT: {stage}")
                    self.ready_gate_id=gate_id; self.ready_role=role; self.ready_task_id=task_id
                    if stage == "AGENT_CALL":
                        self.store.update_human_gate_status(gate_id, "APPROVED", "EXECUTION_STARTED")
                        self.active_gate_id = gate_id
                    self._set_workflow_stage("READY",summary)
                    self._event("approval_persisted",gate_id=gate_id,stage=stage)
                return True
            if decision=="abort":
                self.last_decision="abort"
                if gate_id: self.store.reject_human_gate(gate_id)
                return False
            if decision=="inspect":
                print("Review the stage plan above. No command has run for this gate yet.")
                print(json.dumps(summary,indent=2))
                paths=[path for path in files if isinstance(path,str) and (self.workspace/path).exists()]
                if paths:
                    try:
                        diff=subprocess.run(["git","diff","--",*paths],cwd=self.workspace,stdin=subprocess.DEVNULL,capture_output=True,text=True,timeout=10,check=False)
                        if diff.stdout: print("Current diff:\n"+diff.stdout[:4000])
                    except (OSError,subprocess.TimeoutExpired): pass
                continue
            print("Choose continue, inspect, or abort.")

    def authorize_agent_call(self, role, provider, model, task_id, files, outputs, artifacts=()):
        checkpoint=self._checkpoint_transition()
        if not (self.ready_gate_id and self.ready_role==role and self.ready_task_id==task_id):
            if not self.confirm("AGENT_CALL",role,provider,model,files,outputs,task_id=task_id,artifacts=artifacts): return False

        if not self.store or not self.ready_gate_id: return True
        from orchestrator.workflow.resume import compare_fingerprints
        gate=next((item for item in self.store.list_human_gates(self.workflow_id) if item["id"]==self.ready_gate_id),None)
        if not gate or gate["checkpoint_transition_id"]!=checkpoint:
            raise HumanGateRecoveryRequired("APPROVAL_CHECKPOINT_CHANGED: stage approval is stale")
        status,_=compare_fingerprints(gate["workspace_fingerprint"],self._fingerprint())
        if status not in {"CLEAN_MATCH","SAFE_DIVERGENCE"}:
            raise HumanGateRecoveryRequired(f"APPROVAL_WORKSPACE_DIVERGED: {status}")
        if not self.store.update_human_gate_status(self.ready_gate_id,"APPROVED","EXECUTION_STARTED"):
            if gate.get("status") != "EXECUTION_STARTED":
                raise HumanGateRecoveryRequired("APPROVAL_ALREADY_CONSUMED: agent call was not repeated")
        self.active_gate_id=self.ready_gate_id
        self.ready_gate_id=None
        self._event("stage_execution_started",gate_id=self.active_gate_id,role=role)
        return True

    def agent_call_completed(self, result):
        if not self.active_gate_id or not self.store: return
        status="EXECUTION_COMPLETED" if result.success else "EXECUTION_FAILED"
        self.store.update_human_gate_status(self.active_gate_id,"EXECUTION_STARTED",status,result.success)
        self._event("stage_execution_completed",gate_id=self.active_gate_id,status=status)
        self.active_gate_id=None
