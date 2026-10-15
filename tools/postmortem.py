#!/usr/bin/env python3
"""Postmortem-as-Code: transforma o JSON de um experimento em um postmortem .md.

  python tools/postmortem.py postmortems/data/<arquivo>.json
  python tools/postmortem.py postmortems/data/<arquivo>.json --open-pr

POR QUE automatizar: postmortem escrito "quando sobrar tempo" nunca é escrito. A
parte mecânica (linha do tempo, números, veredito) sai pronta; o humano só
preenche o que exige julgamento: causa raiz e ações. O formato é SEM CULPA:
descreve o sistema, não pessoas.
"""
import argparse
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _fmt(v, kind: str) -> str:
    if v is None:
        return "sem dados"
    return f"{v * 100:.2f}%" if kind == "pct" else f"{v * 1000:.0f} ms"


def render(r: dict) -> str:
    date = r["finished_at"][:10]
    status = "CONFIRMADA ✅" if r["confirmed"] else "REFUTADA ❌"
    ss = r["steady_state"]
    lines = [
        f"# Postmortem: experimento `{r['id']}` ({date})",
        "",
        f"**Hipótese {status}**",
        "",
        "## Hipótese",
        f"> {r['hypothesis']}",
        "",
        f"Estado estável definido como: taxa de erro ≤ {ss['max_error_ratio'] * 100:.2f}% "
        f"e p99 ≤ {ss['max_p99_seconds'] * 1000:.0f} ms. Duração da falha: {r['duration_seconds']} s.",
        "",
        "## Medições",
        "",
        "| Momento | Taxa de erro | p99 |",
        "|---|---|---|",
    ]
    for label, key in (("Antes", "before"), ("Durante a falha", "during"), ("Depois da recuperação", "after")):
        m = r[key]
        lines.append(f"| {label} | {_fmt(m['error_ratio'], 'pct')} | {_fmt(m['p99_seconds'], 'ms')} |")

    if r.get("client"):
        c = r["client"]
        lines += [
            "",
            f"Visão do cliente (contraprova): {c['requests']} requisições, {c['failures']} falhas "
            f"({_fmt(c['client_error_ratio'], 'pct')}).",
        ]

    lines += ["", "## Linha do tempo", ""]
    lines += [f"- `{e['ts']}` {e['event']}" for e in r["events"]]

    lines += ["", "## Impacto", ""]
    if r["violations"]:
        lines += [f"- {v}" for v in r["violations"]]
    else:
        lines.append("- Nenhuma violação do estado estável: o sistema absorveu a falha.")

    lines += ["", "## Causa raiz (PREENCHER)", ""]
    if r["confirmed"]:
        lines.append("_Hipótese confirmada: registrar POR QUE o sistema resistiu (réplicas? retry? cache?)._")
    else:
        lines += [
            "_Hipótese refutada. Roteiro de investigação:_",
            "",
            "1. No Grafana, cruze o horário do experimento com réplicas, reinícios e latência por rota.",
            "2. `kubectl -n aegis get events --sort-by=.lastTimestamp` no intervalo da falha.",
            "3. Houve retry/timeout adequado entre bid-svc e auction-svc/Redis?",
            "4. O readinessProbe tirou o pod doente do balanceamento a tempo?",
        ]

    lines += [
        "",
        "## Ações (PREENCHER)",
        "",
        "- [ ] Ação 1 (dono, prazo)",
        "- [ ] Ação 2 (dono, prazo)",
        "",
        "## O que aprendemos",
        "",
        "_(uma ou duas frases que você diria numa entrevista)_",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("result_json")
    ap.add_argument("--out-dir", default=os.path.join(ROOT, "postmortems"))
    ap.add_argument("--open-pr", action="store_true", help="cria branch, commit e PR via gh")
    args = ap.parse_args()

    with open(args.result_json) as f:
        r = json.load(f)
    path = os.path.join(args.out_dir, f"{r['finished_at'][:10]}-{r['id']}.md")
    os.makedirs(args.out_dir, exist_ok=True)
    with open(path, "w") as f:
        f.write(render(r))
    print(f"postmortem gerado: {path}")

    if args.open_pr:
        branch = f"postmortem/{r['finished_at'][:10]}-{r['id']}"
        run = lambda *c: subprocess.run(c, check=True, cwd=ROOT)  # noqa: E731
        run("git", "checkout", "-b", branch)
        run("git", "add", path)
        run("git", "commit", "-m", f"docs(postmortem): {r['id']} ({r['finished_at'][:10]})")
        run("git", "push", "-u", "origin", branch)
        run("gh", "pr", "create", "--title", f"Postmortem: {r['id']}",
            "--body", "Postmortem gerado automaticamente pelo Chaos Director. Preencha causa raiz e ações.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
