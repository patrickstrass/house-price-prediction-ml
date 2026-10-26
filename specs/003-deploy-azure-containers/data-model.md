# Data Model: Entrega Azure

Os registros desta feature são evidências operacionais externas ao modelo de negócio; não substituem `catalog.model_entries`, as runs MLflow ou `observability`. Timestamps usam UTC; digests/checksums SHA-256 usam 64 caracteres hexadecimais minúsculos; identificadores de operação correlacionam workflow, jobs e logs. Nenhum registro contém segredo, URL com senha ou dados de previsão.

## DeploymentPreflight

| Campo | Regra |
|---|---|
| `preflight_id`, `checked_at` | UUID único, timestamp UTC |
| `subscription_id`, `offer_type`, `region` | Identidade do alvo; `offer_type` deve confirmar Azure for Students |
| `providers`, `quotas`, `resource_estimates` | Disponibilidade, demanda de cota e custo previsto por recurso |
| `credit_remaining`, `credit_source`, `credit_checked_at`, `cost_estimate_30d`, `operational_limit` | Valores não negativos, moeda explícita; crédito com origem/horário verificável; previsão + margem ≤ mínimo de crédito e limite |
| `assumptions`, `price_checked_at` | Horizontes e fontes do preço verificáveis, sem preço hardcoded implícito |
| `feature_002_gate` | `pending|passed|failed`; deve estar `passed` antes de liberar publicação |
| `status`, `reason_codes` | `passed|failed`; falha contém códigos estáveis e bloqueia provisionamento/entrega |

Estado: `created → passed|failed`. `passed` autoriza somente o provisionamento dentro da estimativa aprovada; o StorageProbe separado ainda deve passar antes de publicar modelos. Novo preço/cota/crédito invalida o relatório anterior; um deploy referencia o `preflight_id` usado.

## StorageProbe

| Campo | Regra |
|---|---|
| `probe_id`, `preflight_id`, `environment_id` | Correlação e ambiente do teste |
| `registry_storage_id`, `artifact_storage_id` | Dois recursos duráveis distintos |
| `uid_rw`, `uid_ro`, `mount_mode_ro` | UIDs 10001/10002 quando aplicável; inferência sem capacidade de escrita |
| `checks` | Resultados de fsync de arquivo/diretório, `os.replace`, colisão, mesmo filesystem, checksum, leitores simultâneos, falha injetada e cinco recriações |
| `status`, `checked_at` | `pending|passed|failed`; `passed` só se todos os checks obrigatórios passaram |

Estado: `pending → passed|failed`; alteração do tipo de share, mount, UID ou versão de runtime exige novo probe.

## ImageRelease

| Campo | Regra |
|---|---|
| `release_id`, `git_revision`, `ci_run_id` | Identidade imutável da entrega aprovada |
| `images` | Para trainer, inference e MLflow: tag única, digest ACR e resultado do portão CI |
| `inference_revision`, `environment_id`, `config_fingerprint` | Estado implantado; fingerprint exclui valor de segredo |
| `model_version`, `dataset_sha256`, `mlflow_run_id` | Identidade do modelo/dados efetivamente servidos |
| `health_result`, `prediction_result`, `rollback_result` | Valores `passed|failed|not_run` com horário e evidência |
| `previous_release_id`, `preflight_id`, `backup_set_id` | Ponteiro para estado saudável recuperável |
| `status`, `created_at`, `completed_at` | `prepared|deployed|verified|failed|rolled_back`; ordem temporal UTC |

Transições: `prepared → deployed → verified`; falha em qualquer etapa produz `failed`, depois `rolled_back` somente quando revisão e campeão anteriores passam saúde/previsão. Imagem com digest diferente da aprovada não chega a `deployed`.

## WeeklyExecution

| Campo | Regra |
|---|---|
| `workflow_run_id`, `training_run_id` | Correlação GitHub e negócio; treino usa UUID idempotente derivado do workflow |
| `job_executions` | IDs e resultados terminais de backup, migrações, reconcile/verify e trainer |
| `dataset_identity`, `candidate_metrics`, `promotion_decision` | Referências imutáveis e limites da feature 001 |
| `previous_model_version`, `effective_model_version` | Campeão anterior e final; em rejeição devem coincidir |
| `backup_set_id`, `evidence_uri` | Evidência consultável em falha, rejeição ou aprovação |
| `status` | `running|succeeded|rejected|failed`; falha de portão bloqueia jobs seguintes |

Transições: `running → succeeded|rejected|failed`. Um job de mesmo `workflow_run_id` pode ser repetido sem duplicar versão/run; o lock do catálogo impede mutação simultânea.

## BackupSet

| Campo | Regra |
|---|---|
| `backup_set_id`, `captured_at`, `source_release_id` | Identidade comum do conjunto, UTC e origem |
| `database_dumps` | Um dump cada para `house_price`, `mlflow`, `optuna`, com URI, tamanho e SHA-256 |
| `artifact_copy`, `registry_copy` | Stores distintos, URI e checksum do índice/cópia |
| `model_version`, `pointer_sha256`, `image_digests` | Ponto de restauração e versão de runtime |
| `write_quiesced`, `verified_at`, `restore_test` | Sem escritores durante captura; checksums verificados; restore testado em ambiente isolado |
| `retention_until`, `status` | Retenção explícita; `capturing|complete|invalid|restored` |

Transições: `capturing → complete|invalid`; `complete → restored` apenas após restore isolado, migração/reconciliação, health e previsão. Um banco restaurado de data diferente das shares não constitui um `BackupSet` válido.

## Relações e invariantes

```text
DeploymentPreflight 1 ── n ImageRelease
DeploymentPreflight 1 ── n StorageProbe
ImageRelease       1 ── n WeeklyExecution
ImageRelease       1 ── n BackupSet
WeeklyExecution    n ── 1 BackupSet (quando backup concluído)
ImageRelease       n ── 1 catalog.model_entries (modelo efetivo)
```

- Publicação requer `preflight.status=passed`, `storage_probe.status=passed`, feature 002 validada e jobs de migração/reconciliação/verify em estado terminal bem-sucedido.
- `inference` referencia um pacote íntegro; seu mount não concede escrita, mesmo quando outro job publica uma versão.
- `image_digests` do `ImageRelease` são os aprovados no CI; rollback restaura release anterior e confirma `current.json`, alias, ledger e previsão.
- Dados de métricas continuam em PostgreSQL `observability` para Grafana; não são duplicados nestes registros operacionais.
