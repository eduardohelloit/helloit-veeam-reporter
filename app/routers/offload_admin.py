"""
offload_admin.py — Ingestão de Offload/Capacity Tier via browser.

Espelha o fluxo do Event Viewer (import.html → processing → summary → history):

  GET  /offload/import                  — formulário de upload + cargas recentes
  POST /offload/import                  — recebe .ndjson, grava em disco, inicia thread
  GET  /offload/processing/{import_id}  — página de espera (polling)
  GET  /api/offload/status/{import_id}  — JSON de status (poll endpoint)
  GET  /offload/{import_id}/summary     — resumo pós-carga
  GET  /offload/history                 — histórico de cargas (filtro por cliente)


Todas as rotas são restritas a admin (require_admin).
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
from app.models import Client, OffloadImport
from app.services import branding_service
from app.services.classification_service import situation_badge_class
from app.services.offload_service import ingest_ndjson

router = APIRouter()

BASE_DIR  = Path(__file__).parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
templates.env.globals["situation_badge_class"] = situation_badge_class
templates.env.globals["now"] = datetime.now
templates.env.globals["get_branding"] = branding_service.get_branding
templates.env.filters["format_number"] = lambda n: f"{n:,}".replace(",", ".")

# Diretório de armazenamento dos arquivos .ndjson recebidos via browser
# Reutiliza o volume já montado de uploads do Event Viewer
OFFLOAD_UPLOAD_DIR = Path(os.environ.get("UPLOAD_DIR", "./uploads")) / "offload"

# Limite de tamanho: 500 MB (arquivos NDJSON são texto comprimível)
MAX_OFFLOAD_BYTES = 500 * 1024 * 1024


def _ensure_upload_dir() -> None:
    OFFLOAD_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


# ── Helper de status (sessão própria de DB, chamado do thread) ───────────────

def _update_status(import_id: int, message: str, status: str = "processing") -> None:
    db = SessionLocal()
    try:
        imp = db.query(OffloadImport).get(import_id)
        if imp:
            imp.status         = status
            imp.status_message = message
            imp.updated_at     = datetime.utcnow()
            db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


# ── Worker de background ─────────────────────────────────────────────────────

def _process_offload(
    import_id: int,
    stored_path: Path,
    client_id: int,
    original_filename: str,
) -> None:
    """Processamento assíncrono: delega para ingest_ndjson (já tem dedup e classificação)."""
    db = SessionLocal()
    try:
        ingest_ndjson(
            db,
            client_id=client_id,
            file_path=str(stored_path),
            original_filename=original_filename,
            import_id=import_id,
        )
    except Exception as exc:
        _update_status(import_id, f"Erro na ingestão: {exc}", status="failed")
    finally:
        db.close()


# ═══════════════════════════════════════════════════════════════════════════════
# ROTAS
# ═══════════════════════════════════════════════════════════════════════════════

# ── Formulário de upload ──────────────────────────────────────────────────────

@router.get("/offload/import", response_class=HTMLResponse,
            dependencies=[Depends(require_admin)])
def offload_import_page(request: Request, db: Session = Depends(get_db)):
    return RedirectResponse(url="/import/powershell", status_code=302)  # unificado
    clients = db.query(Client).filter(Client.is_active.is_(True)).order_by(Client.name).all()
    imports = (
        db.query(OffloadImport)
        .order_by(OffloadImport.imported_at.desc())
        .limit(15)
        .all()
    )
    return templates.TemplateResponse(
        "offload_import.html",
        {"request": request, "clients": clients, "imports": imports},
    )


@router.post("/offload/import", dependencies=[Depends(require_admin)])
async def offload_import_upload(
    request: Request,
    file: UploadFile = File(...),
    client_id: int = Form(...),
    db: Session = Depends(get_db),
):
    client = db.query(Client).get(client_id)
    clients = db.query(Client).filter(Client.is_active.is_(True)).order_by(Client.name).all()
    imports = (
        db.query(OffloadImport)
        .order_by(OffloadImport.imported_at.desc())
        .limit(15)
        .all()
    )

    def _error(msg: str, status: int = 400):
        return templates.TemplateResponse(
            "offload_import.html",
            {"request": request, "error": msg, "clients": clients, "imports": imports},
            status_code=status,
        )

    if not client:
        return _error("Cliente não encontrado.")

    fname = file.filename or ""
    if not fname.lower().endswith(".ndjson"):
        return _error("Formato não suportado. O arquivo deve ser .ndjson gerado pelo coletor PowerShell.")

    # Rejeição precoce por Content-Length declarado
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_OFFLOAD_BYTES:
        return _error(
            f"Arquivo acima do limite permitido ({MAX_OFFLOAD_BYTES // (1024 * 1024)} MB).",
            status=413,
        )

    _ensure_upload_dir()
    stored_name = f"{uuid.uuid4()}.ndjson"
    stored_path = OFFLOAD_UPLOAD_DIR / stored_name

    # Cópia em streaming com corte rígido
    written = 0
    try:
        with stored_path.open("wb") as f_out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > MAX_OFFLOAD_BYTES:
                    f_out.close()
                    stored_path.unlink(missing_ok=True)
                    return _error(
                        f"Arquivo acima do limite permitido ({MAX_OFFLOAD_BYTES // (1024 * 1024)} MB).",
                        status=413,
                    )
                f_out.write(chunk)
    except Exception:
        stored_path.unlink(missing_ok=True)
        raise

    file_size_mb = stored_path.stat().st_size / (1024 * 1024)

    # Cria o registro imediatamente para que o redirect funcione antes do processamento
    imp = OffloadImport(
        client_id=client_id,
        filename=stored_name,
        original_filename=fname,
        status="processing",
        status_message=f"Arquivo recebido ({file_size_mb:.1f} MB). Aguardando processamento...",
        uploaded_by=request.session.get("username"),
    )
    db.add(imp)
    db.commit()
    db.refresh(imp)
    import_id = imp.id

    thread = threading.Thread(
        target=_process_offload,
        args=(import_id, stored_path, client_id, fname),
        daemon=True,
    )
    thread.start()

    return RedirectResponse(url=f"/offload/processing/{import_id}", status_code=303)


# ── Página de espera (polling) ────────────────────────────────────────────────

@router.get("/offload/processing/{import_id}", response_class=HTMLResponse,
            dependencies=[Depends(require_admin)])
def offload_processing_page(import_id: int, request: Request, db: Session = Depends(get_db)):
    imp = db.query(OffloadImport).get(import_id)
    if not imp:
        raise HTTPException(status_code=404, detail="Importação não encontrada.")
    if imp.status == "completed":
        return RedirectResponse(url=f"/offload/{import_id}/summary")
    return templates.TemplateResponse(
        "offload_processing.html",
        {"request": request, "imp": imp},
    )


# ── JSON polling endpoint ─────────────────────────────────────────────────────

@router.get("/api/offload/status/{import_id}", dependencies=[Depends(require_admin)])
def api_offload_status(import_id: int, db: Session = Depends(get_db)):
    imp = db.query(OffloadImport).get(import_id)
    if not imp:
        raise HTTPException(status_code=404)
    return JSONResponse({
        "status":    imp.status,
        "message":   imp.status_message or "",
        "inserted":  imp.inserted_sessions or 0,
        "duplicate": imp.duplicate_sessions or 0,
        "total":     imp.total_lines or 0,
    })


# ── Resumo pós-carga ──────────────────────────────────────────────────────────

@router.get("/offload/{import_id}/summary", response_class=HTMLResponse,
            dependencies=[Depends(require_admin)])
def offload_summary(import_id: int, request: Request, db: Session = Depends(get_db)):
    imp = db.query(OffloadImport).get(import_id)
    if not imp:
        raise HTTPException(status_code=404, detail="Importação não encontrada.")
    client = db.query(Client).get(imp.client_id)
    return templates.TemplateResponse(
        "offload_summary.html",
        {"request": request, "imp": imp, "client": client},
    )


# ── Histórico de cargas ───────────────────────────────────────────────────────

@router.get("/offload/history", response_class=HTMLResponse,
            dependencies=[Depends(require_admin)])
def offload_history(
    request: Request,
    client_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
):
    return RedirectResponse(url="/import/history", status_code=302)  # unificado
    q = db.query(OffloadImport).order_by(OffloadImport.imported_at.desc())
    if client_id:
        q = q.filter(OffloadImport.client_id == client_id)
    imports = q.limit(100).all()
    clients = db.query(Client).filter(Client.is_active.is_(True)).order_by(Client.name).all()
    return templates.TemplateResponse(
        "offload_history.html",
        {
            "request":            request,
            "imports":            imports,
            "clients":            clients,
            "selected_client_id": client_id,
        },
    )
