"""
ps_import.py — Importação unificada de coletas PowerShell + histórico único.

Uma tela importa QUALQUER NDJSON de coletor: o tipo é DETECTADO automaticamente
(marcador collection_type ou assinatura de campos) e roteado ao ingestor certo.
Os dois arquivos de disco (backups + capacidade) entram como UM import.

Substitui as telas separadas por coletor (que agora redirecionam para cá).
"""
from __future__ import annotations

import os
import threading
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth import require_admin
from app.database import get_db, SessionLocal
from app.models import (
    Client, OffloadImport, BackupImport, JobConfigImport, DiskUsageImport,
)
from app.services import branding_service
from app.services.import_detect import detect_type, TYPE_LABELS
from app.services.offload_service import ingest_ndjson as _ing_offload
from app.services.backup_perf_service import ingest_ndjson as _ing_backup
from app.services.job_config_service import ingest_ndjson as _ing_jobcfg
from app.services.disk_usage_service import ingest as _ing_disk

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
templates.env.globals["now"] = datetime.now
templates.env.globals["get_branding"] = branding_service.get_branding

UPLOAD_DIR = Path(os.environ.get("UPLOAD_DIR", "./uploads")) / "ps"
MAX_BYTES = 500 * 1024 * 1024

# tipo single -> (Model, ingest_fn)
_SINGLE = {
    "offload":     (OffloadImport,    _ing_offload),
    "backup_perf": (BackupImport,     _ing_backup),
    "job_config":  (JobConfigImport,  _ing_jobcfg),
}


# ── background runners ──────────────────────────────────────────────────────
def _fail(model, import_id: int, msg: str) -> None:
    db = SessionLocal()
    try:
        row = db.query(model).get(import_id)
        if row:
            row.status = "failed"
            row.status_message = msg[:500]
            if hasattr(row, "updated_at"):
                row.updated_at = datetime.utcnow()
            db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


def _run_single(model, ingest_fn, import_id, path, client_id, orig):
    db = SessionLocal()
    try:
        ingest_fn(db, client_id=client_id, file_path=str(path),
                  original_filename=orig, import_id=import_id)
    except Exception as exc:
        _fail(model, import_id, f"Erro na ingestão: {exc}")
    finally:
        db.close()


def _run_disk(import_id, disk_path, cap_path, client_id, disk_orig, cap_orig):
    db = SessionLocal()
    try:
        _ing_disk(db, client_id=client_id, disk_path=str(disk_path),
                  capacity_path=(str(cap_path) if cap_path else None),
                  disk_original=disk_orig, capacity_original=cap_orig,
                  import_id=import_id)
    except Exception as exc:
        _fail(DiskUsageImport, import_id, f"Erro na ingestão: {exc}")
    finally:
        db.close()


async def _save(file: UploadFile) -> tuple[Path, int, str]:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    orig = (file.filename or "coleta.ndjson").strip()
    stored = UPLOAD_DIR / f"{uuid.uuid4().hex}_{orig}"
    size = 0
    with open(stored, "wb") as out:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            out.write(chunk)
    return stored, size, orig


# ── TELA: importar ──────────────────────────────────────────────────────────
@router.get("/import/powershell", response_class=HTMLResponse,
            dependencies=[Depends(require_admin)])
def ps_import_page(request: Request, db: Session = Depends(get_db)):
    clients = db.query(Client).filter(Client.is_active.is_(True)).order_by(Client.name).all()
    return templates.TemplateResponse("ps_import.html", {
        "request": request, "clients": clients, "result": None,
    })


@router.post("/import/powershell", dependencies=[Depends(require_admin)])
async def ps_import_upload(request: Request,
                           client_id: int = Form(...),
                           files: list[UploadFile] = File(...),
                           db: Session = Depends(get_db)):
    user = request.session.get("username")
    clients = db.query(Client).filter(Client.is_active.is_(True)).order_by(Client.name).all()

    def _render(result):
        return templates.TemplateResponse("ps_import.html", {
            "request": request, "clients": clients, "result": result,
            "selected_client_id": client_id,
        })

    saved = []
    for f in files or []:
        if not f or not (f.filename or "").strip():
            continue
        path, size, orig = await _save(f)
        if size > MAX_BYTES:
            saved.append((None, path, orig, size, "Arquivo acima de 500 MB"))
            continue
        t = detect_type(str(path))
        saved.append((t, path, orig, size, None))

    if not saved:
        return _render([{"file": "—", "type": None, "ok": False, "msg": "Nenhum arquivo enviado."}])

    result = []
    disk = {"path": None, "orig": None}
    cap = {"path": None, "orig": None}

    for t, path, orig, size, err in saved:
        if err:
            result.append({"file": orig, "type": None, "ok": False, "msg": err})
            continue
        if t == "disk_backups":
            disk["path"], disk["orig"] = path, orig
        elif t == "repo_capacity":
            cap["path"], cap["orig"] = path, orig
        elif t in _SINGLE:
            model, fn = _SINGLE[t]
            row = model(client_id=client_id, filename=path.name, original_filename=orig,
                        status="processing",
                        status_message=f"Arquivo recebido ({size/1024/1024:.1f} MB). Aguardando...",
                        uploaded_by=user)
            db.add(row); db.commit(); db.refresh(row)
            threading.Thread(target=_run_single,
                             args=(model, fn, row.id, path, client_id, orig),
                             daemon=True).start()
            result.append({"file": orig, "type": TYPE_LABELS[t], "ok": True})
        else:
            result.append({"file": orig, "type": None, "ok": False,
                           "msg": "Tipo não reconhecido (não é um NDJSON de coletor conhecido)."})

    # disco: backups (+ capacidade opcional) => 1 import
    if disk["path"] or cap["path"]:
        if not disk["path"]:
            result.append({"file": cap["orig"], "type": TYPE_LABELS["repo_capacity"], "ok": False,
                           "msg": "Capacidade sem o arquivo de Backups em Disco — envie os dois juntos."})
        else:
            row = DiskUsageImport(client_id=client_id,
                                  disk_filename=disk["orig"], capacity_filename=cap["orig"],
                                  status="processing",
                                  status_message="Arquivos recebidos. Aguardando processamento...",
                                  uploaded_by=user)
            db.add(row); db.commit(); db.refresh(row)
            threading.Thread(target=_run_disk,
                             args=(row.id, disk["path"], cap["path"], client_id,
                                   disk["orig"], cap["orig"]),
                             daemon=True).start()
            lbl = TYPE_LABELS["disk_backups"] + (" + Capacidade" if cap["path"] else "")
            fdesc = disk["orig"] + (f" + {cap['orig']}" if cap["orig"] else "")
            result.append({"file": fdesc, "type": lbl, "ok": True})

    return _render(result)


# ── TELA: histórico unificado ───────────────────────────────────────────────
def _history(db: Session, client_id=None) -> list[dict]:
    names = {c.id: c.name for c in db.query(Client).all()}
    rows = []

    def collect(model, type_key, file_fn, count_attr, detail):
        q = db.query(model)
        if client_id:
            q = q.filter(model.client_id == client_id)
        for r in q.order_by(model.imported_at.desc()).limit(200).all():
            rows.append({
                "type": TYPE_LABELS[type_key],
                "type_key": type_key,
                "client": names.get(r.client_id, "—"),
                "file": file_fn(r),
                "count": getattr(r, count_attr, 0) or 0,
                "status": r.status,
                "at": r.imported_at,
                "by": getattr(r, "uploaded_by", None),
                "url": detail.format(id=r.id),
            })

    collect(OffloadImport,   "offload",      lambda r: r.original_filename, "inserted_sessions", "/offload/{id}/summary")
    collect(BackupImport,    "backup_perf",  lambda r: r.original_filename, "inserted_sessions", "/backups/{id}/summary")
    collect(JobConfigImport, "job_config",   lambda r: r.original_filename, "inserted_jobs",     "/job-config/{id}/summary")
    collect(DiskUsageImport, "disk_backups", lambda r: (r.disk_filename or "—") + (f" + {r.capacity_filename}" if r.capacity_filename else ""),
            "inserted_points", "/disk/{id}/summary")

    rows.sort(key=lambda x: x["at"] or datetime.min, reverse=True)
    return rows


@router.get("/import/history", response_class=HTMLResponse,
            dependencies=[Depends(require_admin)])
def ps_import_history(request: Request, client_id: int | None = None,
                      tipo: str | None = None, db: Session = Depends(get_db)):
    clients = db.query(Client).order_by(Client.name).all()
    rows = _history(db, client_id)
    if tipo:
        rows = [r for r in rows if r["type_key"] == tipo]
    return templates.TemplateResponse("ps_import_history.html", {
        "request": request, "clients": clients, "rows": rows,
        "selected_client_id": client_id, "tipo": tipo,
        "type_labels": TYPE_LABELS,
    })
