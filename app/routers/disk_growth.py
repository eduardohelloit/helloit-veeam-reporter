"""
disk_growth.py — Tela "Crescimento de Fulls".

Evolução do TAMANHO dos backups full ao longo do tempo — por VM e no agregado
do ambiente. Munição para justificar aquisição de repositório (performance
tier): mostra que o ambiente cresce mesmo quando "poucas máquinas foram
adicionadas", separando o crescimento de VMs NOVAS do de VMs EXISTENTES.

Fonte: o histórico que já vive nos pontos de restauração da última carga de
"Backups em Disco" (restore_point_at + is_full + size_bytes). Um único import
traz meses de histórico (fulls semanais + pontos GFS). Sem coletor novo.

Métrica: tamanho LÓGICO por VM (é o que existe por máquina); no agregado do
ambiente aplica-se o fator de calibração (block cloning) para estimar o espaço
REAL em disco.
"""
from __future__ import annotations

import io
from bisect import bisect_right
from collections import defaultdict
from datetime import datetime, date, timedelta

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Client, DiskUsageImport, DiskBackupPoint
from app.services import branding_service
from app.routers.disk_analytics import _fmt_bytes, _real

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
templates.env.globals["get_branding"] = branding_service.get_branding
templates.env.globals["now"] = datetime.now
templates.env.filters["fmt_bytes"] = _fmt_bytes

GB = 1024 ** 3
_DEAD_DAYS = 45   # VM sem full há mais de N dias no checkpoint = fora do ambiente


# ── helpers ─────────────────────────────────────────────────────────────────
def _parse_date(s):
    try:
        return date.fromisoformat(s) if s else None
    except (ValueError, TypeError):
        return None


def _months(d0: date, d1: date) -> float:
    """Distância em meses (float) entre duas datas."""
    return max((d1 - d0).days / 30.44, 0.0)


def _month_checkpoints(df: date, dt: date):
    """Lista de (label 'MM/AAAA', data_checkpoint) — fim de cada mês no intervalo."""
    out = []
    y, m = df.year, df.month
    while (y, m) <= (dt.year, dt.month):
        # último dia do mês, limitado a dt
        if m == 12:
            nxt = date(y + 1, 1, 1)
        else:
            nxt = date(y, m + 1, 1)
        cp = min(date.fromordinal(nxt.toordinal() - 1), dt)
        out.append((f"{m:02d}/{y}", cp))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def _lin_project(xs, ys, ahead_months):
    """Regressão linear simples. Retorna (slope_por_mes, valor_projetado)."""
    n = len(xs)
    if n < 2:
        return 0.0, (ys[-1] if ys else 0.0)
    mx = sum(xs) / n
    my = sum(ys) / n
    var = sum((x - mx) ** 2 for x in xs)
    if var == 0:
        return 0.0, ys[-1]
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / var
    proj = ys[-1] + slope * ahead_months
    return slope, max(proj, 0.0)


class _Series:
    """Série ordenada de fulls de uma VM: (data, tamanho)."""
    __slots__ = ("dates", "sizes")

    def __init__(self):
        self.dates: list[date] = []
        self.sizes: list[int] = []

    def size_at(self, t: date):
        """Tamanho do último full com data <= t (None se não há)."""
        i = bisect_right(self.dates, t)
        return self.sizes[i - 1] if i > 0 else None

    @property
    def first(self):
        return self.dates[0]

    @property
    def last(self):
        return self.dates[-1]


# ── núcleo ──────────────────────────────────────────────────────────────────
def growth_analysis(db: Session, client_id: int, job=None,
                    date_from=None, date_to=None, vm=None, top_n=15) -> dict:
    imp = (db.query(DiskUsageImport)
           .filter(DiskUsageImport.client_id == client_id,
                   DiskUsageImport.status == "completed")
           .order_by(DiskUsageImport.imported_at.desc()).first())
    if not imp:
        return {"has_data": False}
    calib = imp.calib_factor

    q = (db.query(DiskBackupPoint.vm_name, DiskBackupPoint.restore_point_at,
                  DiskBackupPoint.size_bytes, DiskBackupPoint.gfs_period,
                  DiskBackupPoint.job_name)
         .filter(DiskBackupPoint.disk_usage_import_id == imp.id,
                 DiskBackupPoint.is_full.is_(True),
                 DiskBackupPoint.restore_point_at.isnot(None),
                 DiskBackupPoint.size_bytes.isnot(None),
                 DiskBackupPoint.vm_name.isnot(None)))
    if job:
        q = q.filter(DiskBackupPoint.job_name == job)

    df_f = _parse_date(date_from)
    dt_f = _parse_date(date_to)

    # jobs e lista de VMs para os seletores
    jobs = sorted({r[0] for r in db.query(DiskBackupPoint.job_name)
                   .filter(DiskBackupPoint.disk_usage_import_id == imp.id,
                           DiskBackupPoint.job_name.isnot(None)).distinct()})

    # per (vm, dia) -> maior tamanho (dedup agregado x per-VM no mesmo ponto)
    daily = defaultdict(dict)     # vm -> {date: size}
    gfs_days = defaultdict(set)   # vm -> {date} que são GFS
    all_vms = set()
    for vm_name, ts, size, gfs, _job in q.all():
        d = ts.date()
        if dt_f and d > dt_f:
            continue
        all_vms.add(vm_name)
        if size and size > daily[vm_name].get(d, 0):
            daily[vm_name][d] = int(size)
        if gfs and str(gfs).strip():
            gfs_days[vm_name].add(d)

    if not daily:
        return {"has_data": True, "empty": True, "imp": imp, "jobs": jobs,
                "vms": [], "calib": calib}

    series: dict[str, _Series] = {}
    gmin = gmax = None
    for vm_name, dmap in daily.items():
        s = _Series()
        for d in sorted(dmap):
            s.dates.append(d)
            s.sizes.append(dmap[d])
        series[vm_name] = s
        gmin = s.first if gmin is None or s.first < gmin else gmin
        gmax = s.last if gmax is None or s.last > gmax else gmax

    dt = dt_f or gmax
    if dt > gmax:
        dt = gmax
    # baseline: por padrão ~6 meses atrás (janela com boa cobertura de coorte);
    # o usuário pode ampliar/reduzir pelo filtro "De".
    from datetime import timedelta as _td
    df = df_f or max(gmin, dt - _td(days=182))
    if df < gmin:
        df = gmin

    vm_list = sorted(series.keys())

    # ── DETALHE DE UMA VM ────────────────────────────────────────────────────
    if vm and vm in series:
        s = series[vm]
        pts = [{"date": d.strftime("%d/%m/%Y"),
                "iso": d.isoformat(),
                "gb": round(sz / GB, 1),
                "bytes": sz,
                "gfs": d in gfs_days[vm]}
               for d, sz in zip(s.dates, s.sizes) if (not df_f or d >= df) and d <= dt]
        first_sz = s.sizes[0]
        last_sz = s.sizes[-1]
        xs = [_months(s.dates[0], d) for d in s.dates]
        slope, proj = _lin_project(xs, [float(x) for x in s.sizes], 12)
        delta = last_sz - first_sz
        return {
            "has_data": True, "empty": False, "imp": imp, "calib": calib,
            "jobs": jobs, "vms": vm_list, "vm": vm, "mode": "vm",
            "range": {"from": s.first.strftime("%d/%m/%Y"), "to": s.last.strftime("%d/%m/%Y")},
            "vm_detail": {
                "name": vm, "points": pts, "n": len(s.dates),
                "first": first_sz, "last": last_sz,
                "delta": delta, "pct": (delta / first_sz * 100 if first_sz else 0),
                "per_month": slope, "proj12": proj,
                "proj12_delta": proj - last_sz,
            },
            "chart": {
                "labels": [p["date"] for p in pts],
                "gb": [p["gb"] for p in pts],
                "gfs": [p["gfs"] for p in pts],
            },
        }

    # ── VISÃO MACRO (ambiente) ───────────────────────────────────────────────
    # LIMITAÇÃO HONESTA: num único import, cada VM só tem histórico de full até
    # onde a retenção dela guarda (a maioria ~2 meses de fulls semanais; só as
    # com GFS mensal/anual vão mais fundo). Portanto:
    #  • o RITMO do ambiente é medido por VM, cada uma na SUA janela — robusto,
    #    usa todas as VMs (a base do número principal e da projeção);
    #  • a COORTE de histórico longo (full <= df) é corroboração: mesmas VMs,
    #    crescendo ao longo de meses, à prova de "adicionamos poucas máquinas";
    #  • o GRÁFICO usa só os meses de boa cobertura (evita a rampa artificial de
    #    quando as VMs "aparecem" conforme a retenção começa).
    def _active_at(s, t):
        return not ((t - s.last).days > _DEAD_DAYS and s.last < t)

    _r = lambda v: _real(v, calib) or 0

    current_total = sum(s.size_at(dt) or 0 for _v, s in series.items()
                        if _active_at(s, dt) and s.size_at(dt))
    n_active = sum(1 for _v, s in series.items() if _active_at(s, dt) and s.size_at(dt))

    # ritmo do ambiente = Σ dos ritmos por VM (cada um na SUA janela medível)
    env_rate = 0.0
    spans = []
    for _v, s in series.items():
        if not _active_at(s, dt):
            continue
        ds = [d for d in s.dates if d >= df]
        if len(ds) < 2:
            continue
        i0 = s.dates.index(ds[0])
        mm = _months(s.dates[i0], s.last)
        if mm >= 1.0:
            env_rate += (s.size_at(dt) - s.sizes[i0]) / mm
            spans.append(mm)
    proj12 = current_total + env_rate * 12
    avg_span = round(sum(spans) / len(spans), 1) if spans else 0.0

    # coorte de histórico longo (full <= df e ainda ativa) — corroboração
    cohort = [vn for vn, s in series.items()
              if s.size_at(df) is not None and s.first <= df and _active_at(s, dt)]
    coh_start = sum(series[vn].size_at(df) or 0 for vn in cohort)
    coh_end = sum(series[vn].size_at(dt) or 0 for vn in cohort)

    # gráfico: COORTE FIXA semanal. Janela recente (fulls são semanais); plota o
    # MESMO conjunto de VMs (as que já tinham full no início da janela) em todas
    # as semanas -> cobertura constante, sem rampa artificial, linha limpa.
    w_days = min((dt - gmin).days, 56)          # até 8 semanas de janela
    w_start = dt - timedelta(days=w_days)
    wk = []
    d = w_start
    while d <= dt:
        wk.append(d)
        d = d + timedelta(days=7)
    if wk[-1] != dt:
        wk.append(dt)
    chart_vms = [vn for vn, s in series.items()
                 if s.size_at(w_start) is not None and _active_at(s, dt)]
    foot_w = [sum(series[vn].size_at(cp) or 0 for vn in chart_vms) for cp in wk]
    wk_lbl = [c.strftime("%d/%m") for c in wk]
    gi = 0
    n_chart_vms = len(chart_vms)

    # movers: cada VM medida na SUA janela (transparência: histórico em meses)
    movers = []
    for vn, s in series.items():
        end = s.size_at(dt)
        if end is None or not _active_at(s, dt):
            continue
        fd = df if (s.size_at(df) is not None and s.first <= df) else next((d for d in s.dates if d >= df), s.first)
        start_sz = s.size_at(fd) or s.sizes[0]
        delta = end - start_sz
        mm = _months(fd, s.last)
        movers.append({
            "vm": vn, "first": start_sz, "last": end, "delta": delta,
            "pct": (delta / start_sz * 100 if start_sz else 0),
            "per_month": (delta / mm if mm >= 1.0 else 0.0),
            "hist": round(mm, 1),
            "n": len([d for d in s.dates if d >= fd]),
        })
    movers.sort(key=lambda x: x["delta"], reverse=True)

    return {
        "has_data": True, "empty": False, "mode": "macro", "imp": imp, "calib": calib,
        "jobs": jobs, "vms": vm_list, "vm": None,
        "range": {"from": df.strftime("%d/%m/%Y"), "to": dt.strftime("%d/%m/%Y"),
                  "months": round(_months(df, dt), 1)},
        "kpi": {
            "current_real": _r(current_total),
            "rate_real_month": _r(int(env_rate)),
            "proj12_real": _r(int(proj12)),
            "proj12_delta_real": _r(int(proj12 - current_total)),
            "n_active": n_active, "avg_span": avg_span,
        },
        "cohort": {   # corroboração: mesmas VMs, crescendo
            "df": df.strftime("%d/%m/%Y"), "n": len(cohort),
            "start_real": _r(coh_start), "end_real": _r(coh_end),
            "growth_real": _r(coh_end - coh_start),
            "growth_pct": ((coh_end - coh_start) / coh_start * 100 if coh_start else 0),
        },
        "movers": movers,
        "chart": {
            "labels": [wk_lbl[i] for i in range(gi, len(wk))],
            "total_gb": [round(_r(foot_w[i]) / GB, 1) for i in range(gi, len(wk))],
            "n_vms": n_chart_vms,
        },
    }


# ── rotas ───────────────────────────────────────────────────────────────────
@router.get("/analytics/disk-growth", response_class=HTMLResponse)
def disk_growth_page(
    request: Request,
    client_id: int | None = Query(None),
    job: str | None = Query(None),
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    vm: str | None = Query(None),
    top_n: int = Query(15, ge=3, le=50),
    db: Session = Depends(get_db),
):
    if not request.session.get("user_id"):
        return RedirectResponse(url="/login", status_code=303)

    clients = db.query(Client).filter(Client.is_active.is_(True)).order_by(Client.name).all()
    data = {"has_data": False}
    if client_id:
        data = growth_analysis(db, client_id, (job or "").strip() or None,
                               (date_from or "").strip() or None,
                               (date_to or "").strip() or None,
                               (vm or "").strip() or None, top_n)
    return templates.TemplateResponse("analytics_disk_growth.html", {
        "request": request, "clients": clients,
        "selected_client_id": client_id, "data": data,
        "f": {"job": job, "date_from": date_from, "date_to": date_to,
              "vm": vm, "top_n": top_n},
    })


@router.get("/analytics/disk-growth/export-excel")
def disk_growth_export(
    request: Request,
    client_id: int = Query(...),
    job: str | None = Query(None),
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    db: Session = Depends(get_db),
):
    if not request.session.get("user_id"):
        return RedirectResponse(url="/login", status_code=303)
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment

    data = growth_analysis(db, client_id, (job or "").strip() or None,
                           (date_from or "").strip() or None,
                           (date_to or "").strip() or None, None, 999)
    if not data.get("movers"):
        return RedirectResponse(url="/analytics/disk-growth", status_code=303)

    wb = Workbook()
    ws = wb.active
    ws.title = "Crescimento por VM"
    hf = PatternFill("solid", fgColor="0B5ED7")
    hfont = Font(color="FFFFFF", bold=True)
    ws.append(["VM", "Histórico (meses)", "1º full (GB)", "Último full (GB)",
               "Crescimento (GB)", "Crescimento (%)", "GB/mês", "Pontos"])
    for c in ws[1]:
        c.fill = hf; c.font = hfont; c.alignment = Alignment(horizontal="center")
    for m in data["movers"]:
        ws.append([m["vm"], m["hist"], round(m["first"] / GB, 1), round(m["last"] / GB, 1),
                   round(m["delta"] / GB, 1), round(m["pct"], 1),
                   round(m["per_month"] / GB, 2), m["n"]])

    ws2 = wb.create_sheet("Ambiente (mensal)")
    ws2.append(["Mês", "Real estimado (GB)"])
    for c in ws2[1]:
        c.fill = hf; c.font = hfont
    ch = data["chart"]
    for lbl, rl in zip(ch["labels"], ch["total_gb"]):
        ws2.append([lbl, rl])

    for w in (ws, ws2):
        for col in w.columns:
            wd = max((len(str(c.value or "")) for c in col), default=10)
            w.column_dimensions[col[0].column_letter].width = min(wd + 3, 42)

    buf = io.BytesIO(); wb.save(buf); buf.seek(0)
    fname = f"crescimento_fulls_{datetime.now():%Y%m%d_%H%M%S}.xlsx"
    return StreamingResponse(
        buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'})
