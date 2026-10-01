Independently review the implementation and verification evidence for task $task. Deterministic failing checks always block PASS.
Do not attempt to read files via shell or execute any commands.
Analyze only the provided artifacts and context below.
Return strict JSON with the canonical validation contract:
{"status":"PASS|REVISE|BLOCKED","summary":"...","issues":[]}

- "status": exactly one of "PASS", "REVISE", "BLOCKED".
- "summary": concise explanation of the verdict.
- "issues": list of strings describing any issues found ([] if PASS).

EVIDENCE / ARTIFACT:
$artifact

