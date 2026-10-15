# ADR 001: Argo CD em vez de Flux

**Status**: aceita · **Contexto**: precisamos de GitOps para dois clusters, com ApplicationSet e boa visibilidade para estudo e demonstração.

## Decisão
Usar **Argo CD** (com ApplicationSet) rodando no cluster `prod` como *hub* que também gerencia o `dr`.

## Por quê
- **UI** que mostra árvore de recursos, diff e saúde: muito valiosa para aprender e para demonstrar (self-heal visível ao vivo).
- **ApplicationSet com gerador de clusters**: um template produz um Application por cluster rotulado (`aegis.io/env`). Adicionar cluster = registrar o cluster.
- Integra bem com **Argo Rollouts** (mesma família, mesmo modelo mental).
- Modelo *app-of-apps* simples para ligar fases só com commits.

## Alternativas consideradas
| Opção | A favor | Contra (neste contexto) |
|---|---|---|
| **Flux** | leve, 100% declarativo, composição por controllers, ótimo para multi-tenant | sem UI nativa madura; menos "demonstrável" ao vivo |
| Deploy por script/CI (`kubectl apply`) | simples | sem drift detection nem self-heal: não é GitOps |

## Consequências
- **Ponto único de falha da entrega**: se o `prod` cair, o Argo CD cai (o DR continua executando, mas sem receber mudanças). Mitigações em produção: cluster de gestão separado ou Argo CD standby no DR.
- O registro do DR usa `insecure: true` (LAB ONLY); em produção, SANs corretos + `caData`.

## Quando eu escolheria Flux
Muitos clusters com times independentes, preferência por reconciliação 100% pull sem servidor central, ou necessidade de composição fina com Kustomize/Helm controllers.
