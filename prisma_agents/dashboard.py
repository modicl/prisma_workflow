"""
dashboard.py — Dashboard de uso de tokens por sesión PACI.

Lee los reportes JSON de token_reports/ y muestra:
  - Distribución por percentiles (p25 p50 p75 p90 p95 p99)
  - Sesiones outlier (> p90)
  - Desglose por agente
  - Resumen por estado (success / fail / timeout)

Uso:
    python dashboard.py               # reporte en consola
    python dashboard.py --html        # genera token_dashboard.html
    python dashboard.py --dir <path>  # leer reportes desde otro directorio
"""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path


# ── Carga de datos ────────────────────────────────────────────────────────────

def load_reports(reports_dir: Path) -> list[dict]:
    """Carga todos los token reports JSON del directorio."""
    if not reports_dir.exists():
        return []
    reports = []
    for path in sorted(reports_dir.glob("tokens_*.json")):
        try:
            with open(path, encoding="utf-8") as f:
                reports.append(json.load(f))
        except Exception:
            pass
    return reports


# ── Estadísticas ──────────────────────────────────────────────────────────────

def percentile(data: list[float], p: float) -> float:
    if not data:
        return 0.0
    s = sorted(data)
    idx = (len(s) - 1) * p / 100
    lo, hi = int(idx), min(int(idx) + 1, len(s) - 1)
    return s[lo] + (idx - lo) * (s[hi] - s[lo])


def compute_stats(values: list[float]) -> dict:
    if not values:
        return {}
    s = sorted(values)
    mean = sum(s) / len(s)
    return {
        "count": len(s),
        "min": s[0],
        "max": s[-1],
        "mean": mean,
        "p25": percentile(s, 25),
        "p50": percentile(s, 50),
        "p75": percentile(s, 75),
        "p90": percentile(s, 90),
        "p95": percentile(s, 95),
        "p99": percentile(s, 99),
    }


def agent_stats(reports: list[dict]) -> dict[str, list[int]]:
    """Acumula tokens por agente a través de todas las sesiones."""
    agents: dict[str, list[int]] = {}
    for r in reports:
        for agent, usage in r.get("tokens", {}).get("by_agent", {}).items():
            agents.setdefault(agent, []).append(usage.get("total_tokens", 0))
    return agents


# ── Formateo consola ──────────────────────────────────────────────────────────

def fmt(n: float) -> str:
    return f"{int(n):,}"


def bar(value: float, max_val: float, width: int = 30) -> str:
    if max_val == 0:
        return " " * width
    filled = int(round(value / max_val * width))
    return "█" * filled + "░" * (width - filled)


def print_dashboard(reports: list[dict]) -> None:
    W = 70
    print(f"\n{'═' * W}")
    print(f"  DASHBOARD — USO DE TOKENS  |  PACI Workflow")
    print(f"{'═' * W}")

    if not reports:
        print("  No hay reportes en token_reports/. Ejecuta el pipeline primero.")
        print(f"{'═' * W}\n")
        return

    totals = [r["tokens"]["total"] for r in reports if r.get("tokens", {}).get("total", 0) > 0]

    # ── Resumen general ────────────────────────────────────────────────────
    print(f"\n  Sesiones analizadas : {len(reports)}")
    print(f"  Con datos de tokens : {len(totals)}")
    if reports:
        dates = [r.get("timestamp", "") for r in reports]
        dates = [d for d in dates if d]
        if dates:
            print(f"  Rango de fechas     : {dates[0][:10]}  →  {dates[-1][:10]}")

    # Status breakdown
    statuses: dict[str, int] = {}
    for r in reports:
        s = r.get("status", "unknown")
        statuses[s] = statuses.get(s, 0) + 1
    print(f"\n  Estado de sesiones:")
    for s, n in sorted(statuses.items()):
        icon = "✓" if s == "success" else ("⚠" if s == "fail" else "✗")
        print(f"    {icon} {s:<12} {n:>4} sesiones")

    if not totals:
        print("\n  Sin datos de tokens registrados aún.")
        print(f"{'═' * W}\n")
        return

    stats = compute_stats(totals)

    # ── Distribución por percentiles ───────────────────────────────────────
    print(f"\n{'─' * W}")
    print(f"  DISTRIBUCIÓN POR PERCENTILES  (tokens totales / sesión)")
    print(f"{'─' * W}")
    print(f"  {'Percentil':<10} {'Tokens':>10}   {'Barra'}")
    print(f"  {'─'*10} {'─'*10}   {'─'*30}")
    for label, key in [("min", "min"), ("p25", "p25"), ("p50 (med)", "p50"),
                        ("p75", "p75"), ("p90", "p90"), ("p95", "p95"),
                        ("p99", "p99"), ("max", "max")]:
        v = stats[key]
        b = bar(v, stats["max"])
        print(f"  {label:<10} {fmt(v):>10}   {b}")

    print(f"\n  Media: {fmt(stats['mean'])} tokens   |   "
          f"Rango: {fmt(stats['min'])} – {fmt(stats['max'])}")

    # ── Desglose por agente ────────────────────────────────────────────────
    print(f"\n{'─' * W}")
    print(f"  TOKENS PROMEDIO POR AGENTE")
    print(f"{'─' * W}")
    a_stats = agent_stats(reports)
    if a_stats:
        agent_means = {a: sum(v) / len(v) for a, v in a_stats.items()}
        max_mean = max(agent_means.values()) if agent_means else 1
        print(f"  {'Agente':<30} {'Promedio':>10}   {'Barra'}")
        print(f"  {'─'*30} {'─'*10}   {'─'*20}")
        for agent, mean in sorted(agent_means.items(), key=lambda x: -x[1]):
            b = bar(mean, max_mean, 20)
            print(f"  {agent:<30} {fmt(mean):>10}   {b}")
    else:
        print("  Sin desglose por agente disponible.")

    # ── Outliers (> p90) ───────────────────────────────────────────────────
    p90_val = stats["p90"]
    outliers = [r for r in reports if r.get("tokens", {}).get("total", 0) > p90_val]
    print(f"\n{'─' * W}")
    print(f"  SESIONES OUTLIER  (> p90 = {fmt(p90_val)} tokens)  —  {len(outliers)} sesiones")
    print(f"{'─' * W}")
    if outliers:
        print(f"  {'ID Sesión':<38} {'Tokens':>10}  {'Estado':<10}  {'Fecha'}")
        print(f"  {'─'*38} {'─'*10}  {'─'*10}  {'─'*10}")
        for r in sorted(outliers, key=lambda x: -x["tokens"]["total"]):
            sid = r.get("session_id", "?")[:36]
            tok = r["tokens"]["total"]
            st = r.get("status", "?")
            ts = r.get("timestamp", "")[:10]
            flag = "  ⚠ ALTO" if tok > stats["p99"] else ""
            print(f"  {sid:<38} {fmt(tok):>10}  {st:<10}  {ts}{flag}")

        # Desglose del outlier más grande
        biggest = max(outliers, key=lambda x: x["tokens"]["total"])
        by_agent = biggest.get("tokens", {}).get("by_agent", {})
        if by_agent:
            print(f"\n  Desglose del outlier mayor ({fmt(biggest['tokens']['total'])} tokens):")
            for agent, usage in sorted(by_agent.items(), key=lambda x: -x[1].get("total_tokens", 0)):
                pct = usage["total_tokens"] / biggest["tokens"]["total"] * 100
                print(f"    {agent:<30} {fmt(usage['total_tokens']):>10}  ({pct:.1f}%)")
    else:
        print("  Ninguna sesión supera el p90.")

    # ── Histograma simple ──────────────────────────────────────────────────
    print(f"\n{'─' * W}")
    print(f"  HISTOGRAMA (distribución de sesiones por rango de tokens)")
    print(f"{'─' * W}")
    _print_histogram(totals, bins=8)

    print(f"\n{'═' * W}\n")


def _print_histogram(values: list[float], bins: int = 8) -> None:
    if not values:
        return
    min_v, max_v = min(values), max(values)
    if min_v == max_v:
        print(f"  Todas las sesiones: {fmt(min_v)} tokens")
        return
    step = (max_v - min_v) / bins
    counts = [0] * bins
    for v in values:
        idx = min(int((v - min_v) / step), bins - 1)
        counts[idx] += 1
    max_count = max(counts) if counts else 1
    for i, c in enumerate(counts):
        lo = min_v + i * step
        hi = lo + step
        b = bar(c, max_count, 25)
        print(f"  {fmt(lo):>10} – {fmt(hi):>10}  {b}  {c}")


# ── Generación HTML ───────────────────────────────────────────────────────────

def generate_html(reports: list[dict], out_path: Path) -> None:
    totals = [r["tokens"]["total"] for r in reports if r.get("tokens", {}).get("total", 0) > 0]
    stats = compute_stats(totals) if totals else {}
    p90_val = stats.get("p90", 0)
    a_stats = agent_stats(reports)
    agent_means = {a: sum(v) / len(v) for a, v in a_stats.items()} if a_stats else {}

    def pct_bar(value: float, max_val: float) -> str:
        pct = int(value / max_val * 100) if max_val else 0
        color = "#e74c3c" if value > p90_val else "#3498db"
        return (
            f'<div style="background:#ecf0f1;border-radius:4px;height:18px;width:100%">'
            f'<div style="background:{color};width:{pct}%;height:100%;border-radius:4px"></div>'
            f'</div>'
        )

    # Build sessions rows
    session_rows = ""
    max_tok = max(totals) if totals else 1
    for r in sorted(reports, key=lambda x: x.get("timestamp", ""), reverse=True):
        tok = r.get("tokens", {}).get("total", 0)
        sid = r.get("session_id", "?")[:36]
        st = r.get("status", "?")
        ts = r.get("timestamp", "")[:16].replace("T", " ")
        is_outlier = tok > p90_val and tok > 0
        row_bg = "#fff5f5" if is_outlier else "white"
        flag = '<span style="color:#e74c3c;font-weight:bold"> ⚠</span>' if is_outlier else ""
        st_color = "#27ae60" if st == "success" else ("#e67e22" if st == "fail" else "#e74c3c")
        bar_html = pct_bar(tok, max_tok) if tok > 0 else ""
        session_rows += (
            f'<tr style="background:{row_bg}">'
            f'<td style="font-family:monospace;font-size:12px">{sid}</td>'
            f'<td>{ts}</td>'
            f'<td style="color:{st_color};font-weight:bold">{st}</td>'
            f'<td style="text-align:right">{tok:,}{flag}</td>'
            f'<td style="width:200px">{bar_html}</td>'
            f'</tr>\n'
        )

    # Percentile rows
    pct_rows = ""
    if stats:
        for label, key in [("Min", "min"), ("p25", "p25"), ("Mediana (p50)", "p50"),
                            ("p75", "p75"), ("p90", "p90"), ("p95", "p95"),
                            ("p99", "p99"), ("Max", "max")]:
            v = stats[key]
            b = pct_bar(v, stats["max"])
            pct_rows += (
                f'<tr><td><strong>{label}</strong></td>'
                f'<td style="text-align:right">{int(v):,}</td>'
                f'<td style="width:250px">{b}</td></tr>\n'
            )

    # Agent rows
    agent_rows = ""
    if agent_means:
        max_mean = max(agent_means.values())
        for agent, mean in sorted(agent_means.items(), key=lambda x: -x[1]):
            pct = int(mean / max_mean * 100) if max_mean else 0
            b = (
                f'<div style="background:#ecf0f1;border-radius:4px;height:18px">'
                f'<div style="background:#9b59b6;width:{pct}%;height:100%;border-radius:4px"></div>'
                f'</div>'
            )
            agent_rows += (
                f'<tr><td>{agent}</td>'
                f'<td style="text-align:right">{int(mean):,}</td>'
                f'<td style="width:250px">{b}</td></tr>\n'
            )

    # Summary cards
    status_counts: dict[str, int] = {}
    for r in reports:
        s = r.get("status", "unknown")
        status_counts[s] = status_counts.get(s, 0) + 1

    cards = ""
    for label, value, color in [
        ("Sesiones totales", len(reports), "#3498db"),
        ("Con datos tokens", len(totals), "#27ae60"),
        ("Mediana tokens", f"{int(stats.get('p50', 0)):,}" if stats else "–", "#9b59b6"),
        ("p90 tokens", f"{int(stats.get('p90', 0)):,}" if stats else "–", "#e74c3c"),
        ("Outliers (>p90)", sum(1 for t in totals if t > p90_val), "#e67e22"),
    ]:
        cards += (
            f'<div style="background:{color};color:white;padding:16px 24px;'
            f'border-radius:8px;text-align:center;min-width:130px">'
            f'<div style="font-size:28px;font-weight:bold">{value}</div>'
            f'<div style="font-size:12px;margin-top:4px">{label}</div>'
            f'</div>\n'
        )

    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")

    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Token Dashboard — PACI Workflow</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
          margin: 0; padding: 24px; background: #f5f6fa; color: #2c3e50; }}
  h1 {{ margin: 0 0 4px; font-size: 24px; }}
  .subtitle {{ color: #7f8c8d; margin-bottom: 24px; font-size: 14px; }}
  .cards {{ display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 32px; }}
  .section {{ background: white; border-radius: 8px; padding: 20px;
               margin-bottom: 20px; box-shadow: 0 1px 3px rgba(0,0,0,.1); }}
  h2 {{ margin: 0 0 16px; font-size: 16px; color: #34495e;
        border-bottom: 2px solid #ecf0f1; padding-bottom: 8px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
  th {{ text-align: left; padding: 8px 12px; background: #f8f9fa;
        color: #7f8c8d; font-weight: 600; border-bottom: 2px solid #ecf0f1; }}
  td {{ padding: 8px 12px; border-bottom: 1px solid #f0f0f0; }}
  tr:last-child td {{ border-bottom: none; }}
  .footer {{ color: #bdc3c7; font-size: 12px; text-align: center; margin-top: 16px; }}
</style>
</head>
<body>
<h1>Token Dashboard</h1>
<div class="subtitle">PACI Workflow &mdash; Generado {generated_at}</div>

<div class="cards">{cards}</div>

<div class="section">
  <h2>Distribución por Percentiles</h2>
  <table>
    <tr><th>Percentil</th><th style="text-align:right">Tokens</th><th>Distribución</th></tr>
    {pct_rows if pct_rows else '<tr><td colspan="3">Sin datos</td></tr>'}
  </table>
</div>

<div class="section">
  <h2>Tokens Promedio por Agente</h2>
  <table>
    <tr><th>Agente</th><th style="text-align:right">Promedio</th><th>Distribución relativa</th></tr>
    {agent_rows if agent_rows else '<tr><td colspan="3">Sin datos</td></tr>'}
  </table>
</div>

<div class="section">
  <h2>Sesiones (más recientes primero)</h2>
  <p style="font-size:12px;color:#e74c3c;margin-top:-8px">
    ⚠ = Outlier (por encima del p90 = {int(p90_val):,} tokens)
  </p>
  <table>
    <tr>
      <th>ID Sesión</th><th>Fecha</th><th>Estado</th>
      <th style="text-align:right">Tokens</th><th>Barra</th>
    </tr>
    {session_rows if session_rows else '<tr><td colspan="5">Sin sesiones</td></tr>'}
  </table>
</div>

<div class="footer">Token Dashboard &mdash; PACI Workflow</div>
</body>
</html>"""

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  ✓ Dashboard HTML guardado en: {out_path}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Dashboard de tokens PACI Workflow")
    parser.add_argument(
        "--dir",
        default=str(Path(__file__).parent / "token_reports"),
        help="Directorio con los reportes JSON (default: token_reports/)",
    )
    parser.add_argument(
        "--html",
        action="store_true",
        help="Genera token_dashboard.html además del reporte de consola",
    )
    args = parser.parse_args()

    reports_dir = Path(args.dir)
    reports = load_reports(reports_dir)

    print_dashboard(reports)

    if args.html:
        out_path = Path(__file__).parent / "token_dashboard.html"
        generate_html(reports, out_path)


if __name__ == "__main__":
    main()
