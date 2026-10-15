#!/usr/bin/env python3
"""Gera PrometheusRule (regras de gravação + alertas de burn rate) a partir de slos.yaml.

POR QUE gerar em vez de escrever à mão: cada SLO precisa de ~20 regras
(8 janelas de gravação + 4 alertas), todas derivadas da mesma fórmula. À mão,
um typo em uma janela vira um alerta que nunca dispara, e você só descobre no
incidente. Ferramentas como Sloth e Pyrra fazem exatamente isto. Aqui geramos
com ~100 linhas de Python para você ENTENDER o que elas fazem por baixo.
(Desafio da fase 3: trocar este gerador pelo Sloth e comparar a saída.)

A matemática (Google SRE Workbook, cap. 5 "Alerting on SLOs"):

  budget      = 1 - objetivo                (99,9% => 0,001 de erro permitido)
  burn rate   = taxa_de_erro_observada / budget
                1x  = gastando o budget exatamente no ritmo de durar 30 dias
                14,4x = gastaria 2% do budget do mês em 1 hora

  Alerta multi-janela: dispara só se a janela LONGA e a CURTA estouram juntas.
    longa  -> garante que o problema é relevante (não foi um soluço)
    curta  -> garante que ainda está acontecendo (alerta limpa rápido ao resolver)

  Janelas/fatores (severidade):
    1h  & 5m   x14.4  page    (2% do budget em 1h)
    6h  & 30m  x6     page    (5% em 6h)
    1d  & 2h   x3     ticket  (10% em 1 dia)
    3d  & 6h   x1     ticket  (10% em 3 dias)
"""
import sys

import yaml

WINDOWS = ["5m", "30m", "1h", "2h", "6h", "1d", "3d", "30d"]

# (janela longa, janela curta, fator de burn, severidade, nome do alerta)
BURN_ALERTS = [
    ("1h", "5m", 14.4, "page", "AegisErrorBudgetFastBurn"),
    ("6h", "30m", 6, "page", "AegisErrorBudgetModerateBurn"),
    ("1d", "2h", 3, "ticket", "AegisErrorBudgetSlowBurn"),
    ("3d", "6h", 1, "ticket", "AegisErrorBudgetVerySlowBurn"),
]


def _num(x: float) -> str:
    return f"{x:.6g}"


def _one_line(expr: str) -> str:
    return " ".join(expr.split())


def generate(spec: dict) -> dict:
    groups = []
    for slo in spec["slos"]:
        slo_id = slo["id"]
        budget = 1 - slo["objective"] / 100
        template = _one_line(slo["error_ratio"])

        rules = []
        # 1) regras de gravação: pré-calculam o SLI em cada janela
        for w in WINDOWS:
            rules.append(
                {
                    "record": f"aegis:sli_error:ratio_rate{w}",
                    "expr": template.replace("{window}", w),
                    "labels": {"slo": slo_id},
                }
            )
        # 2) quanto do budget de 30 dias ainda resta (1 = intacto, 0 = esgotado)
        rules.append(
            {
                "record": "aegis:error_budget_remaining:ratio",
                "expr": f'1 - (aegis:sli_error:ratio_rate30d{{slo="{slo_id}"}} / {_num(budget)})',
                "labels": {"slo": slo_id},
            }
        )
        # 3) alertas multi-janela
        for long_w, short_w, factor, severity, name in BURN_ALERTS:
            threshold = _num(budget * factor)
            rules.append(
                {
                    "alert": name,
                    "expr": (
                        f'aegis:sli_error:ratio_rate{long_w}{{slo="{slo_id}"}} > {threshold}'
                        f' and aegis:sli_error:ratio_rate{short_w}{{slo="{slo_id}"}} > {threshold}'
                    ),
                    "for": "2m" if severity == "page" else "15m",
                    "labels": {"severity": severity, "slo": slo_id},
                    "annotations": {
                        "summary": f"SLO {slo_id}: budget queimando {factor}x mais rápido que o sustentável",
                        "description": (
                            f"Taxa de erro acima de {threshold} nas janelas {long_w} e {short_w}. "
                            f"Objetivo: {slo['objective']}%. {slo.get('description', '')}"
                        ).strip(),
                    },
                }
            )
        groups.append({"name": f"aegis-slo-{slo_id}", "interval": "30s", "rules": rules})

    return {
        "apiVersion": "monitoring.coreos.com/v1",
        "kind": "PrometheusRule",
        "metadata": {
            "name": "aegis-slo-rules",
            "namespace": "monitoring",
            "labels": {"app.kubernetes.io/part-of": "aegis-bid"},
        },
        "spec": {"groups": groups},
    }


def render(doc: dict) -> str:
    header = "# GERADO por tools/slo_gen.py a partir de slos.yaml. NÃO edite à mão.\n"
    return header + yaml.safe_dump(doc, sort_keys=False, width=10_000, allow_unicode=True)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("uso: slo_gen.py slos.yaml > slo-rules.generated.yaml", file=sys.stderr)
        return 2
    with open(argv[1]) as f:
        spec = yaml.safe_load(f)
    sys.stdout.write(render(generate(spec)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
