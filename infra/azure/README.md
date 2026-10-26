# Azure Container Apps: preparação

Esta pasta contém templates para implantação **futura**. A implementação da feature 003 valida
estes arquivos localmente e não executa `az login`, `az deployment`, `az acr`, jobs ou alterações
na assinatura. `compose.yaml` continua sendo o ambiente local. A [matriz de paridade](../../specs/003-deploy-azure-containers/compose-feasibility.md)
explica por que o importador Compose não equivale à stack completa.

## Inventário

| Arquivo | Recurso/função |
|---|---|
| `main.bicep` | VNet, subnets, ACA environment, ACR e composição dos módulos |
| `identity.bicep` | identidades por app/job, OIDC do GitHub, ACR pull/push e leitura de segredos restrita por segredo |
| `storage.bicep` | shares separadas para artefatos, registry e backup, montagens RW/RO e endpoint privado Files |
| `postgres.bicep` | Flexible Server privado, DNS privado e bancos `house_price`, `mlflow`, `optuna` |
| `container-apps.bicep` | inferência HTTPS externa e MLflow interno |
| `jobs.bicep` | backup, migração, reconciliação, verify, treino e sincronização de release com gatilho manual |
| `monitoring.bicep` | budget, destinatário de custo, workspace de logs e IDs das três regras operacionais Grafana |
| `parameters.example.json` | forma dos parâmetros; valores `REPLACE` não são implantáveis |

**Atenção antes do primeiro deploy**: revisar cotas e custo de cada recurso na assinatura real;
preencher IDs, SKUs, digests, Key Vault e parâmetros seguros; confirmar que a identidade runtime
tem `Key Vault Secrets User` e acesso ao storage, e que GitHub OIDC tem permissão mínima de
deploy e ACR push. `identity.bicep` cria apenas a federação e o ACR push da identidade GitHub;
o papel necessário para implantar no resource group deve ser vinculado externamente, com escopo
restrito, pelo administrador. Cada app/job tem identidade própria e acesso só aos segredos Key
Vault que consome. A compilação Bicep não confirma essas permissões. O template usa chave da
conta para montar Azure Files no ACA; o acesso ao mount é feito pela plataforma ACA, então
os processos não recebem papel de dados de storage nem chave da conta. Validar o suporte da
região/versão a montagem por identidade e migrar para ela se disponível. A conta bloqueia rede
pública e usa endpoint privado para Files;
verificar DNS e conexão do ACA antes de liberar jobs. Não versionar senha de administrador nem
URLs de banco com credenciais: fornecer `postgresAdminPassword` como parâmetro seguro no momento
do deploy e URLs por Key Vault. Nunca use o JSON de exemplo sem substituir valores `REPLACE`.

## Validação local

```bash
bicep build infra/azure/main.bicep --outfile /tmp/house-price-azure-template.json
python -m json.tool infra/azure/parameters.example.json >/dev/null
uv run pytest tests/contract/test_azure_evidence_schemas.py tests/contract/test_azure_preflight.py
```

O script `scripts/azure_apply.py` verifica o relatório de pré-voo por padrão e **não cria
recursos** sem `--execute`. O relatório vem de fatos datados fornecidos pelo operador (assinatura,
provedores, cotas, crédito e orçamento). A oferta estudantil, cotas e preços não foram verificados
na conta real nesta feature. O probe local também não substitui o ensaio do registry no mount ACA:
UID, `fsync`, `os.replace`, leitores simultâneos, falha injetada, RO e cinco recriações precisam
passar antes de liberar publicação. O release script exige resultado `passed` desses checks.

## Sequência para uma operação futura

1. Completar a feature 002 e confirmar backups/restauração e catálogo.
2. Consultar a assinatura por acesso somente leitura; registrar crédito real, cotas e estimativa
   de 30 dias por recurso. Gerar `azure-preflight.json` conforme o contrato da feature 003.
3. Preparar Key Vault, DNS, papéis, imagens aprovadas e parâmetros. Revisar `what-if` antes de
   invocar `scripts/azure_apply.py --execute` em resource group de teste.
4. Fazer probe no mount Azure Files real. Se houver falha de atomicidade, UID ou RO, parar e
   redesenhar o store de modelos antes de continuar.
5. Implantar revisão, verificar digest, saúde, modelo e previsão. Executar cinco recriações e
   rollback de ensaio; registrar tempo e versão. Após `azure_release.py verify --execute`, executar
   o job manual `release-sync` com `RELEASE_JSON` do release verificado e confirmar a gravação de
   `/backup/current-release.json` antes de iniciar o workflow semanal. Este registro não contém
   credenciais; o job de backup recusa começar sem ele.
6. Fazer backup correlacionado e restore isolado. Só então habilitar `AZURE_DEPLOYMENT_ENABLED`
   no ambiente protegido GitHub. Desativar a agenda local em `weekly-training.yml` antes de
   adicionar uma agenda a `azure-weekly.yml`; manter um único ciclo semanal.
7. Confirmar Grafana por conexão privada, alertas, crédito restante e procedimento de desligamento.

O [guia de validação](../../specs/003-deploy-azure-containers/quickstart.md) detalha os comandos,
resultados esperados e portões. Budget Azure envia alertas e não interrompe recursos.
As regras operacionais são provisionadas pelo Grafana a partir de
`grafana/alerts/house-price-alerts.yaml` e consultam `observability` no PostgreSQL por conexão
privada. Bicep expõe os IDs e o workspace correlacionado; não instala um Grafana público.
