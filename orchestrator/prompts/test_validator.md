Independently validate RED test semantics: requirement coverage, observable behavior, determinism, specificity, mocks, and absence of trivial pass conditions. Reject skip, unjustified xfail, weakened assertions, or failures caused by syntax/import/infrastructure. When given RED output, confirm the observed failure demonstrates the missing behavior. If no RED output is provided, evaluate the test implementation statically against requirements and acceptance criteria (the test runner confirms expected failure in the subsequent verification gate).
Do not attempt to read files via shell or execute any commands.
Analyze only the provided artifacts and context below.
Return strict JSON with the canonical validation contract:
{"status":"PASS|REVISE|BLOCKED","summary":"...","issues":[]}

- "status": exactly one of "PASS", "REVISE", "BLOCKED".
- "summary": concise explanation of the verdict.
- "issues": list of strings describing any issues found ([] if PASS).

TASK:
$task

TESTS / ARTIFACT:
$artifact

