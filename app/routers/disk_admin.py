"""
disk_admin.py — Ingestao de uso de disco + capacidade via browser.

Espelha o fluxo de Backup/Offload, mas recebe DOIS arquivos numa carga:
  GET  /disk/import              — formulario (cliente + disk NDJSON + capacity NDJSON)
  POST /disk/import              — grava os arquivos, inicia thread de ingestao
  GET  /disk/processing/{id}     — pagina de espera (polling)
  GET  /api/disk/status/{id}     — JSON de status
  GET  /disk/{id}/summary        — resumo pos-carga
  GET  /disk/history             — historico de cargas

Restrito a admin.
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
from app.models import Client, DiskUsageImport
from app.services import branding_service
from app.services.classification_service import situation_badge_class
from app.services.disk_usage_service import ingest

router = APIRouter()

BASE_DIR  = Path(__file__).parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
templates.env.globals["situation_badge_class"] = situation_badge_class
templates.env.globals["now"] = datetime.now
templates.env.globals["get_branding"] = branding_service.get_branding
templates.env.filters["format_number"] = lambda n: f"{n:,}".replace(",", ".")

DISK_UPLOAD_DIR = Path(os.environ.get("UPLOAD_DIR", "./uploads")) / "disk"
MAX_DISK_BYTES = 500 * 1024 * 1024


def _update_status(import_id: int, message: str, status: str = "processing") -> None:
    db = SessionLocal()
    try:
        imp = db.query(DiskUsageImport).get(import_id)
        if imp:
            imp.status = status
            imp.status_message = message
            imp.updated_at = datetime.utcnow()
            db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


def _process(import_id: int, disk_path: str, cap_path: Optional[str],
             client_id: int, disk_orig: str, cap_orig: Optional[str]) -> None:
    db = SessionLocal()
    try:
        ingest(db, client_id=client_id, disk_path=disk_path, capacity_path=cap_path,
               disk_original=disk_orig, capacity_original=cap_orig, import_id=import_id)
    except Exception as exc:
        _update_status(import_id, f"Erro na ingestao: {exc}", status="failed")
    finally:
        db.close()


async def _save_upload(file: UploadFile, dest_dir: Path) -> tuple[Path, int]:
    dest_dir.mkdir(parents=True, exist_ok=True)
    stored = dest_dir / f"{uuid.uuid4()}.ndjson"
    written = 0
    with stored.open("wb") as out:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            written += len(chunk)
            if written > MAX_DISK_BYTES:
                out.close(); stored.unlink(missing_ok=True)
                raise HTTPException(status_code=413,
                                    detail=f"Arquivo acima de {MAX_DISK_BYTES // (1024*1024)} MB.")
            out.write(chunk)
    return stored, written


# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/disk/import", response_class=HTMLResponse, dependencies=[Depends(require_admin)])
def disk_import_page(request: Request, db: Session = Depends(get_db)):
    return RedirectResponse(url="/import/powershell", status_code=302)  # unificado
    clients = db.query(Client).filter(Client.is_active.is_(True)).order_by(Client.name).all()
    imports = db.query(DiskUsageImport).order_by(DiskUsageImport.imported_at.desc()).limit(15).all()
    return templates.TemplateResponse("disk_import.html",
                                      {"request": request, "clients": clients, "imports": imports})


@router.post("/disk/import", dependencies=[Depends(require_admin)])
async def disk_import_upload(
    request: Request,
    disk_file: UploadFile = File(...),
    capacity_file: Optional[UploadFile] = File(None),
    client_id: int = Form(...),
    db: Session = Depends(get_db),
):
    clients = db.query(Client).filter(Client.is_active.is_(True)).order_by(Client.name).all()
    imports = db.query(DiskUsageImport).order_by(DiskUsageImport.imported_at.desc()).limit(15).all()

    def _error(msg: str, status: int = 400):
        return templates.TemplateResponse(
            "disk_import.html",
            {"request": request, "error": msg, "clients": clients, "imports": imports},
            status_code=status)

    if not db.query(Client).get(client_id):
        return _error("Cliente nao encontrado.")
    dname = disk_file.filename or ""
    if not dname.lower().endswith(".ndjson"):
        return _error("O arquivo de disco deve ser o .ndjson do collect_disk_backups.ps1.")

    disk_stored, _ = await _save_upload(disk_file, DISK_UPLOAD_DIR)

    cap_stored = None
    cap_name = None
    if capacity_file is not None and (capacity_file.filename or "").strip():
        cap_name = capacity_file.filename
        if not cap_name.lower().endswith(".ndjson"):
            disk_stored.unlink(missing_ok=True)
            return _error("O arquivo de capacidade deve ser o .ndjson do collect_repo_capacity.ps1.")
        cap_stored, _ = await _save_upload(capacity_file, DISK_UPLOAD_DIR)

    imp = DiskUsageImport(
        client_id=client_id,
        disk_filename=dname, capacity_filename=cap_name,
        status="processing",
        status_message="Arquivos recebidos. Aguardando processamento...",
        uploaded_by=request.session.get("username"),
    )
    db.add(imp); db.commit(); db.refresh(imp)

    threading.Thread(
        target=_process,
        args=(imp.id, str(disk_stored), str(cap_stored) if cap_stored else None,
              client_id, dname, cap_name),
        daemon=True,
    ).start()

    return RedirectResponse(url=f"/disk/processing/{imp.id}", status_code=303)


@router.get("/disk/processing/{import_id}", response_class=HTMLResponse,
            dependencies=[Depends(require_admin)])
def disk_processing_page(import_id: int, request: Request, db: Session = Depends(get_db)):
    imp = db.query(DiskUsageImport).get(import_id)
    if not imp:
        raise HTTPException(status_code=404, detail="Importacao nao encontrada.")
    if imp.status == "completed":
        return RedirectResponse(url=f"/disk/{import_id}/summary")
    return templates.TemplateResponse("disk_processing.html", {"request": request, "imp": imp})


@router.get("/api/disk/status/{import_id}", dependencies=[Depends(require_admin)])
def api_disk_status(import_id: int, db: Session = Depends(get_db)):
    imp = db.query(DiskUsageImport).get(import_id)
    if not imp:
        raise HTTPException(status_code=404)
    return JSONResponse({
        "status": imp.status, "message": imp.status_message or "",
        "inserted": imp.inserted_points or 0, "total": imp.total_lines or 0,
    })


@router.get("/disk/{import_id}/summary", response_class=HTMLResponse,
            dependencies=[Depends(require_admin)])
def disk_summary_page(import_id: int, request: Request, db: Session = Depends(get_db)):
    imp = db.query(DiskUsageImport).get(import_id)
    if not imp:
        raise HTTPException(status_code=404, detail="Importacao nao encontrada.")
    client = db.query(Client).get(imp.client_id)
    return templates.TemplateResponse("disk_summary.html",
                                      {"request": request, "imp": imp, "client": client})


@router.get("/disk/history", response_class=HTMLResponse, dependencies=[Depends(require_admin)])
def disk_history(request: Request, client_id: Optional[int] = Query(None),
                 db: Session = Depends(get_db)):
    return RedirectResponse(url="/import/history", status_code=302)  # unificado
    q = db.query(DiskUsageImport).order_by(DiskUsageImport.imported_at.desc())
    if client_id:
        q = q.filter(DiskUsageImport.client_id == client_id)
    imports = q.limit(100).all()
    clients = db.query(Client).filter(Client.is_active.is_(True)).order_by(Client.name).all()
    return templates.TemplateResponse("disk_history.html", {
        "request": request, "imports": imports, "clients": clients,
        "selected_client_id": client_id})
