"""
backup_perf_service.py — Ingestão de performance de backups de VM (NDJSON).

Responsabilidades:
  • ler NDJSON do coletor PowerShell (collect_backups.ps1 v2 — uma linha por VM);
  • criar/atualizar rotinas (backup_routines) por client_id + job_name + vm_name;
  • inserir execuções (backup_vm_sessions) de forma idempotente
    (dedup por task_session_id / content_hash);
  • registrar resumo da carga em backup_imports.

Princípios:
  (job_name, vm_name) identifica a ROTINA.  task_session_id identifica a EXECUÇÃO.
  Sem classificação de motivo (diferente do offload) — foco em performance.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from typing import Optional

# Reusa parsers do offload (mesmo formato de data/inteiro do coletor)
from app.services.offload_service import parse_iso, _to_int


# ── Helper de status (atualização mid-thread, sessão de DB própria) ───────────

def _update_backup_status(import_id: int, message: str, status: str = "processing") -> None:
    from app.database import SessionLocal
    from app.models import BackupImport
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


def _to_float(value) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def compute_content_hash(
    client_id: int, job_name: str, vm_name: str,
    started_at_raw: Optional[str], result: Optional[str],
) -> str:
    """sha256 estável (dedup fallback quando não há task_session_id)."""
    parts = [str(client_id), job_name or "", vm_name or "",
             started_at_raw or "", result or ""]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


_VALID_RESULTS = {"Success", "Warning", "Failed"}


def ingest_ndjson(
    db,
    *,
    client_id: int,
    file_path: str,
    original_filename: Optional[str] = None,
    uploaded_by: Optional[str] = None,
    import_id: Optional[int] = None,
) -> dict:
    """Importa um NDJSON de performance de backup de forma idempotente."""
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from app.models import BackupImport, BackupRoutine, BackupVmSession

    # Hash + tamanho do arquivo
    file_size = file_hash = None
    try:
        file_size = os.path.getsize(file_path)
        h = hashlib.sha256()
        with open(file_path, "rb") as fb:
            for chunk in iter(lambda: fb.read(1024 * 1024), b""):
                h.update(chunk)
        file_hash = h.hexdigest()
    except OSError:
        pass

    stored_name = os.path.basename(file_path)

    if import_id is None:
        imp = BackupImport(
            client_id=client_id, filename=stored_name,
            original_filename=original_filename or stored_name,
            file_hash=file_hash, file_size_bytes=file_size,
            status="processing", status_message="Iniciando ingestão...",
            uploaded_by=uploaded_by,
        )
        db.add(imp); db.commit(); db.refresh(imp)
        import_id = imp.id
    else:
        imp = db.query(BackupImport).get(import_id)
        if imp:
            imp.file_hash       = file_hash
            imp.file_size_bytes = file_size
            imp.status_message  = "Arquivo recebido. Iniciando análise..."
            imp.updated_at      = datetime.utcnow()
            db.commit()

    routine_cache: dict[tuple, int] = {}     # (job_name, vm_name) → routine_id
    routine_bounds: dict[tuple, list] = {}   # key → [min_started, max_started]
    seen_keys: set = set()
    rows: list[dict] = []

    total_lines = ignored_lines = failed_lines = 0
    success_count = warning_count = failed_count = 0
    duplicate_sessions = 0
    min_started = max_started = None
    now = datetime.utcnow()

    def _get_or_create_routine(job_name: str, vm_name: str, started_dt) -> int:
        key = (job_name, vm_name)
        if key in routine_cache:
            rid = routine_cache[key]
        else:
            rt = (db.query(BackupRoutine)
                  .filter(BackupRoutine.client_id == client_id,
                          BackupRoutine.job_name == job_name,
                          BackupRoutine.vm_name == vm_name)
                  .first())
            if rt is None:
                rt = BackupRoutine(client_id=client_id, job_name=job_name, vm_name=vm_name,
                                   first_seen_at=started_dt, last_seen_at=started_dt)
                db.add(rt); db.flush()
            rid = rt.id
            routine_cache[key] = rid
            routine_bounds[key] = [rt.first_seen_at, rt.last_seen_at]
        if started_dt is not None:
            b = routine_bounds.setdefault(key, [None, None])
            if b[0] is None or started_dt < b[0]: b[0] = started_dt
            if b[1] is None or started_dt > b[1]: b[1] = started_dt
        return rid

    _update_backup_status(import_id, "Lendo linhas do arquivo...")

    try:
        with open(file_path, "r", encoding="utf-8") as fh:
            for raw_line in fh:
                line = raw_line.strip()
                if not line:
                    continue
                total_lines += 1
                try:
                    rec = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    failed_lines += 1
                    continue

                job_name = (rec.get("job_name") or "").strip() or None
                vm_name  = (rec.get("vm_name") or "").strip() or None
                result   = (rec.get("result") or "").strip() or None
                started_raw = rec.get("started_at")

                # vm_name pode faltar em sessão "magra"; usa o job como fallback de rotina
                if not job_name or not result:
                    ignored_lines += 1
                    continue
                if not vm_name:
                    vm_name = "(sem VM)"

                # normaliza result de task ('Success'/'Warning'/'Failed'); ignora outros
                if result not in _VALID_RESULTS:
                    ignored_lines += 1
                    continue

                started_dt = parse_iso(started_raw)
                ended_dt   = parse_iso(rec.get("ended_at"))
                task_sid   = rec.get("task_session_id")
                if task_sid is not None:
                    task_sid = str(task_sid).strip() or None
                job_sid    = rec.get("job_session_id")
                if job_sid is not None:
                    job_sid = str(job_sid).strip() or None
                source_job_id = rec.get("job_id")
                if source_job_id is not None:
                    source_job_id = str(source_job_id).strip() or None

                duration = _to_int(rec.get("duration_seconds"))
                if duration is None and started_dt and ended_dt:
                    duration = int((ended_dt - started_dt).total_seconds())

                reason_raw = rec.get("reason_raw")
                if reason_raw is not None and not str(reason_raw).strip():
                    reason_raw = None

                content_hash = compute_content_hash(client_id, job_name, vm_name, started_raw, result)

                # Dedup intra-arquivo
                key = ("tid", task_sid) if task_sid else ("hash", content_hash)
                if key in seen_keys:
                    duplicate_sessions += 1
                    continue
                seen_keys.add(key)

                if result == "Success":   success_count += 1
                elif result == "Warning": warning_count += 1
                elif result == "Failed":  failed_count += 1

                if started_dt is not None:
                    if min_started is None or started_dt < min_started: min_started = started_dt
                    if max_started is None or started_dt > max_started: max_started = started_dt

                routine_id = _get_or_create_routine(job_name, vm_name, started_dt)

                rows.append({
                    "client_id":         client_id,
                    "backup_routine_id": routine_id,
                    "backup_import_id":  import_id,
                    "job_session_id":    job_sid,
                    "task_session_id":   task_sid,
                    "source_job_id":     source_job_id,
                    "job_name_snapshot": job_name,
                    "vm_name":           vm_name,
                    "result":            result,
                    "started_at":        started_dt,
                    "ended_at":          ended_dt,
                    "duration_seconds":  duration,
                    "processed_gb":      _to_float(rec.get("processed_gb")),
                    "read_gb":           _to_float(rec.get("read_gb")),
                    "transferred_gb":    _to_float(rec.get("transferred_gb")),
                    "avg_speed_mbps":    _to_float(rec.get("avg_speed_mbps")),
                    "backup_type":       (rec.get("backup_type") or None),
                    "reason_raw":        reason_raw,
                    "content_hash":      content_hash,
                    "collected_at":      parse_iso(rec.get("collected_at")),
                    "imported_at":       now,
                    "source":            (rec.get("source") or None),
                })

        # Consolida first/last_seen das rotinas
        for key, rid in routine_cache.items():
            b = routine_bounds.get(key)
            if not b:
                continue
            rt = db.query(BackupRoutine).get(rid)
            if rt is None:
                continue
            if b[0] is not None and (rt.first_seen_at is None or b[0] < rt.first_seen_at):
                rt.first_seen_at = b[0]
            if b[1] is not None and (rt.last_seen_at is None or b[1] > rt.last_seen_at):
                rt.last_seen_at = b[1]
            rt.updated_at = now
        db.commit()

        _update_backup_status(import_id, f"{total_lines} linhas lidas. Inserindo no banco...")

        inserted = 0
        chunk_size = 500
        for i in range(0, len(rows), chunk_size):
            chunk = rows[i:i + chunk_size]
            stmt = pg_insert(BackupVmSession.__table__).values(chunk).on_conflict_do_nothing()
            inserted += db.execute(stmt).rowcount
            db.commit()

        duplicate_sessions += len(rows) - inserted

        status_message = (
            f"Carga concluída. {inserted} execuções novas inseridas"
            + (f", {duplicate_sessions} duplicadas ignoradas" if duplicate_sessions else "")
            + (f", {ignored_lines} linhas ignoradas" if ignored_lines else "")
            + (f", {failed_lines} linhas com erro de parsing" if failed_lines else "")
            + "."
        )

        imp = db.query(BackupImport).get(import_id)
        imp.status             = "completed"
        imp.status_message     = status_message
        imp.total_lines        = total_lines
        imp.inserted_sessions  = inserted
        imp.duplicate_sessions = duplicate_sessions
        imp.ignored_lines      = ignored_lines
        imp.failed_lines       = failed_lines
        imp.success_count      = success_count
        imp.warning_count      = warning_count
        imp.failed_count       = failed_count
        imp.min_started_at     = min_started
        imp.max_started_at     = max_started
        imp.updated_at         = datetime.utcnow()
        db.commit()

        return {
            "import_id": import_id, "status": "completed", "status_message": status_message,
            "total_lines": total_lines, "inserted_sessions": inserted,
            "duplicate_sessions": duplicate_sessions, "ignored_lines": ignored_lines,
            "failed_lines": failed_lines, "success_count": success_count,
            "warning_count": warning_count, "failed_count": failed_count,
            "min_started_at": min_started, "max_started_at": max_started,
        }

    except Exception as exc:
        db.rollback()
        try:
            imp = db.query(BackupImport).get(import_id)
            if imp:
                imp.status = "failed"
                imp.status_message = f"Erro na ingestão: {exc}"
                imp.updated_at = datetime.utcnow()
                db.commit()
        except Exception:
            db.rollback()
        raise
