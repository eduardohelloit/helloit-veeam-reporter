"""
backup_admin.py — Ingestão de performance de backups via browser.

Espelha o fluxo de Offload:
  GET  /backups/import                 — formulário de upload + cargas recentes
  POST /backups/import                 — recebe .ndjson, grava em disco, inicia thread
  GET  /backups/processing/{id}        — página de espera (polling)
  GET  /api/backups/status/{id}        — JSON de status
  GET  /backups/{id}/summary           — resumo pós-carga
  GET  /backups/history                — histórico de cargas (filtro por cliente)

Todas as rotas restritas a admin.
"""
from __future__ import annotations

import os
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db, SessionLocal
from app.auth import require_admin
from app.models import Client, BackupImport
from app.services import branding_service
from app.services.classification_service import situation_badge_class
from app.services.backup_perf_service import ingest_ndjson

router = APIRouter()

BASE_DIR  = Path(__file__).parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
templates.env.globals["situation_badge_class"] = situation_badge_class
templates.env.globals["now"] = datetime.now
templates.env.globals["get_branding"] = branding_service.get_branding
templates.env.filters["format_number"] = lambda n: f"{n:,}".replace(",", ".")

BACKUP_UPLOAD_DIR = Path(os.environ.get("UPLOAD_DIR", "./uploads")) / "backups"
MAX_BACKUP_BYTES = 500 * 1024 * 1024


def _ensure_upload_dir() -> None:
    BACKUP_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def _update_status(import_id: int, message: str, status: str = "processing") -> None:
    db = SessionLocal()
    try:
        imp = db.query(BackupImport).get(import_id)
        if imp:
            imp.status         = status
            imp.status_message = message
            imp.updated_at     = datetime.utcnow()
            db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


def _process_backup(import_id: int, stored_path: Path, client_id: int, original_filename: str) -> None:
    db = SessionLocal()
    try:
        ingest_ndjson(db, client_id=client_id, file_path=str(stored_path),
                      original_filename=original_filename, import_id=import_id)
    except Exception as exc:
        _update_status(import_id, f"Erro na ingestão: {exc}", status="failed")
    finally:
        db.close()


# ═══════════════════════════════════════════════════════════════════════════════
# ROTAS
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/backups/import", response_class=HTMLResponse,
            dependencies=[Depends(require_admin)])
def backups_import_page(request: Request, db: Session = Depends(get_db)):
    return RedirectResponse(url="/import/powershell", status_code=302)  # unificado
    clients = db.query(Client).filter(Client.is_active.is_(True)).order_by(Client.name).all()
    imports = db.query(BackupImport).order_by(BackupImport.imported_at.desc()).limit(15).all()
    return templates.TemplateResponse(
        "backups_import.html",
        {"request": request, "clients": clients, "imports": imports},
    )


@router.post("/backups/import", dependencies=[Depends(require_admin)])
async def backups_import_upload(
    request: Request,
    file: UploadFile = File(...),
    client_id: int = Form(...),
    db: Session = Depends(get_db),
):
    client = db.query(Client).get(client_id)
    clients = db.query(Client).filter(Client.is_active.is_(True)).order_by(Client.name).all()
    imports = db.query(BackupImport).order_by(BackupImport.imported_at.desc()).limit(15).all()

    def _error(msg: str, status: int = 400):
        return templates.TemplateResponse(
            "backups_import.html",
            {"request": request, "error": msg, "clients": clients, "imports": imports},
            status_code=status,
        )

    if not client:
        return _error("Cliente não encontrado.")

    fname = file.filename or ""
    if not fname.lower().endswith(".ndjson"):
        return _error("Formato não suportado. O arquivo deve ser .ndjson gerado por collect_backups.ps1.")

    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_BACKUP_BYTES:
        return _error(f"Arquivo acima do limite permitido ({MAX_BACKUP_BYTES // (1024 * 1024)} MB).", status=413)

    _ensure_upload_dir()
    stored_name = f"{uuid.uuid4()}.ndjson"
    stored_path = BACKUP_UPLOAD_DIR / stored_name

    written = 0
    try:
        with stored_path.open("wb") as f_out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > MAX_BACKUP_BYTES:
                    f_out.close()
                    stored_path.unlink(missing_ok=True)
                    return _error(f"Arquivo acima do limite permitido ({MAX_BACKUP_BYTES // (1024 * 1024)} MB).", status=413)
                f_out.write(chunk)
    except Exception:
        stored_path.unlink(missing_ok=True)
        raise

    file_size_mb = stored_path.stat().st_size / (1024 * 1024)

    imp = BackupImport(
        client_id=client_id, filename=stored_name, original_filename=fname,
        status="processing",
        status_message=f"Arquivo recebido ({file_size_mb:.1f} MB). Aguardando processamento...",
        uploaded_by=request.session.get("username"),
    )
    db.add(imp); db.commit(); db.refresh(imp)
    import_id = imp.id

    threading.Thread(
        target=_process_backup,
        args=(import_id, stored_path, client_id, fname),
        daemon=True,
    ).start()

    return RedirectResponse(url=f"/backups/processing/{import_id}", status_code=303)


@router.get("/backups/processing/{import_id}", response_class=HTMLResponse,
            dependencies=[Depends(require_admin)])
def backups_processing_page(import_id: int, request: Request, db: Session = Depends(get_db)):
    imp = db.query(BackupImport).get(import_id)
    if not imp:
        raise HTTPException(status_code=404, detail="Importação não encontrada.")
    if imp.status == "completed":
        return RedirectResponse(url=f"/backups/{import_id}/summary")
    return templates.TemplateResponse("backups_processing.html", {"request": request, "imp": imp})


@router.get("/api/backups/status/{import_id}", dependencies=[Depends(require_admin)])
def api_backups_status(import_id: int, db: Session = Depends(get_db)):
    imp = db.query(BackupImport).get(import_id)
    if not imp:
        raise HTTPException(status_code=404)
    return JSONResponse({
        "status":    imp.status,
        "message":   imp.status_message or "",
        "inserted":  imp.inserted_sessions or 0,
        "duplicate": imp.duplicate_sessions or 0,
        "total":     imp.total_lines or 0,
    })


@router.get("/backups/{import_id}/summary", response_class=HTMLResponse,
            dependencies=[Depends(require_admin)])
def backups_summary(import_id: int, request: Request, db: Session = Depends(get_db)):
    imp = db.query(BackupImport).get(import_id)
    if not imp:
        raise HTTPException(status_code=404, detail="Importação não encontrada.")
    client = db.query(Client).get(imp.client_id)
    return templates.TemplateResponse("backups_summary.html", {"request": request, "imp": imp, "client": client})


@router.get("/backups/history", response_class=HTMLResponse,
            dependencies=[Depends(require_admin)])
def backups_history(request: Request, client_id: Optional[int] = Query(None), db: Session = Depends(get_db)):
    return RedirectResponse(url="/import/history", status_code=302)  # unificado
    q = db.query(BackupImport).order_by(BackupImport.imported_at.desc())
    if client_id:
        q = q.filter(BackupImport.client_id == client_id)
    imports = q.limit(100).all()
    clients = db.query(Client).filter(Client.is_active.is_(True)).order_by(Client.name).all()
    return templates.TemplateResponse(
        "backups_history.html",
        {"request": request, "imports": imports, "clients": clients, "selected_client_id": client_id},
    )
