# 01 · Visão geral e arquitetura

Objetivo deste arquivo: depois de lê-lo você consegue desenhar o sistema de memória, descrever o caminho de um lance e de uma mudança de código, e dizer *quem decide o quê*. Leia antes de rodar qualquer comando.

## 1. O que é o AEGIS-Bid (e o que ele não é)

É uma **plataforma de leilões em tempo real** construída como laboratório de Platform Engineering, DevOps e SRE. A aplicação é pequena **de propósito** (três serviços e um Redis). A estrela é tudo o que envolve a aplicação: como ela é entregue, medida, escalada, atacada, protegida e recuperada.

Não é um produto real: não tem autenticação, pagamento, banco transacional nem interface web. Cada atalho está marcado como `LAB ONLY` onde aparece.

### Por que leilão?

O domínio impõe três propriedades que justificam, uma a uma, as peças de plataforma:

| Propriedade do leilão | Consequência técnica | Peça que responde |
|---|---|---|
| Pico de tráfego nos últimos segundos ("sniping") | A fila de lances cresce de repente | autoscaling por fila (KEDA) e teste de carga (k6) |
| Lance duplicado ou maior lance sobrescrito = prejuízo | Correção sob concorrência e retries | idempotência em 3 camadas (ADR 005) |
| Lance precisa responder rápido e quase sempre | Metas explícitas de disponibilidade e latência | SLOs, burn rate, error budget |

## 2. As ideias centrais (o fio condutor)

| Ideia | O problema que ataca | Como aparece no projeto |
|---|---|---|
| **GitOps em dois clusters** | Mudança manual em produção cria drift e some sem rastro | Argo CD observa o Git; ligar uma fase é um commit |
| **Error Budget Gate** | "Pode fazer deploy?" vira opinião | O canary mede o SLO e aborta sozinho; o CI bloqueia feature se o budget acabou |
| **Entrega progressiva** | Deploy é a maior causa de incidentes | Argo Rollouts: 25%, 50%, 100%, com análise no Prometheus |
| **Caos com hipótese escrita antes** | Resiliência "prometida" não é provada | Chaos Mesh + Chaos Director + postmortem gerado |
| **Supply chain verificada** | "O que roda é o que meu pipeline construiu?" | Trivy, Syft, Cosign no CI; Kyverno verifica a assinatura na admissão |
| **DR com RTO/RPO medidos** | "Temos DR" sem nunca ter testado | Pilot light no segundo cluster + failover cronometrado |

## 3. Arquitetura em execução (runtime)

```
 cliente (curl, k6, navegador)
        │  http://localhost:8090   (cabeçalho Host: aegis.localhost)
        ▼
 ┌────────────────────────── host Docker ───────────────────────────────┐
 │  aegis-edge (HAProxy) :8090                                           │
 │     │ normal                         │ backup (só se o prod estiver   │
 │     │                                │ DOWN no health check)          │
 │     ▼                                ▼                                │
 │  k3d-aegis-prod-serverlb:80       k3d-aegis-dr-serverlb:80            │
 │ ┌───────── cluster aegis-prod ─────┐ ┌──────── cluster aegis-dr ─────┐│
 │ │ Traefik (Ingress do k3s)         │ │ Traefik                       ││
 │ │  /api/bids     → bid-svc  (x4)   │ │ bid-svc        (x0, pilot light)│
 │ │  /api/auctions → auction-svc(x2) │ │ auction-svc    (x0)           ││
 │ │ settlement-svc (x1 a x10, KEDA)  │ │ settlement-svc (x0)           ││
 │ │ redis (stream + estado)  ◄───────┼─┼─ redis (réplica do prod)      ││
 │ │ Argo CD (hub), Rollouts, KEDA,   │ │ Argo Rollouts, Kyverno        ││
 │ │ Prometheus, Grafana, Chaos Mesh, │ │                               ││
 │ │ Kyverno                          │ │                               ││
 │ └──────────────────────────────────┘ └───────────────────────────────┘│
 │        rede Docker compartilhada: aegis-net                           │
 └───────────────────────────────────────────────────────────────────────┘
```

Atalhos úteis para depurar sem passar pela borda: prod direto em `localhost:8081`, DR direto em `localhost:8082`. As interfaces (Argo CD, Prometheus, Grafana) são acessadas por `port-forward` (`make argocd-ui`, `make prom-ui`, `make grafana-ui`).

**Por que a rede Docker compartilhada importa:** ela permite três coisas que sem ela não existiriam: o Argo CD do `prod` alcançar a API do `dr`; o Redis do `dr` replicar do `prod`; o HAProxy falar com os dois clusters.

## 4. O caminho de uma mudança: do commit ao pod

```
 você edita código ──► git push (main)
                          │
                          ▼
              GitHub Actions (.github/workflows/ci.yml)
   testes ─► build ─► Trivy (scan) ─► push por digest ─► SBOM ─► Cosign (assina)
                          │
                          ▼
        PR "chore(deploy)" troca as tags em deploy/overlays/{prod,dr}
                          │   você revisa e faz MERGE  ◄── o "deploy" é este merge
                          ▼
   Argo CD (hub no prod) detecta o novo commit na main
      ApplicationSet gera 1 Application por cluster (prod e dr)
                          │
                          ▼
   Argo Rollouts: canary do bid-svc  (25% ─► 50% ─► 100%)
        ▲                 │ consulta o Prometheus a cada 20-30 s
        │                 ▼
   se a análise falhar ─► ABORTA e volta para a versão estável
                          │
                          ▼
   Kyverno (na admissão do Pod): a imagem foi assinada pelo ci.yml da main?
```

Repare que há **um único `kubectl apply` manual** em todo o projeto: o `root-app` (Application raiz) aplicado no bootstrap. Todo o resto vem do Git.

## 5. Um lance de ponta a ponta

Cenário: Ana dá um lance de R$ 15,00 (1500 centavos) no leilão `8cd8b6d5756a`. Siga o caminho, um passo por vez.

| # | Quem | O que acontece | Onde está no código |
|---|---|---|---|
| 1 | Cliente | `POST /api/bids` com o cabeçalho `Idempotency-Key: abc-123` e o corpo `{auction_id, bidder, amount}` | `apps/loadgen/lightning.js` (simula) |
| 2 | HAProxy | Escolhe o backend `prod` (health check em `GET /api/auctions` retorna 200) e encaminha | `bootstrap/haproxy.cfg` |
| 3 | Traefik | Casa o caminho `/api/bids` com o Ingress e envia ao Service `bid-svc` | `deploy/base/ingress.yaml` |
| 4 | Service | Escolhe um pod do `bid-svc` (estável ou canary, proporcional às réplicas) | `deploy/base/bid-svc.yaml` |
| 5 | `bid-svc` | Valida o corpo (pydantic) e o cabeçalho obrigatório; sem eles responde **422** | `apps/bid/main.py`: `BidIn`, `place_bid` |
| 6 | `bid-svc` | Consulta o leilão (com cache do `ends_at_ms`): inexistente = **404**, encerrado = **409**, auction-svc fora = **503** | `HttpAuctionLookup` |
| 7 | `bid-svc` | Calcula `bid_id = sha256(bidder:chave)[:32]` (determinístico) | `make_bid_id` |
| 8 | `bid-svc` | `SET idem:{bid_id} NX EX 86400`. Se já existia: responde **200** `duplicate: true` e **não** publica | camada 1 |
| 9 | `bid-svc` | `XADD bids ...` no Redis Streams e responde **202** `duplicate: false` | camada 2 |
| 10 | `settlement-svc` | `XREADGROUP` lê a mensagem (grupo `settlement`) | `Processor.poll_once` |
| 11 | `settlement-svc` | Executa o script **Lua** atômico: existe o leilão? já processei este `bid_id`? ainda está aberto? é maior que o atual? | camada 3: `APPLY_BID_LUA` |
| 12 | `settlement-svc` | `XACK` **depois** de aplicar e registra métricas (resultado e atraso) | `Processor.handle` |
| 13 | `settlement-svc` | A cada 10 s: reivindica mensagens órfãs (`XAUTOCLAIM`), fecha leilões vencidos, atualiza o gauge de pendentes | `run_worker` |
| 14 | Prometheus | Raspa `/metrics` dos três serviços a cada 10 s (ServiceMonitor) | `deploy/components/monitoring` |
| 15 | KEDA | A cada 5 s mede o **lag** do grupo e ajusta as réplicas do settlement | `deploy/components/autoscaling` |

Dois detalhes que valem ouro em entrevista:

- A resposta **202** significa "aceito na fila", **não** "lance vencedor". O resultado real só existe depois do passo 11. Por isso existe o SLO de frescor da liquidação (tempo entre aceitar e liquidar).
- A garantia contra duplicata **não** está no passo 8 (que é só uma otimização), e sim no passo 11. Veja `02-a-aplicacao.md` e o ADR 005.

## 6. Quatro laços de controle

Um jeito de enxergar a plataforma: quatro ciclos de feedback que se alimentam.

| Laço | Percurso | Quem fecha o laço |
|---|---|---|
| **Entrega** | commit → CI → PR → Argo CD → cluster | o Git é a fonte da verdade; `selfHeal` desfaz desvios |
| **Correção** | retry do cliente → idempotência → efeito único | o script Lua no settlement |
| **Confiabilidade** | métricas → regras de SLO → budget → canary e gate de CI | o Prometheus e as `AnalysisTemplate` |
| **Resiliência** | hipótese → falha injetada → medição → veredito → postmortem → melhoria | o Chaos Director e você |

O laço de **confiabilidade** é o mais importante para a narrativa: as métricas que a aplicação expõe (`apps/common/aegis_common/metrics.py`) viram regras (`slos.yaml` → `slo-rules.generated.yaml`), as regras viram o budget restante, e o budget decide se um deploy segue (canary) e se uma feature entra (gate de CI). Quebrar um nome de métrica quebra tudo isso em silêncio; por isso existe um teste de contrato (e, como você verá na S01, ele tem uma lacuna).

## 7. Mapa do repositório

| Pasta ou arquivo | O que contém | Quem lê |
|---|---|---|
| `apps/` | `auction/`, `bid/`, `settlement/` (FastAPI), `common/aegis_common/` (métricas e cliente Redis), `loadgen/` (k6), `tests/`, `Dockerfile` | o CI e o docker compose |
| `deploy/base/` | Kustomize base: namespace, Redis, 3 serviços, Ingress | o Argo CD, via os overlays |
| `deploy/overlays/{prod,dr}/` | o que muda em cada cluster; as linhas `#faseN#` ligam as fases | o Argo CD (um Application por overlay) |
| `deploy/components/` | recursos opcionais, um por fase: `monitoring`, `canary-analysis`, `autoscaling`, `network-policies`, `redis-expose`, `dr-replication` | os overlays |
| `platform/catalog/` | Applications/ApplicationSets de cada fase **disponíveis**, mas inativos | `make enable` copia para `platform/apps/` |
| `platform/apps/` | o que está **ativo** (a Application raiz observa esta pasta) | o Argo CD |
| `platform/observability/` | `slos.yaml` (especificação) e o que é gerado a partir dela | `make slo-rules` e o Argo CD |
| `platform/policies/` | políticas do Kyverno | o Argo CD |
| `bootstrap/` | configs do k3d, registro de clusters, Application raiz, HAProxy | o `make up` |
| `chaos/` | `catalog.yaml` (hipóteses) e os CRs do Chaos Mesh | o Chaos Director |
| `tools/` | `slo_gen.py`, `dashboard_gen.py`, `budget_gate.py`, `chaos_director.py`, `postmortem.py` e testes | você, o CI |
| `scripts/` | `preflight.sh`, `configure.sh`, `enable-phase.sh`, `failover.sh` | o Makefile |
| `docs/` | guias por fase, ADRs, `LICOES.md`, `ENTREVISTA.md` e este material | você |

## 8. Quem manda em quê (fonte da verdade)

Esta tabela evita a pergunta mais comum de confusão: "se eu mudar X, quem desfaz ou sobrescreve?".

| Coisa | Quem decide | Por que importa |
|---|---|---|
| Quais fases estão ativas | arquivos em `platform/apps/` e linhas descomentadas nos overlays (`make enable`) | ligar uma fase é um commit revisável |
| Tag da imagem de cada serviço | o CI, por PR, nos overlays | o deploy é o merge do PR |
| Réplicas do `settlement-svc` no prod | o KEDA (via HPA) | o Argo CD ignora a diferença (`ignoreDifferences`), senão brigaria com o KEDA |
| Réplicas dos serviços no DR | o script de failover (`scripts/failover.sh`) | idem; no Git ficam em 0 (pilot light) |
| Réplicas do `bid-svc` e `auction-svc` no prod | o Git (4 e 2), mas o Argo CD ignora diferenças de `replicas` | quem escala à mão não é revertido |
| Regras de SLO e dashboard | `slos.yaml` e `dashboard_gen.py`; os arquivos `*.generated.yaml` **nunca** se editam à mão | o CI falha se estiverem desatualizados |
| Qual cluster recebe tráfego | o health check do HAProxy | o failover é automático na borda |
| Quem pode rodar no namespace `aegis` | as políticas do Kyverno (Audit, depois Enforce) | segurança como código |
| O que o canary considera "saudável" | as `AnalysisTemplate` | o limite de 1% de erro e 300 ms está lá |

## 9. Mapa das fases

| Fase | O que liga | Arquivos principais | Prova de que funcionou |
|---|---|---|---|
| 0 | dois clusters, Argo CD, HAProxy | `Makefile`, `bootstrap/*` | `make down && make up` recria tudo |
| 1 | CI com scan, SBOM e assinatura | `.github/workflows/ci.yml` | imagens assinadas e PR de tags aberto |
| 2 | ApplicationSet nos dois clusters, Rollouts | `platform/catalog/fase2-*` | `kubectl delete service` é desfeito pelo Argo CD |
| 3 | Prometheus, Grafana, SLOs | `fase3-*`, `platform/observability/*` | dashboard com dados e alertas em `Status → Rules` |
| 4 | canary com análise e gate de budget | `canary-analysis`, `tools/budget_gate.py` | deploy ruim abortado sozinho |
| 5 | KEDA | `autoscaling`, `fase5-keda.yaml` | réplicas sobem no pico do k6 |
| 6 | Chaos Mesh e Chaos Director | `chaos/*`, `tools/chaos_director.py` | experimento com veredito e postmortem |
| 7 | Kyverno e NetworkPolicies | `platform/policies/*`, `network-policies` | imagem não assinada é rejeitada |
| 8 | replicação do Redis e failover | `redis-expose`, `dr-replication`, `scripts/failover.sh` | RTO e RPO medidos |

## 10. Portas e endereços

| Endereço | O que é | Como abrir |
|---|---|---|
| `localhost:8090` | borda (HAProxy), entrada oficial | `make edge` (já incluso no `make up`) |
| `localhost:8081` | Traefik do prod, direto | automático (k3d) |
| `localhost:8082` | Traefik do DR, direto | automático (k3d) |
| `https://localhost:8443` | UI do Argo CD (usuário `admin`) | `make argocd-ui` |
| `http://localhost:9090` | Prometheus | `make prom-ui` |
| `http://localhost:3000` | Grafana (`admin` / `aegis-lab`, LAB ONLY) | `make grafana-ui` |
| `localhost:8001/8002/8003` | auction/bid/settlement no docker compose | `make dev` |

Para o `curl` falar com a borda: `curl -H 'Host: aegis.localhost' http://localhost:8090/api/auctions`. Se preferir usar o nome direto, adicione `127.0.0.1 aegis.localhost` ao `/etc/hosts`.

## 11. Limitações assumidas (e por que são aceitáveis num laboratório)

Todas estão marcadas como `LAB ONLY` onde aparecem. Conheça-as para dizê-las você mesmo, antes de perguntarem:

- Redis compartilhado entre serviços, sem senha nem TLS (atalho consciente, ADR 002).
- Redis com `emptyDir`: os dados somem se o pod for recriado em outro nó.
- Replicação do Redis por NodePort aberta e sem autenticação.
- Verificação TLS desligada para registrar o DR no Argo CD.
- Senha fixa do Grafana (`aegis-lab`).
- Canary por proporção de réplicas, sem roteamento por peso real (ADR 004).
- Hub único do GitOps: o Argo CD cai junto com o prod (ADR 001).
- Failback não automatizado.
- Não implementados: traces (OpenTelemetry), FinOps (OpenCost), Crossplane, segredos em gerenciador.

## 12. Pontos de atenção encontrados na revisão

A lista completa, com a sessão em que cada um é tratado, está em `00-comece-aqui.md` (seção "O que foi e o que não foi validado"). Os mais importantes para a arquitetura:

- **Retenção × janela de SLO**: o Prometheus guarda 7 dias, mas o "budget de 30 dias" tenta olhar 30. Veja `04-slo-e-ferramentas-internas.md`.
- **Fases 7 e 8 interagem**: as NetworkPolicies "default-deny" impedem a replicação do Redis até você acrescentar regras. Veja a S17.
- **Reprodutibilidade**: várias versões flutuam (`stable`, `"*"`, `latest`). Veja a S07.

## Próximo passo

Leia `02-a-aplicacao.md` e faça as sessões S01 e S02 do roteiro (parte 1).
