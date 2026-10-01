"""Protected paths and gate integrity enforcement.

Snapshots protected files before any worker invocation and verifies integrity
immediately after. Any modification, creation, or deletion of protected paths
is blocked before any gate or test runner executes. Protected paths strictly
precede allowed_files.
"""
from fnmatch import fnmatchcase
import hashlib
from pathlib import Path


DEFAULT_PROTECTED_PATTERNS = (
    "**/conftest.py",
    "conftest.py",
    "**/sitecustomize.py",
    "sitecustomize.py",
    "**/usercustomize.py",
    "usercustomize.py",
    "pyproject.toml",
    "**/pyproject.toml",
    "pytest.ini",
    "**/pytest.ini",
    "tox.ini",
    "**/tox.ini",
    "setup.cfg",
    "**/setup.cfg",
    "ruff.toml",
    "**/ruff.toml",
    ".ruff.toml",
    "**/.ruff.toml",
    "mypy.ini",
    "**/mypy.ini",
    ".mypy.ini",
    "**/.mypy.ini",
    ".coveragerc",
    "**/.coveragerc",
    ".git/hooks/**",
    ".git/hooks/*",
    ".git/config",
    ".orchestrator/state/**",
    ".orchestrator/state/*",
    "orchestrator/workflow/quality_gates.py",
    "orchestrator/workflow/postconditions.py",
    "orchestrator/workflow/protection.py",
    "orchestrator/verification/**",
)


class ProtectedPathViolation(RuntimeError):
    """Raised when an untrusted worker modifies, creates, or deletes a protected path."""


def _digest_file(path: Path) -> str | None:
    if not path.is_file() and not path.is_symlink():
        return None
    try:
        if path.is_symlink():
            return "symlink:" + str(path.readlink())
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def is_protected_path(path: str, is_canary: bool = False, custom_patterns: tuple[str, ...] = ()) -> bool:
    """Return True if path matches any protected path pattern.
    
    Protected paths prevail over allowed_files.
    For non-canary features (e.g. real DSP projects), orchestrator/** is also protected.
    """
    clean = path.replace("\\", "/").strip()
    while clean.startswith("./"):
        clean = clean[2:]
    patterns = list(DEFAULT_PROTECTED_PATTERNS) + list(custom_patterns)
    if not is_canary:
        patterns.append("orchestrator/**")

    for pat in patterns:
        if fnmatchcase(clean, pat) or fnmatchcase(Path(clean).name, pat):
            return True
        # Check component-by-component prefix for patterns ending in /**
        if pat.endswith("/**"):
            prefix = pat[:-3].rstrip("/")
            if clean == prefix or clean.startswith(prefix + "/"):
                return True
        elif pat.endswith("/*"):
            prefix = pat[:-2].rstrip("/")
            parent = str(Path(clean).parent).replace("\\", "/")
            if parent == prefix:
                return True
    return False


def snapshot_protected_paths(root: Path | str, is_canary: bool = False, custom_patterns: tuple[str, ...] = ()) -> dict[str, str | None]:
    """Capture sha256 digest of all existing files matching protected path patterns."""
    base = Path(root).resolve()
    snapshot: dict[str, str | None] = {}

    # 1. Check known directories and files
    targets_to_scan = [
        base / ".git" / "hooks",
        base / ".git" / "config",
        base / ".orchestrator" / "state",
        base / "orchestrator" / "workflow",
        base / "orchestrator" / "verification",
        base / "tests",
    ]
    if not is_canary:
        targets_to_scan.append(base / "orchestrator")

    for target in targets_to_scan:
        if target.is_file() or target.is_symlink():
            rel = target.relative_to(base).as_posix()
            if is_protected_path(rel, is_canary=is_canary, custom_patterns=custom_patterns):
                snapshot[rel] = _digest_file(target)
        elif target.is_dir():
            for p in target.rglob("*"):
                if p.is_file() or p.is_symlink():
                    rel = p.relative_to(base).as_posix()
                    if is_protected_path(rel, is_canary=is_canary, custom_patterns=custom_patterns):
                        snapshot[rel] = _digest_file(p)

    # 2. Check root configs and conftest files in entire repository
    for p in base.glob("*"):
        if p.is_file() or p.is_symlink():
            rel = p.relative_to(base).as_posix()
            if is_protected_path(rel, is_canary=is_canary, custom_patterns=custom_patterns):
                snapshot[rel] = _digest_file(p)

    for p in base.rglob("conftest.py"):
        if p.is_file() or p.is_symlink():
            rel = p.relative_to(base).as_posix()
            snapshot[rel] = _digest_file(p)

    for p in base.rglob("*customize.py"):
        if p.is_file() or p.is_symlink():
            rel = p.relative_to(base).as_posix()
            snapshot[rel] = _digest_file(p)

    return snapshot


def verify_protected_paths(
    root: Path | str,
    before: dict[str, str | None],
    is_canary: bool = False,
    custom_patterns: tuple[str, ...] = ()
) -> list[str]:
    """Compare post-worker state against pre-worker snapshot.
    
    Returns a list of violation descriptions. Empty list means integrity verified.
    """
    base = Path(root).resolve()
    after = snapshot_protected_paths(base, is_canary=is_canary, custom_patterns=custom_patterns)
    violations: list[str] = []

    # Check for modifications or deletions of files that existed before
    for path, old_digest in before.items():
        new_digest = after.get(path)
        if old_digest is not None and new_digest is None:
            violations.append(f"DELETED: protected path {path} was deleted")
        elif old_digest != new_digest:
            violations.append(f"MODIFIED: protected path {path} was modified")

    # Check for creation of new protected files
    for path, new_digest in after.items():
        if path not in before and new_digest is not None:
            violations.append(f"CREATED: protected path {path} was created")

    return violations
