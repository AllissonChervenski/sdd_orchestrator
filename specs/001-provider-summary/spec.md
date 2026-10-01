# Feature Specification: Comando `provider-summary`

**Feature Branch**: `001-provider-summary`  
**Created**: 2026-09-29  
**Status**: Ready for Planning  
**Input**: Adicionar um novo comando CLI somente de leitura: `python -m orchestrator provider-summary`. O comando deve apresentar um resumo dos providers atualmente conhecidos pelo orquestrador, reutilizando exclusivamente os dados e abstrações já existentes no projeto.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Resumo Textual dos Providers Conhecidos (Priority: P1)

Como operador do orquestrador, desejo executar um comando CLI para visualizar rapidamente a disponibilidade e contagem de modelos conhecidos de cada provider, para validar o estado do orquestrador sem disparar testes demorados ou subprocessos.

**Why this priority**: Funcionalidade principal do comando para inspeção humana rápida e validação de ambiente.

**Independent Test**: Executar `python -m orchestrator provider-summary` no terminal e verificar que o comando encerra com status 0 e exibe em stdout o nome, disponibilidade e contagem de modelos de cada provider conhecido.

**Acceptance Scenarios**:

1. **Given** o ambiente do orquestrador com providers conhecidos registrados, **When** o usuário executa `python -m orchestrator provider-summary`, **Then** o processo finaliza com código de saída 0 e imprime as informações de cada provider conhecido (nome, disponibilidade confirmada como `OK` ou indisponível/desconhecida como `UNAVAILABLE`, e contagem inteira de modelos descobertos).
2. **Given** um provider conhecido que não possua modelos descobertos ou cujo catálogo esteja indisponível, **When** o usuário executa `python -m orchestrator provider-summary`, **Then** a contagem de modelos é reportada estavelmente como o número inteiro `0` sem lançar exceções.
3. **Given** um provider conhecido cujo status de disponibilidade seja desconhecido ou não verificado, **When** o comando for executado, **Then** a disponibilidade é reportada estavelmente como `UNAVAILABLE` (equivalente ao booleano `false`).

---

### User Story 2 - Saída Estruturada em JSON (Priority: P1)

Como desenvolvedor ou pipeline automatizado, desejo obter o resumo de providers em formato JSON através da flag `--json`, para que ferramentas externas e scripts possam consumir determinística e programaticamente as informações.

**Why this priority**: Interface programática essencial para integração e testes automatizados.

**Independent Test**: Executar `python -m orchestrator provider-summary --json`, capturar stdout e validar que o conteúdo consiste unicamente em JSON válido parseável com tipagem estável para cada campo.

**Acceptance Scenarios**:

1. **Given** o orquestrador configurado com providers conhecidos, **When** o usuário executa `python -m orchestrator provider-summary --json`, **Then** o processo finaliza com código de saída 0 e emite exclusivamente em stdout um documento JSON válido contendo a lista de providers com chaves `name` (string), `available` (boolean) e `model_count` (integer).
2. **Given** a saída em stdout capturada no modo `--json`, **When** processada por um analisador JSON padrão, **Then** a análise é concluída com sucesso sem presença de texto, cabeçalhos ou logs extras antes ou após o payload.
3. **Given** um provider com catálogo indisponível ou modelos não detectados, **When** o payload JSON é inspecionado, **Then** o campo `model_count` contém estritamente o valor inteiro `0`.
4. **Given** um provider com disponibilidade desconhecida ou não verificada, **When** o payload JSON é inspecionado, **Then** o campo `available` contém estritamente o valor booleano `false`.

---

### User Story 3 - Operação Estritamente Somente Leitura e Local (Priority: P1)

Como operador de sistema seguro, exijo que o comando execute estritamente em modo de leitura local, sem disparar CLIs de provedores externos, subprocessos, chamadas LLM ou testes ao vivo.

**Why this priority**: Atendimento mandatório ao Princípio III da Constituição (reuso local não destrutivo e zero subprocessos).

**Independent Test**: Executar `python -m orchestrator provider-summary` (e com `--json`) em ambiente monitorado garantindo que nenhuma chamada de rede, chamada de modelo ou subprocesso externo (`agy`, `codex`, `opencode`) seja disparada.

**Acceptance Scenarios**:

1. **Given** adapters de provedores disponíveis ou configurados, **When** `provider-summary` é executado, **Then** o comando completa com sucesso sem acionar nenhuma CLI externa ou subprocesso.
2. **Given** execução normal do comando, **When** inspecionadas as operações realizadas, **Then** zero chamadas a APIs de modelos LLM ou smoke tests em tempo de execução são executados.

---

### User Story 4 - Tratamento Controlado de Falhas Esperadas (Priority: P2)

Como usuário do orquestrador, espero que erros comuns (como argumentos inválidos ou falhas na leitura dos dados locais) produzam mensagens de erro claras e código de saída não-zero, sem expor tracebacks não tratados.

**Why this priority**: Ergonomia CLI e contenção de falhas (Princípio IV da Constituição).

**Independent Test**: Executar `python -m orchestrator provider-summary --flag-invalida` e verificar que o processo retorna código diferente de zero com mensagem explicativa em stderr sem traceback bruto.

**Acceptance Scenarios**:

1. **Given** passagem de opções não suportadas, **When** o comando for disparado, **Then** o processo encerra com código não-zero e imprime mensagem de uso em stderr sem traceback não tratado.
2. **Given** falha na leitura dos dados locais, **When** o comando for executado, **Then** o erro é reportado com mensagem compreensível em stderr e código de saída não-zero, sem expor stack trace não tratado.

---

### Edge Cases

- **Provider sem modelos descobertos ou catálogo indisponível**: Quando uma abstração de provider estiver presente sem catálogo de modelos ou quando a descoberta não tiver sido executada, o valor de `model_count` DEVE ser sempre representado de maneira estável como o número inteiro `0` (em JSON: `0`; no modelo `ProviderSummaryItem.model_count`: `0`). Não são permitidos formatos alternativos (tais como `null`, strings descritivas ou ausência da chave).
- **Disponibilidade desconhecida ou não verificada**: O campo `available` requer estritamente um valor booleano (`bool`). Quando o status de disponibilidade do provider não puder ser confirmado ou for desconhecido (por exemplo, ausência de cache ou sondagem prévia), `available` DEVE ser reportado deterministicamente como `false` (em JSON: `false`; na saída textual: `UNAVAILABLE`). O valor booleano `true` é reservado exclusivamente para casos onde a disponibilidade é confirmada afirmativamente.
- **Inconsistência ou indisponibilidade na leitura de dados locais**: Se as abstrações locais do orquestrador encontrarem dados ilegíveis ou incompletos, o erro deve ser tratado graciosamente, reportando mensagem diagnóstica compreensível e código de saída não-zero sem crash bruto.
- **Flags e argumentos inválidos**: Argumentos desconhecidos devem ser rejeitados imediatamente pela camada CLI com código de saída padrão diferente de zero.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001 (Resumo textual)**: Ao executar `python -m orchestrator provider-summary`, o comando DEVE listar os providers conhecidos pelo sistema. Para cada provider, DEVE apresentar:
  - Nome identificador do provider;
  - Disponibilidade conhecida (`OK` se confirmada como disponível, `UNAVAILABLE` se indisponível ou com status desconhecido/não verificado);
  - Quantidade inteira de modelos descobertos (`N` descobertos, sendo `0` quando nenhum modelo for detectado ou se o catálogo estiver indisponível).
  A apresentação visual segue o estilo já utilizado pela CLI existente do orquestrador.
- **FR-002 (Saída JSON)**: Ao executar `python -m orchestrator provider-summary --json`, stdout DEVE conter somente JSON válido e parseável. O formato canônico é:
  ```json
  {
    "providers": [
      {
        "name": "agy",
        "available": true,
        "model_count": 14
      }
    ]
  }
  ```
  Contrato de tipos e representação estável dos campos:
  - `name` (`string`): Identificador do provider (ex.: `"agy"`, `"opencode"`, `"codex"`).
  - `available` (`boolean`): Valor booleano estrito. Deve ser `true` somente se a disponibilidade do provider for conhecida e confirmada. Se a disponibilidade for desconhecida, não verificada ou confirmada indisponível, o valor DEVE ser `false`.
  - `model_count` (`integer`): Contagem inteira de modelos (`int >= 0`). Se nenhum catálogo estiver disponível ou se nenhum modelo tiver sido descoberto, o valor DEVE ser obrigatoriamente `0`.
- **FR-003 (Nenhuma chamada de modelo)**: `provider-summary` DEVE ser estritamente somente de leitura. O comando NÃO PODE executar: AGY, Codex, OpenCode, chamadas LLM ou live smoke tests. DEVE utilizar apenas informações já disponíveis através das abstrações locais do orquestrador.
- **FR-004 (Reutilização)**: A implementação NÃO DEVE duplicar lógica de descoberta de providers, descoberta de modelos, configuração ou capability registry. DEVE reutilizar exclusivamente os componentes existentes do projeto.
- **FR-005 (Falhas)**: Erros esperados DEVEM retornar exit code diferente de zero, produzir mensagem compreensível e não exibir traceback bruto em uso normal.
- **FR-006 (Política TDD)**: A implementação DEVE seguir rigorosamente o ciclo TDD:
  - **Fase RED**: Criação prévia de testes em `tests/` que falhem comprovadamente pelo comportamento ainda inexistente (`EXPECTED_FAILURE`).
  - **Fase GREEN**: Implementação mínima necessária nos arquivos autorizados (`allowed_files`) para satisfazer os testes.
  - **Fase REFACTOR**: Refatoração mantendo todos os testes verdes, sem enfraquecer asserts nem modificar arquivos de teste (`TEST_TAMPERING`).
- **FR-007 (Restrições Arquiteturais)**:
  - NÃO alterar `ModelRouter`.
  - NÃO alterar política de custos.
  - NÃO alterar `StageRegistry`.
  - NÃO alterar `SkillDispatcher`.
  - NÃO alterar o `TDD engine`.
  - NÃO adicionar provider novo.
  - NÃO alterar o comportamento de `doctor`.
  - NÃO realizar chamadas LLM na implementação da feature.
  - NÃO criar dependências externas novas.

### Acceptance Criteria

- **AC-001**: `provider-summary` retorna com sucesso (exit code 0) e apresenta todos os providers conhecidos pelo orquestrador.
- **AC-002**: `provider-summary --json` produz JSON parseável sem qualquer texto, cabeçalho ou log adicional em stdout.
- **AC-003**: Os valores apresentados são derivados das abstrações existentes do projeto e não de uma lista hardcoded de providers.
- **AC-004**: Nenhum adapter de provider executa subprocessos durante a execução do comando.
- **AC-005**: Testes automatizados demonstram que o comando funciona sem executar AGY, Codex ou OpenCode.
- **AC-006**: A suíte de regressão existente continua verde (`pytest -q`).
- **AC-007**: A decomposição e execução TDD comprovam a transição formal RED → GREEN → REFACTOR com testes falhando inicialmente pela ausência do comando/opções.
- **AC-008**: O diff da feature preserva intactos `ModelRouter`, política de custos, `StageRegistry`, `SkillDispatcher`, `TDD engine` e `doctor`, sem novas dependências ou novos providers.
- **AC-009**: Erros de invocação (ex.: flags inválidas ou falha de leitura local) retornam código não-zero com mensagem explicativa em stderr sem traceback não tratado.
- **AC-010**: Quando o status de disponibilidade de um provider for desconhecido ou não verificado, o campo `available` é reportado estavelmente como `false` em JSON e como `UNAVAILABLE` no formato textual.
- **AC-011**: Quando o catálogo de modelos de um provider estiver ausente ou indisponível, o campo `model_count` é reportado estavelmente como o inteiro `0` em JSON e no modelo de dados, e como `0` descobertos no formato textual.

### Key Entities

- **ProviderSummaryItem**: Representação canônica dos dados de um provider:
  - `name`: `str` — Nome identificador do provider (ex.: `"agy"`, `"opencode"`, `"codex"`).
  - `available`: `bool` — Flag booleana estrita. `True` se o provider estiver afirmativamente confirmado como disponível; `False` se estiver indisponível ou se seu status for desconhecido/não verificado.
  - `model_count`: `int` — Quantidade inteira de modelos descobertos conhecidos (`int >= 0`). Quando nenhum catálogo estiver presente ou a contagem for indisponível, o valor é estritamente `0`.
- **ProviderSummaryReport**: Agrupamento estruturado dos resumos de providers para saída textual e serialização JSON (`providers: list[ProviderSummaryItem]`).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% das execuções nominais de `provider-summary` (modo texto e `--json`) finalizam com código de saída 0 e executam de forma síncrona sem requisição de entrada interativa.
- **SC-002**: 100% da saída em stdout sob `--json` é JSON válido parseável compatível com o schema contratado sem texto circundante.
- **SC-003**: 100% dos testes da suíte de regressão passam sem falhas junto com os novos testes dedicados ao comando.
- **SC-004**: Zero subprocessos externos e zero chamadas a APIs de modelos LLM executados pelo comando `provider-summary`.
- **SC-005**: 100% dos providers reportam `model_count` como tipo inteiro (`int`) e `available` como tipo booleano (`bool`), inclusive para casos de catálogo indisponível (`0`) ou disponibilidade desconhecida (`false`).

## Assumptions

- As abstrações existentes no orquestrador (como registries e configs locais de providers/modelos) contêm as informações necessárias para listar providers, disponibilidade conhecida e contagem de modelos descobertos sem necessidade de acionamento em tempo de execução.
- A saída textual seguirá a formatação e convenções de estilo já estabelecidas pela CLI do orquestrador.
- O formato exato da montagem de estruturas internas será definido na fase técnica de planejamento (`plan.md`).

## Out of Scope

- Dashboard interativo ou web UI.
- Métricas históricas de execução.
- Preços ou custos de modelos.
- Alteração de configurações existentes.
- Execução de health checks ao vivo ou smoke tests.
- Benchmark de desempenho de providers.
- Alteração de regras de routing de modelos.
- Interface interativa / prompts interativos no comando.
- Monitoramento contínuo em background.
