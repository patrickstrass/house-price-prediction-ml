# Preparação para Azure for Students

**Estado:** infraestrutura, scripts e contratos da feature 003 são preparados e validados
localmente. Nenhum recurso foi criado, nenhuma imagem foi publicada no ACR e nenhum workflow Azure
foi executado. O custo Azure desta preparação é US$ 0. Os valores de crédito, preço e cota reais
continuam desconhecidos.

## Arquitetura preparada

- Azure Container Apps para inferência HTTPS e MLflow interno; jobs manuais para backup,
  migrações, reconciliação, verificação e treino. GitHub Actions continua o orquestrador semanal.
- Flexible Server PostgreSQL privado para `house_price`, `mlflow` e `optuna`, com TLS e papéis
  separados. Grafana externo precisa de conexão privada/túnel e reader limitado ao schema
  `observability`; não abrir o banco ao público.
- Azure Files clássico separado para artefatos MLflow, registry e backup/evidência. O filesystem
  do contêiner não contém o campeão autoritativo. Inferência monta registry RO.
- Imagens de CI com tag única e digest registrado; ACR push e deploy ficam desabilitados até uma
  operação futura. Segredos vêm de Key Vault/OIDC, nunca de `.env` versionado.

Os arquivos estão em [`infra/azure/`](../infra/azure/README.md). A
[matriz Compose](../specs/003-deploy-azure-containers/compose-feasibility.md) registra os
componentes sem equivalência direta no importador.

## Validação local desta preparação

Em 2026-10-25, `uv sync --frozen --extra dev`, `uv lock --check`, Ruff,
`validate_grafana_queries.py`, `docker compose config --quiet`, a compilação de
`infra/azure/main.bicep`, 133 testes unitários/de contrato, 76 testes de integração
sem Docker (incluindo PostgreSQL real para migração e leitor Grafana), quatro testes
de performance, build local das três imagens, três testes de segurança dos contêineres
e o ensaio de persistência Compose passaram. Este último fez dois treinos e cinco
recriações, preservando campeão, métricas, catálogo e artefatos. Os testes Azure usam
filesystem e clientes simulados:
validam gates e formatos, mas não comprovam rede, mount, permissão, preço, crédito,
cota, alertas ou tempos de resposta da assinatura. O workflow completo do GitHub
precisa passar em execução protegida antes da publicação futura.

O fluxo semanal Azure registra IDs e estados terminais dos jobs, passa um UUID estável
ao trainer e coleta eventos estruturados de backup e treino para evidência. A coleta
via `az containerapp job logs show` usa extensão CLI em preview e precisa de ensaio
na assinatura; falta de evento falha fechada. O orquestrador para MLflow antes do backup,
restaura o app antes da reconciliação e mantém lock do catálogo durante a captura.
`azure_backup.py restore` tem sequência de três bancos, duas shares, reconcile/verify e
saúde/previsão para um alvo isolado previamente criado; nenhum restore real Azure foi
feito. O [quickstart](../specs/003-deploy-azure-containers/quickstart.md) lista os
endpoints e os portões do ensaio futuro.

## Portões pendentes antes de qualquer deploy

1. **Feature 002:** revisar `specs/002-persist-model-mlflow/tasks.md` e executar o checkpoint
   final de publicação protegida, reconciliação, remoção segura e restore. A lista ainda contém
   tarefas não marcadas; nenhuma prova Azure pode substituir esse portão.
2. **Conta e custo:** confirmar oferta Azure for Students, região, provedores, cotas, saldo e
   preços com horário de consulta. O pré-voo exige uma estimativa de 30 dias por compute,
   requisições, PostgreSQL, ACR, shares, backup, logs e rede; margem + estimativa deve caber no
   saldo e no teto operacional. Budget apenas alerta e pode atrasar.
3. **Storage real:** provar `st_uid` 10001/10002, `os.replace`, `fsync` de arquivo/diretório,
   colisão imutável, leitores simultâneos, falha injetada, RO da inferência e cinco recriações
   no mount ACA. O probe local permanece `pending`; não publicar com ele.
4. **Identidade/rede:** verificar Key Vault RBAC e permissões mínimas dos jobs/OIDC, private
   endpoint Files, DNS privado, PostgreSQL `verify-full`, ACR sem admin, ingressos e logs sem
   segredos. O Bicep compilar não comprova conectividade.
5. **Operação:** backup com três dumps e duas cópias do mesmo conjunto; restore isolado,
   reconciliação, health/predict, rollback cronometrado, custo observado e alertas de falha
   semanal, indisponibilidade e qualidade. Nenhum desses ensaios foi realizado em Azure.

## Grafana e ensaio operacional pendente

O Grafana externo precisa chegar ao PostgreSQL por rede privada ou túnel protegido, com TLS
`verify-full`. O papel `grafana_reader` recebe somente `SELECT` no schema `observability`.
Falha de conexão, DNS ou certificado bloqueia a aceitação da implantação futura.

| Alerta | Fonte SQL | Janela e limite configurados | Ensaio futuro |
|---|---|---|---|
| Ciclo semanal atrasado | `observability.grafana_training_status` | Última execução aprovada há mais de 8 dias | Simular atraso e conferir alerta em até 5 min após o limite. |
| Inferência indisponível | `observability.grafana_inference_operations` | Sem heartbeat `ready` nos últimos 5 min; `No Data` alerta | Suspender réplica de teste e conferir alerta em até 5 min após o limite. |
| Qualidade | `observability.grafana_candidate_comparison` | Ao menos um candidato não elegível em 24 h | Publicar evidência de candidato rejeitado e conferir alerta em até 5 min. |

As regras são avaliadas a cada minuto, com persistência de 1 minuto. O ensaio SC-006
deve registrar horário do evento, do cruzamento do limite, da avaliação e do recebimento;
uma simulação local da regra não mede latência do Grafana ou da rede em Azure.

O envelope local inicial de 4 CPUs, 8 GiB RAM e 10 GiB armazenamento serve apenas ao benchmark
local. Região, SKU e preço Azure devem ser escolhidos depois do pré-voo, sem presumir que o
crédito estudantil suporte os serviços.

## Revisão estática de segurança e custo

`main.bicep`, apps, jobs, storage, PostgreSQL, identidade e workflows foram revistos e compilados
localmente. Somente inferência tem ingresso externo; PostgreSQL e Files desabilitam rede pública;
MLflow tem ingresso interno; o ACR mantém endpoint de rede público apenas para o push autenticado
do runner GitHub, sem usuário admin ou pull anônimo. Imagens de produção exigem tag única de CI
e digest conferido no ACR. Apps e jobs usam identidades separadas e referências Key Vault por
segredo. URLs com senha e senha de administrador não estão nos parâmetros versionados; a senha
admin entra como parâmetro Bicep `@secure`. O pré-voo exige estimativas para compute, requisições,
PostgreSQL, ACR, shares, backup, logs e rede. Budget envia notificações e não impõe hard cap.

O ponto que depende de prova Azure é a montagem Files com UID e atomicidade corretos. A coleta
de logs de ACA Job, o stop/start do MLflow durante backup e permissões OIDC/RBAC também precisam
ser validados na assinatura real antes de ativar o workflow semanal. Nenhuma destas provas foi
substituída pela compilação ou pelos testes simulados.

Na revisão final, `git diff --check`, parsing dos dois workflows, compilação de `main.bicep`
e os gates locais da seção 1 do quickstart passaram. A revisão de `main.bicep`, apps,
jobs, `azure_release.py`, `azure_apply.py`, workflow semanal e operações confirmou os
controles acima. O rollback aguarda no máximo 13 minutos pelo job, reservando tempo
dentro do objetivo de 15 minutos para ativação da revisão e verificação HTTP. O tempo
real de rollback ainda requer ensaio cronometrado em Azure. Após essa alteração,
133 testes unitários/de contrato, 76 de integração e quatro de performance passaram;
o ensaio Docker de persistência e os três testes de segurança dos contêineres também
passaram localmente. Nenhum comando de login, provisionamento ou publicação foi executado.

## Mudanças futuras

Toda mudança de fonte externa, esquema, contrato `/v1/predictions`, limite de promoção ou Bicep
deve registrar impacto, atualizar os contratos afetados e executar testes de dados, API,
catálogo, armazenamento, CI e rollback conforme o caso antes da implantação. O registro de
release deve ligar imagem/digest, configuração, modelo, MLflow run e checksum do dataset.
