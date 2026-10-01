import shlex
import re

DENIED = [re.compile(r"^rm\s+-rf\s+(/|~|\$HOME)(?:\s|$)"), re.compile(r"^git\s+reset\s+--hard(?:\s|$)"), re.compile(r"^git\s+clean\s+-fdx(?:\s|$)"), re.compile(r"^(shutdown|reboot|mkfs|dd)(?:\s|$)")]


class CommandPolicy:
    def __init__(self, workspace): self.workspace = __import__("pathlib").Path(workspace).resolve()
    def validate(self, command: str | list[str]):
        parts = shlex.split(command) if isinstance(command, str) else list(command)
        normalized = " ".join(parts)
        if any(p.search(normalized) for p in DENIED): return False, "Blocked dangerous command"
        for token in parts:
            if token.startswith("/"):
                path = __import__("pathlib").Path(token)
                try: path.resolve().relative_to(self.workspace)
                except ValueError: return False, f"Path outside workspace: {token}"
        return True, "allowed"
