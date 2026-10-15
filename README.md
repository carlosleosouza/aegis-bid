# AEGIS-Bid

Plataforma de leilões em tempo real, construída como **projeto de aprendizado e portfólio de Platform/DevOps/SRE**. A aplicação é pequena de propósito; a estrela é a plataforma em volta dela:

- **GitOps** (Argo CD + ApplicationSet) em **dois clusters** k3d: `prod` e `dr`
- **Entrega progressiva** (Argo Rollouts) com **Error Budget Gate**: o canary é abortado se o SLO estiver queimando
- **Observabilidade e SLOs** (Prometheus, Grafana, alertas por burn rate), com regras *geradas* a partir de um YAML
- **Autoscaling por fila** (KEDA) testado com o "Leilão Relâmpago" (k6)
- **Chaos engineering** com hipótese escrita antes (Chaos Mesh + Chaos Director) e **Postmortem-as-Code**
- **Supply chain**: scan (Trivy), SBOM (Syft), assinatura keyless (Cosign) e admissão por política (Kyverno)
- **DR** com borda HAProxy, replicação do Redis e **RTO/RPO medidos**

## Como estudar este projeto

Cada fase tem um guia em `docs/` com o mesmo formato: **objetivo → por quê → passos → verificação → se der errado → pergunta de entrevista**. Trabalhe **uma fase por vez** e só avance quando a verificação passar. Os guias explicam o *porquê* de cada passo; os comentários nos manifests e no código repetem o raciocínio no ponto exato onde a decisão foi tomada.

| Fase | Guia | O que você entrega |
|---|---|---|
| 0 | [Fundação](docs/fase-0-fundacao.md) | `make up` cria 2 clusters + Argo CD + borda |
| 1 | [App e CI](docs/fase-1-app-ci.md) | App rodando local; pipeline com scan, SBOM e assinatura |
| 2 | [GitOps](docs/fase-2-gitops.md) | Merge de PR = deploy nos 2 clusters; self-heal provado |
| 3 | [Observabilidade e SLOs](docs/fase-3-observabilidade.md) | Dashboard "Saúde do Leilão" + alertas de burn rate |
| 4 | [Error Budget Gate](docs/fase-4-error-budget.md) | Rollback automático de uma versão ruim |
| 5 | [Autoscaling e carga](docs/fase-5-autoscaling.md) | KEDA escalando pela fila no Leilão Relâmpago |
| 6 | [Caos](docs/fase-6-caos.md) | Experimentos com hipótese, veredito e postmortem |
| 7 | [Segurança](docs/fase-7-seguranca.md) | Políticas Kyverno e NetworkPolicies, de Audit a Enforce |
| 8 | [DR e postmortem](docs/fase-8-dr-postmortem.md) | Failover com RTO/RPO medidos |

Complementos: [`docs/ENTREVISTA.md`](docs/ENTREVISTA.md) (roteiro de apresentação e perguntas), [`docs/LICOES.md`](docs/LICOES.md) (seu diário de erros: o item mais valioso em entrevista) e [`docs/adr/`](docs/adr) (decisões e trade-offs).

## Início rápido

```bash
make help                              # todos os alvos, com descrição
make configure OWNER=seu-usuario       # troca o placeholder carlosleosouza
# crie o repo PÚBLICO "aegis-bid" no GitHub, faça commit e push na branch main
make test                              # testes unitários (não precisa de cluster)
make up                                # Fase 0 completa (~10-15 min na primeira vez)
make enable PHASE=2                    # liga uma fase: só edita arquivos; depois git commit + push
```

Pré-requisitos, requisitos de memória (ideal 16 GB) e o plano B para 8 GB estão em [docs/fase-0-fundacao.md](docs/fase-0-fundacao.md).

## Estrutura

```
apps/        auction, bid, settlement (FastAPI) + common/ (métricas) + loadgen/ (k6) + testes
deploy/      kustomize: base/ + overlays/{prod,dr} + components/ (um por fase, ligados com `make enable`)
platform/    catalog/ (Applications por fase) · apps/ (o que está ATIVO) · observability/ · policies/
bootstrap/   k3d-prod/dr.yaml, register-clusters.sh, root-app.yaml, haproxy.cfg
chaos/       catálogo de experimentos (hipótese + estado estável) e CRs do Chaos Mesh
tools/       slo_gen, dashboard_gen, budget_gate, chaos_director, postmortem (+ testes)
scripts/     preflight, configure, enable-phase, failover
docs/        guias por fase, ADRs, LICOES, ENTREVISTA
```

## O que já foi validado (e o que ainda não)

Executado automaticamente: testes unitários da aplicação (fluxo de lances, idempotência, redelivery, mensagem venenosa), testes dos manifests e das ferramentas (referências do kustomize, PromQL × métricas reais, ligar as fases 2 a 8 numa cópia) e `yamllint`.

**Ainda não executado contra clusters reais**: `make up`, o sync do Argo CD, o canary, o KEDA, o Chaos Mesh e o failover. Esses passos dependem do seu Docker/k3d e da internet (charts Helm, Sigstore). Os guias trazem a verificação esperada em cada passo; quando algo divergir, registre em `docs/LICOES.md`. É exatamente o material que se conta numa entrevista.

## Limitações assumidas (laboratório)

Redis compartilhado entre serviços, sem senha nem TLS; replicação do Redis em NodePort sem autenticação; verificação TLS desligada para registrar o DR no Argo CD; Grafana com senha fixa. Todos estão marcados como `LAB ONLY` no ponto onde aparecem, com o que mudaria em produção.
