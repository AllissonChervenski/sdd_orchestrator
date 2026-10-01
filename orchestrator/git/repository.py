import subprocess
from pathlib import Path


class GitRepository:
    def __init__(self, cwd): self.cwd=Path(cwd).resolve()
    def available(self):
        try: return subprocess.run(["git","rev-parse","--is-inside-work-tree"],cwd=self.cwd,stdin=subprocess.DEVNULL,capture_output=True,text=True).returncode == 0
        except OSError: return False
    def dirty(self):
        if not self.available(): return False
        return bool(subprocess.run(["git","status","--porcelain"],cwd=self.cwd,stdin=subprocess.DEVNULL,capture_output=True,text=True).stdout.strip())
    def checkpoint(self, message):
        if not self.available() or self.dirty(): return False
        # Empty checkpoint commits are deliberately avoided; task changes must be reviewed first.
        return False

    def branch(self):
        if not self.available(): return None
        result=subprocess.run(["git","branch","--show-current"],cwd=self.cwd,stdin=subprocess.DEVNULL,capture_output=True,text=True,check=False)
        return result.stdout.strip() if result.returncode==0 else None

    def head(self):
        if not self.available(): return None
        result=subprocess.run(["git","rev-parse","HEAD"],cwd=self.cwd,stdin=subprocess.DEVNULL,capture_output=True,text=True,check=False)
        return result.stdout.strip() if result.returncode==0 else None

    def create_worktree(self, destination, branch):
        if not self.available(): raise RuntimeError("Git repository required for worktree")
        if self.dirty(): raise RuntimeError("Cannot create worktree from a dirty workspace")
        target=Path(destination).resolve()
        if target.exists(): raise RuntimeError(f"Worktree path already exists: {target}")
        target.parent.mkdir(parents=True,exist_ok=True)
        result=subprocess.run(["git","worktree","add","-b",branch,str(target),"HEAD"],cwd=self.cwd,stdin=subprocess.DEVNULL,capture_output=True,text=True,check=False)
        if result.returncode: raise RuntimeError(f"Worktree creation failed: {result.stderr.strip()}")
        return target
