# AEGIS-Bid: material de estudo aprofundado

## O que é este material

Os guias `docs/fase-N-*.md` são **guias de execução**: objetivo, passos, verificação. Este diretório (`docs/estudo/`) é o **livro de estudo**: explica *cada peça por dentro* (o que ela faz, por que está aqui, como quebra), mostra o caminho de ponta a ponta e organiza tudo em **20 sessões curtas** para você estudar pausadamente, sem pressa e sem pular etapas.

Ao terminar, você deve conseguir:

1. Explicar, sem consultar, o que acontece desde um `POST /api/bids` até o lance ser liquidado, e **por que** ele nunca é aplicado duas vezes.
2. Explicar cada componente da plataforma (Argo CD, Rollouts, KEDA, Prometheus, Chaos Mesh, Kyverno, HAProxy...) com o problema específico que ele resolve *neste* projeto.
3. Ter executado, medido e registrado os experimentos (canary revertido, escala por fila, caos, bloqueio de política, failover com RTO/RPO), com números seus.
4. Apresentar o projeto em entrevista e responder à pergunta "o que deu errado e o que você aprendeu?" com casos reais.

## Ordem de leitura

| # | Arquivo | Para quê | Quando ler |
|---|---|---|---|
| 00 | `00-comece-aqui.md` (este) | método, pré-requisitos, cronograma | primeiro |
| 01 | `01-visao-geral.md` | arquitetura, fluxo de um lance, mapa do repositório, quem manda em quê | antes de qualquer comando |
| 02 | `02-a-aplicacao.md` | os 3 serviços, Redis por dentro, testes, laboratório de mutações | sessões S01 e S02 |
| 03 | `03-componentes-da-plataforma.md` | cada componente de infraestrutura: o quê, por quê, como está configurado | consulte durante as fases 0 a 8 |
| 04 | `04-slo-e-ferramentas-internas.md` | matemática de SLO/burn rate e os scripts de `tools/` | sessões S08 a S14 |
| 05 | `05-roteiro-parte-1-fundacao-app-gitops.md` | sessões S00 a S07 (fases 0, 1 e 2) | em ordem |
| 06 | `06-roteiro-parte-2-observabilidade-entrega-escala.md` | sessões S08 a S12 (fases 3, 4 e 5) | em ordem |
| 07 | `07-roteiro-parte-3-caos-seguranca-dr.md` | sessões S13 a S19 (fases 6, 7, 8 e fechamento) | em ordem |
| 08 | `08-glossario.md` | termos em ordem alfabética | sempre que travar num termo |
| 09 | `09-autoavaliacao.md` | perguntas para responder sem consultar | ao fim de cada parte |

## O método: prever, observar, explicar

Em cada sessão do roteiro você repete o mesmo ciclo. É ele que transforma "executei comandos" em "entendo o sistema":

1. **Prever.** Antes de rodar um comando, escreva (no papel ou em `docs/LICOES.md`) o que você acha que vai acontecer e por quê.
2. **Observar.** Rode e compare com a previsão. O roteiro indica o que olhar e traz a saída esperada.
3. **Explicar.** Se a previsão estava errada, descubra *por que* antes de seguir. Esse é o ponto em que se aprende de verdade.

Regras de ouro:

- **Uma sessão, um objetivo.** Termine a sessão no checkpoint, mesmo que sobre energia. Voltar descansado vale mais.
- **Checkpoint é porta, não sugestão.** Se a verificação falhar, não avance: investigue e registre em `docs/LICOES.md`.
- **Leia antes de rodar.** Cada sessão lista o que ler antes. Comandos sem leitura viram receita de bolo.
- **Quebre de propósito.** Os laboratórios de mutação e os experimentos de caos existem para você ver o sistema *falhar* de forma controlada.
- **Anote números.** Tempo do `make up`, RTO, RPO, p99 antes/depois do KEDA. Números reais valem mais que adjetivos no currículo.
- **Um commit por sessão**, com uma mensagem que diga o que você aprendeu (`git commit -m "estudo S06: ..."`).
- **Responda a autoavaliação sem consultar** (`09-autoavaliacao.md`). Se não souber, volte ao trecho indicado.

## Convenções deste material

| Marca | Significa |
|---|---|
| **Saída capturada** | texto copiado de uma execução real feita durante a revisão deste material (aplicação rodando contra um Redis real) |
| **Saída esperada** | texto derivado da leitura dos manifests e da documentação oficial; **não foi executado em cluster**. Se a sua divergir, isso é informação valiosa: investigue e registre |
| **Checkpoint** | condição que precisa ser verdadeira para seguir |
| **Armadilha** | erro comum ou ponto traiçoeiro |
| **Registre** | o que anotar em `docs/LICOES.md` |
| **Exercício** | algo para você fazer sozinho, com solução escondida em um bloco `<details>` |

## Autodiagnóstico de pré-requisitos

Marque o que você consegue explicar *em voz alta, em duas frases*. Onde faltar, leia o link oficial **antes** da sessão indicada. Não precisa dominar: precisa ter o vocabulário.

| Conceito | Sessão em que passa a ser necessário | Onde estudar (fonte oficial) |
|---|---|---|
| Containers e imagens Docker (build, run, rede, volume) | S00 | https://docs.docker.com/get-started/ |
| Kubernetes básico: Pod, Deployment, Service, Ingress, Namespace, probes | S03 | https://kubernetes.io/docs/tutorials/kubernetes-basics/ |
| Git e GitHub: branch, commit, push, Pull Request | S00 | https://docs.github.com/en/get-started |
| HTTP: métodos, códigos de status, cabeçalhos | S01 | https://developer.mozilla.org/en-US/docs/Web/HTTP |
| Redis: strings, hashes, sets, **streams** e consumer groups | S01 | https://redis.io/docs/latest/develop/data-types/streams/ |
| Prometheus: counter, gauge, histogram, `rate()`, PromQL básico | S08 | https://prometheus.io/docs/introduction/overview/ e https://prometheus.io/docs/prometheus/latest/querying/basics/ |
| Kustomize (base, overlay, patch) | S03 | https://kubernetes.io/docs/tasks/manage-kubernetes-objects/kustomization/ |
| SLI, SLO, error budget, burn rate | S09 | https://sre.google/workbook/alerting-on-slos/ |
| GitOps com Argo CD | S06 | https://argo-cd.readthedocs.io/ |

Documentação oficial das ferramentas que aparecem ao longo do projeto: [k3d](https://k3d.io), [k3s](https://docs.k3s.io), [Argo Rollouts](https://argo-rollouts.readthedocs.io), [KEDA](https://keda.sh/docs/), [Chaos Mesh](https://chaos-mesh.org/docs/), [Kyverno](https://kyverno.io/docs/), [Sigstore/Cosign](https://docs.sigstore.dev), [HAProxy](https://docs.haproxy.org), [k6](https://grafana.com/docs/k6/latest/).

## Ferramentas a instalar (uma vez, na sessão S00)

| Ferramenta | Para quê | Verificação |
|---|---|---|
| Docker | roda os nós dos clusters | `docker --version` |
| k3d (>= 5.5) | cria clusters Kubernetes em containers | `k3d version` |
| kubectl | conversa com os clusters | `kubectl version --client` |
| git e make | fluxo GitOps e atalhos | `git --version`, `make --version` |
| python3 (>= 3.12 recomendado) | testes e `tools/` | `python3 --version` |
| k6 (a partir da S12) | gerador de carga | `k6 version` |
| helm (opcional) | só para inspecionar charts | `helm version` |
| plugin `kubectl-argo-rollouts` (opcional, S10) | visualizar o canary | `kubectl argo rollouts version` |
| `cosign` (opcional, S05) | verificar assinatura de imagem | `cosign version` |
| redis-server local (opcional, S01) | roda os 2 testes de concorrência | `redis-server --version` |

**Memória**: o ideal é 16 GB livres para o Docker. Com 8 GB use o plano B descrito em `docs/fase-0-fundacao.md` (um cluster só, fases ligadas aos poucos). O `make preflight` mede e avisa.

## Cronograma: 20 sessões (cerca de 37 horas)

Sugestão de ritmo: 3 a 4 sessões por semana, ou seja, 5 a 6 semanas. Nada impede de ir mais devagar.

| Sessão | Tema | Fase | Duração | Arquivo do roteiro |
|---|---|---|---|---|
| S00 | Ambiente, repositório e `configure` | 0 | 1 h | parte 1 |
| S01 | A aplicação por dentro + laboratório de mutações | 1 | 2 h | parte 1 |
| S02 | A aplicação rodando (curl, Redis, fila) | 1 | 1,5 h | parte 1 |
| S03 | Fundação, teoria: ler antes de subir | 0 | 1,5 h | parte 1 |
| S04 | Fundação, prática: `make up` | 0 | 2 h | parte 1 |
| S05 | CI com cadeia de suprimentos | 1 | 2 h | parte 1 |
| S06 | GitOps: merge de PR = deploy | 2 | 2 h | parte 1 |
| S07 | Self-heal, drift e versões fixadas | 2 | 1,5 h | parte 1 |
| S08 | Prometheus, Grafana e coleta de métricas | 3 | 2 h | parte 2 |
| S09 | SLOs, burn rate e alertas | 3 | 2 h | parte 2 |
| S10 | Canary saudável | 4 | 1,5 h | parte 2 |
| S11 | Deploy ruim revertido + gate de budget | 4 | 2 h | parte 2 |
| S12 | KEDA e o Leilão Relâmpago | 5 | 2 h | parte 2 |
| S13 | Caos com método científico (1º experimento) | 6 | 2 h | parte 3 |
| S14 | Demais experimentos e postmortem | 6 | 2 h | parte 3 |
| S15 | Kyverno em modo Audit | 7 | 1,5 h | parte 3 |
| S16 | Enforce, NetworkPolicy e provas de bloqueio | 7 | 2 h | parte 3 |
| S17 | Replicação do Redis e RPO | 8 | 1,5 h | parte 3 |
| S18 | Failover, RTO e failback | 8 | 2 h | parte 3 |
| S19 | Fechamento: números, README, vídeo, ensaio | todas | 3 h | parte 3 |

**Trilha essencial** (se o tempo for curto): S00 a S11, ou seja, fases 0 a 4, cerca de 21 horas. Cobre GitOps, SLO, canary e gate de budget, que é o coração da história do projeto. O restante (KEDA, caos, políticas, DR) enriquece, mas a trilha essencial já sustenta uma boa apresentação.

## O que foi e o que não foi validado (leia com atenção)

**Executado durante a revisão deste material, neste ambiente:**

- `make test`: 12 testes da aplicação e 19 dos manifests e ferramentas passam. `make lint` (yamllint) passa.
- A aplicação (auction, bid, settlement) rodou de verdade, contra um **Redis real**, e os resultados que aparecem em `02-a-aplicacao.md` marcados como *saída capturada* vêm dessa execução.
- 11 **mutações** foram aplicadas ao código numa cópia para ver quais testes as detectam (resultados em `02-a-aplicacao.md`). Duas lacunas reais de teste apareceram e viraram exercícios, com solução verificada.
- Pontos de documentação oficial do KEDA foram conferidos contra a página e o código-fonte do scaler.

**Não executado aqui (depende do seu Docker, do k3d e da internet):** `make up`, o sync do Argo CD, o canary, o KEDA, o Chaos Mesh, o Kyverno, o failover e o CI no GitHub. Nessas sessões, tudo que aparece como *saída esperada* foi derivado de leitura, não de execução. Tratem como hipótese: confirmar ou refutar essas hipóteses é parte do estudo.

**Pontos de atenção encontrados na revisão** (cada um é tratado na sessão indicada):

| # | Achado | Tratado em |
|---|---|---|
| 1 | O `cooldownPeriod` do KEDA só vale para escala a zero; com `minReplicaCount: 1`, a descida de N para 1 é do HPA (janela de 300 s por padrão). Os guias diziam outra coisa e foram corrigidos | S12 |
| 2 | Nenhum teste protege a ordem "aplicar, depois `XACK`" (a regra central do ADR 005): a mutação M3 passa despercebida | S01 |
| 3 | O teste que cruza PromQL × métricas pode ser enganado por um docstring com o nome antigo (mutação M6) | S01 |
| 4 | Com `retention: 7d` no Prometheus, o "budget de 30 dias" é calculado sobre no máximo 7 dias de dados | S09 |
| 5 | As NetworkPolicies da Fase 7 bloqueiam a replicação do Redis da Fase 8 (egress do DR e ingress do prod), a não ser que se acrescentem regras | S17 |
| 6 | Versões flutuantes: Argo CD `stable`, charts `"*"`, imagens `trivy:latest` e `syft:latest` no CI | S07 |
| 7 | A documentação do KEDA se contradiz sobre `activationLagCount` ser obrigatório; no código-fonte ele tem padrão `0` | S12 |
| 8 | `aegis_settlement_pending_entries` é publicado por *cada* réplica com o mesmo valor global: agregue com `max()`, nunca com `sum()` | S12 |

Nada disso invalida o projeto: **é exatamente o tipo de achado que se conta em entrevista.** Mas você precisa saber que existe.

## Próximo passo

Leia `01-visao-geral.md` e depois comece a parte 1 do roteiro (S00).
