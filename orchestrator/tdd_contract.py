from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
import json

from orchestrator.validation.parser import _extract
from orchestrator.verification.policies import CommandPolicy


@dataclass(frozen=True)
class TestDesign:
    task_id: str
    requirement_ids: list[str]
    acceptance_criteria_ids: list[str]
    created_tests: list[str]
    test_commands: list[list[str]]
    fixture_files: list[str] = field(default_factory=list)


def hash_test_files(root: str | Path) -> dict[str, str]:
    root=Path(root).resolve()
    hashes={}
    for path in root.rglob("*"):
        if not (path.is_file() or path.is_symlink()) or any(part in {".git", ".venv", ".orchestrator", "build", "dist", "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache"} for part in path.parts): continue
        relative=path.relative_to(root)
        if "tests" not in relative.parts and not path.name.startswith("test_") and path.name not in {"conftest.py","pytest.ini","tox.ini","setup.cfg","pyproject.toml","package.json"}: continue
        hashes[str(relative)]=sha256(str(path.readlink()).encode() if path.is_symlink() else path.read_bytes()).hexdigest()
    return hashes


def changed_test_hashes(before: dict[str,str], root: str | Path) -> list[str]:
    after=hash_test_files(root)
    return sorted(path for path in set(before)|set(after) if before.get(path)!=after.get(path))


def _design_object(raw, depth=0):
    if depth>5: return None
    if isinstance(raw,dict):
        if "task_id" in raw and "created_tests" in raw: return raw
        for value in reversed(list(raw.values())):
            found=_design_object(value,depth+1)
            if found: return found
    elif isinstance(raw,list):
        for value in reversed(raw):
            found=_design_object(value,depth+1)
            if found: return found
    elif isinstance(raw,str):
        parsed=_extract(raw)
        if parsed is not None:
            found=_design_object(parsed,depth+1)
            if found: return found
        for line in reversed(raw.splitlines()):
            try: value=json.loads(line)
            except ValueError: continue
            found=_design_object(value,depth+1)
            if found: return found
    return None


def parse_test_design(raw: str | dict, task_id: str, requirement_ids: list[str], acceptance_criteria_ids: list[str], workspace: str | Path, changed_test_files: list[str]) -> TestDesign:
    obj=_design_object(raw)
    if not isinstance(obj,dict): raise ValueError("TestDesigner did not return a JSON object")
    if obj.get("task_id")!=task_id: raise ValueError("TestDesigner task_id does not match task")
    if set(obj.get("requirement_ids",[]))!=set(requirement_ids) or set(obj.get("acceptance_criteria_ids",[]))!=set(acceptance_criteria_ids):
        raise ValueError("TestDesigner omitted linked requirements or acceptance criteria")
    created=obj.get("created_tests")
    commands=obj.get("test_commands")
    if not isinstance(created,list) or not created or not all(isinstance(item,str) and item.strip() for item in created): raise ValueError("created_tests must contain test IDs")
    if not isinstance(commands,list) or not commands or not all(isinstance(cmd,list) and cmd and all(isinstance(token,str) for token in cmd) for cmd in commands): raise ValueError("test_commands must contain argument arrays")
    root=Path(workspace).resolve(); changed=set(changed_test_files)
    for test_id in created:
        test_path=test_id.split("::",1)[0]
        if not test_path or test_path.startswith("-") or ".." in Path(test_path).parts: raise ValueError("invalid test path")
        path=(root/test_path).resolve()
        try: relative=str(path.relative_to(root))
        except ValueError: raise ValueError("test path outside workspace") from None
        if relative not in changed or not path.is_file(): raise ValueError("created_tests must reference a modified test file")
        if not ("tests" in Path(relative).parts or Path(relative).name.startswith("test_")): raise ValueError("created_tests must reference test files")
    policy=CommandPolicy(root)
    valid_commands = []
    for command in commands:
        allowed,reason=policy.validate(command)
        if not allowed: raise ValueError(reason)
        executable=Path(command[0]).name
        safe=(executable in {"python","python3"} and command[1:3] in (["-m","pytest"],["-m","unittest"])) or \
             executable=="pytest" or (executable,command[1] if len(command)>1 else "") in {
                 ("cargo","test"),("go","test"),("pio","test"),("npm","test"),("pnpm","test"),("ctest","-R"),("make","test")}
        if not safe: raise ValueError("test_commands must use an approved test runner")
        if any(test_id in command for test_id in created):
            valid_commands.append(command)
    if not valid_commands: raise ValueError("test_commands must be task-specific")
    commands = valid_commands
    if any(not any(test_id in command for command in commands) for test_id in created): raise ValueError("each created test needs a task-specific command")
    fixtures_raw = obj.get("fixture_files") or obj.get("fixtures") or []
    if not isinstance(fixtures_raw, list) or any(not isinstance(f, str) or not f.strip() for f in fixtures_raw):
        raise ValueError("fixture_files must be a list of relative path strings")
    valid_fixtures = []
    for f in fixtures_raw:
        if f.startswith("-") or ".." in Path(f).parts:
            raise ValueError(f"invalid fixture path: {f}")
        fp = (root / f).resolve()
        try:
            rel_fix = str(fp.relative_to(root))
        except ValueError:
            raise ValueError(f"fixture path outside workspace: {f}")
        if not fp.is_file():
            raise ValueError(f"declared fixture file not found: {f}")
        valid_fixtures.append(rel_fix)
    return TestDesign(task_id,list(requirement_ids),list(acceptance_criteria_ids),list(created),[list(command) for command in commands],fixture_files=valid_fixtures)
