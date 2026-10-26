# Implementation Plan: Preparar implantação no Azure

**Feature**: `003-deploy-azure-containers` | **Git branch**: `main` | **Date**: 2026-10-25 | **Spec**: [spec.md](spec.md)

**Input**: `specs/003-deploy-azure-containers/spec.md` e [research.md](research.md).

## Summary

Preparar a implantação futura das imagens aprovadas pelo CI no Azure for Students, com templates, scripts, testes locais e instruções para inferência persistente, treino semanal, MLflow, métricas, backup/restore e rollback. Esta feature não provisiona Azure nem publica imagens. O `compose.yaml` continua local: a importação oficial Compose existe, mas não cobre com equivalência comprovada esta stack. A alternativa preparada é uma topologia declarativa de Container Apps, jobs, PostgreSQL gerenciado e armazenamento durável, com pré-voo de assinatura/custo e prova de semântica do registry exigidos no momento do deploy futuro.

## Technical Context

**Language/Version**: Python 3.12–3.14 para aplicação e scripts; YAML/Bicep (ou ARM equivalente) para definições Azure; Bash apenas em jobs existentes.

**Primary Dependencies**: Azure CLI/Container Apps, GitHub Actions com OIDC, Azure Container Registry, Azure Database for PostgreSQL Flexible Server, Azure Files clássico, Azure Storage para backups/evidência, MLflow 3.17.2, PostgreSQL 17, FastAPI, SQLAlchemy/psycopg, Grafana externo.

**Storage**: Três bancos lógicos (`house_price`, `mlflow`, `optuna`) em um servidor PostgreSQL gerenciado; uma share durável para artefatos MLflow e outra para registry de modelos; local separado para conjuntos correlacionados de backup/evidência. Ver [data-model.md](data-model.md).

**Testing**: pytest unitário/contrato/integração local com fakes; validação estática das definições Azure; simulações de publicação, backup, restore e rollback. Ensaio de mounts/UIDs em ACA, rede/segredos, cinco recriações e SLOs ficam como portões descritos para o deploy futuro; ver [quickstart.md](quickstart.md).

**Target Platform**: Azure Container Apps Linux `amd64` em assinatura Azure for Students; execução local Docker Compose para desenvolvimento.

**Project Type**: Serviço HTTP de inferência, rastreamento interno, jobs batch e infraestrutura declarativa.

**Performance Goals**: Nesta preparação, validar estaticamente templates e contratos e simular gates sem credenciais Azure. Para o deploy futuro, o roteiro exige cinco recriações sem perda, rollback ≤15 min, rastreabilidade ≤2 min e alertas operacionais ≤5 min após limite definido; os SLOs de treino/inferência exigem medição na capacidade Azure escolhida.

**Constraints**: Nenhuma chamada de criação, publicação ou execução na assinatura nesta feature; crédito/cotas reais desconhecidos até pré-voo futuro; imagens com tag única e digest registrado; não-root; apenas inferência com ingresso externo HTTPS; três stores separados; transações e `os.replace`/`fsync` do registry preservados; Azure Files só após prova em mount real; GitHub Actions como único agendador semanal; feature 002 precisa passar seus portões antes do deploy futuro.

**Scale/Scope**: Uma implantação estudantil, inicialmente uma réplica de inferência e uma de MLflow, um job de treino por vez e uma mutação de catálogo por vez. Nenhuma alta disponibilidade ou autoescala está presumida. Dimensionamento final depende do pré-voo e do benchmark.

## Constitution Check

*Gate de preparação: PASS para entrega de artefatos e validação local. Conformidade operacional Azure permanece pendente dos ensaios e pré-voo abaixo; nenhum princípio é dispensado.*

| Princípio/portão | Decisão de conformidade | Prova exigida antes do primeiro deploy |
|---|---|---|
| I. Experimentos rastreáveis | MLflow continua oficial; run liga código, dados, parâmetros, métricas, artefatos e imagem | Contrato de release e consulta de run/version; falha de tracking bloqueia promoção |
| II. Dados externos governados | Não se adiciona API de dados nesta feature; snapshot validado mantém identidade imutável | Teste de dataset inválido; qualquer fonte nova exige contrato/impacto |
| III. Automação semanal segura | GitHub Actions mantém agenda local; workflow Azure preparado sem execução automática até ativação futura; jobs Azure têm gatilho manual | Evidência semanal futura, exclusividade, falha explícita, OIDC e ausência de segredos nos logs |
| IV. Contêiner como entrega | Mesmos digests aprovados no CI; apps/jobs não-root; rollback por revisão e campeão | Verificar digest/tag, saúde, previsão, restauração e orçamento/cota reais |
| V. Observabilidade | PostgreSQL `observability` continua fonte Grafana, com leitor restrito | Consultas e alertas remotos, logs correlacionados e ausência de payload sensível |
| CI/revisão e promoção | Qualidade, contratos, integração, imagens e storage probe bloqueiam deploy; promoção mantém gates existentes | CI verde, checkpoint 002, ensaio Azure Files, SLO e registro de decisão |
| Backup, rollback e custo | Backup de três stores; restore testado; previsão de custo e alerta | Conjunto de backup válido, ensaio de rollback ≤15 min, relatório de crédito/cota |

Nenhuma exceção constitucional é planejada. Se o mount Azure Files falhar nos testes de atomicidade/UID, o desenho retorna à fase de pesquisa; não se altera o princípio de promoção segura.

## Architecture and Data Flow

```text
GitHub Actions (fluxo Azure manual e inativo até configuração futura; depois schedule + OIDC)
  ├─ CI → ACR: trainer/inference/mlflow com tags únicas + digests
  ├─ pré-voo de custo/cotas/identidade
  ├─ backup → mlflow-migrate job → catalog-migrate/reconcile job → verify
  ├─ trainer job → verify/evidência → decisão de promoção
  └─ deploy de revisão de inference + health/predict/rollback

Azure Container Apps environment (rede privada)
  ├─ inference app ── registry share (somente leitura) ── HTTPS externo
  ├─ mlflow app ───── artifact share (escrita) ────────── ingresso interno
  └─ jobs trainer/reconcile/migrate/backup ──────────── sem ingresso
         │                  │                  │
         └─ registry share  └─ artifact proxy └─ PostgreSQL Flexible Server privado
                                               ├─ house_price/catalog/observability
                                               ├─ mlflow
                                               └─ optuna

Backup/evidence store separado → dumps + cópias das duas shares + manifesto/checksums
Grafana externo → acesso privado ao reader PostgreSQL
```

### Decisions that differ from Compose

- `build` vira publicação de imagem por CI; deploy não reconstrói bytes.
- `depends_on` vira sequência de jobs no workflow com resultado terminal e sondas explícitas.
- Volumes Docker externos viram stores Azure separados; bind mount `weekly-output` vira destino persistente de backup/evidência.
- `127.0.0.1` e redes Compose viram ingresso HTTPS externo para inferência e endpoints privados para os demais recursos.
- `docker/postgres/init.sql` vira bootstrap Azure idempotente de papéis/bancos, sem presumir superusuário.
- `reconcile_catalog.sh`/preflight devem verificar mounts ACA, UID, artifact proxy, DB/MLflow e a igualdade entre alias, ledger e `current.json`.

## Preparation Deliverables and Future Gate Sequence

Nesta feature, produzir e validar localmente templates, scripts, workflows inertes por padrão, contratos, testes e guia. A sequência abaixo é o runbook de uma implantação futura; não será executada como parte desta feature.

1. **Pré-voo somente leitura**: verificar identidade da assinatura estudantil, região, provedores, cotas, crédito, custo projetado de 30 dias e teto operacional. Persistir [preflight report](contracts/azure-preflight.schema.json). Se falhar, não provisionar.
2. **Prova de storage isolada**: provisionar apenas recursos mínimos autorizados e executar `CatalogReconciler.preflight`, `os.replace`, `fsync` de arquivo/diretório, colisão, leitores simultâneos, RO da inferência, UIDs e cinco recriações. Não habilitar promoção até o resultado ser `passed`.
3. **Infraestrutura declarativa**: aplicar os templates preparados para criar rede privada, PostgreSQL, três destinos duráveis, ACR, segredos/identidades, ACA environment, MLflow e inferência; checar consumo e conectividade.
4. **Bootstrap/restauração**: criar bancos/papéis, restaurar conjunto correlacionado ou inicializar vazio, executar backup pré-migração, migrações, reconciliação e verify. Retornar falha sem iniciar treino quando houver conflito.
5. **Entrega**: CI aprova e publica imagem uma vez; registrar digest; criar revisão de inferência; comparar digest implantado, health, modelo e previsão; gravar [release record](contracts/azure-release.schema.json).
6. **Ciclo semanal**: GitHub Actions aciona jobs manuais em ordem, observa estado terminal, executa portões, preserva campeão em falha, cria evidência e [backup set](contracts/azure-backup-set.schema.json) mesmo sem promoção.
7. **Operação**: testar restore isolado e rollback da revisão+modelo+configuração; validar Grafana/alertas; consultar custo observado. O runbook cobre parada e exclusão controlada após backup.

## Requirement Coverage

| Requisitos | Elemento de desenho |
|---|---|
| FR-001, FR-015 | Pré-voo de assinatura, cotas, preço e relatório de custo; alerta + runbook de parada (budget não é hard cap) |
| FR-002 | Matriz de paridade em `compose-feasibility.md` e recursos declarativos separados |
| FR-003, FR-014 | Publicação única ACR, registro de digest/revisão, health/predict e rollback |
| FR-004, FR-006, FR-016 | Inferência HTTPS externa, registry de leitura, não-root, sondas e tolerância a falha do tracking |
| FR-005, FR-013 | PostgreSQL + shares independentes; backup set correlacionado e restauração isolada |
| FR-007 | OIDC, identidade gerenciada, secret refs e rede privada |
| FR-008 | Jobs serializados de backup/migração/reconciliação/verify antes de treino |
| FR-009–FR-011 | GitHub Actions semanal/manual, trainer job, MLflow e gates existentes |
| FR-012 | PostgreSQL `observability` para Grafana, alertas e logs correlacionados |
| FR-017 | Revisão de impacto e testes para mudanças de fonte/contrato/infraestrutura |

## Project Structure

### Documentation (this feature)

```text
specs/003-deploy-azure-containers/
├── spec.md
├── compose-feasibility.md
├── plan.md
├── research.md
├── data-model.md
├── contracts/
│   ├── azure-preflight.schema.json
│   ├── azure-release.schema.json
│   ├── azure-backup-set.schema.json
│   └── azure-operations.md
└── quickstart.md
```

### Source Code (repository root, implementation targets)

```text
infra/azure/                   # templates declarativos, parâmetros sem segredos e validação
scripts/azure_preflight.py     # relatório read-only de assinatura/custo/cota
scripts/azure_storage_probe.py # prova de semântica de registry em ACA
scripts/azure_backup.py       # conjunto correlacionado, restore/verify
scripts/azure_release.py      # registro de digest, revisão, saúde e rollback
.github/workflows/ci.yml      # publicar apenas imagens aprovadas
.github/workflows/weekly-training.yml  # alternativa local mantida
.github/workflows/azure-weekly.yml     # fluxo Azure semanal/manual via jobs
src/house_price/               # apenas adaptações de configuração/segurança necessárias
tests/contract/                # esquemas e comando
tests/integration/             # preflight, mount, rede, jobs, backup/rollback
docs/azure-deployment-assumptions.md
docs/operations.md
README.md
```

**Structure Decision**: Manter uma única aplicação Python e os mesmos Dockerfiles. Infraestrutura Azure tem diretório próprio, enquanto `compose.yaml` segue como ambiente local. Os scripts acima são interfaces a implementar nesta feature, sem acioná-los contra uma assinatura.

## Phase Outputs

- [research.md](research.md): decisões e fontes para Compose, ACA, storage, banco, backup, identidade, orçamento e Grafana.
- [data-model.md](data-model.md): estados e relações de pré-voo, imagem/release, execução, backup e decisão de storage.
- [contracts/](contracts/): relatórios e invariantes consumidos por CI, operador e testes.
- [quickstart.md](quickstart.md): validação local da preparação e roteiro separado para gates Azure futuros.

## Complexity Tracking

Nenhuma violação constitucional ou componente extra sem justificativa. O PostgreSQL gerenciado e o backup set separado existem para preservar três estados autoritativos com recuperação verificável.
