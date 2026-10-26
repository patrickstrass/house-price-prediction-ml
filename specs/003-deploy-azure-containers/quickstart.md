# Quickstart de validação: implantação Azure

Este guia tem duas etapas. A seção 1 valida **a preparação desta feature** localmente, sem credenciais, publicação de imagens ou recursos Azure. As seções 2–7 são **instruções para uma implantação futura** e não devem ser executadas para concluir esta feature. Quando essa implantação for decidida, use uma assinatura Azure for Students com crédito, região e permissões confirmados; substitua os identificadores de exemplo e guarde relatórios fora do repositório.

## 1. Validação local da preparação — executar nesta feature

```bash
uv sync --frozen --extra dev
uv run ruff check src scripts tests migrations
uv run pytest tests/unit tests/contract
uv run pytest tests/integration -m 'not docker'
docker compose config --quiet
uv run pytest tests/contract/test_azure_evidence_schemas.py tests/contract/test_azure_preflight.py tests/contract/test_azure_release.py tests/contract/test_azure_backup_set.py tests/contract/test_azure_compose_parity.py
bicep build infra/azure/main.bicep --outfile /tmp/house-price-azure-template.json
```

Valide estaticamente os templates Bicep e o YAML dos workflows; use fakes para os clientes Azure nos testes de integração. Registre como **pendentes para deploy futuro** o checkpoint final da feature 002, a compatibilidade do mount ACA, valores de crédito/cota e os ensaios operacionais. O `compose.yaml` serve de referência local; consulte [compose-feasibility.md](compose-feasibility.md).

## Implantação futura — executar somente após decisão separada

## 2. Pré-voo read-only na assinatura

```bash
az login
az account show --output table
az provider show --namespace Microsoft.App --query registrationState --output tsv
az containerapp env list-usages --resource-group <RG> --name <ACA_ENV> --output table
uv run python scripts/azure_subscription_snapshot.py --subscription <SUBSCRIPTION_ID> \
  --region <REGION> --offer-type 'Azure for Students' \
  --quotas <DATED_QUOTA_EVIDENCE_JSON> --output <SUBSCRIPTION_FACTS_JSON> --read-only-query
uv run python scripts/azure_preflight.py --subscription <SUBSCRIPTION_ID> \
  --region <REGION> --facts <SUBSCRIPTION_FACTS_JSON> \
  --credit-evidence <CREDIT_EVIDENCE_JSON> --prices <REGIONAL_PRICES_JSON> \
  --operational-limit <AMOUNT> --safety-margin <AMOUNT> \
  --output <PREFLIGHT_JSON>
```

`env list-usages` é aplicável após o ambiente existir; antes disso, forneça evidência datada das cotas da assinatura/região. `--offer-type` deve corresponder à oferta comprovada no portal; `--prices` e `--credit-evidence` são registros datados do operador, sem credenciais. Espere relatório conforme [azure-preflight.schema.json](contracts/azure-preflight.schema.json), `status=passed`, preço e saldo com timestamp e soma prevista abaixo dos dois limites. Se não houver conta, saldo ou cotas suficientes, a validação termina aqui. Budget/alerta não interrompe consumo automaticamente.

## 3. Prova de storage isolada

Após aprovação do pré-voo e provisionamento mínimo em ambiente de teste, execute:

```bash
uv run python scripts/azure_storage_probe.py --environment-id <ACA_ENV_ID> \
  --preflight-id <PREFLIGHT_ID> --registry-storage-id <REGISTRY_STORAGE_ID> \
  --artifact-storage-id <ARTIFACT_STORAGE_ID> \
  --registry-path /model-registry --artifact-path /mlflow-artifacts \
  --runtime <IMMUTABLE_ACA_IMAGE_DIGEST> --protocol SMB \
  --output <STORAGE_PROBE_JSON>
```

O comando acima produz `pending` mesmo quando o probe básico passa. Dentro da réplica de inferência, execute `python /usr/local/bin/azure-storage-evidence readonly --registry-path /model-registry --output /tmp/readonly.json`; após cada uma das cinco recriações, execute `observe --registry-path /model-registry --output /tmp/observation-N.json` e copie os cinco relatórios para o operador. Finalize com `uv run python scripts/azure_storage_evidence.py finalize --basic <STORAGE_PROBE_JSON> --readonly <READONLY_JSON> --observations <OBS1> <OBS2> <OBS3> <OBS4> <OBS5> --output <FINAL_PROBE_JSON>`. O finalizador exige proveniência ACA, IDs de réplica distintos e o mesmo checksum/versão do ponteiro. O operador ainda confirma saúde e previsão após cada recriação. Não altere o JSON manualmente para `passed`. Se qualquer operação de filesystem falhar, não configure promoção no Azure; volte ao desenho do store. [StorageProbe](data-model.md#storageprobe) detalha os checks.

## 4. Provisionamento e entrega

Aplicar `infra/azure/` somente depois dos passos 2–3 e da revisão de `what-if`. A publicação de imagens no ACR fica no job manual protegido do CI. O script `azure_release.py deploy` prepara e valida o [release record](contracts/azure-release.schema.json); somente `--execute` consulta digests no ACR e cria uma revisão. `verify --execute` só transfere tráfego após saúde, versão, checksum dos dados e previsão passarem. Confira também `model-catalog verify` para ledger/alias/ponteiro. Verifique:

```bash
az containerapp show --name <INFERENCE_APP> --resource-group <RG> \
  --query '{revision:properties.latestReadyRevisionName,image:properties.template.containers[0].image}'
curl --fail https://<INFERENCE_FQDN>/health/ready
curl --fail https://<INFERENCE_FQDN>/v1/model
curl --fail --request POST https://<INFERENCE_FQDN>/v1/predictions \
  --header 'Content-Type: application/json' \
  --data '{"instances":[{"MedInc":8.3252,"HouseAge":41,"AveRooms":6.9841,"AveBedrms":1.0238,"Population":322,"AveOccup":2.5556,"Latitude":37.88,"Longitude":-122.23}]}'
```

Confirme que MLflow usa ingresso interno e que PostgreSQL/artefatos não têm acesso público. Cinco reinícios/recriações devem manter o mesmo campeão/checksum sem retreinamento.
Depois que `azure_release.py verify --execute` gerar o release verificado, inicie o job manual
`release-sync` com o JSON compacto do release em `RELEASE_JSON`; confira que
`/backup/current-release.json` e `/backup/releases/<release_id>.json` foram gravados antes de
liberar o workflow semanal. O job de backup
recusa um release ausente ou divergente do ponteiro atual.

## 5. Ciclo semanal, falha e evidência

Acione `azure-weekly.yml` manualmente em ambiente de teste. Confirme a ordem backup → migração MLflow → migração/reconciliação catálogo → verify → trainer → verify/evidência e IDs terminais dos jobs. Repita com dataset inválido e candidato rejeitado: nenhum deve trocar o campeão. Relatório e alerta devem aparecer mesmo sem nova promoção; Grafana precisa consultar `observability` por ligação privada.

## 6. Restore e rollback

Selecione um [backup set](contracts/azure-backup-set.schema.json) com três dumps e duas cópias verificadas. Crie previamente um ambiente isolado com três bancos vazios, duas shares vazias, MLflow e inferência de teste. Forneça os quatro endpoints isolados via `RESTORE_HOUSE_PRICE_DATABASE_URL`, `RESTORE_MLFLOW_DATABASE_URL`, `RESTORE_OPTUNA_DATABASE_URL` e `RESTORE_MLFLOW_TRACKING_URI` (HTTPS). Execute `azure_backup.py restore` com `--backup-set`, `--isolated-target` vazio, `--artifact-target`, `--registry-target`, `--release`, `--health-url`, `--target-environment-id` diferente do release e `--confirm-isolated`. O script verifica hashes, restaura os três bancos, materializa artefatos/registry, executa reconcile/verify e verifica saúde e previsão; só então grava `restore-result.json` com `status=restored`. Injete versão de inferência com readiness falhando; `azure_release.py rollback --execute` inicia o job privado `release-rollback`, que restaura sob lock o campeão anterior ainda íntegro, o alias e o ponteiro. O script exige saúde e previsão antes de reverter o tráfego. Se o pacote anterior estiver ausente ou inválido, restaure o backup correlacionado em alvo isolado primeiro. Confirme saúde, modelo e previsão em até 15 minutos. Não teste restore ou falha destrutiva sobre o ambiente ativo.

## 7. Custo e desligamento

Compare consumo observado com estimativa/limite em cada ciclo semanal. Confirme que o budget da assinatura tem destinatário e limiares definidos, gere um ensaio controlado de alerta e verifique o recebimento; valide também os alertas de falha semanal, indisponibilidade e qualidade em até cinco minutos após o limite configurado. Revise alertas do Azure Cost Management sabendo que eles não suspendem recursos nem têm latência garantida de cinco minutos. Antes de desligar, capture/valide o backup set e registre versões/digests. Pare jobs e apps conforme runbook; remova recursos apenas em operação administrativa separada e autorizada.
