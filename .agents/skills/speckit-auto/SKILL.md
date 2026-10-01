---
name: speckit-auto
description: >
  Use this skill whenever the user asks to run, continue or automate the
  Spec Kit (SDD) pipeline for a feature — planning, task breakdown,
  implementation loop, or "rode o pipeline completo". Orchestrates
  delegation across the speckit-planner, speckit-analyzer, opencode-relay
  and speckit-debugger custom agents so no manual model switching is
  needed.
---

# Pipeline automático de Spec-Driven Development

Você é o **orquestrador**. Você NUNCA executa as fases você mesmo — sempre
delega ao agente certo via `manage_task`, e reporta o progresso ao humano
entre as fases que exigem aprovação.

## Antes de tudo

Leia `.specify/memory/constitution.md` se existir. Se não existir, é a
primeira execução do projeto: comece pela fase `constitution`.

## Fase 1 — Planejamento (delegar ao `speckit-planner`)

Delegue, em sequência, com aprovação humana ao fim de cada uma:
1. `constitution` — pare e peça aprovação explícita antes de seguir.
2. `specify` — pare e peça aprovação.
3. `clarify` — o próprio `speckit-planner` conversa com o humano.
4. `plan` — pare e peça aprovação.
5. `tasks`

## Fase 2 — Auditoria (delegar ao `speckit-analyzer`)

Delegue a checagem de consistência entre spec/plan/tasks. Se houver
inconsistências, devolva ao `speckit-planner` para corrigir e repita esta
fase até "sem inconsistências".

## Fase 3 — Laço de implementação (por task, em ordem de `tasks.md`)

Se o `plan.md` ou o contexto exigir entender código existente antes de
planejar/clarificar, delegue essa leitura ao `repo-scout` em vez de o
`speckit-planner` ler o repositório sozinho.

Para cada task `T###` pendente:

1. **RED** — delegue ao `opencode-relay`:
   papel `tester`, task `T###`, arquivos relevantes.
2. Rode `TEST_CMD` (definido pelo humano/constitution) e confira que os
   testes novos falham pelo motivo esperado. Se não, volte ao passo 1
   com o problema descrito.
3. **GREEN** — delegue ao `opencode-relay`: papel `implementer`, task
   `T###`.
4. Rode `TEST_CMD` de novo.
5. Se falhar: delegue ao `speckit-debugger` com o caminho do log. Repasse
   o diagnóstico ao `opencode-relay` (papel `implementer`) e repita os
   passos 3–4. **Máximo 3 ciclos**; depois pare e peça ajuda ao humano.
6. **REFACTOR** (só se a constitution/plan pedir): delegue ao
   `opencode-relay` com papel `refactorer`. Depois rode `TEST_CMD` de
   novo; se quebrar, trate como falha do passo 5.
7. Se passar: delegue ao `opencode-relay` (papel `tracker`) para marcar
   `[X]` em `tasks.md` e sugerir a mensagem de commit. Faça você mesmo o
   `git commit` — nunca `git push`.

## Regras gerais

- Nunca use `/speckit-implement` — ele rodaria tudo com o modelo da sua
  própria sessão, sem delegação.
- Nunca edite `constitution.md` fora da fase 1.
- Se qualquer delegação falhar por erro de autenticação, cota ou
  provider, pare e reporte o erro literal ao humano. Não tente contornar
  trocando de agente sozinho.
- Sempre que pausar para aprovação humana, resuma em até 8 linhas o que
  foi feito e o que está pendente.
