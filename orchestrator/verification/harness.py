from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any
import json
import shlex
import subprocess
import time
import re
from .policies import CommandPolicy


@dataclass
class VerificationResult:
    command: list[str]
    success: bool
    exit_code: int | None
    stdout: str
    stderr: str
    duration: float
    classification: str = "PASS"
    name: str = ""
    status: str = ""
    category: str = ""
    cause: str = ""

    def __post_init__(self):
        if not self.status: self.status=self.classification


@dataclass
class RequirementVerification:
    requirement_id: str
    checks: list[str] = field(default_factory=list)
    results: list[VerificationResult] = field(default_factory=list)
    status: str = "BLOCKED"


def final_verification_pass(results, requirements=()):
    """Only deterministic evidence can authorize the final workflow transition."""
    return bool(results) and all(result.success for result in results) and all(item.status=="PASS" for item in requirements)


class VerificationHarness:
    def __init__(self, workspace, commands=None, timeout=600):
        self.workspace = Path(workspace).resolve(); self.commands = commands or {}; self.timeout = timeout; self.policy = CommandPolicy(self.workspace); self.requirement_results=[]; self.on_command=None
    def detect(self):
        root = self.workspace
        found = {key:[] for key in ("task_tests","regression_tests","build","tests","lint","type","static","syntax","requirements")}
        def add(kind,name,command,reason): found[kind].append({"name":name,"command":command,"reason":reason})
        pyproject=root/"pyproject.toml"
        if pyproject.exists():
            try:
                import tomllib
                project=tomllib.loads(pyproject.read_text())
            except (ValueError,OSError): project={}
            if (root/"tests").exists() or "pytest" in project.get("tool",{}):
                add("regression_tests","pytest regression",["python","-m","pytest","-q"],"Python tests found")
                add("tests","pytest full suite",["python","-m","pytest","-q"],"Python tests found")
            if "build-system" in project:
                add("build","Python wheel",["python","-m","pip","wheel","--no-deps","."],"pyproject.toml declares a build system; requires declared build dependencies")
            if "ruff" in project.get("tool",{}) or (root/"ruff.toml").exists() or (root/".ruff.toml").exists():
                add("lint","ruff",["ruff","check","."],"Ruff configuration found")
            if "mypy" in project.get("tool",{}) or (root/"mypy.ini").exists():
                add("type","mypy",["python","-m","mypy"],"Mypy configuration found")
            add("syntax","Python syntax",["python","-m","compileall","-q","orchestrator"],"Python package found; syntax check only")
        if (root/"package.json").exists():
            try: scripts=json.loads((root/"package.json").read_text()).get("scripts",{})
            except (ValueError,OSError): scripts={}
            manager="pnpm" if (root/"pnpm-lock.yaml").exists() else "npm"
            for script,kind in (("test","tests"),("build","build"),("lint","lint"),("typecheck","type")):
                if script in scripts:
                    command=[manager,"run",script]
                    add(kind,f"{manager} {script}",command,f"package.json script {script}")
                    if script=="test":
                        add("regression_tests",f"{manager} regression",command,"package.json test script")
        if (root/"Cargo.toml").exists():
            add("build","cargo build",["cargo","build"],"Cargo.toml found")
            for kind in ("tests","regression_tests"): add(kind,"cargo test",["cargo","test"],"Cargo.toml found")
        if (root/"go.mod").exists():
            add("build","go build",["go","build","./..."],"go.mod found")
            for kind in ("tests","regression_tests"): add(kind,"go test",["go","test","./..."],"go.mod found")
            add("static","go vet",["go","vet","./..."],"go.mod found")
        if (root/"platformio.ini").exists(): add("build","PlatformIO build",["pio","run"],"platformio.ini found")
        if (root/"CMakeLists.txt").exists(): add("build","CMake build",["cmake","--build","build"],"CMakeLists.txt found; configure build directory first")
        if (root/"Makefile").exists():
            content=(root/"Makefile").read_text()
            for target,kind in (("build","build"),("test","tests"),("lint","lint")):
                if re.search(rf"(?m)^{target}\s*:",content): add(kind,f"make {target}",["make",target],f"Makefile target {target}")
        if (root/".clang-tidy").exists(): add("static","clang-tidy",["clang-tidy","-p","build","."],"clang-tidy config found; review compile database and source paths")
        if (root/"cppcheck.cfg").exists(): add("static","cppcheck",["cppcheck","--enable=warning","."],"cppcheck config found")
        return found
    def run_command(self, command, name="", category=""):
        args = shlex.split(command) if isinstance(command, str) else list(command)
        allowed, reason = self.policy.validate(args)
        if not allowed:
            result=VerificationResult(args, False, None, "", reason, 0, "BLOCKED",name,"BLOCKED",category)
            if self.on_command: self.on_command("finished",result)
            return result
        if self.on_command: self.on_command("started",{"command":args,"name":name,"category":category})
        start = time.monotonic()
        try:
            import os
            env = dict(os.environ)
            cfg_file = self.workspace / ".orchestrator" / "build" / "setup.cfg"
            if cfg_file.is_file():
                env["DIST_EXTRA_CONFIG"] = str(cfg_file.resolve())
            cp = subprocess.run(args, cwd=self.workspace, env=env, stdin=subprocess.DEVNULL, text=True, capture_output=True, timeout=self.timeout, check=False)
            status="PASS" if cp.returncode == 0 else "FAIL"
            result=VerificationResult(args, cp.returncode == 0, cp.returncode, cp.stdout, cp.stderr, time.monotonic()-start, status,name,status,category)
        except (OSError, subprocess.TimeoutExpired) as exc:
            result=VerificationResult(args, False, None, "", str(exc), time.monotonic()-start, "INFRASTRUCTURE_FAILURE",name,"INFRASTRUCTURE_FAILURE",category)
        if self.on_command: self.on_command("finished",result)
        return result
    def run(self, categories=None):
        configured=self.commands
        results = []
        for category in categories or ("build", "tests", "lint", "type", "static", "syntax", "requirements"):
            if category=="requirements":
                for requirement in self.run_requirements(): results.extend(requirement.results)
                continue
            commands = configured.get(category, [])
            for command in commands:
                if isinstance(command,dict): results.append(self.run_command(command["command"],command.get("name",""),category))
                else: results.append(self.run_command(command,category=category))
        return results

    def run_requirements(self):
        self.requirement_results=[]
        for item in self.commands.get("requirements",[]):
            requirement_id=item.get("requirement_id","")
            checks=item.get("checks",[])
            results=[self.run_command(check["command"],check.get("name",requirement_id),"requirements") if isinstance(check,dict) else self.run_command(check,requirement_id,"requirements") for check in checks]
            status="PASS" if results and all(result.success for result in results) else "BLOCKED" if not results else "FAIL"
            self.requirement_results.append(RequirementVerification(requirement_id,[r.name for r in results],results,status))
        return self.requirement_results

    def run_red(self, command, expected_markers=(), expected_test_ids=(), expected_failure=None, allowed_files=()):
        result = self.run_command(command, category="task_tests")
        output = result.stdout + result.stderr
        lowered = output.lower()

        # Exit codes 2, 3, 4, 5 from pytest indicate interrupted, internal, usage, or no tests collected
        if result.exit_code in (2, 3, 4, 5):
            result.classification = "INVALID_TEST"
            result.cause = f"Pytest exit code {result.exit_code} indicates internal/usage/collection error"
            result.status = result.classification
            return result

        # Tests skipped or xfailed never count as functional RED
        has_skipped = bool(re.search(r"\b\d+\s+skipped\b", lowered) or "skipped [" in lowered or "=== skipped" in lowered)
        has_xfailed = bool(re.search(r"\b\d+\s+xfailed\b", lowered) or "xfail [" in lowered)
        has_failed = bool(re.search(r"\b\d+\s+failed\b", lowered) or "failed [" in lowered or "failed in " in lowered)

        if (has_skipped or has_xfailed) and not has_failed:
            result.classification = "INVALID_TEST"
            result.cause = "Tests skipped or xfailed cannot serve as functional RED"
            result.status = result.classification
            return result

        # Syntax and collection errors
        if "syntaxerror:" in lowered:
            result.classification = "INVALID_TEST"
            result.cause = "Syntax error in test or imported source"
            result.status = result.classification
            return result

        # ModuleNotFoundError: internal vs external dependency
        mod_match = re.search(r"ModuleNotFoundError:\s*(?:No module named\s*)?['\"]?([^'\"\s\n]+)['\"]?", output)
        if mod_match:
            missing_mod = mod_match.group(1)
            is_internal = self._is_internal_module(missing_mod, allowed_files, expected_failure)
            if is_internal:
                result.classification = "EXPECTED_FAILURE"
                result.cause = f"Discovered task test failed with missing internal module: {missing_mod}"
                result.status = result.classification
                return result
            else:
                result.classification = "INVALID_TEST"
                result.cause = f"Missing external dependency or invalid test import: {missing_mod}"
                result.status = result.classification
                return result

        # ImportError: cannot import name ... from ... and AttributeError
        import_match = re.search(r"ImportError:\s+cannot import name\s+['\"]([^'\"]+)['\"]", output)
        attr_match = re.search(r"AttributeError:\s+(?:module\s+['\"][^'\"]+['\"]\s+has no attribute|type object\s+['\"][^'\"]+['\"]\s+has no attribute|.*has no attribute)\s+['\"]([^'\"]+)['\"]", output)
        missing_symbol = import_match.group(1) if import_match else (attr_match.group(1) if attr_match else None)

        if missing_symbol:
            declared = (
                (expected_failure and missing_symbol in expected_failure)
                or any(missing_symbol.lower() in str(m).lower() for m in expected_markers)
            )
            if declared:
                result.classification = "EXPECTED_FAILURE"
                result.cause = f"Discovered task test failed with missing declared symbol: {missing_symbol}"
                result.status = result.classification
                return result
            else:
                result.classification = "INVALID_TEST"
                result.cause = f"ImportError/AttributeError for undeclared symbol '{missing_symbol}'; must be declared in expected_failure"
                result.status = result.classification
                return result

        discovered = any(token in lowered for token in ("collected ", "ran ", "--- fail", "test result: failed", "failed in ", "passed in "))
        infra_tokens = ("timed out", "permission denied", "connection refused", "no such file or directory")
        invalid_tokens = ("syntaxerror", "modulenotfounderror", "importerror", "no tests ran", "no tests collected", "collected 0 items", "error collecting", "usage error", "pytest: error: unrecognized arguments")
        assertion_tokens = ("assertionerror", "assert ", "assertion failed", "expected:", "not equal", "failed: test", "=== fail", "test result: failed")

        if result.classification in ("INFRASTRUCTURE_FAILURE", "BLOCKED"):
            result.cause = result.stderr or "Command could not run"
        elif any(token in lowered for token in infra_tokens):
            result.classification = "INFRASTRUCTURE_FAILURE"
            result.cause = "Test infrastructure failed"
        elif not discovered or any(token in lowered for token in invalid_tokens):
            result.classification = "INVALID_TEST"
            result.cause = "No valid discovered test execution or test collection/syntax error"
        elif result.success:
            result.classification = "UNEXPECTED_FAILURE"
            result.cause = "RED test passed before implementation"
        elif not any(token in lowered for token in assertion_tokens):
            result.classification = "UNEXPECTED_FAILURE"
            result.cause = "Failure did not show an assertion about missing behavior"
        elif expected_test_ids and not any(test_id in output for test_id in expected_test_ids):
            result.classification = "UNEXPECTED_FAILURE"
            result.cause = "Failure was not linked to a declared task test"
        elif expected_markers and not any(marker.lower() in lowered for marker in expected_markers):
            result.classification = "UNEXPECTED_FAILURE"
            result.cause = "Failure did not match the expected behavior marker"
        else:
            result.classification = "EXPECTED_FAILURE"
            result.cause = "Discovered task test failed with a linked assertion"

        result.status = result.classification
        return result

    def _is_internal_module(self, mod_name: str, allowed_files: Any = (), expected_failure: Any = None) -> bool:
        """Check if a missing module is internal to the project under test."""
        # 1. Check against declared allowed_files
        allowed_list = [str(p).replace("\\", "/") for p in (allowed_files or ())]
        for allowed in allowed_list:
            mod_path = mod_name.replace(".", "/")
            if mod_path in allowed or allowed.endswith(mod_name.split(".")[-1] + ".py"):
                return True

        # 2. Check if declared in expected_failure
        if expected_failure and mod_name in str(expected_failure):
            return True

        # 3. Check workspace top-level packages and python files
        parts = mod_name.split(".")
        root_pkg = parts[0]
        ws_pkg = self.workspace / root_pkg
        src_pkg = self.workspace / "src" / root_pkg
        if ws_pkg.exists() or src_pkg.exists() or (self.workspace / f"{root_pkg}.py").is_file():
            return True

        return False

    def save(self, path, results):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps([asdict(r) for r in results], indent=2))


def run_verify_command(root, config_path="orchestrator.yaml"):
    """Protected verification entrypoint isolated from CLI editing scope."""
    from orchestrator.config.loader import load_config
    root_path = Path(root).resolve()
    cfg_file = Path(config_path) if Path(config_path).is_absolute() else root_path / config_path
    cfg = load_config(cfg_file)
    harness = VerificationHarness(root_path, cfg.verification)
    results = harness.run()
    for r in results:
        print(f"{r.status}: {r.name or r.category or 'command'}: {' '.join(r.command)} ({r.duration:.2f}s)")
    for item in harness.requirement_results:
        print(f"Requirement {item.requirement_id}: {item.status}")
    if not results:
        print("No verification commands configured")
    if not final_verification_pass(results, harness.requirement_results):
        raise SystemExit(1)
    return results
