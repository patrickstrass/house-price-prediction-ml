# Feature Specification: Preparar implantação no Azure

**Feature Branch**: `main` (diretório de feature independente da branch)

**Created**: 2026-10-25

**Status**: Draft

**Input**: User description: "Agora faça as preparações necessarias para rodar na azure. Utilize Azure Container Services e verifique se tem como colocar diretamente o docker compose lá. Se não escreva a alternativa." Escopo confirmado: preparar código, infraestrutura e instruções, sem implantar diretamente na Azure nesta feature.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Implantar previsão com segurança (Priority: P1)

Como responsável pelo projeto, quero preparar a mesma versão aprovada localmente para execução no Azure for Students, de modo que usuários possam consultar o modelo aprovado sem perda de dados quando os contêineres forem recriados.

**Why this priority**: O serviço de previsão e o modelo vigente são o primeiro resultado útil da implantação.

**Independent Test**: Validar localmente templates, contratos, imagem e simulações de publicação, recriação e rollback; fornecer roteiro executável para o futuro ensaio em Azure, com prova de storage real como portão ainda pendente.

**Acceptance Scenarios**:

1. **Given** uma imagem aprovada e um modelo íntegro, **When** o responsável implanta a versão no Azure, **Then** o serviço fica acessível pelo endereço autorizado, informa saúde e versão e responde a uma previsão válida.
2. **Given** um modelo aprovado em uso, **When** o serviço de previsão é reiniciado ou recriado, **Then** o mesmo modelo íntegro continua disponível sem novo treinamento.
3. **Given** uma versão implantada com falha de saúde, **When** o responsável solicita rollback, **Then** a última combinação saudável de imagem, modelo e configuração volta a atender previsões e a falha fica registrada.

---

### User Story 2 - Executar o ciclo semanal no Azure (Priority: P2)

Como responsável pela operação, quero que a execução semanal e a execução manual controlada mantenham rastreabilidade, validação, decisão de promoção e evidências mesmo quando nenhum modelo novo for aprovado.

**Why this priority**: A automação mantém o modelo auditável e evita que uma implantação apenas de inferência se torne uma cópia sem manutenção.

**Independent Test**: Simular localmente um ciclo manual com candidato rejeitado e dados inválidos, inspecionar métricas, decisão e evidência, e validar o contrato de backup/restore; documentar o ensaio futuro em Azure.

**Acceptance Scenarios**:

1. **Given** recursos persistentes e credenciais válidas, **When** a execução semanal começa, **Then** ela valida dados, executa os portões de qualidade, registra parâmetros, métricas, artefatos e decisão e publica evidência consultável.
2. **Given** dados inválidos ou candidato abaixo do limite, **When** a execução termina, **Then** ela falha ou rejeita a promoção explicitamente, preserva o último modelo válido e registra o diagnóstico.
3. **Given** migração ou reconciliação incompleta, **When** o treinamento é solicitado, **Then** a publicação fica bloqueada até que os dados e o catálogo sejam verificados.

---

### User Story 3 - Escolher a forma de implantação e controlar custo (Priority: P3)

Como responsável por uma assinatura estudantil, quero saber se a configuração local pode ser importada diretamente e, se não preservar todos os requisitos, ter uma alternativa reproduzível com estimativa de custo e limites antes de ativar recursos pagos.

**Why this priority**: Evita implantação parcialmente funcional ou consumo de crédito sem previsão.

**Independent Test**: Revisar o inventário de recursos, as diferenças entre os ambientes, o método de estimativa mensal e o procedimento de desligamento; verificar localmente que cada componente tem destino ou portão documentado sem criar recursos Azure.

**Acceptance Scenarios**:

1. **Given** a configuração local existente, **When** se avalia sua importação, **Then** cada serviço, dado persistente, segredo, dependência, porta e tarefa agendada tem suporte comprovado ou uma alternativa documentada.
2. **Given** crédito, cota ou serviço indisponível na assinatura/região, **When** a avaliação prévia é executada, **Then** a implantação é interrompida antes de criar recursos de custo recorrente e apresenta a restrição específica.
3. **Given** uma implantação ativa, **When** o responsável consulta o estado operacional, **Then** encontra consumo observado, alertas, backups e um caminho testável de desligamento e recuperação.

### Edge Cases

- A configuração local depende de um recurso sem equivalente direto na importação: a implantação deve exigir adaptação explícita e teste de paridade antes de liberar tráfego.
- Um dos três estados persistentes está mais novo que os outros após restauração: a reconciliação deve relatar conflito e preservar o último campeão íntegro.
- O repositório de artefatos ou o banco de dados fica indisponível: o treinamento falha de modo rastreável; a inferência deve continuar com o campeão local válido sempre que seu armazenamento estiver acessível.
- A assinatura estudantil não oferece cota suficiente, o crédito está expirado ou a estimativa supera o teto: a implantação é bloqueada com diagnóstico.
- Credencial, certificado ou armazenamento perde acesso: não deve haver fallback para segredos versionados nem promoção parcial.
- Uma atualização altera a origem de dados, o contrato de previsão ou os critérios de promoção: a documentação de impacto e os testes correspondentes devem preceder a implantação.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: A preparação MUST identificar, antes da implantação, a assinatura e região alvo, os serviços disponíveis, as cotas aplicáveis, o crédito restante e os recursos necessários à operação contínua.
- **FR-002**: A preparação MUST registrar se a configuração local pode ser importada diretamente com equivalência de comportamento; cada recurso não equivalente MUST ter alternativa e verificação de aceite documentadas.
- **FR-003**: A implantação MUST usar no Azure as mesmas versões imutáveis de imagens aprovadas pelos portões de CI, com vínculo rastreável entre imagem, configuração, modelo e origem dos dados.
- **FR-004**: O serviço de previsão MUST expor saúde, versão ativa e previsão por acesso protegido em trânsito, sem expor publicamente componentes administrativos ou o armazenamento de dados.
- **FR-005**: A solução MUST preservar separadamente metadados de experimentos e catálogo, artefatos de experimento e pacotes de modelo, inclusive após reinício, recriação e atualização dos contêineres.
- **FR-006**: A inferência MUST ler apenas um pacote de modelo íntegro e MUST permanecer funcional quando componentes de treinamento ou rastreamento estiverem indisponíveis, desde que o pacote vigente continue acessível.
- **FR-007**: Credenciais e tokens MUST vir de mecanismos externos de segredos, com acesso mínimo necessário, sem valores em código, imagem, configuração versionada, logs, métricas ou evidências.
- **FR-008**: Migrações, verificação dos três estados persistentes e reconciliação MUST concluir com sucesso antes que uma execução possa publicar ou promover um modelo.
- **FR-009**: O ciclo de coleta, tratamento, avaliação e treinamento MUST continuar acionável semanalmente pelo GitHub Actions e manualmente de forma controlada, com estados e logs correlacionáveis.
- **FR-010**: Dados de entrada MUST ser validados; falhas de validação e de portões de qualidade MUST bloquear promoção e preservar o último modelo válido.
- **FR-011**: Cada execução MUST registrar no MLflow código, dependências, referência imutável dos dados, parâmetros, métricas, artefatos e decisão de promoção.
- **FR-012**: A solução MUST disponibilizar ao Grafana a fonte definida de métricas operacionais, de dados e de qualidade por versão e janela temporal, com alertas para falha semanal, indisponibilidade e violação dos limites de qualidade.
- **FR-013**: A implantação MUST produzir e verificar backups correlacionados dos três estados persistentes e documentar a ordem de restauração, a reconciliação após restore e a retenção.
- **FR-014**: Cada implantação MUST registrar o resultado de saúde e previsão e oferecer rollback verificável para a última combinação saudável de imagem, modelo e configuração.
- **FR-015**: Antes de ativar recursos de custo recorrente, a preparação MUST apresentar estimativa mensal por recurso, teto de consumo menor ou igual ao crédito disponível, alerta de orçamento e procedimento de desligamento.
- **FR-016**: Os componentes MUST executar sem privilégios sempre que suportado, com verificações de saúde aplicáveis e acesso à rede e ao armazenamento limitado à sua função.
- **FR-017**: A implantação MUST documentar o impacto de mudanças em fontes externas, esquema de dados, contrato de inferência, critérios de promoção e infraestrutura, incluindo os testes correspondentes.

### Scope Boundaries

- Esta feature entrega definições de infraestrutura, scripts, testes locais e instruções verificáveis para implantação futura. Não cria recursos Azure, não migra dados para a assinatura, não publica imagens no ACR e não entra em produção. Saldos, cotas e preço reais permanecem pendentes até acesso posterior à assinatura.
- Os cenários de aceite de operação Azure descrevem o comportamento que a preparação deve permitir; ensaios com mounts reais, recriação, alertas, restauração e rollback são portões para um deploy futuro e não critérios de conclusão desta preparação.
- O ambiente Azure for Students é o destino final; o ambiente local permanece disponível para desenvolvimento e testes.
- O Grafana pode continuar externo, desde que acesse a fonte de métricas com conectividade e permissões documentadas, sem abrir o banco ao público.
- Nenhuma fonte externa nova de dados é introduzida nesta feature; as exigências de contrato e validação passam a valer se uma fonte for adicionada futuramente.

### Key Entities *(include if feature involves data)*

- **Versão implantada**: associação de imagem imutável, configuração, versão do modelo, referência dos dados, data e resultado dos portões de validação.
- **Pacote de modelo**: identidade estável, manifesto e checksums do campeão e das versões preservadas.
- **Conjunto de estados persistentes**: metadados/catálogo, artefatos de experimentos e pacotes de modelos com referências de backup correlacionadas.
- **Execução semanal**: identificação, estado, métricas, decisão de promoção, evidências e diagnóstico de falha.
- **Estimativa operacional**: região, cotas, crédito disponível, custo mensal previsto por recurso, teto e consumo observado.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% dos recursos do Compose têm destino Azure ou limitação/alternativa documentada com verificação de aceite.
- **SC-002**: Templates de infraestrutura e workflows passam por validação estática local; nenhum teste da feature exige credenciais ou criação de recursos Azure.
- **SC-003**: Contratos e simulações locais cobrem publicação, cinco recriações, falha de dados/qualidade/storage, rollback e preservação do campeão; o guia separa explicitamente o que exige ensaio Azure posterior.
- **SC-004**: O pré-voo com evidências simuladas bloqueia 100% dos cenários de oferta, crédito, preço ou cota inválidos e não chama APIs de provisionamento.
- **SC-005**: Cada recurso proposto tem item de estimativa, fonte de preço, cota a conferir, limite operacional e procedimento de desligamento; valores reais são preenchidos somente quando houver acesso à assinatura.
- **SC-006**: O guia futuro inclui comandos e resultados esperados para verificar imagem/modelo/dados, cinco recriações, rollback em até 15 minutos, ciclo semanal, alertas em até cinco minutos e restore correlacionado.
- **SC-007**: Nenhum recurso, imagem, workflow Azure ou segredo é criado, publicado ou executado na conta durante esta feature.

## Assumptions

- "Azure Container Services" é interpretado como a família de serviços de contêiner da Azure, com Azure Container Apps como destino preferencial para serviços e tarefas; a viabilidade de importação é detalhada em [compose-feasibility.md](compose-feasibility.md).
- A assinatura é Azure for Students; a elegibilidade, cotas, regiões e crédito real ainda precisam ser lidos da conta antes de provisionar recursos.
- A publicação segura, reconciliação e remoção protegida previstas na feature 002 devem estar concluídas e validadas antes do primeiro deploy Azure.
- O catálogo e o rastreamento existentes permanecem a fonte oficial de experimentos; PostgreSQL continua a fonte de métricas consultadas pelo Grafana.
- O serviço de previsão é o único componente candidato a entrada pública; rastreamento, banco e tarefas administrativas ficam restritos.
- O projeto reutiliza os limites de qualidade, contratos de previsão e retenção de evidências já definidos nas features 001 e 002, salvo mudança explícita e revisada.
