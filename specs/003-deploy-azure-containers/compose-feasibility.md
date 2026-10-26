# Viabilidade de Docker Compose na Azure

**Verificado em:** 2026-10-25. Esta é uma análise documental e do `compose.yaml` atual; não houve importação em uma assinatura Azure.

## Resultado

Se "Azure Container Services" significar o antigo produto **Azure Container Service (ACS)**, ele foi [descontinuado em 1º de fevereiro de 2020](https://learn.microsoft.com/en-us/lifecycle/products/azure-container-service). Esta feature usa a família atual de serviços Azure e avalia **Azure Container Apps** como destino preferencial.

O Azure Container Apps oferece o comando oficial e GA [`az containerapp compose create`](https://learn.microsoft.com/en-us/cli/azure/containerapp/compose?view=azure-cli-latest), que importa uma especificação Compose para criar um ou mais Container Apps. Portanto, existe importação direta de *parte* de um Compose. Ela não significa executar `docker compose up` na Azure nem garante equivalência integral com este projeto.

O `compose.yaml` atual **não deve ser importado sem adaptação**. A [orientação da equipe Azure para o importador Compose](https://techcommunity.microsoft.com/blog/appsonazureblog/accelerating-azure-container-apps-with-the-azure-cli-and-compose-files/3516636) documenta ausência de suporte a `build` e mounts de volume no fluxo de importação. O arquivo atual depende de `build`, três volumes externos duráveis, um bind mount de backup, jobs de migração/reconciliação/treino, `depends_on` com portões de saúde e redes internas. A documentação atual do comando confirma criação de Container Apps; [jobs agendados/manuais](https://learn.microsoft.com/en-us/azure/container-apps/jobs) e [montagens persistentes](https://learn.microsoft.com/en-us/azure/container-apps/storage-mounts) têm configuração própria. Assim, o importador pode auxiliar um serviço simples ou um Compose adaptado, mas não é o caminho de implantação completo desta stack. Essa conclusão sobre o arquivo específico é uma inferência a partir das capacidades documentadas e de seus recursos presentes.

Para Azure Container Instances, a antiga integração `docker compose` com contexto ACI foi [retirada](https://learn.microsoft.com/en-us/answers/questions/1502862/how-to-overcome-the-retirement-of-docker-compose-i). ACI ainda permite grupos multicontêiner definidos por [template Azure](https://learn.microsoft.com/en-us/azure/container-instances/tutorial-docker-compose), mas não resolve por si só o ciclo semanal, a durabilidade e os portões deste projeto.

## Alternativa recomendada para o planejamento

| Componente local | Destino Azure a avaliar | Verificação obrigatória |
|---|---|---|
| `inference` | Container App com ingresso HTTPS e uma réplica inicial | Mesmo digest de imagem aprovado no CI, readiness, previsão, registry só leitura, rollback de imagem e modelo |
| `trainer` | Container App Job acionado pelo GitHub Actions semanalmente e manualmente | Finalização explícita, bloqueio de promoção, lock de catálogo, evidência mesmo sem novo campeão |
| `mlflow-migrate` e `catalog-reconcile` | Jobs manuais/exclusivos antes do treino ou da atualização | Backup antes da migração; verificação e reconciliação antes de liberar publicação |
| `mlflow` | Container App de acesso interno | Artefatos via proxy HTTP, health check, compatibilidade cliente/servidor, sem entrada pública |
| `postgres` | Azure Database for PostgreSQL Flexible Server, condicionado a preço e disponibilidade na assinatura | Persistência, TLS, papéis mínimos, backup/restore e custo compatível com crédito estudantil |
| `mlflow-artifacts` e `model-registry` | Armazenamento durável separado, com Azure Files clássico como candidato para mounts | Checksums, leitura/escrita não-root, atomicidade de publicação, permissões, restore e separação de falhas |
| `weekly-output` | Armazenamento durável de evidência/backup separado do runtime | Retenção, checksums, acesso mínimo e restauração testada |
| Grafana externo | Fonte PostgreSQL com acesso privado ou túnel protegido | Consultas, alertas, TLS e sem exposição pública do banco |

### Paridade dos campos do Compose

| Campo em `compose.yaml` | Destino preparado | Aceite antes do deploy |
|---|---|---|
| `services` (`postgres`, `mlflow`, `mlflow-migrate`, `trainer`, `catalog-reconcile`, `inference`) | `postgres.bicep`, `container-apps.bicep` e `jobs.bicep` | Os seis papéis têm destino e ordem de execução comprovados. |
| `build` | Job manual do CI gera trainer, inference e MLflow; ACR recebe tags únicas e digests aprovados. | Digest efetivo da revisão coincide com manifesto do CI. |
| `volumes` externos (`postgres-data`, `mlflow-artifacts`, `model-registry`) | Flexible Server e duas shares Azure Files separadas. | Persistência após cinco recriações, UID e atomicidade provados no mount real. |
| Bind mount `./weekly-output:/backup` | Share `backup-evidence`, montada nos jobs autorizados. | Backup de três bancos e duas shares verificável e recuperável. |
| `ports` locais 5432, 5000 e 8000 | PostgreSQL privado, MLflow com ingresso interno e inferência HTTPS externa. | Somente inferência alcançável publicamente; TLS e DNS privados funcionam. |
| `environment` e segredos | Key Vault secret refs e parâmetros sem credenciais no Bicep. | Nenhum valor secreto em repositório, release record ou logs; URLs verificam TLS. |
| `healthcheck` | Probes ACA para MLflow/inferência e verify job para catálogo. | Readiness, versão, checksum e previsão aprovados antes de rotear tráfego. |
| `depends_on` | Sequência no GitHub Actions: backup → migração → reconcile → verify → trainer → verify. | Falha terminal interrompe os jobs seguintes; campeão anterior permanece. |
| `restart` | Escala mínima 1 nos apps contínuos; jobs manuais com retry 0. | Recuperação do serviço e status final do job demonstrados. |
| Agenda semanal | GitHub Actions permanece único agendador; jobs ACA têm gatilho manual. | Apenas um workflow semanal ativo e correlação por `workflow_run_id`. |

O [Azure Container Apps aceita Azure Files clássico como armazenamento persistente](https://learn.microsoft.com/en-us/azure/container-apps/storage-mounts); o filesystem local do contêiner é efêmero. A escolha exata para o registry depende de um ensaio de `fsync`/rename, permissões não-root e consistência antes de aceitar promoção atômica. A [documentação de jobs](https://learn.microsoft.com/en-us/azure/container-apps/jobs) cobre gatilhos manual e agendado; por exigência da constituição do projeto, o GitHub Actions continua orquestrador oficial do ciclo semanal, mesmo se acionar um job Azure.

A [configuração de segredos do Container Apps](https://learn.microsoft.com/en-us/azure/container-apps/manage-secrets) permite referências a Key Vault com identidade gerenciada. O acesso HTTP interno/externo e TCP tem [regras de ingresso próprias](https://learn.microsoft.com/en-us/azure/container-apps/ingress-how-to), distintas de `ports: 127.0.0.1` e das redes Compose. As imagens precisam ser publicadas com digest imutável antes da implantação. Não se deve copiar `.env` local para a nuvem.

## Portões antes de provisionar

1. Consultar assinatura, região, provedores, cotas, crédito e preços efetivos. O [Azure for Students inclui crédito limitado](https://learn.microsoft.com/azure/education-hub/about-azure-for-students), e [esgotá-lo pode desabilitar serviços](https://learn.microsoft.com/en-us/azure/cost-management-billing/manage/azurestudents-subscription-disabled). Nenhum preço fixo ou disponibilidade foi presumido.
2. Estimar 30 dias de consumo por serviço, armazenamento, banco, rede e monitoramento; definir teto e alerta inferiores ao saldo disponível.
3. Validar topologia e permissões sem expor MLflow, PostgreSQL ou Grafana ao público; testar comunicação por nomes Azure, não pelos nomes Compose locais.
4. Testar os três backups correlacionados e a restauração; publicar somente após saúde, previsão e rollback comprovados.

**Decisão nesta feature:** especificar uma implantação declarativa Azure por recurso, usando as imagens aprovadas pelo CI. A importação Compose permanece opção para protótipos ou serviços simples se um teste de paridade demonstrar os requisitos acima; não é o mecanismo de produção proposto para o `compose.yaml` atual.
