"""
disk_analytics.py — Tela "Backups em Disco".

GET /analytics/disk?client_id=&job=   — consumo por VM/job, vencimento de
imutabilidade (logico + real calibrado), espaco livre real por extent
(performance x capacity) e alerta de extent cheio.

A separacao performance x capacity vem das amostras de capacidade; o consumo
por VM vem dos pontos de restauracao (ja deduplicados por rp_id na ingestao).
O "real" aplica o fator de calibracao logico/real (block cloning).
"""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import (
    Client, DiskUsageImport, DiskBackupPoint, RepoCapacitySample,
)
from app.services import branding_service
from app.services.classification_service import situation_badge_class

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
templates.env.globals["get_branding"] = branding_service.get_branding
templates.env.globals["situation_badge_class"] = situation_badge_class
templates.env.globals["now"] = datetime.now


def _fmt_bytes(n):
    """Formata bytes em unidade binaria (mesma escala do console Veeam)."""
    if n is None:
        return "—"
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if abs(n) < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} EB"


templates.env.filters["fmt_bytes"] = _fmt_bytes


def _real(logical, calib):
    """Converte tamanho logico -> real usando o fator de calibracao.
    (func.sum de BigInteger volta Decimal no Postgres -> normaliza p/ float.)"""
    if logical and calib:
        return int(float(logical) / calib)
    return None


def disk_summary(db: Session, client_id: int, job: str | None = None,
                 expira_ate: str | None = None, modo: str = "dia") -> dict:
    # modo do drill-down: "dia" = vence EXATAMENTE na data;
    #                     "ate" = tudo liberável ATÉ a data (acumulado).
    modo = "ate" if modo == "ate" else "dia"
    imp = (db.query(DiskUsageImport)
           .filter(DiskUsageImport.client_id == client_id,
                   DiskUsageImport.status == "completed")
           .order_by(DiskUsageImport.imported_at.desc())
           .first())
    if not imp:
        return {"has_data": False}
    iid = imp.id
    calib = imp.calib_factor

    # ── Capacidade dos repositorios (performance x capacity) ─────────────────
    samples = (db.query(RepoCapacitySample)
               .filter(RepoCapacitySample.disk_usage_import_id == iid).all())

    def tier_list(t):
        out = []
        for s in samples:
            if s.tier != t:
                continue
            pct = (round(100 * s.used_bytes / s.total_bytes)
                   if (s.total_bytes and s.used_bytes is not None) else None)
            out.append({
                "name": s.name, "repo_type": s.repo_type, "sobr": s.sobr_name,
                "total": s.total_bytes, "free": s.free_bytes, "used": s.used_bytes,
                "pct": pct, "path": s.path,
                "alert": (pct is not None and pct >= 90),
            })
        return sorted(out, key=lambda x: -(x["used"] or 0))

    performance = tier_list("Performance")
    capacity    = tier_list("Capacity")
    standalone  = tier_list("Standalone")
    archive     = tier_list("Archive")
    perf_total = sum((x["total"] or 0) for x in performance)
    perf_free  = sum((x["free"]  or 0) for x in performance)
    perf_used  = sum((x["used"]  or 0) for x in performance)

    # ── Pontos de restauracao (opcional filtrado por job) ────────────────────
    base = db.query(DiskBackupPoint).filter(DiskBackupPoint.disk_usage_import_id == iid)
    if job:
        base = base.filter(DiskBackupPoint.job_name == job)

    logical = base.with_entities(
        func.coalesce(func.sum(DiskBackupPoint.size_bytes), 0)).scalar() or 0
    n_points = base.count()
    n_vms = base.with_entities(DiskBackupPoint.vm_name).distinct().count()

    # por VM (top 60 por consumo)
    vm_rows = (base.with_entities(
                    DiskBackupPoint.vm_name, DiskBackupPoint.job_name,
                    func.sum(DiskBackupPoint.size_bytes),
                    func.count(), func.max(DiskBackupPoint.restore_point_at))
               .group_by(DiskBackupPoint.vm_name, DiskBackupPoint.job_name)
               .order_by(func.sum(DiskBackupPoint.size_bytes).desc())
               .limit(60).all())
    vms = [{
        "vm": r[0], "job": r[1], "logical": int(r[2] or 0),
        "real": _real(r[2], calib), "points": r[3],
        "last": r[4].strftime("%d/%m/%Y %H:%M") if r[4] else "—",
    } for r in vm_rows]

    # imutabilidade por faixa de vencimento (logico + real)
    now = datetime.utcnow()
    labels = ["expirado", "ate_7d", "ate_30d", "ate_60d", "ate_90d", "mais_90d"]
    buckets = {k: 0 for k in labels}
    imm = (base.filter(DiskBackupPoint.immutable_until.isnot(None))
           .with_entities(DiskBackupPoint.immutable_until, DiskBackupPoint.size_bytes).all())
    for until, size in imm:
        size = size or 0
        d = (until - now).days
        if   d <= 0:  buckets["expirado"] += size
        elif d <= 7:  buckets["ate_7d"]   += size
        elif d <= 30: buckets["ate_30d"]  += size
        elif d <= 60: buckets["ate_60d"]  += size
        elif d <= 90: buckets["ate_90d"]  += size
        else:         buckets["mais_90d"] += size
    imm_buckets = [{"label": k, "logical": buckets[k], "real": _real(buckets[k], calib)}
             for k in labels]

    # ── Cronograma de expiracao POR DIA (com acumulado real) ─────────────────
    from sqlalchemy import cast, Date
    # VBR/Veeam exibe a imutabilidade no fuso LOCAL do servidor (America/Sao_Paulo,
    # UTC-3, sem DST). ImmutableTillUtc e guardado em UTC -> converter p/ local
    # ANTES de truncar em data, senao valores logo apos a meia-noite UTC caem no
    # dia errado (ex.: 27/08 01:59 UTC = 26/08 22:59 local, o que o Veeam mostra).
    _imm_local_ts = DiskBackupPoint.immutable_until - timedelta(hours=3)
    _imm_local = cast(_imm_local_ts, Date)
    daily_rows = (base.filter(DiskBackupPoint.immutable_until.isnot(None))
                  .with_entities(_imm_local,
                                 func.count(),
                                 func.coalesce(func.sum(DiskBackupPoint.size_bytes), 0),
                                 func.min(_imm_local_ts),
                                 func.max(_imm_local_ts))
                  .group_by(_imm_local)
                  .order_by(_imm_local).all())
    today = now.date()
    immut_daily = []
    cum = 0
    for d, c, s, tmin, tmax in daily_rows:
        s = int(s or 0)
        r = _real(s, calib) or 0
        cum += r
        hmin = tmin.strftime("%H:%M") if tmin else ""
        hmax = tmax.strftime("%H:%M") if tmax else ""
        htxt = hmin if (not hmax or hmax == hmin) else f"{hmin}–{hmax}"
        immut_daily.append({
            "date": d.strftime("%d/%m/%Y"), "iso": d.isoformat(),
            "time": htxt,                      # hora LOCAL (min–max) de vencimento no dia
            "points": c, "logical": s, "real": _real(s, calib),
            "cum_real": cum, "expired": d <= today,
        })

    # seletor de data: quanto vence NO dia e ACUMULADO ate o dia + quais jobs/VMs
    expira_sel = None
    expira_jobs: list = []
    expira_vms: list = []
    if expira_ate:
        try:
            from datetime import date as _date
            target = _date.fromisoformat(expira_ate)
            on_pts = on_log = cum_pts = cum_log = 0
            for d, c, s, _tmin, _tmax in daily_rows:
                s = int(s or 0)
                if d <= target:
                    cum_pts += c; cum_log += s
                if d == target:
                    on_pts = c; on_log = s
            expira_sel = {
                "date": target.strftime("%d/%m/%Y"),
                "on_points": on_pts, "on_logical": on_log, "on_real": _real(on_log, calib),
                "cum_points": cum_pts, "cum_logical": cum_log, "cum_real": _real(cum_log, calib),
            }
            # drill-down: conforme o modo escolhido —
            #   "dia" → vence EXATAMENTE na data (o que liberar naquele dia);
            #   "ate" → tudo liberável ATÉ a data (acumulado), útil quando o dia
            #           isolado vem quase vazio (a imutabilidade vence em lotes).
            if modo == "ate":
                day_base = base.filter(_imm_local <= target)
            else:
                day_base = base.filter(_imm_local == target)
            jr = (day_base.with_entities(
                        DiskBackupPoint.job_name, func.count(),
                        func.coalesce(func.sum(DiskBackupPoint.size_bytes), 0))
                  .group_by(DiskBackupPoint.job_name)
                  .order_by(func.sum(DiskBackupPoint.size_bytes).desc()).all())
            expira_jobs = [{"job": r[0], "points": r[1],
                            "logical": int(r[2] or 0), "real": _real(r[2], calib)} for r in jr]
            vr = (day_base.with_entities(
                        DiskBackupPoint.vm_name, DiskBackupPoint.job_name, func.count(),
                        func.coalesce(func.sum(DiskBackupPoint.size_bytes), 0))
                  .group_by(DiskBackupPoint.vm_name, DiskBackupPoint.job_name)
                  .order_by(func.sum(DiskBackupPoint.size_bytes).desc()).limit(300).all())
            expira_vms = [{"vm": r[0], "job": r[1], "points": r[2],
                           "logical": int(r[3] or 0), "real": _real(r[3], calib)} for r in vr]
        except ValueError:
            pass

    # extent por ponto so existe se a coleta usou -WithExtent
    has_extent = (db.query(DiskBackupPoint.id)
                  .filter(DiskBackupPoint.disk_usage_import_id == iid,
                          DiskBackupPoint.extent_name.isnot(None)).first() is not None)

    # GFS (retencao estendida)
    gfs_rows = (base.filter(DiskBackupPoint.gfs_period.isnot(None))
                .with_entities(DiskBackupPoint.gfs_period,
                               func.sum(DiskBackupPoint.size_bytes), func.count())
                .group_by(DiskBackupPoint.gfs_period)
                .order_by(func.sum(DiskBackupPoint.size_bytes).desc()).all())
    gfs = [{"period": r[0], "logical": int(r[1] or 0), "real": _real(r[1], calib),
            "points": r[2]} for r in gfs_rows]

    # lista de jobs (para o filtro)
    job_rows = (db.query(DiskBackupPoint.job_name)
                .filter(DiskBackupPoint.disk_usage_import_id == iid,
                        DiskBackupPoint.job_name.isnot(None))
                .distinct().order_by(DiskBackupPoint.job_name).all())
    jobs = [j[0] for j in job_rows]

    return {
        "has_data": True,
        "import_id": iid,
        "collected_range": (
            (imp.min_point_at.strftime("%d/%m/%Y") if imp.min_point_at else "—")
            + " a "
            + (imp.max_point_at.strftime("%d/%m/%Y") if imp.max_point_at else "—")),
        "imported_at": imp.imported_at.strftime("%d/%m/%Y %H:%M") if imp.imported_at else "—",
        "calib": calib,
        "performance": performance, "capacity": capacity,
        "standalone": standalone, "archive": archive,
        "perf_total": perf_total, "perf_free": perf_free, "perf_used": perf_used,
        "perf_pct": (round(100 * perf_used / perf_total) if perf_total else None),
        "logical": int(logical), "real": _real(logical, calib),
        "n_points": n_points, "n_vms": n_vms,
        "vms": vms, "immut": imm_buckets, "gfs": gfs,
        "immut_daily": immut_daily, "expira_ate": expira_ate, "expira_sel": expira_sel,
        "expira_modo": modo,
        "expira_jobs": expira_jobs, "expira_vms": expira_vms,
        "has_extent": has_extent,
        "jobs": jobs, "job": job,
    }


@router.get("/analytics/disk", response_class=HTMLResponse)
def analytics_disk_page(
    request: Request,
    client_id: int | None = Query(None),
    job: str | None = Query(None),
    expira_ate: str | None = Query(None),
    modo: str = Query("dia"),
    db: Session = Depends(get_db),
):
    if not request.session.get("user_id"):
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/login", status_code=303)

    clients = db.query(Client).filter(Client.is_active.is_(True)).order_by(Client.name).all()
    data = {"has_data": False}
    if client_id:
        data = disk_summary(db, client_id, (job or "").strip() or None,
                            (expira_ate or "").strip() or None, modo)
    return templates.TemplateResponse("analytics_disk.html", {
        "request": request, "clients": clients,
        "selected_client_id": client_id, "data": data,
    })
