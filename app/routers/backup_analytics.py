"""
backup_analytics.py — Análise de performance de backups de VM.
Rota principal: GET /analytics/backups

Foco: identificar rotinas (job + VM) LENTAS vs. o próprio baseline, e
detectar incidentes simultâneos (muitas VMs lentas na mesma janela = infra).
"""
from __future__ import annotations

import io
import math
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import case, func, or_
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Client, BackupRoutine, BackupVmSession, UserClient
from app.services import branding_service
from app.services.classification_service import situation_badge_class

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
templates.env.globals["get_branding"] = branding_service.get_branding
templates.env.globals["situation_badge_class"] = situation_badge_class
templates.env.globals["now"] = datetime.now

_MEDIAN = lambda col: func.percentile_cont(0.5).within_group(col.asc())


# ── Auth helpers ──────────────────────────────────────────────────────────────

def _require_login(request: Request) -> None:
    if not request.session.get("user_id"):
        raise HTTPException(status_code=401, detail="Não autenticado.")


def _scoped_client_ids(db: Session, request: Request):
    if request.session.get("is_admin"):
        return None
    uid = request.session.get("user_id")
    if not uid:
        return set()
    return {r[0] for r in db.query(UserClient.client_id)
            .filter(UserClient.user_id == uid).all()}


def _assert_scope(client_id: int, db: Session, request: Request) -> None:
    allowed = _scoped_client_ids(db, request)
    if allowed is not None and client_id not in allowed:
        raise HTTPException(status_code=403, detail="Cliente fora do seu escopo.")


# ── Format helpers ────────────────────────────────────────────────────────────

def _parse_date(s: Optional[str], end_of_day: bool = False) -> Optional[datetime]:
    if not s:
        return None
    try:
        dt = datetime.strptime(s, "%Y-%m-%d")
        return dt.replace(hour=23, minute=59, second=59) if end_of_day else dt
    except ValueError:
        return None


def _fmt(dt):  return dt.strftime("%d/%m/%Y %H:%M:%S") if dt else None
def _iso(dt):  return dt.isoformat() if dt else None


def _fmt_dur(secs) -> Optional[str]:
    if secs is None:
        return None
    secs = int(round(abs(secs)))
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h{m:02d}m{s:02d}s" if h else (f"{m}m{s:02d}s" if m else f"{s}s")


def _sess_dict(s: BackupVmSession, routine: Optional[BackupRoutine] = None) -> dict:
    return {
        "id":               s.id,
        "job_session_id":   s.job_session_id,
        "task_session_id":  s.task_session_id,
        "job_name":         s.job_name_snapshot or (routine.job_name if routine else None),
        "vm_name":          s.vm_name or (routine.vm_name if routine else None),
        "result":           s.result,
        "started_at":       _iso(s.started_at),
        "started_at_fmt":   _fmt(s.started_at),
        "ended_at_fmt":     _fmt(s.ended_at),
        "duration_seconds": s.duration_seconds,
        "duration_fmt":     _fmt_dur(s.duration_seconds),
        "processed_gb":     s.processed_gb,
        "read_gb":          s.read_gb,
        "transferred_gb":   s.transferred_gb,
        "avg_speed_mbps":   s.avg_speed_mbps,
        "backup_type":      s.backup_type,
        "reason_raw":       s.reason_raw,
        "collected_at_fmt": _fmt(s.collected_at),
        "imported_at_fmt":  _fmt(s.imported_at),
        "source":           s.source,
        "backup_import_id": s.backup_import_id,
    }


# ── Página ────────────────────────────────────────────────────────────────────

@router.get("/analytics/backups", response_class=HTMLResponse)
def analytics_backups_page(request: Request, db: Session = Depends(get_db)):
    _require_login(request)
    allowed = _scoped_client_ids(db, request)
    q = db.query(Client).filter(Client.is_active.is_(True))
    if allowed is not None:
        q = q.filter(Client.id.in_(allowed)) if allowed else q.filter(False)
    clients = q.order_by(Client.name).all()
    return templates.TemplateResponse("analytics_backups.html", {
        "request": request, "clients": clients,
        "is_admin": bool(request.session.get("is_admin")),
    })


# ── API: autocomplete de rotinas (job + VM) ───────────────────────────────────

@router.get("/api/backups/analytics/routines")
def api_routines(
    request: Request,
    client_id: int = Query(...),
    search: str = Query(""),
    limit: int = Query(30, ge=1, le=100),
    db: Session = Depends(get_db),
):
    _require_login(request)
    _assert_scope(client_id, db, request)

    q = db.query(BackupRoutine).filter(BackupRoutine.client_id == client_id)
    if search.strip():
        pat = f"%{search.strip()}%"
        q = q.filter(or_(BackupRoutine.job_name.ilike(pat), BackupRoutine.vm_name.ilike(pat)))
    routines = q.order_by(BackupRoutine.vm_name).limit(limit).all()
    if not routines:
        return []

    rids = [r.id for r in routines]
    stats = {
        row.rid: row for row in db.query(
            BackupVmSession.backup_routine_id.label("rid"),
            func.count().label("total"),
            func.sum(case((BackupVmSession.result == "Failed",  1), else_=0)).label("failed"),
            func.sum(case((BackupVmSession.result == "Warning", 1), else_=0)).label("warning"),
            func.sum(case((BackupVmSession.result == "Success", 1), else_=0)).label("success"),
            func.max(BackupVmSession.started_at).label("last_exec"),
            _MEDIAN(BackupVmSession.duration_seconds).label("med"),
        ).filter(BackupVmSession.client_id == client_id,
                 BackupVmSession.backup_routine_id.in_(rids))
         .group_by(BackupVmSession.backup_routine_id).all()
    }

    out = []
    for r in routines:
        s = stats.get(r.id)
        med = int(s.med) if (s and s.med is not None) else None
        out.append({
            "routine_id":        r.id,
            "job_name":          r.job_name,
            "vm_name":           r.vm_name,
            "total_executions":  s.total if s else 0,
            "failed_count":      s.failed if s else 0,
            "warning_count":     s.warning if s else 0,
            "success_count":     s.success if s else 0,
            "last_execution_fmt": _fmt(s.last_exec) if s else None,
            "median_duration_seconds": med,
            "median_duration_fmt":     _fmt_dur(med),
        })
    out.sort(key=lambda x: (-x["failed_count"], -x["total_executions"]))
    return out


# ── Helper: janela base de uma rotina (last_n OU datas) ───────────────────────

def _routine_base_q(db, client_id, routine_id, dt_start, dt_end, last_n):
    base = db.query(BackupVmSession).filter(
        BackupVmSession.client_id == client_id,
        BackupVmSession.backup_routine_id == routine_id,
    )
    if last_n:
        sub = (db.query(BackupVmSession.id)
               .filter(BackupVmSession.client_id == client_id,
                       BackupVmSession.backup_routine_id == routine_id)
               .order_by(BackupVmSession.started_at.desc())
               .limit(last_n).subquery())
        return base.filter(BackupVmSession.id.in_(db.query(sub.c.id)))
    if dt_start:
        base = base.filter(BackupVmSession.started_at >= dt_start)
    if dt_end:
        base = base.filter(BackupVmSession.started_at <= dt_end)
    return base


# ── API: resumo da rotina (baseline) ──────────────────────────────────────────

@router.get("/api/backups/analytics/routine-summary")
def api_routine_summary(
    request: Request,
    client_id: int = Query(...),
    routine_id: int = Query(...),
    date_start: Optional[str] = Query(None),
    date_end: Optional[str] = Query(None),
    last_n: Optional[int] = Query(None, ge=1, le=20000),
    db: Session = Depends(get_db),
):
    _require_login(request)
    _assert_scope(client_id, db, request)
    rt = db.query(BackupRoutine).filter(
        BackupRoutine.id == routine_id, BackupRoutine.client_id == client_id).first()
    if not rt:
        raise HTTPException(status_code=404, detail="Rotina não encontrada.")

    dt_start = _parse_date(date_start)
    dt_end   = _parse_date(date_end, end_of_day=True)
    base = _routine_base_q(db, client_id, routine_id, dt_start, dt_end, last_n)

    agg = base.with_entities(
        func.count().label("total"),
        func.sum(case((BackupVmSession.result == "Success", 1), else_=0)).label("success"),
        func.sum(case((BackupVmSession.result == "Warning", 1), else_=0)).label("warning"),
        func.sum(case((BackupVmSession.result == "Failed",  1), else_=0)).label("failed"),
        func.min(BackupVmSession.started_at).label("first_exec"),
        func.max(BackupVmSession.started_at).label("last_exec"),
        func.avg(BackupVmSession.duration_seconds).label("avg_dur"),
        _MEDIAN(BackupVmSession.duration_seconds).label("med_dur"),
        func.max(BackupVmSession.duration_seconds).label("max_dur"),
        func.avg(BackupVmSession.avg_speed_mbps).label("avg_speed"),
        func.avg(BackupVmSession.transferred_gb).label("avg_transf"),
    ).first()

    total  = agg.total or 0
    failed = agg.failed or 0
    first_failure = (base.filter(BackupVmSession.result.in_(["Failed", "Warning"]))
                     .order_by(BackupVmSession.started_at.asc()).first())
    last_success  = (base.filter(BackupVmSession.result == "Success")
                     .order_by(BackupVmSession.started_at.desc()).first())

    by_type = dict(base.with_entities(BackupVmSession.backup_type, func.count())
                   .group_by(BackupVmSession.backup_type).all())

    med = int(agg.med_dur) if agg.med_dur is not None else None
    return {
        "job_name":           rt.job_name,
        "vm_name":            rt.vm_name,
        "total_executions":   total,
        "success_count":      agg.success or 0,
        "warning_count":      agg.warning or 0,
        "failed_count":       failed,
        "failure_rate":       round(failed / total * 100, 1) if total else 0.0,
        "median_duration_seconds": med,
        "median_duration_fmt":     _fmt_dur(med),
        "avg_duration_fmt":   _fmt_dur(agg.avg_dur) if agg.avg_dur else None,
        "max_duration_fmt":   _fmt_dur(agg.max_dur) if agg.max_dur else None,
        "first_execution_fmt": _fmt(agg.first_exec),
        "last_execution_fmt":  _fmt(agg.last_exec),
        "first_failure_fmt":   _fmt(first_failure.started_at) if first_failure else None,
        "last_success_fmt":    _fmt(last_success.started_at)  if last_success  else None,
        "avg_speed_mbps":      round(agg.avg_speed, 1) if agg.avg_speed else None,
        "avg_transferred_gb":  round(agg.avg_transf, 1) if agg.avg_transf else None,
        "full_count":          int(by_type.get("Full", 0)),
        "incremental_count":   int(by_type.get("Incremental", 0)),
    }


# ── API: execuções (paginado OU últimas N) ────────────────────────────────────

def _apply_exec_filters(q, client_id, routine_id, result, backup_type,
                        min_duration, reason_search):
    q = q.filter(BackupVmSession.client_id == client_id,
                 BackupVmSession.backup_routine_id == routine_id)
    if result:
        q = q.filter(BackupVmSession.result == result)
    if backup_type:
        q = q.filter(BackupVmSession.backup_type == backup_type)
    if min_duration:
        q = q.filter(BackupVmSession.duration_seconds >= min_duration)
    if reason_search:
        q = q.filter(BackupVmSession.reason_raw.ilike(f"%{reason_search}%"))
    return q


@router.get("/api/backups/analytics/executions")
def api_executions(
    request: Request,
    client_id: int = Query(...),
    routine_id: int = Query(...),
    date_start: Optional[str] = Query(None),
    date_end: Optional[str] = Query(None),
    result: Optional[str] = Query(None),
    backup_type: Optional[str] = Query(None),
    min_duration: Optional[int] = Query(None, ge=0),
    only_slow: bool = Query(False),
    slow_ratio: float = Query(2.0, ge=1.0, le=50.0),
    reason_search: Optional[str] = Query(None),
    sort: str = Query("recent"),       # recent | slowest
    last_n: Optional[int] = Query(None, ge=1, le=20000),
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=10, le=500),
    db: Session = Depends(get_db),
):
    _require_login(request)
    _assert_scope(client_id, db, request)
    rt = db.query(BackupRoutine).filter(
        BackupRoutine.id == routine_id, BackupRoutine.client_id == client_id).first()
    if not rt:
        raise HTTPException(status_code=404, detail="Rotina não encontrada.")

    # baseline mediano da rotina (para only_slow)
    med = db.query(_MEDIAN(BackupVmSession.duration_seconds)).filter(
        BackupVmSession.client_id == client_id,
        BackupVmSession.backup_routine_id == routine_id).scalar()
    med = int(med) if med is not None else None

    order = (BackupVmSession.duration_seconds.desc() if sort == "slowest"
             else BackupVmSession.started_at.desc())

    if last_n:
        q = _apply_exec_filters(db.query(BackupVmSession), client_id, routine_id,
                                result, backup_type, min_duration, reason_search)
        if only_slow and med:
            q = q.filter(BackupVmSession.duration_seconds >= slow_ratio * med)
        rows = (q.order_by(BackupVmSession.started_at.desc()).limit(last_n).all())
        if sort == "slowest":
            rows.sort(key=lambda s: -(s.duration_seconds or 0))
        else:
            rows.reverse()
        items = [_sess_dict(s, rt) for s in rows]
        return {"items": items, "total": len(items), "page": 1, "page_size": len(items),
                "pages": 1, "mode": "last_n", "median_seconds": med}

    dt_start = _parse_date(date_start)
    dt_end   = _parse_date(date_end, end_of_day=True)
    q = _apply_exec_filters(db.query(BackupVmSession), client_id, routine_id,
                            result, backup_type, min_duration, reason_search)
    if dt_start:
        q = q.filter(BackupVmSession.started_at >= dt_start)
    if dt_end:
        q = q.filter(BackupVmSession.started_at <= dt_end)
    if only_slow and med:
        q = q.filter(BackupVmSession.duration_seconds >= slow_ratio * med)

    total = q.count()
    pages = max(1, math.ceil(total / page_size))
    page  = min(page, pages)
    rows  = q.order_by(order).offset((page - 1) * page_size).limit(page_size).all()
    items = [_sess_dict(s, rt) for s in rows]
    return {"items": items, "total": total, "page": page, "page_size": page_size,
            "pages": pages, "mode": "paged", "median_seconds": med}


# ── Helper: conjunto de execuções LENTAS do cliente na janela ─────────────────

def _slow_rows(db, client_id, dt_start, dt_end, ratio, extra_min, search):
    """Retorna lista de dicts das execuções lentas (duração ≥ ratio×mediana_da_rotina
    E ≥ extra_min acima), com info da rotina. Mediana por rotina via subquery."""
    med_sq = (db.query(
                BackupVmSession.backup_routine_id.label("rid"),
                _MEDIAN(BackupVmSession.duration_seconds).label("med"))
              .filter(BackupVmSession.client_id == client_id,
                      BackupVmSession.duration_seconds.isnot(None))
              .group_by(BackupVmSession.backup_routine_id).subquery())

    q = (db.query(BackupVmSession, BackupRoutine, med_sq.c.med)
         .join(BackupRoutine, BackupRoutine.id == BackupVmSession.backup_routine_id)
         .join(med_sq, med_sq.c.rid == BackupVmSession.backup_routine_id)
         .filter(BackupVmSession.client_id == client_id,
                 BackupVmSession.duration_seconds.isnot(None),
                 med_sq.c.med > 0,
                 BackupVmSession.duration_seconds >= ratio * med_sq.c.med,
                 BackupVmSession.duration_seconds - med_sq.c.med >= extra_min * 60))
    if dt_start:
        q = q.filter(BackupVmSession.started_at >= dt_start)
    if dt_end:
        q = q.filter(BackupVmSession.started_at <= dt_end)
    if search:
        pat = f"%{search}%"
        q = q.filter(or_(BackupRoutine.job_name.ilike(pat), BackupRoutine.vm_name.ilike(pat)))

    rows = []
    for s, rt, med in q.order_by(BackupVmSession.started_at.desc()).limit(5000).all():
        dur = s.duration_seconds or 0
        rows.append({
            "id": s.id, "routine_id": rt.id, "job_name": rt.job_name, "vm_name": rt.vm_name,
            "started_at": s.started_at, "started_at_fmt": _fmt(s.started_at),
            "duration_seconds": dur, "duration_fmt": _fmt_dur(dur),
            "median_seconds": int(med), "median_fmt": _fmt_dur(med),
            "ratio": round(dur / med, 1) if med else 0,
            "extra_min": round((dur - med) / 60),
            "transferred_gb": s.transferred_gb, "avg_speed_mbps": s.avg_speed_mbps,
            "backup_type": s.backup_type, "result": s.result,
        })
    return rows


# ── API: ranking de execuções lentas do cliente ───────────────────────────────

@router.get("/api/backups/analytics/slowest")
def api_slowest(
    request: Request,
    client_id: int = Query(...),
    last_days: int = Query(14, ge=1, le=180),
    ratio: float = Query(2.5, ge=1.0, le=50.0),
    extra_min: int = Query(30, ge=0, le=1440),
    search: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
):
    _require_login(request)
    _assert_scope(client_id, db, request)

    now = db.query(func.max(BackupVmSession.started_at)).filter(
        BackupVmSession.client_id == client_id).scalar() or datetime.utcnow()
    dt_start = now - timedelta(days=last_days)

    rows = _slow_rows(db, client_id, dt_start, None, ratio, extra_min, search)
    rows.sort(key=lambda r: -r["extra_min"])
    return {"items": rows[:limit], "total": len(rows),
            "window_start_fmt": _fmt(dt_start), "window_end_fmt": _fmt(now)}


# ── API: incidentes simultâneos (clusters de início) ──────────────────────────

@router.get("/api/backups/analytics/incidents")
def api_incidents(
    request: Request,
    client_id: int = Query(...),
    last_days: int = Query(14, ge=1, le=180),
    ratio: float = Query(2.5, ge=1.0, le=50.0),
    extra_min: int = Query(30, ge=0, le=1440),
    bucket_min: int = Query(30, ge=5, le=240),
    min_routines: int = Query(3, ge=2, le=100),
    db: Session = Depends(get_db),
):
    _require_login(request)
    _assert_scope(client_id, db, request)

    now = db.query(func.max(BackupVmSession.started_at)).filter(
        BackupVmSession.client_id == client_id).scalar() or datetime.utcnow()
    dt_start = now - timedelta(days=last_days)
    rows = _slow_rows(db, client_id, dt_start, None, ratio, extra_min, None)

    def bucket(dt):
        m = (dt.minute // bucket_min) * bucket_min
        return dt.replace(minute=m, second=0, microsecond=0)

    clusters = defaultdict(dict)   # bucket → {routine_id: best_row}
    for r in rows:
        if not r["started_at"]:
            continue
        b = bucket(r["started_at"])
        cur = clusters[b].get(r["routine_id"])
        if cur is None or r["extra_min"] > cur["extra_min"]:
            clusters[b][r["routine_id"]] = r

    incidents = []
    for b, routmap in clusters.items():
        if len(routmap) < min_routines:
            continue
        evs = sorted(routmap.values(), key=lambda r: -r["ratio"])
        fails = sum(1 for e in evs if e["result"] in ("Failed", "Warning"))
        incidents.append({
            "when_fmt":   b.strftime("%d/%m/%Y %H:%M"),
            "when_iso":   _iso(b),
            "n_routines": len(evs),
            "n_failed":   fails,
            "avg_extra_min": round(sum(e["extra_min"] for e in evs) / len(evs)),
            "worst": evs[0],
            "items": evs[:15],
        })
    incidents.sort(key=lambda x: -x["n_routines"])
    return {"incidents": incidents, "total": len(incidents),
            "window_start_fmt": _fmt(dt_start), "window_end_fmt": _fmt(now)}


# ── API: exportar execuções para Excel ────────────────────────────────────────

@router.get("/api/backups/analytics/export-excel")
def api_export_excel(
    request: Request,
    client_id: int = Query(...),
    routine_id: int = Query(...),
    date_start: Optional[str] = Query(None),
    date_end: Optional[str] = Query(None),
    result: Optional[str] = Query(None),
    backup_type: Optional[str] = Query(None),
    min_duration: Optional[int] = Query(None, ge=0),
    last_n: Optional[int] = Query(None, ge=1, le=20000),
    db: Session = Depends(get_db),
):
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter

    _require_login(request)
    _assert_scope(client_id, db, request)
    rt = db.query(BackupRoutine).filter(
        BackupRoutine.id == routine_id, BackupRoutine.client_id == client_id).first()
    if not rt:
        raise HTTPException(status_code=404, detail="Rotina não encontrada.")

    q = _apply_exec_filters(db.query(BackupVmSession), client_id, routine_id,
                            result, backup_type, min_duration, None)
    if last_n:
        rows = q.order_by(BackupVmSession.started_at.desc()).limit(last_n).all()
        rows.reverse()
    else:
        dt_start = _parse_date(date_start)
        dt_end   = _parse_date(date_end, end_of_day=True)
        if dt_start:
            q = q.filter(BackupVmSession.started_at >= dt_start)
        if dt_end:
            q = q.filter(BackupVmSession.started_at <= dt_end)
        rows = q.order_by(BackupVmSession.started_at.asc()).limit(20000).all()

    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "Execucoes Backup"
    headers = ["Início", "Fim", "Resultado", "Duração (s)", "Duração",
               "Processado (GB)", "Lido (GB)", "Transferido (GB)", "Veloc (MB/s)",
               "Tipo", "Motivo", "Session ID"]
    hdr_font = Font(bold=True, color="FFFFFF"); hdr_fill = PatternFill("solid", fgColor="368CFF")
    thin = Side(style="thin", color="DDDDDD"); border = Border(thin, thin, thin, thin)
    res_fill = {"Success": PatternFill("solid", fgColor="D4EDDA"),
                "Warning": PatternFill("solid", fgColor="FFF3CD"),
                "Failed":  PatternFill("solid", fgColor="F8D7DA")}

    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(headers))
    c = ws.cell(row=1, column=1, value=f"Backups — {rt.job_name} / {rt.vm_name}")
    c.font = Font(bold=True, size=13, color="FFFFFF"); c.fill = PatternFill("solid", fgColor="5B21B6")
    for col, h in enumerate(headers, 1):
        cell = ws.cell(row=2, column=col, value=h)
        cell.font = hdr_font; cell.fill = hdr_fill; cell.border = border
        cell.alignment = Alignment(horizontal="center", wrap_text=True)

    r = 3
    for s in rows:
        vals = [_fmt(s.started_at), _fmt(s.ended_at), s.result, s.duration_seconds,
                _fmt_dur(s.duration_seconds), s.processed_gb, s.read_gb, s.transferred_gb,
                s.avg_speed_mbps, s.backup_type, s.reason_raw, s.task_session_id]
        for col, v in enumerate(vals, 1):
            cell = ws.cell(row=r, column=col, value=v); cell.border = border
            cell.alignment = Alignment(vertical="top", wrap_text=(col == 11))
            if s.result in res_fill:
                cell.fill = res_fill[s.result]
        r += 1

    widths = [19, 19, 10, 11, 11, 14, 11, 15, 11, 12, 50, 22]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = "A3"

    buf = io.BytesIO(); wb.save(buf); buf.seek(0)
    safe = "".join(ch if ch.isalnum() else "_" for ch in f"{rt.job_name}_{rt.vm_name}")[:50]
    fname = f"Backups_{safe}_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
    return StreamingResponse(
        buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


# ── Categorização (sugestão) de motivos de erro p/ o gráfico de ofensores ─────
def _categorize_reason(reason: Optional[str]) -> str:
    r = (reason or "").lower()
    if not r.strip():
        return "Sem motivo"
    # ordem importa: do mais específico p/ o mais genérico
    if any(k in r for k in ("vss", "freeze", "shadow copy", "vsscontrol", "application-aware", "guest for freeze")):
        return "VSS"
    if any(k in r for k in ("agent is", "can't run command because agent", "guest agent", "agente")):
        return "Agente"
    if any(k in r for k in ("timeout", "timed out", "took longer", "stalled", "slow ", "too slow")):
        return "Timeout / Desempenho"
    if any(k in r for k in ("network", "connection", "rpc", "unreachable", "socket")):
        return "Rede"
    if any(k in r for k in ("repository", "reposit", "storage", "no space", "disk full", "out of space",
                            "i/o", "read error", "write error", "unable to allocate", "backup file")):
        return "Repositório / Performance"
    if "see log file" in r:
        return "Genérico (ver log)"
    return "Outros"


@router.get("/api/backups/analytics/export-all-errors")
def api_export_all_errors(
    request: Request,
    client_id: int = Query(...),
    date_start: Optional[str] = Query(None),
    date_end: Optional[str] = Query(None),
    include_warning: bool = Query(True),
    db: Session = Depends(get_db),
):
    """
    Exporta TODOS os erros (todas as máquinas) do cliente no período — para
    compilar o gráfico de ofensores. Aba 'Erros' (bruto) + aba 'Resumo por categoria'.
    """
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter

    _require_login(request)
    _assert_scope(client_id, db, request)
    client = db.query(Client).get(client_id)

    dt_start = _parse_date(date_start)
    dt_end   = _parse_date(date_end, end_of_day=True)
    if dt_start is None:
        dt_start = datetime.now() - timedelta(days=30)
    if dt_end is None:
        dt_end = datetime.now()

    results = ["Failed"] + (["Warning"] if include_warning else [])
    rows = (db.query(BackupVmSession)
            .filter(BackupVmSession.client_id == client_id,
                    BackupVmSession.result.in_(results),
                    BackupVmSession.started_at >= dt_start,
                    BackupVmSession.started_at <= dt_end)
            .order_by(BackupVmSession.started_at.asc())
            .limit(50000).all())

    wb = openpyxl.Workbook()
    thin = Side(style="thin", color="DDDDDD"); border = Border(thin, thin, thin, thin)
    hdr_font = Font(bold=True, color="FFFFFF"); hdr_fill = PatternFill("solid", fgColor="368CFF")
    res_fill = {"Warning": PatternFill("solid", fgColor="FFF3CD"),
                "Failed":  PatternFill("solid", fgColor="F8D7DA")}

    from collections import Counter
    period_lbl = f"{dt_start.strftime('%d/%m/%Y')} a {dt_end.strftime('%d/%m/%Y')}"

    # ── Agrupa por EXECUÇÃO DO JOB (mesma rotina + mesmo horário = job_session_id) ──
    # Colapsa as N VMs que falharam numa mesma execução em 1 erro de rotina.
    job_groups: dict = defaultdict(list)
    for s in rows:
        key = s.job_session_id or f"{s.job_name_snapshot}|{s.started_at}"
        job_groups[key].append(s)

    # ── Aba 1: Erros por VM (bruto) ───────────────────────────────────────────
    ws = wb.active; ws.title = "Erros (por VM)"
    headers = ["Início", "Máquina (VM)", "Rotina", "Resultado", "Categoria (sugestão)",
               "Duração", "Tipo", "Motivo", "Session ID", "Job Session ID"]
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(headers))
    c = ws.cell(row=1, column=1,
                value=f"Erros de Backup por VM — {client.name if client else ''} — {period_lbl} — "
                      f"{len(rows)} ocorrência(s) por VM / {len(job_groups)} por rotina")
    c.font = Font(bold=True, size=13, color="FFFFFF"); c.fill = PatternFill("solid", fgColor="5B21B6")
    for col, h in enumerate(headers, 1):
        cell = ws.cell(row=2, column=col, value=h)
        cell.font = hdr_font; cell.fill = hdr_fill; cell.border = border
        cell.alignment = Alignment(horizontal="center", wrap_text=True)

    cat_counts: dict = defaultdict(int)   # por VM
    r = 3
    for s in rows:
        cat = _categorize_reason(s.reason_raw)
        cat_counts[cat] += 1
        vals = [_fmt(s.started_at), s.vm_name, s.job_name_snapshot, s.result, cat,
                _fmt_dur(s.duration_seconds), s.backup_type, s.reason_raw,
                s.task_session_id, s.job_session_id]
        for col, v in enumerate(vals, 1):
            cell = ws.cell(row=r, column=col, value=v); cell.border = border
            cell.alignment = Alignment(vertical="top", wrap_text=(col == 8))
            if s.result in res_fill:
                cell.fill = res_fill[s.result]
        r += 1
    for i, w in enumerate([18, 22, 34, 10, 22, 11, 12, 60, 22, 22], 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = "A3"

    # ── Aba 2: Erros por ROTINA (deduplicado por execução de job) ─────────────
    wsj = wb.create_sheet("Erros por rotina")
    jheaders = ["Início", "Rotina", "Resultado", "VMs afetadas", "VMs (nomes)",
                "Categoria (sugestão)", "Motivo (predominante)", "Job Session ID"]
    wsj.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(jheaders))
    cj = wsj.cell(row=1, column=1,
                  value=f"Erros de Backup por Rotina — {client.name if client else ''} — {period_lbl} — "
                        f"{len(job_groups)} execução(ões) de job com erro")
    cj.font = Font(bold=True, size=13, color="FFFFFF"); cj.fill = PatternFill("solid", fgColor="5B21B6")
    for col, h in enumerate(jheaders, 1):
        cell = wsj.cell(row=2, column=col, value=h)
        cell.font = hdr_font; cell.fill = hdr_fill; cell.border = border
        cell.alignment = Alignment(horizontal="center", wrap_text=True)

    cat_counts_job: dict = defaultdict(int)   # por rotina
    job_rows = []
    for grp in job_groups.values():
        started = min((g.started_at for g in grp if g.started_at), default=None)
        job_name = grp[0].job_name_snapshot
        result = "Failed" if any(g.result == "Failed" for g in grp) else "Warning"
        vms = sorted({g.vm_name for g in grp if g.vm_name})
        reasons = Counter((g.reason_raw or "").strip() for g in grp if (g.reason_raw or "").strip())
        top_reason = reasons.most_common(1)[0][0] if reasons else ""
        if len(reasons) > 1:
            top_reason = f"{top_reason}  (+{len(reasons)-1} outro(s) motivo(s))"
        cat = _categorize_reason(reasons.most_common(1)[0][0] if reasons else None)
        cat_counts_job[cat] += 1
        job_rows.append((started, job_name, result, len(vms), ", ".join(vms), cat, top_reason,
                         grp[0].job_session_id))
    job_rows.sort(key=lambda x: (x[0] or datetime.min))

    r = 3
    for (started, job_name, result, nvms, vmnames, cat, reason, jsid) in job_rows:
        vals = [_fmt(started), job_name, result, nvms, vmnames, cat, reason, jsid]
        for col, v in enumerate(vals, 1):
            cell = wsj.cell(row=r, column=col, value=v); cell.border = border
            cell.alignment = Alignment(vertical="top", wrap_text=(col in (5, 7)))
            if result in res_fill:
                cell.fill = res_fill[result]
        r += 1
    for i, w in enumerate([18, 34, 10, 13, 40, 22, 60, 22], 1):
        wsj.column_dimensions[get_column_letter(i)].width = w
    wsj.freeze_panes = "A3"

    # ── Aba 3: Resumo por categoria (por VM x por rotina) ─────────────────────
    ws2 = wb.create_sheet("Resumo por categoria")
    for col, h in enumerate(["Categoria", "Erros por VM", "Erros por rotina"], 1):
        cell = ws2.cell(row=1, column=col, value=h)
        cell.font = hdr_font; cell.fill = hdr_fill
    cats = sorted(set(cat_counts) | set(cat_counts_job),
                  key=lambda k: -(cat_counts.get(k, 0)))
    rr = 2
    for cat in cats:
        ws2.cell(row=rr, column=1, value=cat)
        ws2.cell(row=rr, column=2, value=cat_counts.get(cat, 0))
        ws2.cell(row=rr, column=3, value=cat_counts_job.get(cat, 0))
        rr += 1
    ws2.cell(row=rr, column=1, value="TOTAL").font = Font(bold=True)
    ws2.cell(row=rr, column=2, value=len(rows)).font = Font(bold=True)
    ws2.cell(row=rr, column=3, value=len(job_groups)).font = Font(bold=True)
    ws2.column_dimensions["A"].width = 28
    ws2.column_dimensions["B"].width = 14; ws2.column_dimensions["C"].width = 16

    buf = io.BytesIO(); wb.save(buf); buf.seek(0)
    safe = "".join(ch if ch.isalnum() else "_" for ch in (client.name if client else "cliente"))[:40]
    fname = f"Erros_Backup_{safe}_{dt_start.strftime('%Y%m%d')}_{dt_end.strftime('%Y%m%d')}.xlsx"
    return StreamingResponse(
        buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )
