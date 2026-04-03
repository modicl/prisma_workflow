"""
dashboard.py — Dashboard de uso de tokens por sesión PACI.

Fuente principal: PostgreSQL (BD_LOGS) via vistas v_session_token_usage.
Fallback:         token_reports/*.json si BD_LOGS no está disponible.

Uso:
    python dashboard.py                    # mes actual desde BD
    python dashboard.py --all              # todas las sesiones desde BD
    python dashboard.py --create-views     # crear/actualizar vistas en la BD
    python dashboard.py --html             # también genera token_dashboard.html
    python dashboard.py --dir token_reports/  # leer desde JSON files
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import subprocess

from dotenv import load_dotenv


def _find_dotenv() -> Path | None:
    """Busca .env en el directorio del script; si es un worktree, lo busca en el repo principal."""
    local = Path(__file__).parent / ".env"
    if local.exists():
        return local
    # En worktrees el .env puede estar en el repo principal
    try:
        result = subprocess.check_output(
            ["git", "worktree", "list", "--porcelain"],
            cwd=str(Path(__file__).parent),
            stderr=subprocess.DEVNULL,
            text=True,
        )
        for line in result.splitlines():
            if line.startswith("worktree "):
                candidate = Path(line.split(" ", 1)[1]) / "prisma_agents" / ".env"
                if candidate.exists():
                    return candidate
    except Exception:
        pass
    return None


_env_file = _find_dotenv()
if _env_file:
    load_dotenv(_env_file)

VIEWS_SQL = Path(__file__).parent / "sql" / "views.sql"
APP_NAME = "paci_workflow"


# ── Conexión a BD ─────────────────────────────────────────────────────────────

def _sync_url(db_url: str) -> str:
    return db_url.replace("postgresql+asyncpg://", "postgresql://")


async def _connect():
    import asyncpg
    url = _sync_url(os.environ.get("BD_LOGS", ""))
    return await asyncpg.connect(url, ssl="require")


async def apply_views() -> None:
    """Crea o reemplaza las vistas en la BD."""
    conn = await _connect()
    sql = VIEWS_SQL.read_text(encoding="utf-8")
    await conn.execute(sql)
    await conn.close()
    print("  ✓ Vistas creadas/actualizadas: v_session_token_usage, v_session_token_usage_mes")


# ── Carga de datos desde BD ───────────────────────────────────────────────────

async def load_from_db(only_current_month: bool = True) -> list[dict]:
    """Lee sesiones desde la vista PostgreSQL."""
    conn = await _connect()
    view = "v_session_token_usage_mes" if only_current_month else "v_session_token_usage"

    # Si la vista no existe, crearla automáticamente
    exists = await conn.fetchval(
        "SELECT 1 FROM information_schema.views WHERE table_name = $1", view
    )
    if not exists:
        print(f"  [Info] Vista {view} no existe, creando...")
        await apply_views()

    rows = await conn.fetch(f"SELECT * FROM {view} ORDER BY create_time DESC")
    await conn.close()

    sessions = []
    for r in rows:
        by_agent_raw = r["by_agent"]
        if isinstance(by_agent_raw, str):
            by_agent_raw = json.loads(by_agent_raw)

        sessions.append({
            "session_id": r["session_id"],
            "timestamp": r["create_time"].isoformat() if r["create_time"] else "",
            "status": r["status"] or "unknown",
            "tokens": {
                "total":    int(r["total_tokens"] or 0),
                "input":    int(r["input_tokens"] or 0),
                "output":   int(r["output_tokens"] or 0),
                "by_agent": by_agent_raw or {},
            },
        })
    return sessions


# ── Carga de datos desde JSON files (fallback) ────────────────────────────────

def load_from_files(reports_dir: Path) -> list[dict]:
    if not reports_dir.exists():
        return []
    sessions = []
    for path in sorted(reports_dir.glob("tokens_*.json")):
        try:
            with open(path, encoding="utf-8") as f:
                sessions.append(json.load(f))
        except Exception:
            pass
    return sorted(sessions, key=lambda x: x.get("timestamp", ""), reverse=True)


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
    return {
        "count": len(s),
        "min": s[0], "max": s[-1],
        "mean": sum(s) / len(s),
        "p25": percentile(s, 25), "p50": percentile(s, 50),
        "p75": percentile(s, 75), "p90": percentile(s, 90),
        "p95": percentile(s, 95), "p99": percentile(s, 99),
    }


def agent_stats(sessions: list[dict]) -> dict[str, list[int]]:
    agents: dict[str, list[int]] = {}
    for s in sessions:
        for agent, usage in s.get("tokens", {}).get("by_agent", {}).items():
            agents.setdefault(agent, []).append(
                usage.get("total_tokens", 0) if isinstance(usage, dict) else 0
            )
    return agents


# ── Formateo consola ──────────────────────────────────────────────────────────

def fmt(n: float) -> str:
    return f"{int(n):,}"


def bar(value: float, max_val: float, width: int = 30) -> str:
    if max_val == 0:
        return "░" * width
    filled = int(round(value / max_val * width))
    return "█" * filled + "░" * (width - filled)


def print_dashboard(sessions: list[dict], source: str = "BD") -> None:
    W = 72
    print(f"\n{'═' * W}")
    print(f"  DASHBOARD TOKENS  |  PACI Workflow  |  Fuente: {source}")
    print(f"{'═' * W}")

    if not sessions:
        print("  Sin sesiones registradas en el período seleccionado.")
        print(f"{'═' * W}\n")
        return

    totals = [s["tokens"]["total"] for s in sessions if s["tokens"]["total"] > 0]

    # ── Resumen general ────────────────────────────────────────────────────
    dates = [s.get("timestamp", "")[:10] for s in sessions if s.get("timestamp")]
    print(f"\n  Sesiones en período : {len(sessions)}")
    print(f"  Con datos tokens    : {len(totals)}")
    if dates:
        print(f"  Rango de fechas     : {min(dates)}  →  {max(dates)}")

    statuses: dict[str, int] = {}
    for s in sessions:
        st = s.get("status", "unknown")
        statuses[st] = statuses.get(st, 0) + 1
    print(f"\n  Estado de sesiones:")
    for st, n in sorted(statuses.items()):
        icon = "✓" if st == "success" else ("⚠" if st == "fail" else "✗")
        print(f"    {icon} {st:<12} {n:>4}")

    if not totals:
        print("\n  Sin tokens registrados. Ejecuta el pipeline para generar datos.")
        print(f"{'═' * W}\n")
        return

    stats = compute_stats(totals)

    # ── Percentiles ────────────────────────────────────────────────────────
    print(f"\n{'─' * W}")
    print(f"  PERCENTILES  (tokens totales / sesión)")
    print(f"{'─' * W}")
    print(f"  {'Percentil':<12} {'Tokens':>10}   Distribución")
    print(f"  {'─'*12} {'─'*10}   {'─'*30}")
    for label, key in [("min", "min"), ("p25", "p25"), ("p50  (mediana)", "p50"),
                        ("p75", "p75"), ("p90", "p90"), ("p95", "p95"),
                        ("p99", "p99"), ("max", "max")]:
        v = stats[key]
        print(f"  {label:<12} {fmt(v):>10}   {bar(v, stats['max'])}")
    print(f"\n  Media: {fmt(stats['mean'])} tokens  |  Rango: {fmt(stats['min'])} – {fmt(stats['max'])}")

    # ── Por agente ─────────────────────────────────────────────────────────
    print(f"\n{'─' * W}")
    print(f"  TOKENS PROMEDIO POR AGENTE")
    print(f"{'─' * W}")
    a_stats = agent_stats(sessions)
    if a_stats:
        agent_means = {a: sum(v) / len(v) for a, v in a_stats.items()}
        max_mean = max(agent_means.values()) or 1
        print(f"  {'Agente':<28} {'Promedio':>10}  {'%Total':>7}  Barra")
        print(f"  {'─'*28} {'─'*10}  {'─'*7}  {'─'*20}")
        total_mean = sum(agent_means.values())
        for agent, mean in sorted(agent_means.items(), key=lambda x: -x[1]):
            pct = mean / total_mean * 100 if total_mean else 0
            print(f"  {agent:<28} {fmt(mean):>10}  {pct:>6.1f}%  {bar(mean, max_mean, 20)}")
    else:
        print("  Sin desglose por agente.")

    # ── Outliers ───────────────────────────────────────────────────────────
    p90_val = stats["p90"]
    outliers = [s for s in sessions if s["tokens"]["total"] > p90_val]
    print(f"\n{'─' * W}")
    print(f"  OUTLIERS  (> p90 = {fmt(p90_val)} tokens)  —  {len(outliers)} sesión/es")
    print(f"{'─' * W}")
    if outliers:
        print(f"  {'ID Sesión':<36} {'Tokens':>10}  {'Estado':<10}  {'Fecha'}")
        print(f"  {'─'*36} {'─'*10}  {'─'*10}  {'─'*10}")
        for s in sorted(outliers, key=lambda x: -x["tokens"]["total"]):
            sid = s.get("session_id", "?")[:36]
            tok = s["tokens"]["total"]
            st  = s.get("status", "?")
            ts  = s.get("timestamp", "")[:10]
            flag = "  ⚠ MUY ALTO" if tok > stats["p99"] else ""
            print(f"  {sid:<36} {fmt(tok):>10}  {st:<10}  {ts}{flag}")

        biggest = max(outliers, key=lambda x: x["tokens"]["total"])
        by_agent = biggest.get("tokens", {}).get("by_agent", {})
        if by_agent:
            print(f"\n  Desglose de la sesión con más tokens ({fmt(biggest['tokens']['total'])} tokens):")
            for agent, usage in sorted(by_agent.items(),
                                       key=lambda x: -(x[1].get("total_tokens", 0) if isinstance(x[1], dict) else 0)):
                t = usage.get("total_tokens", 0) if isinstance(usage, dict) else 0
                pct = t / biggest["tokens"]["total"] * 100 if biggest["tokens"]["total"] else 0
                print(f"    {agent:<30} {fmt(t):>10}  ({pct:.1f}%)")
    else:
        print("  Ninguna sesión supera el p90.")

    # ── Histograma ─────────────────────────────────────────────────────────
    print(f"\n{'─' * W}")
    print(f"  HISTOGRAMA")
    print(f"{'─' * W}")
    _print_histogram(totals)
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
        counts[min(int((v - min_v) / step), bins - 1)] += 1
    max_c = max(counts) or 1
    for i, c in enumerate(counts):
        lo, hi = min_v + i * step, min_v + (i + 1) * step
        print(f"  {fmt(lo):>10} – {fmt(hi):>10}  {bar(c, max_c, 25)}  {c}")


# ── HTML ──────────────────────────────────────────────────────────────────────

def generate_html(sessions: list[dict], out_path: Path, source: str = "BD") -> None:
    totals = [s["tokens"]["total"] for s in sessions if s["tokens"]["total"] > 0]
    stats = compute_stats(totals) if totals else {}
    p90_val = stats.get("p90", 0)
    a_stats = agent_stats(sessions)
    agent_means = {a: sum(v) / len(v) for a, v in a_stats.items()} if a_stats else {}
    total_mean = sum(agent_means.values()) or 1

    def css_bar(value: float, max_val: float, color: str = "#3498db") -> str:
        pct = int(value / max_val * 100) if max_val else 0
        return (
            f'<div style="background:#ecf0f1;border-radius:4px;height:16px">'
            f'<div style="background:{color};width:{pct}%;height:100%;border-radius:4px;'
            f'transition:width .3s"></div></div>'
        )

    max_tok = max(totals) if totals else 1

    # Cards
    cards = ""
    for label, value, color in [
        ("Sesiones", len(sessions), "#3498db"),
        ("Con tokens", len(totals), "#27ae60"),
        ("Mediana", f"{int(stats.get('p50', 0)):,}" if stats else "–", "#9b59b6"),
        ("p90", f"{int(stats.get('p90', 0)):,}" if stats else "–", "#e67e22"),
        ("Outliers >p90", sum(1 for t in totals if t > p90_val), "#e74c3c"),
    ]:
        cards += (
            f'<div class="card" style="background:{color}">'
            f'<div class="card-val">{value}</div>'
            f'<div class="card-lbl">{label}</div>'
            f'</div>\n'
        )

    # Percentile rows
    pct_rows = ""
    if stats:
        for label, key in [("Min", "min"), ("p25", "p25"), ("Mediana (p50)", "p50"),
                            ("p75", "p75"), ("p90", "p90"), ("p95", "p95"),
                            ("p99", "p99"), ("Max", "max")]:
            v = stats[key]
            color = "#e74c3c" if key in ("p90", "p95", "p99", "max") else "#3498db"
            pct_rows += (
                f"<tr><td><strong>{label}</strong></td>"
                f"<td class='num'>{int(v):,}</td>"
                f"<td>{css_bar(v, stats['max'], color)}</td></tr>\n"
            )

    # Agent rows
    agent_rows = ""
    if agent_means:
        max_mean = max(agent_means.values()) or 1
        for agent, mean in sorted(agent_means.items(), key=lambda x: -x[1]):
            pct = mean / total_mean * 100
            agent_rows += (
                f"<tr><td>{agent}</td>"
                f"<td class='num'>{int(mean):,}</td>"
                f"<td class='num'>{pct:.1f}%</td>"
                f"<td>{css_bar(mean, max_mean, '#9b59b6')}</td></tr>\n"
            )

    # Session rows
    session_rows = ""
    for s in sessions:
        tok = s["tokens"]["total"]
        sid = s.get("session_id", "?")[:36]
        st  = s.get("status", "?")
        ts  = s.get("timestamp", "")[:16].replace("T", " ")
        is_out = tok > p90_val and tok > 0
        row_bg = "#fff5f5" if is_out else "white"
        st_color = "#27ae60" if st == "success" else ("#e67e22" if st == "fail" else "#e74c3c")
        flag = '<span style="color:#e74c3c"> ⚠</span>' if is_out else ""
        session_rows += (
            f'<tr style="background:{row_bg}">'
            f'<td class="mono">{sid}</td>'
            f'<td>{ts}</td>'
            f'<td style="color:{st_color};font-weight:bold">{st}</td>'
            f'<td class="num">{tok:,}{flag}</td>'
            f'<td style="min-width:150px">{css_bar(tok, max_tok) if tok else ""}</td>'
            f'</tr>\n'
        )

    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")

    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Token Dashboard — PACI Workflow</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
          background: #f0f2f5; color: #2c3e50; padding: 24px; }}
  h1   {{ font-size: 22px; font-weight: 700; margin-bottom: 2px; }}
  .sub {{ color: #7f8c8d; font-size: 13px; margin-bottom: 24px; }}
  .cards {{ display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 28px; }}
  .card {{ color: white; padding: 16px 22px; border-radius: 10px;
           text-align: center; min-width: 110px; }}
  .card-val {{ font-size: 30px; font-weight: 700; }}
  .card-lbl {{ font-size: 11px; margin-top: 4px; opacity: .9; }}
  .box  {{ background: white; border-radius: 10px; padding: 20px;
           margin-bottom: 20px; box-shadow: 0 1px 4px rgba(0,0,0,.08); }}
  h2   {{ font-size: 14px; font-weight: 700; color: #555; text-transform: uppercase;
          letter-spacing: .5px; border-bottom: 2px solid #f0f2f5; padding-bottom: 10px;
          margin-bottom: 16px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  th   {{ text-align: left; padding: 8px 10px; background: #f8f9fa;
          color: #888; font-weight: 600; border-bottom: 2px solid #f0f0f0; }}
  td   {{ padding: 8px 10px; border-bottom: 1px solid #f5f5f5; vertical-align: middle; }}
  tr:last-child td {{ border-bottom: none; }}
  .num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  .mono {{ font-family: monospace; font-size: 11px; }}
  .foot {{ text-align: center; color: #bdc3c7; font-size: 11px; margin-top: 16px; }}
</style>
</head>
<body>
<h1>Dashboard de Tokens</h1>
<div class="sub">PACI Workflow &nbsp;|&nbsp; Fuente: {source} &nbsp;|&nbsp; Generado {generated_at}</div>

<div class="cards">{cards}</div>

<div class="box">
  <h2>Distribución por percentiles</h2>
  <table>
    <tr><th>Percentil</th><th class="num">Tokens</th><th style="width:300px">Barra</th></tr>
    {pct_rows or '<tr><td colspan="3" style="color:#aaa">Sin datos</td></tr>'}
  </table>
</div>

<div class="box">
  <h2>Consumo promedio por agente</h2>
  <table>
    <tr><th>Agente</th><th class="num">Promedio</th><th class="num">% del total</th><th style="width:250px">Barra</th></tr>
    {agent_rows or '<tr><td colspan="4" style="color:#aaa">Sin datos</td></tr>'}
  </table>
</div>

<div class="box">
  <h2>Sesiones — más recientes primero
    <span style="font-weight:400;color:#e74c3c;font-size:12px;margin-left:8px">
      ⚠ = outlier &gt; p90 ({int(p90_val):,} tokens)
    </span>
  </h2>
  <table>
    <tr><th>ID Sesión</th><th>Fecha</th><th>Estado</th>
        <th class="num">Tokens</th><th style="min-width:150px">Barra</th></tr>
    {session_rows or '<tr><td colspan="5" style="color:#aaa">Sin sesiones</td></tr>'}
  </table>
</div>

<div class="foot">Token Dashboard · PACI Workflow · {generated_at}</div>
</body>
</html>"""

    out_path.write_text(html, encoding="utf-8")
    print(f"  ✓ HTML generado: {out_path}")


# ── Main ──────────────────────────────────────────────────────────────────────

async def async_main(args) -> None:
    if args.create_views:
        await apply_views()
        return

    db_url = os.environ.get("BD_LOGS")

    if db_url and not args.dir:
        # ── Modo BD ───────────────────────────────────────────────────────
        only_month = not args.all
        label = "BD · mes actual" if only_month else "BD · todas las sesiones"
        try:
            sessions = await load_from_db(only_current_month=only_month)
            source = label
        except Exception as e:
            print(f"  [Aviso] No se pudo conectar a la BD: {e}")
            print("  Usando fallback JSON en token_reports/")
            sessions = load_from_files(Path(__file__).parent / "token_reports")
            source = "JSON local"
    else:
        # ── Modo JSON (fallback / --dir) ───────────────────────────────────
        d = Path(args.dir) if args.dir else Path(__file__).parent / "token_reports"
        sessions = load_from_files(d)
        source = f"JSON ({d.name}/)"

    print_dashboard(sessions, source=source)

    if args.html:
        out = Path(__file__).parent / "token_dashboard.html"
        generate_html(sessions, out, source=source)


def main():
    parser = argparse.ArgumentParser(description="Dashboard de tokens PACI Workflow")
    parser.add_argument("--all", action="store_true",
                        help="Mostrar todas las sesiones (default: solo mes actual)")
    parser.add_argument("--html", action="store_true",
                        help="Genera token_dashboard.html")
    parser.add_argument("--dir", metavar="PATH",
                        help="Leer desde directorio de JSON files en lugar de BD")
    parser.add_argument("--create-views", action="store_true",
                        help="Crear/actualizar vistas en la BD y salir")
    args = parser.parse_args()
    asyncio.run(async_main(args))


if __name__ == "__main__":
    main()
