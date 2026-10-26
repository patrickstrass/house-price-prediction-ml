# Azure Operations Contract

Este contrato define as interfaces a implementar. Nesta feature os scripts e o workflow são preparados e testados localmente, sem executar chamadas que criem recursos, publiquem imagens ou iniciem jobs na assinatura. Todo comando, quando usado no futuro, retorna JSON em stdout e diagnóstico sem segredo em stderr. Saída JSON é validada pelos três esquemas deste diretório. As opções de assinatura/região não têm valores embutidos.

## Pré-voo e storage

```text
azure-preflight --subscription SUBSCRIPTION_ID --region REGION
  --facts PATH --credit-evidence PATH --prices PATH
  --operational-limit AMOUNT --safety-margin AMOUNT --output PATH
azure-storage-probe --environment-id RESOURCE_ID --registry-storage-id RESOURCE_ID
  --preflight-id UUID --artifact-storage-id RESOURCE_ID
  --registry-path PATH --artifact-path PATH --output PATH
```

O pré-voo é read-only: valida oferta Azure for Students, provider `Microsoft.App`, PostgreSQL, Storage e ACR, disponibilidade regional, cotas, saldo e 30 dias de custo por recurso. `--credit-evidence` recebe um registro local do saldo consultado no portal/sistema oficial da assinatura, contendo moeda, valor, origem e horário; não contém credenciais e é conferido contra a assinatura alvo. A soma `cost_estimate_30d + safety_margin` deve ser ≤ `credit_remaining` e ≤ `operational_limit`; quotas disponíveis devem cobrir demandas. Se saldo/preço/cota não puderem ser comprovados, retorna `failed` com reason code, não valor inventado. Budget Azure é alerta, não hard cap. O storage probe só aprova se todos os checks de [data-model.md](../data-model.md) passarem em mount real. Ele não publica campeão.

## Entrega

```text
azure-release deploy --preflight PATH --storage-probe PATH --ci-manifest PATH
  --target-config PATH --output PATH [--resource-group NAME --app NAME --acr-name NAME --execute]
azure-release verify --release PATH --resource-group NAME --app NAME --output PATH --execute
azure-release rollback --release PATH --previous-release PATH
  --resource-group NAME --app NAME --output PATH --execute
```

`deploy` sem `--execute` apenas prepara o manifesto; com a flag, confere digests ACR e atualiza a imagem de inferência para criar revisão. `verify` consulta `/health/ready`, `/v1/model` e previsão na revisão antes de rotear tráfego e exige evidência recente de `model-catalog verify` para `current.json`/ledger/alias. `rollback` inicia o job privado `release-rollback`, que restaura campeão, alias e ponteiro sob lock; só reativa revisão anterior se ela servir o mesmo modelo/dataset do release saudável. Pacote ausente ou inválido exige recuperação do backup correlacionado antes. Os scripts recusam declarar sucesso se não puderem provar esses estados.

## Jobs e dependências

O workflow `.github/workflows/azure-weekly.yml` é entregue sem agenda automática ativa; `workflow_dispatch` fica protegido por ambiente e só deve ser acionado no deploy futuro. Após essa implantação, o operador habilita a agenda semanal no GitHub Actions, que continua sendo o único agendador. O workflow usa `concurrency` exclusivo e OIDC e aciona os jobs `backup`, `mlflow-migrate`, `catalog-reconcile`, `verify` e `trainer`. Para cada job, registra execution ID, espera estado terminal e falha antes do próximo quando estado não for sucesso. `trainer` recebe `--run-id` estável derivado do GitHub run ID. Em qualquer desfecho, publica IDs e estados dos jobs sem segredo. A identidade do dataset, métricas e decisão de promoção ainda dependem da integração dos resultados reais do treino/MLflow; a ausência deles não comprova aprovação.

## Backup e restauração

```text
azure-backup capture --dumps-dir PATH --artifacts-dir PATH --registry-dir PATH
  --destination PATH --retention-until UTC_TIMESTAMP --writers-quiesced
azure-backup verify --backup-set PATH
azure-backup stage --backup-set PATH --isolated-target PATH
azure-backup restore --backup-set PATH --isolated-target EMPTY_STAGING_PATH
  --artifact-target EMPTY_ISOLATED_SHARE --registry-target EMPTY_ISOLATED_SHARE
  --release VERIFIED_RELEASE_JSON --health-url HTTPS_ISOLATED_INFERENCE
  --target-environment-id DIFFERENT_ENVIRONMENT_ID --confirm-isolated
```

O orquestrador para o app MLflow e espera `Stopped` antes do backup; o job `azure-backup-job` mantém o lock do catálogo ao produzir três dumps e duas cópias sob um `backup_set_id`, valida checksums e registra retenção. MLflow reinicia após a migração e antes da reconciliação, inclusive após falha. A CLI `capture` exige escritores pausados e dumps prontos. `stage` somente prepara arquivos. `restore` exige URLs dos três bancos isolados em `RESTORE_HOUSE_PRICE_DATABASE_URL`, `RESTORE_MLFLOW_DATABASE_URL` e `RESTORE_OPTUNA_DATABASE_URL`, além de `RESTORE_MLFLOW_TRACKING_URI` por HTTPS; rejeita URLs iguais às ativas. Ela executa `pg_restore` nos três bancos, materializa shares isoladas, roda reconcile/verify e saúde/previsão, nesta ordem. O ambiente isolado precisa existir e alcançar os endpoints privados antes da execução. Nenhum passo promove automaticamente um pacote recuperado no ambiente ativo.

## Códigos e segurança

| Código | Significado |
|---|---|
| `0` | Portão ou operação concluída e comprovada |
| `2` | Argumento/configuração inválido |
| `3` | Pré-voo ou prova de storage falhou; provisionamento/publicação bloqueado |
| `4` | Azure, banco ou armazenamento indisponível; repetição pode ser segura |
| `5` | Divergência de imagem, backup, modelo ou catálogo |
| `6` | Falha em persistir evidência/manifesto/release |
| `7` | Operação protegida recusada, incluindo rollback sem alvo válido |
| `8` | Lock de mutação indisponível |

Credenciais só entram por OIDC, secret refs e variáveis de ambiente de runtime. `--output` contém somente paths locais de evidência; relatórios e logs não incluem valores de segredo, URL com usuário/senha, payload de previsão ou path absoluto de host. A API HTTP existente não muda seu contrato de [OpenAPI da feature 001](../../001-train-container-metrics/contracts/openapi.yaml).
