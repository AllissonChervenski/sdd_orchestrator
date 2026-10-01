from dataclasses import dataclass, field
from pathlib import Path
import json
from .transitions import TDDPhase, transition


@dataclass
class TDDTask:
    task: str
    requirements: list[str] = field(default_factory=list)
    acceptance_criteria: list[str] = field(default_factory=list)
    test_type: str = "UNIT"
    phase: TDDPhase = TDDPhase.ANALYZE
    evidence: dict = field(default_factory=dict)
    attempts: dict = field(default_factory=lambda: {"red": 0, "green": 0, "refactor": 0})

    def advance(self, target, evidence=None):
        merged = {**self.evidence, **(evidence or {})}
        self.phase = transition(self.phase, target, merged)
        self.evidence = merged
        return self.phase

    def save(self, path: str | Path):
        path=Path(path)
        payload = {"task": self.task, "requirement": self.requirements, "acceptance_criteria": self.acceptance_criteria, "test_type": self.test_type, "phase": self.phase.value, "attempts": self.attempts, **self.evidence}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2))
        sections=[("ANALYZE",self.evidence.get("analysis",{})),("RED",self.evidence.get("red",{})),
            ("GREEN",self.evidence.get("green",{"attempts":self.evidence.get("green_attempts",0)})),
            ("REFACTOR",{"attempts":self.evidence.get("refactor_attempts",0),"performed":self.evidence.get("refactor_performed")}),
            ("REGRESSION",self.evidence.get("regression",{})),("REVIEW",{"status":self.evidence.get("review_status","PENDING"),"provider":self.evidence.get("reviewer_provider")}),
            ("FINAL VERIFY",self.evidence.get("final_verify",{}))]
        lines=[f"# TDD report: {self.task}","",f"Phase: **{self.phase.value}**",f"Requirements: {', '.join(self.requirements) or 'none'}",f"Acceptance criteria: {', '.join(self.acceptance_criteria) or 'none'}",""]
        for title,value in sections:
            lines.extend([f"## {title}","",f"```json\n{json.dumps(value,indent=2)}\n```",""])
        lines.extend(["## Changed files","",f"Tests: {', '.join(self.evidence.get('red_test_files',[])) or 'none'}",f"Production: {', '.join(self.evidence.get('production_files_changed',[])) or 'none'}",""])
        path.with_suffix(".md").write_text("\n".join(lines))

    def complete(self):
        return self.phase == TDDPhase.COMPLETE
