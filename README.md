# House Price Prediction MLOps

Pipeline MLOps reproduzível para o dataset California Housing. O projeto compara regressão linear,
Ridge, Lasso, árvore de decisão e floresta aleatória, otimiza hiperparâmetros com Optuna, registra
experimentos no MLflow e publica pacotes de modelo imutáveis em um volume Docker persistente.

O serviço FastAPI utiliza o campeão do volume, enquanto PostgreSQL fornece uma projeção estável para
um Grafana externo. GitHub Actions executa CI e treinamento semanal. A implantação Azure for
Students está documentada como próximo incremento e não gera custos nesta feature.

## Contêineres e dados

| Serviço Compose | O que faz | Quando roda | Dados persistentes |
|---|---|---|---|
| `postgres` | Guarda os bancos `house_price`, `mlflow` e `optuna`. Cria os papéis e schemas iniciais quando o volume está vazio. | Contínuo; os demais serviços aguardam seu healthcheck. | `postgres-data` |
| `mlflow-migrate` | Salva um dump do banco MLflow e aplica as migrações do tracking server antes de iniciá-lo. | Uma execução por inicialização do MLflow. | Dump em `weekly-output/` |
| `mlflow` | Recebe as runs, métricas e artefatos de treino; expõe a interface local na porta 5000. | Contínuo, depois de `mlflow-migrate`. | Banco `mlflow` e volume `mlflow-artifacts` |
| `catalog-reconcile` | Verifica o pacote no registry, o alias `champion` no MLflow e o catálogo PostgreSQL; reconcilia estados recuperáveis e falha diante de conflito. | Uma execução antes do `trainer`. | Banco `house_price` e `model-registry` |
| `trainer` | Valida o dataset offline, testa cinco famílias de modelos com Optuna, registra as runs e só publica o vencedor se passar os critérios de promoção. | Sob demanda com `docker compose run --rm trainer`. | Banco `optuna`, MLflow, catálogo e `model-registry` |
| `inference` | Serve a API de previsão usando o campeão publicado; lê o registry sem permissão de escrita. | Contínuo; `/health/ready` só fica pronto após existir um campeão válido. | Lê `model-registry` em modo somente leitura |

Grafana fica fora do Compose e consulta o schema `observability` com `grafana_reader`.

Os volumes nomeados são `postgres-data`, `mlflow-artifacts` e `model-registry`. Não execute
`docker compose down -v` durante a operação normal.

Antes do primeiro `up`, crie os volumes externos estáveis com
`./scripts/manage_persistent_storage.sh create`. Inspecione-os com `inspect`; a destruição
coordenada exige backup e o token explícito `destroy --confirm destroy`.

A interface do MLflow fica disponível apenas no host local em <http://127.0.0.1:5000>.

## Requisitos

- Docker Engine e Compose v2;
- para desenvolvimento: Python 3.12–3.14 e `uv`;
- referência de SLO: Linux, 4 CPUs, 8 GiB RAM e 10 GiB livres.

## Execução local

```bash
cp .env.example .env
# Substitua todos os valores changeme em .env.
set -a
source .env
set +a
mkdir -p weekly-output
./scripts/manage_persistent_storage.sh create
docker compose config --quiet
docker compose build trainer catalog-reconcile inference mlflow mlflow-migrate
docker compose up -d --wait postgres mlflow
curl --fail http://127.0.0.1:5000/health
docker compose run --rm --entrypoint alembic \
  -e MIGRATION_DATABASE_URL="$MIGRATION_DATABASE_URL" \
  trainer upgrade head
docker compose up -d inference
docker compose run --rm trainer
```

O roteiro acima acontece nesta ordem:

1. `cp` cria a configuração local a partir do exemplo. Preencha as senhas em `.env`; `source`
   exporta essas variáveis para que o Compose consiga montar as URLs dos três bancos.
2. `mkdir` cria o diretório de dumps e evidências. O script de storage cria os três volumes
   externos antes do Compose; eles continuam existindo após `docker compose down`.
3. `docker compose config --quiet` valida a sintaxe e as variáveis obrigatórias. `build` cria
   as imagens locais dos cinco serviços com Dockerfile. `trainer` e `catalog-reconcile` usam
   o mesmo Dockerfile, mas o Compose guarda imagens separadas para eles; reconstrua ambos
   quando mudar código de catálogo ou tracking.
4. `up -d --wait postgres mlflow` inicia PostgreSQL, executa `mlflow-migrate` como dependência
   e aguarda MLflow saudável. O `curl` seguinte confirma que seu endpoint `/health` responde.
5. `alembic upgrade head` aplica as migrações do catálogo e da observabilidade em uma execução
   temporária da imagem `trainer`, usando a URL de migração. O contêiner é removido ao terminar.
6. `up -d inference` inicia a API na porta local 8000. Ela pode estar **não pronta** até o
   primeiro modelo ser promovido; isso é esperado.
7. `run --rm trainer` executa primeiro a dependência `catalog-reconcile`, depois faz um treino
   único e remove o contêiner temporário. Em caso de aprovação, publica o campeão no volume;
   a inferência passa a atendê-lo sem reconstruir sua imagem.

**Build não é treino.** `docker compose build trainer` prepara uma imagem reutilizável: instala
as dependências Python e copia código, migrações e o snapshot offline para dentro dela. Nenhum
modelo é ajustado nesse passo. `docker compose run --rm trainer` cria um contêiner temporário a
partir dessa imagem e executa o treinamento de fato. Faça o build antes do primeiro treino e
novamente quando alterar código, dependências, Dockerfile ou dataset. Se a mudança afetar o
catálogo ou o MLflow, reconstrua também `catalog-reconcile` ou `mlflow-migrate`. Treinos seguintes
com as mesmas imagens não precisam de novo build; cada `run` inicia uma nova execução de treino.

Para acompanhar ou encerrar os serviços contínuos, use `docker compose ps`,
`docker compose logs -f postgres mlflow inference` e `docker compose down`.
O comando `down` preserva os volumes externos. A primeira inicialização do PostgreSQL cria
bancos e papéis; mudar senhas no `.env` depois disso não altera automaticamente as senhas
já gravadas no banco.

O job one-shot `catalog-reconcile` aplica a migração do catálogo, verifica registry e artifact
proxy e reconcilia conteúdo antes de liberar o trainer. Consultas e manutenção usam:

```bash
docker compose run --rm --entrypoint model-catalog trainer list --json
docker compose run --rm --entrypoint model-catalog trainer verify --probe-artifacts --json
docker compose run --rm --entrypoint model-catalog trainer reconcile --mode check --json
```

Depois do primeiro campeão:

```bash
curl --fail http://localhost:8000/health/ready
curl --fail http://localhost:8000/v1/model
curl --fail --request POST http://localhost:8000/v1/predictions \
  --header 'Content-Type: application/json' \
  --data '{"instances":[{"MedInc":8.3252,"HouseAge":41,"AveRooms":6.9841,"AveBedrms":1.0238,"Population":322,"AveOccup":2.5556,"Latitude":37.88,"Longitude":-122.23}]}'
```

## Desenvolvimento e testes

```bash
uv sync --frozen --extra dev
uv run ruff check src scripts migrations tests
uv run pytest tests/unit tests/contract
uv run pytest tests/integration
./scripts/validate_quickstart.sh
```

O snapshot `data/california_housing.parquet` é versionado por manifesto e checksum. Para regenerá-lo
deliberadamente durante manutenção:

```bash
uv run python scripts/build_dataset_snapshot.py --output-dir data
```

Treinamentos normais não fazem download nem acessam a internet.

## MLflow e promoção

### Como o treinamento offline funciona

“Offline” significa que **o treino não baixa dados durante a execução**. O snapshot
`data/california_housing.parquet` e o manifesto `data/dataset-manifest.json` já estão no
repositório e são copiados para a imagem `trainer`. O `source_uri` do manifesto documenta a
origem do California Housing; o processo de treino não o consulta. MLflow, Optuna e PostgreSQL
continuam sendo serviços locais necessários para registrar o experimento.

Ao executar `docker compose run --rm trainer`, o processo segue estas etapas:

1. Confere se catálogo, alias MLflow e ponteiro `current.json` estão coerentes antes de treinar.
2. Verifica o SHA-256 do Parquet contra o manifesto, 20.640 linhas, as oito colunas de entrada
   na ordem esperada, o alvo `MedHouseVal`, tipos numéricos e ausência de valores nulos,
   infinitos ou linhas duplicadas. Dataset inválido encerra o comando antes do ajuste de modelos.
3. Separa dados de treino e holdout com embaralhamento e semente fixa (`42` por padrão).
   O holdout usa 33% das linhas por padrão e não participa da busca de hiperparâmetros.
4. Com Optuna, faz validação cruzada de cinco folds **somente nos dados de treino**. Compara
   regressão linear, Ridge, Lasso, árvore de decisão e floresta aleatória. Os orçamentos padrão
   são, respectivamente, 4, 12, 12, 16 e 10 trials; estudos e trials ficam no banco `optuna`.
   Na linear, os quatro trials cobrem todas as combinações de `fit_intercept` (sim/não) e
   `positive` (coeficientes obrigatoriamente não negativos: sim/não). Ela não usa taxa de
   aprendizado porque `LinearRegression` ajusta os coeficientes por mínimos quadrados.
5. Ajusta o melhor conjunto de parâmetros de cada família nos dados de treino e mede todos os
   cinco finalistas no mesmo holdout. Registra MAE, MSE, RMSE, R², parâmetros, gráficos e modelo
   no MLflow, além das projeções para o Grafana no banco `house_price`.
6. Aplica os critérios de promoção: R² ≥ 0,50 e, se já há campeão, MAE e RMSE não podem piorar
   mais de 5%. O desempate usa RMSE, MAE, R² e a ordem das famílias. Sem candidato elegível,
   termina com `succeeded_no_promotion` e mantém o campeão anterior.
7. Valida o pacote vencedor e, quando há promoção, publica uma nova versão no
   `model-registry`, atualiza o alias MLflow `champion` e troca `current.json` atomicamente.
   A API `inference` lê esse ponteiro e passa a servir a versão aprovada.

Na interface MLflow, a run principal registra `selection.winner_family`,
`selection.decision` e `selection.effective_model_version`, além da métrica
`selection.winner_holdout_rmse`. Cada run de família recebe
`selection.winner=true|false`; o artefato `selection/family-comparison.json` lista os cinco
resultados, elegibilidade e motivos de rejeição. Quando há promoção, o alias `champion` do
Model Registry aponta para a versão aprovada. Uma família pode vencer a comparação da execução
sem substituir o campeão anterior se a publicação for equivalente ou rejeitada.

O comando aceita opções como `--seed`, `--test-fraction`, `--cv-folds`, `--timeout-seconds` e
`--trial-budget familia=quantidade`. Por exemplo, para um ensaio local menor:

```bash
docker compose run --rm trainer train --timeout-seconds 300 \
  --trial-budget ridge=2 --trial-budget lasso=2 \
  --trial-budget decision_tree=2 --trial-budget random_forest=2
```

O exemplo reduz algumas buscas, mas ainda treina as cinco famílias. A saída final informa
`training_run_id`, `status`, `promoted` e `model_version`; confira os detalhes de cada tentativa
no MLflow em <http://127.0.0.1:5000>. Execuções novas recebem um UUID gerado pelo trainer;
`--run-id` permite fornecer um UUID explícito para correlacionar uma execução automatizada.

Cada execução cria uma run pai, cinco runs de família e trials aninhados. Os cinco finalistas usam o
mesmo holdout e registram MAE, MSE, RMSE e R².

O pacote é validado em staging, movido para `models/<model_version>`, associado ao alias MLflow
`champion` e publicado por troca atômica de `current.json`. Falhas compensam o alias. Um resultado
idêntico ao campeão não é promovido novamente.

### Se experimentos forem excluídos no MLflow

Excluir um experimento pela interface do MLflow pode deixá-lo no estado `deleted`; seu nome
continua ocupado. Se `house-price-training-v2` ou `house-price-model-reconciliation` estiver
assim, `catalog-reconcile` e `trainer` falham ao tentar ativá-los. `docker compose run --rm trainer`
não restaura experimentos automaticamente. Para recuperar um experimento excluído de forma
reversível, confirme seu nome e estado e restaure **o mesmo ID** pela API do MLflow. Não remova
volumes para resolver esse erro: isso apagaria também bancos, modelos e histórico persistente.
Se os dados foram removidos permanentemente, use um backup correlacionado antes de tentar treinar.

Com o serviço `mlflow` saudável, este comando restaura somente os dois experimentos esperados
que estejam no estado `deleted`:

```bash
docker compose exec -T mlflow python - <<'PY'
from mlflow import MlflowClient

client = MlflowClient(tracking_uri="http://127.0.0.1:5000")
for name in ("house-price-training-v2", "house-price-model-reconciliation"):
    experiment = client.get_experiment_by_name(name)
    if experiment is None:
        print(f"{name}: não existe")
    elif experiment.lifecycle_stage == "deleted":
        client.restore_experiment(experiment.experiment_id)
        print(f"{name}: restaurado (ID {experiment.experiment_id})")
    else:
        print(f"{name}: {experiment.lifecycle_stage} (ID {experiment.experiment_id})")
PY
```

## Grafana externo

Use `grafana/datasource.example.yaml` com `grafana_reader` e importe
`grafana/dashboards/house-price-ml.json`. Os painéis exibem MAE/MSE/RMSE/R², candidatos, convergência
Optuna, estados, saúde, volume, erros e p50/p95/p99.

As regras em `grafana/alerts/house-price-alerts.yaml` cobrem execução semanal atrasada, ausência de
heartbeat da inferência e violações de qualidade. O padrão local publica PostgreSQL somente em
`127.0.0.1`; acesso remoto exige túnel ou TLS e firewall.

## Automação semanal

`.github/workflows/weekly-training.yml` usa agenda semanal e execução manual. Em runner hospedado,
restaura o último `weekly-state`, valida o snapshot, treina, cria backup e publica evidência por 90
dias. Configure os secrets:

- `MLOPS_POSTGRES_PASSWORD`;
- `MLOPS_MIGRATION_PASSWORD`;
- `MLOPS_PIPELINE_PASSWORD`;
- `MLOPS_GRAFANA_PASSWORD`.

## Operação e Azure

- [Operação, backup, restore e rollback](docs/operations.md)
- [Premissas para Azure for Students](docs/azure-deployment-assumptions.md)
- [Preparação declarativa do Azure Container Apps](infra/azure/README.md)
- [Validação local e roteiro de implantação futura](specs/003-deploy-azure-containers/quickstart.md)
- [Especificação e contratos](specs/001-train-container-metrics/)
- [Persistência e catálogo](specs/002-persist-model-mlflow/)
