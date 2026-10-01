from dataclasses import dataclass, field


SDD_STAGES = ["CONSTITUTION", "CONSTITUTION_VALIDATION", "SPECIFICATION", "SPECIFICATION_VALIDATION", "PLAN", "PLAN_VALIDATION", "TASKS", "TASKS_VALIDATION", "CROSS_ARTIFACT_VALIDATION", "IMPLEMENTATION", "FINAL_VALIDATION", "COMPLETE"]


@dataclass
class Workflow:
    workflow_id: str
    feature: str
    stage: str = "CONSTITUTION"
    completed_tasks: list[str] = field(default_factory=list)
    blocked_tasks: list[str] = field(default_factory=list)
    attempts: dict[str, int] = field(default_factory=dict)
    traceability: list[dict] = field(default_factory=list)

    def advance(self, stage):
        current_index = SDD_STAGES.index(self.stage)
        if stage not in SDD_STAGES or SDD_STAGES.index(stage) != current_index + 1:
            raise ValueError(f"Invalid SDD transition: {self.stage} -> {stage}")
        self.stage = stage
