# ADR 004: Canary (e não blue/green) para o bid-svc

**Status**: aceita · **Contexto**: o `bid-svc` é o caminho crítico (lances) e tem picos de tráfego; queremos reverter automaticamente com base em SLO.

## Decisão
**Canary** com Argo Rollouts: `25% → pausa 60 s → 50% → pausa 60 s → 100%`, com `AnalysisTemplate` consultando o Prometheus.

## Por quê
- **Raio de explosão pequeno**: se a versão nova for ruim, só uma fração das requisições é afetada, e a análise aborta antes do 100%.
- **Mede com tráfego real** (parcial), em vez de testar só na troca total.
- Não exige **dobrar a capacidade** (blue/green mantém dois ambientes completos); relevante em notebook.

## Alternativas
| Opção | A favor | Contra (aqui) |
|---|---|---|
| **Blue/green** | rollback instantâneo (troca do Service), sem versões misturadas | custo de dobrar réplicas; a análise acontece antes da troca (pré-promoção), com pouco tráfego real |
| RollingUpdate padrão | simples | sem análise automática de SLO |

## Limitações assumidas
- **Sem traffic router** (Traefik/service mesh integrado ao Rollouts), o percentual é *aproximado pela proporção de réplicas* (4 réplicas: 25% ≈ 1 pod). Em produção: roteamento por peso real.
- **Versões misturadas** durante o canary: o código precisa ser compatível entre versões adjacentes (contratos de dados e do stream).
- Com pouco tráfego, p99 do canary vem `NaN` e não reprova (decisão explícita); por isso gere carga durante o teste.

## Quando eu escolheria blue/green
Mudanças incompatíveis de schema que não toleram versões misturadas, ou quando o rollback precisa ser *instantâneo* e a capacidade extra é barata.
