---

description: "Tarefas de implementação da implantação Azure da feature 003"
---

# Tasks: Preparar implantação no Azure

**Input**: `specs/003-deploy-azure-containers/{spec,plan,research,data-model,quickstart}.md` e `contracts/`.

**Organization**: tarefas por história da especificação. Esta feature termina quando código, templates, contratos, testes locais e runbook estiverem prontos. Nenhuma tarefa cria recurso, publica imagem ou executa workflow na Azure; os ensaios com conta real são instruções para uma implantação posterior. Faltas de crédito, cota ou prova de storage bloqueiam esse deploy futuro.

**Format**: `[ID] [P?] [Story] ação e caminho exato`. `[P]` indica arquivos independentes que podem avançar em paralelo após as dependências anteriores da fase.

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: preparar interfaces e estrutura sem criar recursos Azure.

- [X] T001 Criar `infra/azure/README.md` com inventário de recursos declarativos, variáveis de ambiente, parâmetros sem segredos, ordem de aplicação e proibição de provisionar antes do pré-voo.
- [X] T002 [P] Criar `src/house_price/azure/__init__.py` e `src/house_price/azure/evidence.py` para serialização JSON versionada, timestamps UTC, SHA-256, validação pelos esquemas de `specs/003-deploy-azure-containers/contracts/` e escrita atômica sem segredos.
- [X] T003 [P] Atualizar `pyproject.toml` e `uv.lock` somente com dependências necessárias aos scripts Azure e à validação de contratos, preservando o lock reproduzível.
- [X] T004 [P] Adicionar `tests/contract/test_azure_evidence_schemas.py` para validar os três JSON Schemas 2020-12 e rejeitar manifests com status, hash, UUID ou campos obrigatórios inválidos.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: comprovar condições comuns antes de qualquer implantação paga ou publicação de modelo.

- [X] T005 Criar `tests/contract/test_azure_preflight.py` com cenários de oferta incorreta, saldo/cota indisponível, preço obsoleto e custo projetado acima de crédito ou teto; cada falha deve impedir provisionamento.
- [X] T006 Implementar `src/house_price/azure/preflight.py` e `scripts/azure_preflight.py` conforme `contracts/azure-operations.md` e `azure-preflight.schema.json`: `preflight_id` UUID, `checked_at`/`credit_checked_at`/`price_checked_at` UTC, moeda ISO, valores não negativos, `providers`, `resource_estimates`, `quotas`, `assumptions`, `feature_002_gate=pending|passed|failed`, `status=passed|failed` e `reason_codes` estáveis; exigir evidência do crédito com origem e hora, consultar disponibilidade/cotas/preços atuais, e bloquear se estimativa de 30 dias + margem superar crédito ou limite.
- [X] T007 [P] Criar `infra/azure/main.bicep`, `infra/azure/parameters.example.json` e `scripts/azure_apply.py` com parâmetros de região/SKU/capacidade sem segredos nem preço fixo, tags e IDs de saída; o wrapper deve validar relatório `status=passed` de T006 antes de invocar a implantação Bicep.
- [X] T008 [P] Criar `infra/azure/identity.bicep` para identidade gerenciada, permissões mínimas ACR/Key Vault/storage e federação OIDC GitHub por repositório/ambiente; documentar a vinculação de credenciais externas em `infra/azure/README.md`.
- [X] T009 Adicionar gate de implantação em `scripts/azure_release.py` que valida `preflight_id`, expiração/identidade do relatório, `feature_002_gate=passed`, estado do storage probe e digest aprovado antes de invocar Azure; falhar fechado em falta de evidência.
- [X] T010 Inspecionar o checkpoint da feature 002 em `specs/002-persist-model-mlflow/tasks.md` e registrar em `docs/azure-deployment-assumptions.md` quais provas de reconciliação, publicação protegida e restauração serão exigidas antes do deploy futuro.

**Checkpoint**: pré-voo é validado localmente com evidências simuladas; nenhum comando de provisionamento é executado nesta feature.

---

## Phase 3: User Story 1 — Implantar previsão com segurança (Priority: P1) 🎯 MVP

**Goal**: preparar a entrega de imagem aprovada, campeão íntegro e serviço HTTPS com persistência e rollback.

**Independent Test**: validar localmente contratos, templates e fluxo de release com Azure simulado; conferir no runbook os comandos e critérios do futuro ensaio de storage, cinco recriações e rollback em até 15 minutos.

### Tests for User Story 1

- [X] T011 [P] [US1] Criar `tests/contract/test_azure_release.py` para validar tags únicas, digests `sha256:` com 64 hexadecimais, `ci_approved=true`, `config_sha256`, `dataset_sha256`, estados `prepared|deployed|verified|failed|rolled_back` e proibição de `verified` sem saúde/previsão aprovadas conforme `contracts/azure-release.schema.json`.
- [X] T012 [P] [US1] Criar `tests/integration/test_azure_storage_probe.py` com filesystem local e cliente ACA simulado para `st_uid` 10001/10002, mesmo filesystem, escrita e `fsync`, `os.replace`, colisão imutável, leitores simultâneos, falha entre alias/ponteiro e mount RO; documentar em `specs/003-deploy-azure-containers/quickstart.md` a prova pendente no mount real e cinco recriações futuras.
- [X] T013 [P] [US1] Criar `tests/integration/test_azure_inference_release.py` com cliente ACA simulado para digest, `/health/ready`, `/v1/model`, `/v1/predictions`, isolamento de MLflow/DB, continuidade com MLflow indisponível e rollback; documentar em `specs/003-deploy-azure-containers/quickstart.md` o ensaio real de recriações e limite de 15 minutos.

### Implementation for User Story 1

- [X] T014 [US1] Implementar `src/house_price/azure/storage_probe.py` e `scripts/azure_storage_probe.py` com `probe_id`, `preflight_id`, `environment_id`, IDs distintos de share, UID RW/RO, resultados individuais e `status=pending|passed|failed`; invalidar probe se protocolo, mount, UID ou runtime mudar.
- [X] T015 [P] [US1] Criar `infra/azure/storage.bicep` com shares separadas de artefatos MLflow, registry de modelos e backup/evidência, acessos mínimos e montagem de registry RW apenas em jobs autorizados e RO na inferência; não presumir sucesso do probe.
- [X] T016 [P] [US1] Criar `infra/azure/postgres.bicep` com Flexible Server, DNS/rede privada, TLS verificado e parâmetros de SKU/capacidade sujeitos ao pré-voo; não expor PostgreSQL publicamente.
- [X] T017 [US1] Criar `infra/azure/bootstrap-postgres.sql` e `scripts/azure_bootstrap_postgres.py` para configurar idempotentemente `house_price`, `mlflow`, `optuna` e papéis separados de bootstrap, migração, pipeline e leitura de observabilidade sem presumir superusuário gerenciado.
- [X] T018 [P] [US1] Criar `infra/azure/container-apps.bicep` com apps `inference` e `mlflow`, imagens parametrizadas por tag única, ingresso HTTPS externo somente para inferência, ingresso interno para MLflow, sondas, não-root, secret refs, mounts distintos e URL real do tracking.
- [X] T019 [US1] Atualizar `src/house_price/settings.py`, `docker/inference.Dockerfile`, `docker/mlflow.Dockerfile` e `docker/trainer.Dockerfile` apenas onde necessário para TLS PostgreSQL, hosts internos MLflow, mounts/UIDs ACA e leitura RO do campeão, mantendo os contratos locais de inferência.
- [X] T020 [US1] Implementar `src/house_price/azure/release.py` e completar `scripts/azure_release.py` com `deploy`, `verify` e `rollback`: associar `release_id`, `git_revision`, `ci_run_id`, imagem/tag/digest aprovado, `preflight_id`, `storage_probe_id`, `config_sha256`, `model_version`, `dataset_sha256`, `mlflow_run_id`, `inference_revision`, `previous_release_id`, `backup_set_id`, `created_at`/`completed_at` UTC e `health_result`/`prediction_result`/`rollback_result=passed|failed|not_run`; só marcar `deployed` após comparar imagem efetiva e `verified` após saúde, modelo, previsão e catálogo/alias/ponteiro coerentes.
- [X] T021 [US1] Implementar em `src/house_price/azure/release.py` rollback de revisão, campeão e configuração para `previous_release_id` saudável sob lock; confirmar saúde e previsão antes de `status=rolled_back`, registrar `failed` se qualquer etapa não completar.
- [X] T022 [US1] Atualizar `.github/workflows/ci.yml` para construir e validar localmente imagens trainer/inference/MLflow; preparar publicação de tags únicas no ACR por OIDC e manifesto de digests somente em job manual desabilitado até configuração explícita, mantendo gates de CI antes da publicação futura.
- [X] T023 [US1] Executar localmente `tests/contract/test_azure_release.py`, `tests/integration/test_azure_storage_probe.py` e `tests/integration/test_azure_inference_release.py`; registrar em `docs/azure-deployment-assumptions.md` resultados simulados e pendências de mount real, cinco recriações e tempo de rollback, sem apresentar simulação como prova Azure.

**Checkpoint**: US1 está preparada após testes locais; probe real Azure Files, recriações e rollback medido ficam explicitamente pendentes antes de uma implantação futura.

---

## Phase 4: User Story 2 — Executar o ciclo semanal no Azure (Priority: P2)

**Goal**: preparar jobs finitos e GitHub Actions para preservar o campeão em rejeição e produzir evidência e backups recuperáveis após ativação futura.

**Independent Test**: simular localmente um ciclo com candidato rejeitado e dados inválidos; validar evidência terminal, campeão intacto e lógica de backup/restore com stores locais isolados.

### Tests for User Story 2

- [X] T024 [P] [US2] Criar `tests/contract/test_azure_backup_set.py` para exigir dumps `house_price`/`mlflow`/`optuna`, cópias distintas de artefatos/registry com URI, tamanho e SHA-256, `write_quiesced=true` e `verified_at` em `complete`, `restore_test=passed` em `restored`, retenção UTC e nenhum segredo em URI.
- [X] T025 [P] [US2] Criar `tests/integration/test_azure_weekly.py` com estados terminais dos jobs em ordem, lock/idempotência por `workflow_run_id`, bloqueio de trainer após falha de backup/migração/reconciliação/verify, candidato rejeitado ou dataset inválido sem troca de campeão e evidência em todos os resultados.
- [X] T026 [P] [US2] Criar `tests/integration/test_azure_restore.py` para backup correlacionado, checksum e restauração isolada de três bancos e duas shares; reconciliação deve detectar divergência e não criar versão duplicada nem promover automaticamente.

### Implementation for User Story 2

- [X] T027 [US2] Criar `infra/azure/jobs.bicep` com jobs manuais `backup`, `mlflow-migrate`, `catalog-reconcile`, `trainer` e `verify`, sem cron Azure nem ingresso público, timeout/retry explícitos, identidade/secret refs e limite de uma mutação de catálogo por vez.
- [X] T028 [US2] Implementar `src/house_price/azure/jobs.py` e `scripts/azure_jobs.py` para iniciar cada job, correlacionar `workflow_run_id` e `training_run_id` UUID estável, esperar execution IDs e resultados terminais, interromper sequência em erro e registrar `dataset_identity`, `candidate_metrics`, `promotion_decision`, `previous_model_version`, `effective_model_version`, `backup_set_id`, `evidence_uri` e `status=running|succeeded|rejected|failed` conforme `WeeklyExecution` de `data-model.md`.
- [X] T029 [P] [US2] Implementar `src/house_price/azure/backup.py` e `scripts/azure_backup.py` com `capture`, `verify`, `restore`: `backup_set_id` UUID, `captured_at`/`verified_at`/`retention_until` UTC, três dumps, duas cópias, hashes e tamanhos, `model_version`, `pointer_sha256`, digests e lock/pausa de escritores; restore exige alvo isolado e executa bancos → artefatos → registry → reconcile/verify → saúde/previsão.
- [X] T030 [US2] Adaptar `scripts/migrate_mlflow.sh` e `scripts/reconcile_catalog.sh` para endpoints/mounts ACA, abortar em conflito e confirmar `current.json`, ledger e alias antes de liberar `trainer`; manter os caminhos locais compatíveis.
- [X] T031 [US2] Criar `.github/workflows/azure-weekly.yml` sem gatilho automático ativo, com `workflow_dispatch` protegido por ambiente, OIDC, `concurrency` exclusivo e sequência backup → migração MLflow → catálogo/reconcile → verify → treino → verify/evidência; documentar em `docs/operations.md` como ativar a agenda semanal só após deploy futuro, preservando GitHub Actions como único agendador.
- [X] T032 [US2] Integrar `scripts/create_weekly_evidence.py` ao workflow `.github/workflows/azure-weekly.yml` para registrar identidade imutável dos dados, parâmetros/métricas/artefatos MLflow, decisão, versão anterior/efetiva, IDs de jobs, backup e diagnóstico sem payload ou segredo, inclusive na falha/rejeição.
- [X] T033 [US2] Executar localmente `tests/contract/test_azure_backup_set.py`, `tests/integration/test_azure_weekly.py` e `tests/integration/test_azure_restore.py` com clientes/stores simulados e registrar em `docs/azure-deployment-assumptions.md` resultados e pendências do ensaio futuro de restore Azure.

**Checkpoint**: US2 está preparada quando o fluxo simulado tem resultado rastreável e o restore local isolado não promove indevidamente; execução Azure segue pendente.

---

## Phase 5: User Story 3 — Escolher a forma de implantação e controlar custo (Priority: P3)

**Goal**: demonstrar paridade com Compose, custo/cota observáveis e operação com alertas e desligamento recuperável.

**Independent Test**: confrontar cada serviço/volume/porta/segredo/dependência do Compose com templates e runbook Azure; simular crédito/cota insuficiente e verificar bloqueio sem chamada de criação; validar exemplos de consulta de custo, alertas e desligamento sem conta ativa.

### Tests for User Story 3

- [X] T034 [P] [US3] Criar `tests/contract/test_azure_compose_parity.py` para comparar `compose.yaml` com a matriz de `specs/003-deploy-azure-containers/compose-feasibility.md` e exigir destino/aceite para serviço, volume, bind mount, porta, segredo, healthcheck, `depends_on` e agendamento.
- [X] T035 [P] [US3] Criar `tests/integration/test_azure_cost_gate.py` para verificar recurso/estimativa de 30 dias, crédito com origem e horário, cotas, margem e teto, ausência de chamadas de provisionamento em `failed` e ausência de promessa de hard cap por budget.
- [X] T036 [P] [US3] Criar `tests/integration/test_azure_observability.py` para consultar métricas por versão/janela no PostgreSQL privado via leitor Grafana e confirmar alertas de falha semanal, indisponibilidade e qualidade em até cinco minutos após cada limite definido.

### Implementation for User Story 3

- [X] T037 [US3] Atualizar `specs/003-deploy-azure-containers/compose-feasibility.md` e `infra/azure/README.md` com matriz executável de paridade `compose.yaml` → ACA app/job, PostgreSQL, Azure Files, backup, rede, segredos e sequência; declarar `az containerapp compose create` apenas como importação parcial sem equivalência desta stack.
- [X] T038 [US3] Implementar `src/house_price/azure/cost.py` e `scripts/azure_cost.py` para registrar custo observado por recurso, janela/moeda e comparação com `resource_estimates`, crédito e `operational_limit`, sem usar budget como bloqueio automático; retornar diagnóstico se API de custo/saldo estiver atrasada ou indisponível.
- [X] T039 [US3] Criar `infra/azure/monitoring.bicep` com orçamento/alertas configuráveis, logs correlacionados por workflow/job/release/modelo e alertas operacionais para falha semanal, indisponibilidade e qualidade; manter PostgreSQL `observability` como fonte Grafana por acesso privado.
- [X] T040 [US3] Documentar em `docs/operations.md` consulta de custo/crédito/cotas, avaliação de alerta, backup antes de parar, parada de jobs/apps, remoção administrativa separada, restauração isolada e rollback; registrar que orçamento Azure alerta mas não corta recursos automaticamente.
- [X] T041 [US3] Documentar em `docs/azure-deployment-assumptions.md` conectividade privada do Grafana, reader mínimo, fonte de cada métrica, janela/limite dos três alertas e roteiro futuro de ensaio SC-006; se a conexão privada falhar no deploy futuro, registrar bloqueio da implantação.
- [X] T042 [US3] Executar localmente `tests/contract/test_azure_compose_parity.py`, `tests/integration/test_azure_cost_gate.py` e `tests/integration/test_azure_observability.py` com entradas simuladas; registrar em `docs/azure-deployment-assumptions.md` que preço, cota, crédito, alertas e desligamento reais dependem de validação futura na assinatura.

**Checkpoint**: US3 está preparada quando todos os componentes locais têm alternativa documentada, gates de custo/cota falham corretamente na simulação e há instruções verificáveis para alertas/operação futuros.

---

## Phase 6: Polish & Cross-Cutting Concerns

- [X] T043 [P] Atualizar `README.md` com a rota Azure preparada, pré-requisitos, portões, referência ao `specs/003-deploy-azure-containers/quickstart.md` e distinção entre validação local concluída e deploy ACA futuro.
- [X] T044 [P] Atualizar `docs/azure-deployment-assumptions.md` com análise de impacto e testes exigidos para alteração de fonte externa, esquema, contrato de inferência, critérios de promoção ou infraestrutura conforme FR-017.
- [X] T045 Executar somente a seção local de `specs/003-deploy-azure-containers/quickstart.md` e os gates de CI de `.github/workflows/ci.yml`; registrar em `docs/azure-deployment-assumptions.md` resultados locais e lista de validações Azure pendentes, sem login/provisionamento/publicação.
- [X] T046 Revisar `infra/azure/main.bicep`, `infra/azure/container-apps.bicep`, `infra/azure/jobs.bicep`, `scripts/azure_release.py`, `scripts/azure_apply.py`, `.github/workflows/azure-weekly.yml` e `docs/operations.md` para eliminar segredos/URLs com senha, acesso público indevido, tags mutáveis, recursos não estimados e desvios da constituição; repetir os testes impactados.

---

## Dependencies & Execution Order

### Phase Dependencies

- Setup T001–T004 → Foundational T005–T010 → fases por história. T006 depende de T005; T009 valida relatório de storage probe simulado e falha se ausente; T010 registra pendências da feature 002 sem bloquear a preparação local.
- US1: T011–T013 definem provas antes de T014–T023. T014 depende do pré-voo T006; T020 depende de T014–T019; T021 depende de T020. T023 exige T022 e executa apenas provas locais.
- US2: T024–T026 definem provas antes de T027–T033. T031 depende de T027–T030; T033 depende de T031–T032 e de stores locais isolados.
- US3: T034–T036 definem provas antes de T037–T042. A documentação de paridade T037 pode ser feita após Foundation; os testes usam entradas locais sem acessar a assinatura.
- Polish T043–T046 depende das três histórias. T045 executa somente validações locais e mantém uma lista explícita dos portões Azure pendentes.

### User Story Completion Order

```text
Setup → Foundational (pré-voo simulado e pendências 002)
                   ├─ US1: templates/probe → inferência → rollback simulado (MVP)
                   ├─ US2: jobs/backup → ciclo semanal → restore local
                   └─ US3: paridade/custo → instruções de alertas/operação
Todas → Polish
Implantação futura: crédito/cota reais → storage probe ACA → deploy → ensaios operacionais
```

### Parallel Opportunities

- Setup: T002, T003 e T004 atuam em arquivos diferentes após T001.
- US1: T011–T013 podem ser escritos em paralelo; T015, T016 e T018 tratam recursos declarativos separados, depois convergem em T020.
- US2: T024–T026 são provas independentes; T029 pode avançar em paralelo a T027–T028, depois converge no workflow T031.
- US3: T034–T036 são provas independentes; T037, T038 e T039 atuam em artefatos distintos após Foundation.

## Implementation Strategy

1. **MVP**: terminar Setup, Foundational e US1; o resultado é uma rota de inferência Azure reproduzível, validada localmente e com portões futuros explícitos.
2. **Incremento semanal**: implementar US2 sem trocar o orquestrador GitHub Actions; o workflow Azure permanece sem agenda automática e sem execução durante esta feature.
3. **Preparação completa**: fechar US3 e Polish, incluindo paridade Compose, simulador de custo, instruções para alertas, Grafana privado e recuperação futura.

Os testes locais demonstram a preparação, não a operação Azure. O guia identifica separadamente os ensaios reais necessários quando o responsável decidir implantar. Esta lista termina sem login Azure, recursos criados, imagens publicadas ou workflow Azure acionado.
