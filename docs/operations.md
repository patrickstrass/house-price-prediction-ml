# Operação local

## Perfil de referência

O perfil usado pelos SLOs possui Linux, 4 CPUs, 8 GiB de RAM, 10 GiB livres, Docker Engine e
Compose v2. O tempo de inicialização considera imagens e dependências previamente baixadas. Registre
CPU, memória, disco e duração produzidos por `tests/performance/test_end_to_end_slo.py` antes de
comparar ambientes diferentes.

O envelope do Compose limita `trainer` a 2 CPUs/3 GiB, PostgreSQL a 0,75 CPU/1,5 GiB, MLflow a
0,75 CPU/2,5 GiB e inferência a 0,5 CPU/1 GiB. Os três volumes compartilham o orçamento mínimo de 10 GiB do host;
monitore o uso antes de reter novas execuções.

## Ciclo de vida dos volumes

Os volumes externos possuem nomes estáveis: `house-price-postgres-data`,
`house-price-mlflow-artifacts` e `house-price-model-registry`. Crie e verifique sem alterar dados:

```bash
./scripts/manage_persistent_storage.sh create
./scripts/manage_persistent_storage.sh inspect
```

Somente após backup validado e parada da stack, remova-os com
`./scripts/manage_persistent_storage.sh destroy --confirm destroy`. O script recusa volumes sem o
rótulo gerenciado. Reserve ao menos 10 GiB somados e alerte antes de 80% de uso.

## Componentes e portas

- PostgreSQL: publicado somente em `127.0.0.1:${POSTGRES_HOST_PORT}` para Grafana no mesmo host;
- inferência: `127.0.0.1:${INFERENCE_HOST_PORT}`;
- MLflow: publicado somente em `127.0.0.1:${MLFLOW_HOST_PORT}` para inspeção local;
- trainer e rede de treinamento: sem publicação externa;
- Grafana: serviço externo, no mesmo host. Acesso remoto ao PostgreSQL exige túnel ou TLS e firewall.

As quatro senhas são fornecidas por `.env` local ou GitHub Secrets. Nunca registre `.env`, dumps,
payloads de imóveis ou credenciais em logs e artifacts.

## Backup

Faça backup consistente antes de manutenção:

```bash
mkdir -p backup
for database in house_price mlflow optuna; do
  docker compose exec -T postgres pg_dump --format=custom --username "$POSTGRES_USER" "$database" > "backup/${database}.dump"
done
docker run --rm -v house-price-model-registry:/source:ro -v "$PWD/backup:/backup" alpine:3.22 tar czf /backup/model-registry.tar.gz -C /source .
docker run --rm -v house-price-mlflow-artifacts:/source:ro -v "$PWD/backup:/backup" alpine:3.22 tar czf /backup/mlflow-artifacts.tar.gz -C /source .
sha256sum backup/* > backup/SHA256SUMS
```

## Restore

Valide `sha256sum -c backup/SHA256SUMS` e restaure nesta ordem: pare escritores; restaure os três
bancos; restaure `mlflow-artifacts`; restaure `model-registry`; execute `mlflow-migrate`; inicie
MLflow; execute `catalog-reconcile`; e rode `model-catalog verify --probe-artifacts --json`. Só
então libere novo treinamento. A inferência pode continuar servindo um campeão local íntegro durante
indisponibilidade do catálogo. O workflow semanal automatiza essa sequência.

Antes de qualquer migração, retenha dumps e checksums. Para rollback, pare escritores, restaure o
dump e volumes do mesmo conjunto, execute a versão anterior das imagens e verifique o campeão. Não
faça downgrade destrutivo do catálogo sobre dados que não estejam em backup.

## Verificação, reconciliação e resolução

`model-catalog verify` é read-only. `reconcile --mode check` apenas relata; `--mode apply` usa lock,
adota versões íntegras e nunca promove. Depois de restore, divergência de alias permanece explícita.
Repare apenas em direção ao pacote já servido e validado:

```bash
model-catalog resolve-champion --prefer volume --apply --confirm VERSION --json
```

Falha parcial mantém o campeão anterior. Código 8 indica lock ocupado e deve ser repetido com
backoff; códigos 4/6 indicam dependência/persistência e exigem correção antes de nova mutação.

## Remoção protegida

`model-catalog delete --model-version VERSION --json` mostra impacto sem mutar. A aplicação exige
`--apply --confirm VERSION`, recusa ponteiro/alias champion, último modelo íntegro e mutação ativa,
cria tombstone e move o pacote para `.trash`. A remoção física é separada e irreversível:

```bash
model-catalog purge --deletion-id UUID --apply --confirm UUID --json
```

Não copie manualmente um pacote tombstonado de backup: a reconciliação o mantém em conflito. Para
falha após trash, repita `delete` com a mesma versão; a operação converge sem mover novamente.

## Rollback

Cada manifesto contém `previous_model_version`. Para rollback, valide o pacote anterior, atualize o
alias MLflow `champion` e substitua `current.json` atomicamente pelo ponteiro anterior. Confirme
`/health/ready`, `/v1/model` e uma previsão. Se qualquer validação falhar, restaure alias e ponteiro
originais antes de liberar o lock de promoção.

## Alertas e diagnóstico

O Grafana avalia a cada minuto:

- última execução semanal com mais de oito dias;
- ausência de heartbeat `ready` por cinco minutos, incluindo `No Data`;
- candidatos que violaram os gates de R², MAE ou RMSE.

Correlacione incidentes por `training_run_id`, `mlflow_run_id`, `model_version`,
`environment_version` e `request_id`. A inferência nunca registra atributos enviados pelo usuário.

## Remoção de dados

`docker compose down` preserva volumes externos. Mesmo `down -v` não é o procedimento de remoção;
use apenas o script gerenciado acima após backup validado e autorização explícita.

## Implantação Azure futura

A feature 003 prepara a [infraestrutura](../infra/azure/README.md) e o
[guia de validação](../specs/003-deploy-azure-containers/quickstart.md), sem criar recursos.
Antes de qualquer ação paga, execute o pré-voo da assinatura com preço, cota e crédito atuais,
revise `what-if` do Bicep e confirme o checkpoint da feature 002. O template cria um budget com
avisos em 80% e 100% do teto; esses avisos não limitam automaticamente o consumo.

Após provisionamento separado, faça a prova do registry no mount ACA e mantenha bloqueada a
publicação enquanto seu estado for `pending` ou `failed`. Implante imagens aprovadas com tag única,
confirme digest, saúde, previsão e versão servida; teste cinco recriações e rollback em até 15
minutos. O backup precisa conter dumps de `house_price`, `mlflow`, `optuna`, cópia de artefatos e
cópia do registry com um `backup_set_id` comum. Restaure sempre em alvo isolado e verifique
checksums, alias, ledger e ponteiro antes de liberar novo treino.
No ciclo Azure, `azure_jobs.py` espera MLflow parar antes do backup e o reinicia após a migração;
o job de backup segura o lock do catálogo durante dumps e cópias. A coleta de evidência de jobs
usa `az containerapp job logs show`, comando CLI em preview que deve ser validado no ambiente
real. O evento de treino inclui identidade imutável dos dados, métricas/parâmetros por família,
decisão e versão efetiva. A falta de evidência terminal impede declarar sucesso.

O workflow `.github/workflows/azure-weekly.yml` tem apenas gatilho manual e é ignorado enquanto
`AZURE_DEPLOYMENT_ENABLED` não for `true` no ambiente GitHub protegido. Quando a implantação estiver
validada, desative a agenda de `.github/workflows/weekly-training.yml` antes de adicionar agenda
semanal ao workflow Azure. Configure OIDC e permissões mínimas; não use secrets longos no GitHub.

Para Grafana externo, use `grafana/datasource.azure.example.yaml`, conectividade privada ou túnel
protegido ao PostgreSQL e o papel
`grafana_reader` apenas para leitura de `observability`. Confirme alertas de ciclo semanal atrasado,
ausência de heartbeat e violação de R²/MAE/RMSE; a regra de indisponibilidade deve tratar `No Data`.
Registre fonte, janela, limite, destinatário e hora de recebimento. Azure Cost Management pode
atrasar mais que os alertas operacionais, portanto não use budget para SLO de cinco minutos.

Ao desligar, capture e verifique um backup correlacionado, registre release/digests, pare os jobs e
apps, consulte custo residual de PostgreSQL, storage, ACR, logs, rede e Key Vault e só então planeje
remoção de recursos em operação administrativa separada. Para recuperação, restaure bancos,
artefatos e registry do mesmo backup, execute migrações/reconcile/verify e valide uma previsão.
`scripts/azure_backup.py stage` só prepara o conjunto; `restore` exige ambiente isolado,
três URLs de banco e endpoint MLflow isolados, executa `pg_restore`, materializa as duas shares,
verifica catálogo e consulta saúde/previsão. `scripts/azure_release.py rollback` usa o job
privado `release-rollback` para restaurar campeão/alias/ponteiro sob lock antes de ativar a revisão
anterior; falha se o pacote anterior não estiver íntegro.

