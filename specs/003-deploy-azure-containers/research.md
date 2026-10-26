# Research: Azure Container Apps para House Price MLOps

**Date**: 2026-10-25. Fontes oficiais foram verificadas nesta data. Escolhas de região, SKU e preço são deliberadamente determinadas pelo pré-voo da assinatura real; não há credenciais Azure neste repositório.

## R1 — Destino e importação de Compose

**Decision**: Usar Azure Container Apps (ACA) com definições declarativas explícitas para serviços e jobs. Manter `compose.yaml` como ambiente local e inventário de paridade; não usá-lo como artefato de produção Azure.

**Rationale**: [`az containerapp compose create`](https://learn.microsoft.com/en-us/cli/azure/containerapp/compose?view=azure-cli-latest) importa Compose para criar apps, mas a [orientação oficial da equipe Azure](https://techcommunity.microsoft.com/blog/appsonazureblog/accelerating-azure-container-apps-with-the-azure-cli-and-compose-files/3516636) não promete `build` nem mounts. Este projeto também exige jobs de duração finita, três domínios de persistência, redes distintas e portões de início que o importador não valida como uma implantação íntegra. O produto legado Azure Container Service [foi aposentado](https://learn.microsoft.com/en-us/lifecycle/products/azure-container-service).

**Alternatives considered**: Importação de Compose adaptado apenas para protótipo sujeito a teste de paridade; Azure Container Instances por template para grupos multicontêiner, mas sem resolver o ciclo de jobs/revisões deste projeto; AKS com maior operação e custo potencial.

## R2 — Topologia de execução e ordenação

**Decision**: `inference` e `mlflow` como Container Apps separados; `trainer`, `mlflow-migrate`, migração do catálogo/reconciliação e backup/restore como jobs manuais acionados pelo GitHub Actions. O workflow semanal segue como único agendador; jobs Azure não recebem cron independente. Configurar um escritor de catálogo por vez, além do lock PostgreSQL existente.

**Rationale**: [Container Apps Jobs](https://learn.microsoft.com/en-us/azure/container-apps/jobs) distinguem tarefas finitas de serviços HTTP e suportam gatilho manual. O estado terminal de cada job deve ser observado antes do próximo: backup → migração MLflow → migração/reconciliação catálogo → verify → treino → verify/evidência. `depends_on` do Compose local não substitui essa ordenação na nuvem. A inferência não depende desses jobs para continuar servindo um campeão íntegro.

**Alternatives considered**: Cron Azure paralelo (duas agendas e risco de sobreposição); executar treino como app sempre ligado (custo ocioso e sem resultado terminal); um grupo ACI único (ciclos de vida diferentes acoplados).

## R3 — Banco e permissões

**Decision**: Um Azure Database for PostgreSQL Flexible Server privado como destino dos bancos lógicos `house_price`, `mlflow` e `optuna`, com papéis separados de administrador de bootstrap, migração, pipeline e leitor de métricas. Adaptar o bootstrap local de `docker/postgres/init.sql` para provisionamento idempotente e privilégios permitidos pelo serviço gerenciado. Exigir TLS com verificação de certificado e DNS privado.

**Rationale**: O serviço [permite múltiplos bancos](https://learn.microsoft.com/azure/postgresql/configure-maintain/concepts-servers), e a [gestão de acesso por papéis](https://learn.microsoft.com/en-us/azure/postgresql/security/security-access-control) suporta privilégio mínimo. A instância gerenciada evita colocar diretórios de dados PostgreSQL em um compartilhamento Azure Files sem garantia de semântica equivalente. [Rede privada](https://learn.microsoft.com/en-us/azure/postgresql/flexible-server/concepts-networking-private) e [TLS](https://learn.microsoft.com/en-us/azure/postgresql/flexible-server/concepts-networking-ssl-tls) exigem configuração própria.

**Alternatives considered**: PostgreSQL em Container App com volume de arquivos, rejeitado por durabilidade/compatibilidade não comprovadas; banco público protegido só por firewall, rejeitado para produção por expor superfície desnecessária. A escolha final de SKU/região fica subordinada ao pré-voo de crédito/cotas.

## R4 — Artefatos e registry

**Decision**: Usar dois Azure Files *clássicos* separados como candidatos para `mlflow-artifacts` e `model-registry`; um terceiro destino lógico separado guarda backups/evidências. MLflow monta somente artefatos com escrita; trainer e reconciliador montam o registry com escrita; inferência o monta com acesso de leitura. Validar em ACA dois registros de storage para a mesma share com modos de acesso distintos, se necessário.

**Rationale**: [ACA oferece mounts persistentes Azure Files](https://learn.microsoft.com/en-us/azure/container-apps/storage-mounts), enquanto o filesystem do contêiner é efêmero. O registry usa `fsync`, `os.replace`, diretórios imutáveis e leitura concorrente. A semântica exata no mount escolhido e permissões de UID 10001/10002 são um **portão técnico**, não uma hipótese de produção. Preferir SMB com criptografia em trânsito; testar escolha efetiva de protocolo e opções de mount. Se a prova falhar, redesenhar o store/commit protocol no `$speckit-implement` antes de qualquer promoção Azure. Não relaxar a atomicidade.

**Alternatives considered**: Volume efêmero (perda de campeão); mount direto de Blob (não suportado por ACA como volume nessa configuração); tratar Azure Files como substituto garantido do volume Docker (sem evidência de `fsync`/rename e UID).

## R5 — Backup e restauração

**Decision**: Criar um conjunto de backup correlacionado por `backup_set_id`: dumps lógicos dos três bancos, snapshots/cópias consistentes das duas shares, checksums, versão de imagem/modelo e sequência de restauração. Parar escritores ou adquirir um bloqueio operacional antes da captura; ensaiar restore em recursos isolados e reconciliar/verify antes de liberar treino.

**Rationale**: O [PITR automático do Flexible Server](https://learn.microsoft.com/en-us/azure/postgresql/backup-restore/concepts-backup-restore) restaura um servidor inteiro e seus arquivos não são exportáveis; não cria um conjunto atômico com arquivos do MLflow e do registry. Dumps e cópias com um manifesto comum permitem o teste de SC-007. Backups automáticos permanecem defesa adicional.

**Alternatives considered**: Apenas PITR do banco (não restaura artefatos/modelos); apenas GitHub artifact (retido por tempo limitado e independente da cópia de dados); snapshots sem congelar escritores (risco de conjunto inconsistente).

## R6 — Imagens, autenticação, segredos e rede

**Decision**: CI constrói e aprova uma vez as imagens `trainer`, `inference` e `mlflow`, publica tags únicas no ACR e registra/valida seus digests antes de criar revisões ACA. GitHub Actions usa federação OIDC com papéis mínimos; apps/jobs usam segredos externos e identidade gerenciada onde suportado. Somente inferência tem ingresso HTTPS externo. MLflow tem ingresso interno; banco e storage são acessíveis pela rede privada, e Grafana externo usa caminho privado/túnel.

**Rationale**: ACA recomenda [tags únicas e evita `latest`](https://learn.microsoft.com/en-us/azure/container-apps/containers); o [digest do registry identifica os bytes](https://learn.microsoft.com/en-us/azure/container-registry/container-registry-concepts). [OIDC no GitHub Actions](https://learn.microsoft.com/en-us/azure/developer/github/connect-from-azure-openid-connect) evita credencial Azure de longa duração. [Key Vault e segredos ACA](https://learn.microsoft.com/en-us/azure/container-apps/manage-secrets), [ingresso interno/externo](https://learn.microsoft.com/en-us/azure/container-apps/ingress-how-to) e [revisões](https://learn.microsoft.com/en-us/azure/container-apps/revisions) cobrem isolamento e rollback. As URLs locais `postgres` e `mlflow` devem ser substituídas por nomes reais da nuvem; `--allowed-hosts` do MLflow precisa cobrir somente hosts internos aprovados.

**Alternatives considered**: Reconstruir imagens durante deploy (bytes distintos de CI); credenciais Azure estáticas em GitHub; publicar MLflow/PostgreSQL na internet; tag `latest` sem prova de digest.

## R7 — Orçamento e cotas

**Decision**: Um pré-voo somente leitura consulta conta, região, cotas e saldo, estima 30 dias de vCPU, memória, requisições, PostgreSQL, ACR, storage, backup, logs e rede, e produz relatório aprovado para a região selecionada. A implantação exige previsão abaixo do crédito restante e do limite operacional configurado, cria alertas e mantém runbook de parada/remoção controlada. Não prometer teto automático de cobrança.

**Rationale**: [ACA cobra consumo por segundo e requisições](https://learn.microsoft.com/en-us/azure/container-apps/billing); [cotas variam por assinatura e região](https://learn.microsoft.com/en-us/azure/container-apps/quotas). [Budgets alertam, mas não interrompem recursos, e os dados de custo podem atrasar](https://learn.microsoft.com/en-us/azure/cost-management-billing/costs/tutorial-acm-create-budgets). O crédito [Azure for Students é limitado](https://learn.microsoft.com/azure/education-hub/about-azure-for-students). Sem acesso à assinatura, valores reais não podem ser preenchidos agora.

**Alternatives considered**: Limite baseado só no budget Azure (não é hard cap); SKU ou preço fixo no repositório (pode ficar incorreto); provisionar antes de consultar cotas/credito.

## R8 — Observabilidade e alertas

**Decision**: Manter PostgreSQL `observability` como fonte oficial dos painéis Grafana, com leitor de privilégio mínimo e transporte protegido. Persistir logs estruturados e evidência de job/deploy com IDs de execução, modelo e revisão; manter alertas de falha semanal, saúde e qualidade. A integração Grafana remota exige conectividade privada validada; caso indisponível, implantação não é declarada concluída.

**Rationale**: Respeita a constituição e as projeções/alertas já existentes. Um dashboard visual sem fonte acessível não satisfaz FR-012 nem SC-008.

**Alternatives considered**: Publicar o PostgreSQL para Grafana na internet (risco desnecessário); trocar fonte para dados ad hoc da ACA (quebraria continuidade do histórico existente).

## Itens decididos por prova no ambiente alvo

- Região, SKUs, cotas e custos efetivos são entradas de pré-voo, não escolhas em aberto no desenho.
- O registry em Azure Files só é aprovado depois de prova de semântica de `fsync`/rename, falhas injetadas, UID não-root, leitura concorrente e mount de inferência sem escrita.
- Se a assinatura não suportar rede privada, PostgreSQL ou custo previsto, o deploy para e apresenta relatório; não há fallback que descarte portões constitucionais.
