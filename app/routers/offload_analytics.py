"""
offload_analytics.py — Visão analítica/técnica de investigação de Offload.
Separada do dashboard gerencial (/dashboard/offload).
Rota principal: GET /analytics/offload
"""
from __future__ import annotations

import math
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import case, func, or_
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import (
    Client, OffloadJob, OffloadReasonCategory, OffloadSession, UserClient,
)
from app.services import branding_service
from app.services.classification_service import situation_badge_class

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
# base.html depende destes globals do Jinja (mesmo padrão dos outros routers)
templates.env.globals["get_branding"] = branding_service.get_branding
templates.env.globals["situation_badge_class"] = situation_badge_class
templates.env.globals["now"] = datetime.now


# ── Auth helpers (espelham main.py sem import circular) ──────────────────────

def _require_login(request: Request) -> None:
    if not request.session.get("user_id"):
        raise HTTPException(status_code=401, detail="Não autenticado.")


def _scoped_client_ids(db: Session, request: Request):
    """None → admin (sem restrição).  set → IDs permitidos para o usuário."""
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


def _fmt(dt: Optional[datetime]) -> Optional[str]:
    return dt.strftime("%d/%m/%Y %H:%M:%S") if dt else None


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


def _fmt_dur(secs) -> Optional[str]:
    if secs is None:
        return None
    secs = int(abs(secs))
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h{m:02d}m{s:02d}s" if h else (f"{m}m{s:02d}s" if m else f"{s}s")


def _load_cat_map(db: Session) -> dict[int, dict]:
    return {c.id: {"name": c.name, "color": c.color or "#6c757d"}
            for c in db.query(OffloadReasonCategory).all()}


def _session_to_dict(s: OffloadSession, cat_map: dict,
                     job_name: str = None, job_name_normalized: str = None) -> dict:
    cat = cat_map.get(s.reason_category_id, {})
    return {
        "id":                    s.id,
        "session_id":            s.session_id,
        "source_job_id":         s.source_job_id,
        "job_name":              job_name,
        "job_name_normalized":   job_name_normalized,
        "result":                s.result,
        "state":                 s.state,
        "started_at":            _iso(s.started_at),
        "started_at_fmt":        _fmt(s.started_at),
        "ended_at":              _iso(s.ended_at),
        "ended_at_fmt":          _fmt(s.ended_at),
        "duration_seconds":      s.duration_seconds,
        "duration_fmt":          _fmt_dur(s.duration_seconds),
        "reason_raw":            s.reason_raw,
        "reason_normalized":     s.reason_normalized,
        "reason_category":       cat.get("name"),
        "reason_category_color": cat.get("color"),
        "matched_pattern":       s.matched_pattern,
        "target_ip":             s.target_ip,
        "target_port":           s.target_port,
        "content_hash":          s.content_hash,
        "collected_at_fmt":      _fmt(s.collected_at),
        "imported_at_fmt":       _fmt(s.imported_at),
        "source":                s.source,
        "offload_import_id":     s.offload_import_id,
    }


def _apply_filters(q, client_id, job_id, dt_start, dt_end,
                   result=None, cat_id=None, reason_search=None,
                   only_with_reason=False):
    q = q.filter(
        OffloadSession.client_id == client_id,
        OffloadSession.offload_job_id == job_id,
    )
    if dt_start:
        q = q.filter(OffloadSession.started_at >= dt_start)
    if dt_end:
        q = q.filter(OffloadSession.started_at <= dt_end)
    if result:
        q = q.filter(OffloadSession.result == result)
    if cat_id:
        q = q.filter(OffloadSession.reason_category_id == cat_id)
    if reason_search:
        pat = f"%{reason_search}%"
        q = q.filter(or_(
            OffloadSession.reason_raw.ilike(pat),
            OffloadSession.reason_normalized.ilike(pat),
            OffloadSession.matched_pattern.ilike(pat),
        ))
    if only_with_reason:
        q = q.filter(OffloadSession.reason_raw.isnot(None))
    return q


# ── Página ────────────────────────────────────────────────────────────────────

@router.get("/analytics/offload", response_class=HTMLResponse)
def analytics_offload_page(request: Request, db: Session = Depends(get_db)):
    _require_login(request)
    allowed = _scoped_client_ids(db, request)
    q = db.query(Client).filter(Client.is_active.is_(True))
    if allowed is not None:
        q = q.filter(Client.id.in_(allowed)) if allowed else q.filter(False)
    clients = q.order_by(Client.name).all()
    categories = (db.query(OffloadReasonCategory)
                  .filter(OffloadReasonCategory.is_active.is_(True))
                  .order_by(OffloadReasonCategory.name).all())
    return templates.TemplateResponse("analytics_offload.html", {
        "request":   request,
        "clients":   clients,
        "categories": categories,
        "is_admin":  bool(request.session.get("is_admin")),
    })


# ── API: busca de rotinas (autocomplete) ──────────────────────────────────────

@router.get("/api/offload/analytics/jobs")
def api_analytics_jobs(
    request: Request,
    client_id: int = Query(...),
    search: str = Query(""),
    limit: int = Query(30, ge=1, le=100),
    db: Session = Depends(get_db),
):
    _require_login(request)
    _assert_scope(client_id, db, request)

    q = db.query(OffloadJob).filter(OffloadJob.client_id == client_id)
    if search.strip():
        pat = f"%{search.strip()}%"
        q = q.filter(or_(
            OffloadJob.job_name.ilike(pat),
            OffloadJob.job_name_normalized.ilike(pat),
        ))
    jobs = q.order_by(OffloadJob.job_name).limit(limit).all()
    if not jobs:
        return []

    job_ids = [j.id for j in jobs]
    stats_rows = (
        db.query(
            OffloadSession.offload_job_id,
            func.count().label("total"),
            func.sum(case((OffloadSession.result == "Failed",  1), else_=0)).label("failed"),
            func.sum(case((OffloadSession.result == "Warning", 1), else_=0)).label("warning"),
            func.sum(case((OffloadSession.result == "Success", 1), else_=0)).label("success"),
            func.max(OffloadSession.started_at).label("last_exec"),
        )
        .filter(OffloadSession.client_id == client_id,
                OffloadSession.offload_job_id.in_(job_ids))
        .group_by(OffloadSession.offload_job_id)
        .all()
    )
    stats = {r.offload_job_id: r for r in stats_rows}

    result = []
    for j in jobs:
        s = stats.get(j.id)
        result.append({
            "offload_job_id":      j.id,
            "job_name":            j.job_name,
            "job_name_normalized": j.job_name_normalized,
            "total_executions":    s.total  if s else 0,
            "failed_count":        s.failed if s else 0,
            "warning_count":       s.warning if s else 0,
            "success_count":       s.success if s else 0,
            "last_execution_fmt":  _fmt(s.last_exec) if s else None,
        })
    result.sort(key=lambda x: (-x["failed_count"], -x["total_executions"]))
    return result


# ── API: resumo da rotina ─────────────────────────────────────────────────────

@router.get("/api/offload/analytics/job-summary")
def api_analytics_job_summary(
    request: Request,
    client_id: int = Query(...),
    offload_job_id: int = Query(...),
    date_start: Optional[str] = Query(None),
    date_end: Optional[str] = Query(None),
    last_n: Optional[int] = Query(None, ge=1, le=20000),
    db: Session = Depends(get_db),
):
    _require_login(request)
    _assert_scope(client_id, db, request)

    job = db.query(OffloadJob).filter(
        OffloadJob.id == offload_job_id, OffloadJob.client_id == client_id,
    ).first()
    if not job:
        raise HTTPException(status_code=404, detail="Rotina não encontrada.")

    cat_map = _load_cat_map(db)
    dt_start = _parse_date(date_start)
    dt_end   = _parse_date(date_end, end_of_day=True)

    base_q = db.query(OffloadSession).filter(
        OffloadSession.client_id == client_id,
        OffloadSession.offload_job_id == offload_job_id,
    )
    if last_n:
        # Modo "últimas N": resume sobre a mesma janela da timeline (ignora datas)
        id_subq = (
            db.query(OffloadSession.id)
            .filter(OffloadSession.client_id == client_id,
                    OffloadSession.offload_job_id == offload_job_id)
            .order_by(OffloadSession.started_at.desc())
            .limit(last_n).subquery()
        )
        base_q = base_q.filter(OffloadSession.id.in_(db.query(id_subq.c.id)))
    else:
        if dt_start:
            base_q = base_q.filter(OffloadSession.started_at >= dt_start)
        if dt_end:
            base_q = base_q.filter(OffloadSession.started_at <= dt_end)

    agg = base_q.with_entities(
        func.count().label("total"),
        func.sum(case((OffloadSession.result == "Success", 1), else_=0)).label("success"),
        func.sum(case((OffloadSession.result == "Warning", 1), else_=0)).label("warning"),
        func.sum(case((OffloadSession.result == "Failed",  1), else_=0)).label("failed"),
        func.min(OffloadSession.started_at).label("first_exec"),
        func.max(OffloadSession.started_at).label("last_exec"),
        func.avg(OffloadSession.duration_seconds).label("avg_dur"),
    ).first()

    total  = agg.total  or 0
    failed = agg.failed or 0
    fail_rate = round(failed / total * 100, 1) if total else 0.0

    first_failure = (base_q
                     .filter(OffloadSession.result.in_(["Failed", "Warning"]))
                     .order_by(OffloadSession.started_at.asc()).first())
    last_success  = (base_q
                     .filter(OffloadSession.result == "Success")
                     .order_by(OffloadSession.started_at.desc()).first())

    cat_agg = (
        base_q
        .filter(OffloadSession.result.in_(["Failed", "Warning"]),
                OffloadSession.reason_category_id.isnot(None))
        .with_entities(OffloadSession.reason_category_id, func.count().label("n"))
        .group_by(OffloadSession.reason_category_id)
        .order_by(func.count().desc()).first()
    )
    most_common_cat = (cat_map.get(cat_agg.reason_category_id, {}).get("name")
                       if cat_agg else None)

    fail_warn_total = (agg.failed or 0) + (agg.warning or 0)
    cat_rows = (
        base_q
        .filter(OffloadSession.result.in_(["Failed", "Warning"]))
        .with_entities(OffloadSession.reason_category_id, func.count().label("n"))
        .group_by(OffloadSession.reason_category_id)
        .order_by(func.count().desc()).limit(10).all()
    )
    by_category = []
    for row in cat_rows:
        ci = cat_map.get(row.reason_category_id, {})
        by_category.append({
            "category_id":   row.reason_category_id,
            "category_name": ci.get("name", "Sem categoria"),
            "color":         ci.get("color", "#6c757d"),
            "count":         row.n,
            "pct":           round(row.n / fail_warn_total * 100) if fail_warn_total else 0,
        })

    return {
        "job_name":                    job.job_name,
        "job_name_normalized":         job.job_name_normalized,
        "total_executions":            total,
        "success_count":               agg.success or 0,
        "warning_count":               agg.warning or 0,
        "failed_count":                failed,
        "failure_rate":                fail_rate,
        "first_execution_fmt":         _fmt(agg.first_exec),
        "last_execution_fmt":          _fmt(agg.last_exec),
        "first_failure_fmt":           _fmt(first_failure.started_at) if first_failure else None,
        "last_success_fmt":            _fmt(last_success.started_at)  if last_success  else None,
        "most_common_reason_category": most_common_cat,
        "avg_duration_fmt":            _fmt_dur(agg.avg_dur) if agg.avg_dur else None,
        "by_category":                 by_category,
    }


# ── API: execuções (paginado + last_n) ───────────────────────────────────────

@router.get("/api/offload/analytics/executions")
def api_analytics_executions(
    request: Request,
    client_id: int = Query(...),
    offload_job_id: int = Query(...),
    date_start: Optional[str] = Query(None),
    date_end: Optional[str] = Query(None),
    result: Optional[str] = Query(None),
    reason_category_id: Optional[int] = Query(None),
    reason_search: Optional[str] = Query(None),
    only_with_reason: bool = Query(False),
    only_transitions: bool = Query(False),
    last_n: Optional[int] = Query(None, ge=1, le=20000),
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=10, le=500),
    db: Session = Depends(get_db),
):
    _require_login(request)
    _assert_scope(client_id, db, request)

    job = db.query(OffloadJob).filter(
        OffloadJob.id == offload_job_id, OffloadJob.client_id == client_id,
    ).first()
    if not job:
        raise HTTPException(status_code=404, detail="Rotina não encontrada.")

    cat_map = _load_cat_map(db)

    if last_n:
        # Modo "últimas N" — ignora datas, retorna em ordem cronológica (ASC)
        q = _apply_filters(db.query(OffloadSession), client_id, offload_job_id,
                           None, None, result, reason_category_id, reason_search,
                           only_with_reason)
        rows = q.order_by(OffloadSession.started_at.desc()).limit(last_n).all()
        rows.reverse()
        items = [_session_to_dict(s, cat_map, job.job_name, job.job_name_normalized)
                 for s in rows]
        if only_transitions:
            items = _keep_transitions(items)
        return {"items": items, "total": len(items), "page": 1,
                "page_size": len(items), "pages": 1, "mode": "last_n"}

    # Modo paginado (data obrigatória neste caso)
    dt_start = _parse_date(date_start)
    dt_end   = _parse_date(date_end, end_of_day=True)
    q = _apply_filters(db.query(OffloadSession), client_id, offload_job_id,
                       dt_start, dt_end, result, reason_category_id,
                       reason_search, only_with_reason)
    total = q.count()
    pages = max(1, math.ceil(total / page_size))
    page  = min(page, pages)
    rows  = (q.order_by(OffloadSession.started_at.desc())
              .offset((page - 1) * page_size).limit(page_size).all())
    items = [_session_to_dict(s, cat_map, job.job_name, job.job_name_normalized)
             for s in rows]
    if only_transitions:
        items = _keep_transitions(items)
    return {"items": items, "total": total, "page": page,
            "page_size": page_size, "pages": pages, "mode": "paged"}


def _keep_transitions(items: list[dict]) -> list[dict]:
    """Mantém apenas a execução em que o resultado mudou em relação à anterior."""
    if not items:
        return []
    out = [items[0]]
    for i in range(1, len(items)):
        if items[i]["result"] != items[i - 1]["result"]:
            out.append(items[i])
    return out


# ── API: primeiro erro ────────────────────────────────────────────────────────

@router.get("/api/offload/analytics/first-error")
def api_analytics_first_error(
    request: Request,
    client_id: int = Query(...),
    offload_job_id: int = Query(...),
    date_start: Optional[str] = Query(None),
    date_end: Optional[str] = Query(None),
    reason_category_id: Optional[int] = Query(None),
    reason_search: Optional[str] = Query(None),
    result_filter: str = Query("Failed"),
    context_size: int = Query(5, ge=1, le=20),
    db: Session = Depends(get_db),
):
    _require_login(request)
    _assert_scope(client_id, db, request)

    job = db.query(OffloadJob).filter(
        OffloadJob.id == offload_job_id, OffloadJob.client_id == client_id,
    ).first()
    if not job:
        raise HTTPException(status_code=404, detail="Rotina não encontrada.")

    cat_map  = _load_cat_map(db)
    dt_start = _parse_date(date_start)
    dt_end   = _parse_date(date_end, end_of_day=True)

    result_values = (["Failed", "Warning"] if result_filter == "any"
                     else [result_filter] if result_filter in ("Failed", "Warning")
                     else ["Failed"])

    q = db.query(OffloadSession).filter(
        OffloadSession.client_id == client_id,
        OffloadSession.offload_job_id == offload_job_id,
        OffloadSession.result.in_(result_values),
    )
    if dt_start:
        q = q.filter(OffloadSession.started_at >= dt_start)
    if dt_end:
        q = q.filter(OffloadSession.started_at <= dt_end)
    if reason_category_id:
        q = q.filter(OffloadSession.reason_category_id == reason_category_id)
    if reason_search:
        pat = f"%{reason_search}%"
        q = q.filter(or_(
            OffloadSession.reason_raw.ilike(pat),
            OffloadSession.reason_normalized.ilike(pat),
            OffloadSession.matched_pattern.ilike(pat),
        ))

    first_err = q.order_by(OffloadSession.started_at.asc()).first()
    if not first_err:
        return {"first_error": None, "previous_success": None,
                "time_gap_seconds": None, "time_gap_fmt": None,
                "context_before": [], "context_after": []}

    t0 = first_err.started_at

    prev_success = (
        db.query(OffloadSession)
        .filter(OffloadSession.client_id == client_id,
                OffloadSession.offload_job_id == offload_job_id,
                OffloadSession.result == "Success",
                OffloadSession.started_at < t0)
        .order_by(OffloadSession.started_at.desc()).first()
    )

    ctx_before_rows = (
        db.query(OffloadSession)
        .filter(OffloadSession.client_id == client_id,
                OffloadSession.offload_job_id == offload_job_id,
                OffloadSession.started_at <= t0)
        .order_by(OffloadSession.started_at.desc())
        .limit(context_size + 1).all()
    )
    ctx_before_rows.reverse()

    ctx_after_rows = (
        db.query(OffloadSession)
        .filter(OffloadSession.client_id == client_id,
                OffloadSession.offload_job_id == offload_job_id,
                OffloadSession.started_at > t0)
        .order_by(OffloadSession.started_at.asc())
        .limit(context_size).all()
    )

    gap = None
    if prev_success and prev_success.started_at and t0:
        gap = int((t0 - prev_success.started_at).total_seconds())

    def _s(sess):
        return _session_to_dict(sess, cat_map, job.job_name, job.job_name_normalized)

    return {
        "job_name":         job.job_name,
        "first_error":      _s(first_err),
        "previous_success": _s(prev_success) if prev_success else None,
        "time_gap_seconds": gap,
        "time_gap_fmt":     _fmt_dur(gap),
        "context_before":   [_s(s) for s in ctx_before_rows],
        "context_after":    [_s(s) for s in ctx_after_rows],
    }


# ── API: transições de status ─────────────────────────────────────────────────

@router.get("/api/offload/analytics/status-transitions")
def api_analytics_status_transitions(
    request: Request,
    client_id: int = Query(...),
    offload_job_id: int = Query(...),
    date_start: Optional[str] = Query(None),
    date_end: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    _require_login(request)
    _assert_scope(client_id, db, request)

    job = db.query(OffloadJob).filter(
        OffloadJob.id == offload_job_id, OffloadJob.client_id == client_id,
    ).first()
    if not job:
        raise HTTPException(status_code=404, detail="Rotina não encontrada.")

    cat_map  = _load_cat_map(db)
    dt_start = _parse_date(date_start)
    dt_end   = _parse_date(date_end, end_of_day=True)

    q = db.query(OffloadSession).filter(
        OffloadSession.client_id == client_id,
        OffloadSession.offload_job_id == offload_job_id,
    )
    if dt_start:
        q = q.filter(OffloadSession.started_at >= dt_start)
    if dt_end:
        q = q.filter(OffloadSession.started_at <= dt_end)

    sessions = q.order_by(OffloadSession.started_at.asc()).limit(5000).all()

    transitions = []
    prev = None
    phase_start = None
    for s in sessions:
        if prev is None:
            prev = s
            phase_start = s.started_at
            continue
        if s.result != prev.result:
            phase_dur = None
            if phase_start and s.started_at:
                phase_dur = int((s.started_at - phase_start).total_seconds())
            cat = cat_map.get(s.reason_category_id, {})
            transitions.append({
                "changed_at":            _fmt(s.started_at),
                "changed_at_iso":        _iso(s.started_at),
                "previous_result":       prev.result,
                "new_result":            s.result,
                "session_id":            s.session_id,
                "reason_raw":            s.reason_raw,
                "reason_category":       cat.get("name"),
                "reason_category_color": cat.get("color"),
                "phase_duration_fmt":    _fmt_dur(phase_dur),
            })
            phase_start = s.started_at
        prev = s

    return {"transitions": transitions, "total": len(transitions)}


# ── API: exportar execuções para Excel ────────────────────────────────────────

@router.get("/api/offload/analytics/export-excel")
def api_analytics_export_excel(
    request: Request,
    client_id: int = Query(...),
    offload_job_id: int = Query(...),
    date_start: Optional[str] = Query(None),
    date_end: Optional[str] = Query(None),
    result: Optional[str] = Query(None),
    reason_category_id: Optional[int] = Query(None),
    reason_search: Optional[str] = Query(None),
    only_with_reason: bool = Query(False),
    last_n: Optional[int] = Query(None, ge=1, le=20000),
    db: Session = Depends(get_db),
):
    """Exporta as execuções filtradas (mesmos filtros da tabela) para .xlsx."""
    import io
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
    from fastapi.responses import StreamingResponse

    _require_login(request)
    _assert_scope(client_id, db, request)

    job = db.query(OffloadJob).filter(
        OffloadJob.id == offload_job_id, OffloadJob.client_id == client_id,
    ).first()
    if not job:
        raise HTTPException(status_code=404, detail="Rotina não encontrada.")

    cat_map = _load_cat_map(db)

    # Mesma lógica de seleção da tabela (last_n OU período), sem paginação
    if last_n:
        q = _apply_filters(db.query(OffloadSession), client_id, offload_job_id,
                           None, None, result, reason_category_id, reason_search,
                           only_with_reason)
        rows = q.order_by(OffloadSession.started_at.desc()).limit(last_n).all()
        rows.reverse()
    else:
        dt_start = _parse_date(date_start)
        dt_end   = _parse_date(date_end, end_of_day=True)
        q = _apply_filters(db.query(OffloadSession), client_id, offload_job_id,
                           dt_start, dt_end, result, reason_category_id,
                           reason_search, only_with_reason)
        # teto de segurança para exportação
        rows = q.order_by(OffloadSession.started_at.asc()).limit(20000).all()

    # ── Monta a planilha ──────────────────────────────────────────────────────
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Execucoes Offload"

    headers = [
        "Início", "Fim", "Resultado", "Duração (s)", "Categoria",
        "Motivo (bruto)", "IP Destino", "Porta", "Session ID",
        "Source Job ID", "Estado", "Regra/Pattern", "Carga", "Importado em",
    ]

    title_font  = Font(bold=True, size=13, color="FFFFFF")
    title_fill  = PatternFill("solid", fgColor="5B21B6")
    hdr_font    = Font(bold=True, color="FFFFFF")
    hdr_fill    = PatternFill("solid", fgColor="368CFF")
    thin        = Side(style="thin", color="DDDDDD")
    border      = Border(left=thin, right=thin, top=thin, bottom=thin)
    res_fill = {
        "Success": PatternFill("solid", fgColor="D4EDDA"),
        "Warning": PatternFill("solid", fgColor="FFF3CD"),
        "Failed":  PatternFill("solid", fgColor="F8D7DA"),
    }

    # Linha de título
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(headers))
    c = ws.cell(row=1, column=1, value=f"Análise de Offload — {job.job_name}")
    c.font = title_font
    c.fill = title_fill
    c.alignment = Alignment(horizontal="left", vertical="center")
    ws.row_dimensions[1].height = 24

    # Cabeçalho
    for col, h in enumerate(headers, start=1):
        cell = ws.cell(row=2, column=col, value=h)
        cell.font = hdr_font
        cell.fill = hdr_fill
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = border

    # Dados
    r = 3
    for s in rows:
        cat = cat_map.get(s.reason_category_id, {})
        values = [
            _fmt(s.started_at), _fmt(s.ended_at), s.result, s.duration_seconds,
            cat.get("name"), s.reason_raw, s.target_ip, s.target_port,
            s.session_id, s.source_job_id, s.state, s.matched_pattern,
            (f"#{s.offload_import_id}" if s.offload_import_id else None),
            _fmt(s.imported_at),
        ]
        for col, v in enumerate(values, start=1):
            cell = ws.cell(row=r, column=col, value=v)
            cell.border = border
            cell.alignment = Alignment(vertical="top", wrap_text=(col == 6))
            if s.result in res_fill:
                cell.fill = res_fill[s.result]
        r += 1

    # Larguras
    widths = [19, 19, 11, 12, 24, 60, 15, 8, 22, 22, 14, 28, 10, 19]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = "A3"

    # Rodapé informativo
    foot = ws.cell(row=r + 1, column=1,
                   value=f"Total de execuções exportadas: {len(rows)} — "
                         f"gerado em {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
    foot.font = Font(italic=True, size=9, color="888888")

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    safe = "".join(ch if ch.isalnum() else "_" for ch in job.job_name)[:40]
    fname = f"Offload_{safe}_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )
