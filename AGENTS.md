# AGENTS.md — Regras de Operação dos Agentes Externos com o SDD Orchestrator (Python)

> **Princípio Central de Controle:**
> **O Python é o único Orquestrador do sistema** (`python -m orchestrator`). 
> O Python gerencia o fluxo de trabalho, máquinas de estado (SDD e TDD), persistência em SQLite, proteção de testes (`TEST_TAMPERING`), contenção de escopo (`SCOPE_VIOLATION`) e verificação determinística (testes, linters, tipagem).
>
> **Antigravity (`agy`), OpenCode (`opencode`), Codex (`codex`) e Claude Code (`claude`) são PROVEDORES DE AGENTES EXTERNOS (Workers)**.
> Eles são invocados pelo Python para produzir ou inspecionar artefatos usando as skills do SpecKit. Nenhum modelo ou agente externo pode sobrepor um portão determinístico do Python.

---

## 1. Configuração e Stack do Projeto

- **Orquestrador Central**: Python 3.10+ (`orchestrator/`)
- **Test Runner (RED / GREEN)**: `python -m pytest -q`
- **Linter & Formatador**: `python -m ruff check orchestrator tests`
- **Verificação de Tipos**: `python -m mypy`
- **Sintaxe**: `python -m compileall -q orchestrator tests`
- **Diretório de Especificação do SpecKit**: `specs/<feature>/`
- **Memória & Constituição**: `.specify/memory/constitution.md`
- **Pasta de Logs**: `logs/` (temporários, fora do commit)

---

## 2. Regras Comuns (Para Todos os Agentes e Provedores)

1. **Supremacia da Constituição**: Antes de qualquer trabalho, consulte `.specify/memory/constitution.md`. Os princípios nela definidos prevalecem sobre qualquer instrução subsequente.
2. **Fonte da Verdade**: A documentação viva reside em `specs/<feature>/`:
   - `spec.md` (Requisitos funcionais estáveis `FR-xxx` e critérios de aceitação `AC-xxx`).
   - `plan.md` (Decisões técnicas e arquiteturais).
   - `tasks.md` (Tarefas com contrato JSON, dependências e `allowed_files`).
   - `checklists/` (Critérios de qualidade e verificação).
3. **Escopo Restrito por Tarefa (`T###`)**: Trabalhe estritamente na tarefa indicada e modifique apenas arquivos listados no escopo (`allowed_files`). O Python bloqueia violações como `SCOPE_VIOLATION`.
4. **Proteção Anti-Adulteração de Testes (`TEST_TAMPERING`)**: Durante as fases GREEN e REFACTOR, **nenhum agente pode modificar, enfraquecer asserts ou pular (`skip`) arquivos de teste criados na fase RED**. Alterações em testes requerem um novo ciclo formal de RED autorizado pelo orquestrador.
5. **Comandos Seguros**: Nunca execute comandos destrutivos (`rm -rf`, `git reset --hard`, `git push --force`). O orquestrador bloqueia chamadas inseguras via `CommandPolicy`.
6. **Concisão e Evidências**: Termine sempre com um resumo objetivo de até 8 a 10 linhas: arquivos criados/alterados, testes executados e status final.

---

## 3. Matriz de Papéis e Provedores de Agentes

O orquestrador Python roteia dinamicamente os papéis para os provedores configurados:

| Papel no Orquestrador | Função | Provedores Suportados | Como o Python Aciona |
| :--- | :--- | :--- | :--- |
| **constitution / specification** | Cria constituição e spec.md via SpecKit | `agy`, `codex`, `opencode`, `claude` | Invocação da skill `speckit-specify` / prompt |
| **validators (spec, plan, tasks)** | Validação independente do artefato | Provedor diferente do autor (`prefer_different_provider_from_author`) | Prompt de validação estrito (`PASS`, `REVISE`, `BLOCKED`) |
| **planning** | Desenvolve plano técnico e decisões | `codex`, `agy`, `opencode`, `claude` | Invocação da skill `speckit-plan` |
| **tasks** | Decompõe tarefas com contrato JSON | `agy`, `codex`, `opencode` | Invocação da skill `speckit-tasks` com contrato JSON |
| **consistency_agent** | Auditoria cruzada de coerência (`speckit-analyze`) (legado: `cross_artifact_validator`) | `codex`, `agy`, `opencode` | Invocação da skill `speckit-analyze` |
| **test_designer (RED)** | Escreve testes no `tests/` que falham | `opencode` (`tester`), `codex`, `agy` | `opencode run --agent tester` ou CLI dedicada |
| **coder (GREEN)** | Implementação mínima para passar o teste | `opencode` (`implementer`), `codex`, `agy` | `opencode run --agent implementer` ou CLI dedicada |
| **refactorer (REFACTOR)** | Refatora mantendo testes verdes | `opencode` (`refactorer`), `codex` | `opencode run --agent refactorer` |
| **debugger** | Diagnóstico de falha de teste | `agy` (Flash), `codex` | Subagente `speckit-debugger` com logs |
| **code_reviewer / final_reviewer** | Revisão de código e evidências | Provedor independente | Validação estruturada pós-verificação determinística |

---

## 4. Ciclo SpecKit Orquestrado pelo Python

O Python conduz a execução das fases do SpecKit sequencialmente:

```
┌─────────────────┐      ┌───────────────┐      ┌────────────┐      ┌─────────────┐
│ 1. CONSTITUTION │ ──►  │ 2. SPECIFY    │ ──►  │ 3. PLAN    │ ──►  │ 4. TASKS    │
│ (Aprovação)     │      │ (spec.md)     │      │ (plan.md)  │      │ (tasks.md)  │
└─────────────────┘      └───────────────┘      └────────────┘      └─────────────┘
                                                                           │
                                                                           ▼
                                                                    ┌─────────────┐
                                                                    │ 5. ANALYSIS │
                                                                    │ (speckit-   │
                                                                    │  analyze)   │
                                                                    └─────────────┘
                                                                           │
                                                                           ▼
                                                                    ┌─────────────┐
                                                                    │ 6. TDD LOOP │
                                                                    │ (por task)  │
                                                                    └─────────────┘
```

1. **Constitution**: O Python invoca o agente para `.specify/memory/constitution.md`. Exige aprovação humana antes de prosseguir.
2. **Specification**: O agente executa com a skill `speckit-specify` para produzir `specs/<feature>/spec.md`. Um validador independente valida.
3. **Clarify (se necessário)**: Resolve pontos ambíguos `[NEEDS CLARIFICATION]`.
4. **Plan**: O agente executa com a skill `speckit-plan` para produzir `specs/<feature>/plan.md`. Um validador independente valida.
5. **Tasks**: O agente executa com a skill `speckit-tasks` gerando `tasks.md` com o bloco JSON de tarefas consumível pelo orquestrador.
6. **Analysis (speckit-analyze)**: O agente `consistency_agent` (antigo `cross_artifact_validator`) executa a skill `speckit-analyze` para conferir a consistência e rastreabilidade total antes do código.

---

## 5. Ciclo de Implementação TDD (Controlado pelo Python)

Para cada tarefa `T###` de `tasks.md`:

```
┌─────────┐      ┌─────────────┐      ┌───────────┐      ┌──────────────┐      ┌────────┐
│ 1. RED  │ ──►  │ 2. PYTHON   │ ──►  │ 3. GREEN  │ ──►  │ 4. REFACTOR  │ ──►  │ 5. END │
│ (Agent) │      │ CONFIRMA RED│      │  (Agent)  │      │ (Regressão)  │      │ (Done) │
└─────────┘      └─────────────┘      └───────────┘      └──────────────┘      └────────┘
```

1. **RED**: Python aciona o agente de testes (ex: `opencode run --agent tester`). O agente deve criar testes em `tests/` e retornar o `TestDesign`.
2. **Confirmação RED pelo Python**: O Python roda o comando de teste específico. O resultado **deve ser falha esperada (`EXPECTED_FAILURE`)**. Se passar ou der erro de sintaxe/import, o Python rejeita e bloqueia o avanço.
3. **GREEN**: Python captura os hashes dos testes (proteção SHA-256) e aciona o agente implementador (ex: `opencode run --agent implementer`). O implementador só pode alterar arquivos de `allowed_files`.
4. **Verificação GREEN pelo Python**: O Python roda o teste da tarefa e os testes de regressão. Qualquer alteração em arquivo de teste é bloqueada como `TEST_TAMPERING`.
5. **REFACTOR**: Python aciona o refatorador e em seguida executa toda a suíte de regressão.
6. **Revisão e Checkpoint**: Validador independente revisa o diff. O Python grava o checkpoint no SQLite com a impressão digital do workspace (`WorkspaceFingerprint`).
