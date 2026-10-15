# ADR 003: Monorepo (app + plataforma + manifests) em vez de repo GitOps separado

**Status**: aceita para o laboratório · **Contexto**: um autor, um projeto de estudo, ciclo curto de iteração.

## Decisão
Um **único repositório** com `apps/`, `deploy/`, `platform/` e `docs/`. O CI atualiza as tags de imagem em `deploy/overlays/*` por **PR** (nunca por push direto).

## Por quê
- Uma pessoa só: dois repos dobrariam o atrito sem trazer isolamento real.
- Facilita estudar: uma mudança de código e o manifest correspondente aparecem no mesmo `git diff`.
- Testes de consistência cruzam código e manifests (ex.: PromQL × métricas reais, labels do chaos × labels dos pods). Num repo separado, esses testes exigiriam checkout duplo.

## Riscos e mitigações
| Risco do monorepo | Mitigação aplicada |
|---|---|
| Commit de tag dispara novo build (loop) | o job `build` ignora commits `chore(deploy)` |
| Qualquer mudança de app dispara sync | o Argo CD só reage a `deploy/overlays/*` e `platform/apps/` |
| Permissões: quem edita código edita produção | em time: CODEOWNERS e *branch protection* em `deploy/` e `platform/` |

## Quando eu separaria
Times diferentes donos do código e da plataforma, auditoria exigindo trilha de mudanças de produção isolada, ou política de acesso distinta (devs não podem alterar `platform/`). Então: repo de config separado e o CI abre PR *nele*.
