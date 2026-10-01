# Briefing Arquitetural: Integração das Skills do SpecKit no SDD Orchestrator

Este documento consolida o estado atual do repositório, o objetivo da reformulação arquitetural, os desafios técnicos identificados e as instruções para que o ChatGPT formule o plano, o prompt de execução e a escolha do modelo ideal.

---

## 1. Contexto do Repositório Atual (`agents_emg`)

O projeto é um **Orquestrador de Spec-Driven Development (SDD) e Test-Driven Development (TDD)** escrito em Python.

### Princípio Arquitetural Fundamental
> *O Python orquestra CLIs de agentes externos (`agy`, `codex`, `opencode`). Os agentes produzem e inspecionam artefatos semânticos; o Python detém o controle estrito de transições de estado, retentativas, persistência, políticas de comando e verificação determinística. Nenhuma resposta de modelo de linguagem pode sobrepor um teste, build, linter ou verificação estática com falha.*

### Fluxo de Trabalho (Workflow Pipeline)
```
1. Feature Request (Entrada em linguagem natural)
2. Constitution (constitution.md) ──► Constitution Validator
3. Specification (spec.md) ─────────► Specification Validator
4. Plan (plan.md) ──────────────────► Plan Validator
5. Tasks (tasks.md) ────────────────► Tasks Validator
6. Cross-Artifact Validation (Validação de coerência mútua entre todos os artefatos)
7. TDD Task Driver (Ciclo estrito por tarefa):
   a. Analyze (TestDesigner analisa requisitos sem alterar código)
   b. RED (TestDesigner cria testes; Python valida falha semântica EXPECTED_FAILURE)
   c. GREEN (Coder implementa código com hashes dos testes protegidos contra alteração)
   d. REFACTOR (Refactorer otimiza; Python executa regressão completa)
   e. Code Review (Validador independente avalia evidências)
8. Verificação Determinística Final (Harness executando Pytest, Ruff, Mypy, Compileall e checagens de requisitos)
9. Final Reviewer (Revisão final pós-verificação determinística)
```

### Componentes Principais do Código
- `orchestrator/workflow/driver.py`: Driver sequencial do SDD e orquestração das tarefas.
- `orchestrator/workflow/transitions.py` & `orchestrator/tdd.py`: Máquina de estados formal de TDD (`ANALYZE`, `RED_GENERATE`, `RED_VERIFY`, `GREEN_IMPLEMENT`, `GREEN_VERIFY`, `REFACTOR`, `REGRESSION_VERIFY`, `REVIEW`, `COMPLETE`, `BLOCKED`) com proteção anti-adulteração de testes (`TEST_TAMPERING`) via hashes SHA-256.
- `orchestrator/agents/roles.py`: Catálogo de 15 papéis especializados com capacidades necessárias e diretriz de independência (`prefer_different_provider_from_author=True`).
- `orchestrator/agents/router.py` & `orchestrator/agents/cost.py`: Roteamento empírico por capacidades, histórico estatístico e escada de custos (Luna → Sol → Sonnet → Astra).
- `orchestrator/agents/runner.py`: Dispara as CLIs externas, faz snapshots do workspace antes e depois das chamadas e bloqueia alterações fora de escopo (`SCOPE_VIOLATION`) ou escritas incompletas (`PARTIAL_WRITE`).
- `orchestrator/tdd_contract.py`: Contrato formal de testes emitido pelo `test_designer` (`task_id`, `requirement_ids`, `acceptance_criteria_ids`, `created_tests`, `test_commands`).
- `orchestrator/traceability.py`: Rastreabilidade persistente em SQLite ligando Requisito (`FR-xxx`) ↔ Critério de Aceitação (`AC-xxx`) ↔ Decisão de Plano ↔ Tarefas (`Txxx`) ↔ IDs de Testes ↔ Arquivos de Produção ↔ Resultados dos Comandos.
- `orchestrator/prompts/*.md`: Templates rudimentares de prompt atualmente lidos pelo Python (ex: `specification.md`, `plan.md`, `tasks.md`, `coder.md`, etc.).

---

## 2. A Arquitetura de 3 Camadas: Python ──► specify ──► Agent

A arquitetura oficial que estamos implementando segue estritamente três camadas bem definidas:

```
┌────────────────────────────────────────────────────────┐
│               1. PYTHON (ORQUESTRADOR)                 │
│  - State Machine e Driver do Workflow                  │
│  - Roteador Empírico e Avaliador de Custos             │
│  - Escolhe dinamicamente o MELHOR agente para a etapa  │
│  - Garante portões de verificação determinísticos      │
└───────────────────────────┬────────────────────────────┘
                            │ Aciona a etapa via
                            ▼
┌────────────────────────────────────────────────────────┐
│                  2. SPECIFY (SPECKIT)                  │
│  - Catálogo de Skills metodológicas de SDD             │
│  - Infraestrutura (.specify/, templates e checklists)  │
│  - Define o procedimento exato da tarefa               │
└───────────────────────────┬────────────────────────────┘
                            │ Executa a skill através do
                            ▼
┌────────────────────────────────────────────────────────┐
│           3. AGENT (WORKER SELECIONADO)                │
│       (agy, opencode, codex ou futuro claude)          │
│  - Executa a skill correspondente com o prompt/contexto│
│  - Exemplo: na tarefa de constitution, o agente        │
│    selecionado executará speckit-constitution "prompt" │
└────────────────────────────────────────────────────────┘
```

### Como Funciona na Prática:
1. **O Python comanda o ciclo**: Ao entrar na fase de `constitution`, `specification`, `plan` ou `tasks`, o Python consulta o `ModelRouter` para selecionar o provedor/modelo mais qualificado e com melhor custo-benefício (garantindo também a regra de independência quando for fase de validação).
2. **O Python delega usando o SpecKit**: Em vez de injetar prompts estáticos manuais, o Python invoca o agente selecionado instruindo-o a executar a skill correspondente do SpecKit:
   - Fase `constitution` ➔ Agente selecionado roda `speckit-constitution "<prompt>"`
   - Fase `specification` ➔ Agente selecionado roda `speckit-specify "<prompt>"`
   - Fase `clarify` ➔ Agente selecionado roda `speckit-clarify`
   - Fase `plan` ➔ Agente selecionado roda `speckit-plan`
   - Fase `tasks` ➔ Agente selecionado roda `speckit-tasks`
   - Fase `analyze` ➔ Validador independente roda `speckit-analyze`
3. **O Python recebe os artefatos e valida**: O Python intercepta os artefatos gerados pelo SpecKit (em `specs/<feature>/` ou `.specify/`), valida contratos determinísticos e submete ao validador independente antes de avançar para a próxima fase.

---

## 3. Análise de Complexidade e Atritos Técnicos

A complexidade da reformulação foi avaliada como **Média (Moderada)**, pois a base do orquestrador já é altamente modular. No entanto, há 5 pontos de atrito que precisam ser resolvidos no design arquitetural:

### 1. Suporte a Skills entre Provedores Heterogêneos
- **AGY CLI**: Suporta nativamente skills em pastas como `.agents/skills/` ou parâmetros dedicados.
- **OpenCode CLI**: Opera prioritariamente com comandos e agentes (`.opencode/commands/` ou flag `--agent`).
- **Codex CLI**: Executa prioritariamente via `codex exec [prompt]`.
- **Desafio**: O orquestrador precisa de um mecanismo unificado de **resolução e fallback de skills**:
  - Se a CLI do provedor suportar a skill nativamente, delega para a CLI.
  - Se a CLI não suportar nativamente (ou for `codex`), o Python deve carregar o conteúdo do `SKILL.md` e injetá-lo no contexto do prompt do agente.

### 2. Contrato de Dados de `tasks.md` (Ponto Crítico)
- O SpecKit padrão gera tarefas formatadas em Markdown livre para consumo humano/agente (`- [ ] T001: Implementar...`).
- O motor de TDD do orquestrador (`orchestrator/workflow/driver.py` e `orchestrator/tdd.py`) depende estritamente de um bloco JSON com schema bem definido:
  ```json
  {
    "tasks": [
      {
        "id": "T001",
        "requirements": ["FR-001"],
        "acceptance_criteria": ["AC-001-01"],
        "plan_decisions": ["D001"],
        "dependencies": [],
        "test_type": "UNIT",
        "allowed_files": ["src/service.py"]
      }
    ]
  }
  ```
- **Desafio**: O `speckit-tasks` precisa ser adaptado (ou complementado por instrução injetada) para obrigatoriamente produzir esse bloco JSON estruturado, garantindo que o ciclo TDD e a proteção de escopo de arquivos (`allowed_files`) continuem funcionando.

### 3. Estrutura de Diretórios e Escopo de Arquivos
- O SpecKit opera convencionalmente com `specs/<NNN-feature-name>/` e `.specify/`.
- O orquestrador atualmente centraliza os artefatos em `.orchestrator/runs/<workflow-id>/`.
- O modo estrito do `AgentRunner` bloqueia qualquer escrita fora de arquivos autorizados como `SCOPE_VIOLATION`.
- **Desafio**: Padronizar onde os artefatos viverão (se dentro de `specs/` ou no diretório de run do orquestrador) e atualizar a lista de permissões (`file_scopes` no `orchestrator.yaml`).

### 4. Preservação da Validação Independente (Autor vs. Validador)
- No SpecKit padrão, o agente autor frequentemente realiza "auto-validação" gerando checklists como `checklists/requirements.md`.
- No SDD Orchestrator, **a validação por modelo independente é obrigatória**: um artefato gerado pelo provedor X deve ser avaliado por um provedor/modelo Y, retornando estritamente `PASS`, `REVISE` ou `BLOCKED`.
- **Desafio**: A skill SpecKit deve instruir o **Agente Autor**, e o artefato resultante (junto com os checklists) deve ser submetido ao **Agente Validador Independente**, que mantém a autoridade do portão.

### 5. Interatividade vs. Modo Headless
- O SpecKit gera perguntas de clarificação com tabelas `[NEEDS CLARIFICATION]` esperando interação do usuário.
- O orquestrador precisa de uma política clara: em execuções interativas (`--interactive`), essas perguntas devem passar pelo `InteractiveGate`; em execuções headless ou automatizadas, o agente deve assumir premissas razoáveis (*reasonable defaults*) sem travar o subprocesso.

---

## 4. O Que Solicitamos ao ChatGPT

Com base em todo o contexto e análise técnica acima, solicite ao ChatGPT duas entregas principais:

### Entrega 1: Recomendação e Escolha do Modelo de IA Ideal
Indicar e justificar qual modelo de IA (ex: Claude 3.7 Sonnet com Extended Thinking, Claude 3.5 Sonnet, GPT-4.5 / o3, etc.) deve ser selecionado para executar essa reformulação técnica no código.
- Critérios de avaliação: capacidade de raciocínio arquitetural em Python, manutenção de contratos e schemas de dados, compreensão de CLI tooling de agentes, e rigor para não quebrar a suíte de testes existente.

### Entrega 2: Elaboração do Prompt de Execução para a Reformulação
Criar um **Prompt Completo, Estruturado e Pronto para Uso** que será entregue ao modelo de IA executor selecionado para realizar as modificações no repositório.

O prompt deve instruir o modelo a:
1. **Adicionar Suporte a Skills nos Papéis (`orchestrator/agents/roles.py`)**:
   - Incluir mapeamento de skills (ex: `skill_name: "speckit-specify"`, `speckit-plan`, `speckit-tasks`, etc.) na definição dos papéis.
2. **Implementar Carregador e Fallback de Skills (`orchestrator/agents/runner.py` e `providers/`)**:
   - Resolver caminhos das skills (`.agents/skills/<skill_name>/SKILL.md`).
   - Passar nativamente para CLIs compatíveis ou injetar o conteúdo de forma transparente quando o provedor não possuir suporte nativo a skills.
3. **Harmonizar o `speckit-tasks` com o Contrato TDD**:
   - Garantir que a geração de tarefas contenha o JSON com `id`, `requirements`, `acceptance_criteria`, `allowed_files` e `dependencies`.
4. **Adequar Escopo de Arquivos e Workflow Driver (`orchestrator/workflow/driver.py` e `orchestrator.yaml`)**:
   - Ajustar caminhos de artefatos gerados pelo SpecKit e atualizar permissões de escopo (`file_scopes`).
5. **Preservar a Validação Independente e Verificação Determinística**:
   - Manter os portões de validação com agentes independentes e o harness determinístico intocados.
6. **Validar a Refatoração**:
   - Rodar a suíte de testes unitários (`pytest -q`) garantindo 100% de compatibilidade e ausência de regressões.
