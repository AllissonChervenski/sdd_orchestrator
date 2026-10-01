# Refatoração do Orquestrador para SpecKit Dev

Você está refatorando o repositório atual `agents_emg`.

O objetivo é **SIMPLIFICAR** a arquitetura existente.

## PRINCÍPIO FUNDAMENTAL

O Python **NÃO implementa a metodologia SDD**.

O SpecKit Dev é a fonte de verdade para:

- constitution
- specify
- clarify
- checklist
- plan
- tasks
- analyze
- implement
- converge

e quaisquer outras skills SpecKit instaladas no projeto.

O Python atua exclusivamente como **CONTROL PLANE / AGENT HARNESS**:

- controla a ordem das etapas;
- escolhe role/provider/model;
- despacha a skill correta para o agente;
- persiste estado;
- controla retries;
- controla resume;
- verifica workspace;
- aplica file scopes;
- mantém independência entre agentes;
- coleta métricas;
- executa gates determinísticos.

Não reimplemente em Python o comportamento interno de uma skill SpecKit.

## PRIMEIRA REGRA

Antes de modificar código, inspecione a instalação **REAL** do SpecKit neste repositório.

Descubra:

- diretórios;
- skills instaladas;
- `SKILL.md`;
- convenções;
- comandos;
- arquivos `.specify/`;
- estrutura `specs/`;
- integração disponível para AGY;
- integração disponível para OpenCode;
- integração disponível para Codex.

Não invente paths, flags ou sintaxe.

A instalação existente é a fonte de verdade.

---

# ARQUITETURA ALVO

A arquitetura desejada é:

```text
WorkflowEngine
    ↓
StageRegistry
    ↓
ModelRouter existente
    ↓
SkillDispatcher
    ↓
ProviderAdapter
    ├── AGY
    ├── Codex
    └── OpenCode
    ↓
SpecKit Skill
    ↓
Artifacts
    ↓
StateStore / Verification
```

O WorkflowEngine deve conhecer:

- qual etapa vem depois;
- qual role executa;
- qual skill deve ser despachada;
- pré-condições;
- retry/resume;
- expected artifacts.

O WorkflowEngine **NÃO** deve conhecer as instruções metodológicas internas da skill.

---

# TASK 1 — STAGES E ROLES

Refatore `orchestrator/agents/roles.py` e componentes relacionados.

Adicione associação declarativa entre role e SpecKit skill.

Exemplos conceituais:

```text
constitution_agent → speckit-constitution
specification_agent → speckit-specify
clarifier_agent → speckit-clarify
requirements_reviewer → speckit-checklist
architect_agent → speckit-plan
task_agent → speckit-tasks
consistency_agent → speckit-analyze
implementation_agent → speckit-implement
convergence_agent → speckit-converge
```

Os nomes reais devem ser obtidos da instalação real.

Crie `StageRegistry` declarativo.

Campos possíveis:

- `name`
- `skill_name`
- `role`
- `optional`
- `once_per_project`
- `expected_artifacts`
- `prerequisites`
- `mutability`
- `retry_policy`

Não copie conteúdo de `SKILL.md` para o registry.

Constitution deve ser `once_per_project`.

Adicione testes.

Pare e execute a suíte antes da próxima task.

---

# TASK 2 — SKILL RESOLVER E SKILL DISPATCHER

Implemente um `SkillResolver`.

Responsabilidades:

1. localizar a skill instalada;
2. validar sua existência;
3. localizar seu `SKILL.md`;
4. identificar capabilities necessárias;
5. decidir como ela deve ser entregue ao provider.

Implemente um `SkillDispatcher` com interface conceitual:

```python
run_skill(
    role,
    provider,
    model,
    skill_name,
    arguments,
    cwd,
    execution_policy,
)
```

O `SkillDispatcher` **NÃO** deve conhecer detalhes internos de cada provider.

Delegue isso aos `ProviderAdapters`.

## Estratégia de execução

Quando o provider possuir suporte nativo à skill:

- usar o mecanismo nativo real.

Quando não possuir:

- carregar o `SKILL.md` da instalação local;
- fornecer seu conteúdo/contexto ao agente de forma controlada.

A fonte deve permanecer sendo o `SKILL.md` instalado.

Não mantenha cópias divergentes dos conteúdos das skills dentro de `orchestrator/prompts/`.

## Segurança

Preserve:

- workspace snapshots;
- file scope;
- `SCOPE_VIOLATION`;
- `PARTIAL_WRITE`;
- timeouts;
- command policy;
- telemetria.

Não introduza `shell=True` desnecessariamente.

## Providers

Investigue e implemente corretamente para:

- AGY;
- Codex;
- OpenCode.

Não assuma que os três usam a mesma sintaxe de skill.

Adicione testes usando mocks.

Não faça chamadas LLM reais nos testes.

Execute toda a suíte antes da próxima task.

---

# TASK 3 — SPECKIT TASKS + TDD

Este é um ponto crítico.

O SpecKit continua responsável por produzir `tasks.md`.

Não crie um segundo sistema de tasks.

Entretanto, nosso TDD engine necessita informações estruturadas.

A solução deve preservar o formato/convenções oficiais do SpecKit e adicionar uma representação estruturada compatível com o harness.

Precisamos preservar pelo menos:

- task id;
- requirements;
- acceptance criteria;
- plan decisions;
- dependencies;
- test type;
- allowed files.

Investigue primeiro se o SpecKit possui mecanismo oficial de:

- customization;
- template;
- extension;
- structured metadata;
- machine-readable output.

Prefira mecanismo oficial.

Não faça fork desnecessário da skill upstream.

## TDD

Para mudança comportamental, a decomposição deve representar:

```text
RED
→ GREEN
→ REFACTOR
```

Exemplo conceitual:

```text
T010 [RED]
Criar teste demonstrando comportamento ausente.

T011 [GREEN] depends_on=T010
Implementar o mínimo para o teste passar.

T012 [REFACTOR] depends_on=T011
Refatorar preservando comportamento.
```

Não quebre a compatibilidade do formato SpecKit.

Se necessário, implemente um adapter/parser separado:

```text
SpecKit tasks.md
→ TaskContract
```

e **NÃO** altere o artefato original.

## Gates determinísticos

### RED
- teste descoberto;
- teste executado;
- falha esperada;
- não aceitar erro de sintaxe/import como RED válido.

### GREEN
- teste correspondente passa;
- test hashes não podem ser adulterados.

### REFACTOR
- regressão continua verde.

Preserve `TEST_TAMPERING`.

Adicione testes extensivos para o adapter e os ciclos RED/GREEN/REFACTOR.

Execute toda a suíte antes da próxima task.

---

# TASK 4 — ARTEFATOS E FILE SCOPES

Adote a estrutura oficial do SpecKit como fonte principal dos artefatos:

```text
.specify/
specs/<feature>/
```

Não duplique artefatos SDD em `.orchestrator/runs/`.

`.orchestrator/` deve guardar apenas dados operacionais:

- state;
- logs;
- metrics;
- checkpoints;
- reports;
- snapshots.

Atualize:

- file scopes;
- workspace fingerprint;
- artifact discovery;
- traceability;
- resume;
- expected artifacts.

O `AgentRunner` deve permitir que cada skill escreva somente nos paths que a própria etapa legitimamente necessita.

Continue bloqueando alterações fora de escopo.

Preserve compatibilidade com workflows existentes quando razoável.

Adicione testes.

---

# TASK 5 — QUALITY GATES E INTERAÇÃO

Não duplique funcionalidades do SpecKit.

Use:

- `speckit-clarify` para clarificações;
- `speckit-checklist` para checklist de requisitos;
- `speckit-analyze` para análise cruzada dos artefatos;
- `speckit-converge` para convergência semântica após implementação.

Remova ou desative componentes próprios apenas quando comprovadamente duplicarem essas funções.

## Independência

Preserve a política existente de independência entre autor e validador quando houver um gate externo útil.

Não force um validator extra quando a única coisa que ele faria seria repetir literalmente `speckit-analyze`.

Quando existir validator independente:

```text
author provider/model != validator provider/model
```

quando houver alternativa compatível.

## Clarify

### Modo interactive
Perguntas reais `[NEEDS CLARIFICATION]` devem chegar ao `InteractiveGate` e ao usuário.

### Modo headless
Não deixe subprocesso travado aguardando stdin.

A política de fallback deve ser configurável.

Não invente silenciosamente decisões críticas.

Adicione testes.

---

# TASK 6 — IMPLEMENT / CONVERGE / LIMPEZA

Integre corretamente:

```text
speckit-implement
→ speckit-converge
```

Converge deve poder gerar trabalho restante.

Suporte loop limitado:

```text
IMPLEMENT
→ CONVERGE

se gaps:
→ IMPLEMENT
→ CONVERGE

até:

CONVERGED

ou:

max_convergence_iterations
```

Não permita loop infinito.

Investigue a interação entre `speckit-implement` e nosso TDD granular.

Não permita duas entidades implementarem a mesma task.

Escolha uma única autoridade de execução e documente a decisão.

Preserve o `VerificationHarness` separado:

- pytest;
- Ruff;
- mypy;
- syntax/build;
- outros checks configurados.

```text
SpecKit Converge
= verificação semântica

VerificationHarness
= verificação determinística
```

Não permita que resultado de modelo transforme um gate determinístico `FAIL` em `PASS`.

## Limpeza

Depois de comprovar paridade:

- remova ou deprecie prompts internos que duplicavam skills SpecKit;
- não remova prompts específicos do harness que ainda possuam função própria;
- atualize README e diagramas.

---

# MODELOS E ROTEAMENTO

**NÃO** altere a política existente do `ModelRouter` sem necessidade.

Preserve:

- Luna-first;
- Sol mediante necessidade;
- Sonnet como alternativa robusta;
- Astra somente em casos extremos;
- Opus fora da rota normal;
- OpenCode econômico elegível para coding;
- `CostAwareRouter`;
- Ponytail;
- Caveman.

Essa refatoração não deve transformar modelos fortes em padrão.

---

# MIGRAÇÃO

Faça alterações incrementalmente.

Após cada TASK:

1. execute testes específicos;
2. execute `pytest -q`;
3. execute Ruff;
4. execute mypy;
5. corrija regressões antes de avançar.

Não faça uma refatoração massiva sem checkpoints.

---

# CRITÉRIO FINAL

Ao final deve ser verdade que:

- Python não contém implementação própria do SDD;
- SpecKit é fonte de verdade metodológica;
- cada stage aponta para uma skill real;
- SkillDispatcher funciona nos providers suportados;
- fallback de `SKILL.md` funciona;
- tasks do SpecKit alimentam o contrato TDD;
- RED/GREEN/REFACTOR continuam determinísticos;
- artefatos vivem nos locais corretos do SpecKit;
- independência relevante foi preservada;
- clarify não trava headless;
- analyze substitui validação transversal redundante;
- implement/converge funcionam sem duplicação;
- resume continua válido;
- file scopes continuam seguros;
- VerificationHarness continua soberano;
- testes existentes e novos passam.

Ao terminar, apresente:

- arquivos modificados;
- componentes removidos/deprecados;
- arquitetura resultante;
- decisões de compatibilidade;
- limitações conhecidas;
- saída resumida de pytest/Ruff/mypy.

Não execute uma feature real durante esta refatoração.
