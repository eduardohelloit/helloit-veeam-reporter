"""
job_config_service.py — Ingestão de auditoria de configuração de rotinas Veeam (NDJSON).

Responsabilidades:
  • ler NDJSON do coletor PowerShell (collect_job_config.ps1);
  • cada linha = configuração de uma rotina num momento de coleta (snapshot);
  • criar job_config_snapshots + job_config_objects de forma idempotente;
  • registrar resumo da carga em job_config_imports.

Dedup:
  • snapshot: UNIQUE(client_id, job_id, collected_at) WHERE job_id IS NOT NULL
              fallback UNIQUE(client_id, content_hash) WHERE job_id IS NULL
  • object:   UNIQUE(snapshot_id, object_id)   WHERE object_id IS NOT NULL
              fallback UNIQUE(snapshot_id, object_name) WHERE object_id IS NULL

Nota sobre 'objects':
  O PowerShell ConvertTo-Json colapsa arrays de 1 elemento para dict.
  iter_objects() trata ambos os casos.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from typing import Optional

from app.services.offload_service import parse_iso


# ── Helpers ────────────────────────────────────────────────────────────────────

def _to_int(value) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _to_bool(value) -> Optional[bool]:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    s = str(value).strip().lower()
    if s in ("true", "1", "yes"):
        return True
    if s in ("false", "0", "no"):
        return False
    return None


def _str(value) -> Optional[str]:
    if value is None:
        return None
    s = str(value).strip()
    return s if s and s.lower() not in ("n/a", "null", "none", "") else None


def _json_list(value) -> Optional[str]:
    """
    Serializa para JSON SEMPRE válido (array) ou None.
    Trata os 3 formatos que o ConvertTo-Json do PS 5.1 produz:
      • list  -> ["a","b"]      (vários elementos)
      • str   -> ["a"]          (escalar; PS colapsa array de 1 elemento)
      • dict  -> usa .values()  (colapso atípico) ou None se vazio
    Garante que o detalhe da tela possa fazer json.loads() sem estourar.
    """
    if value is None:
        return None
    if isinstance(value, dict):
        if not value:
            return None
        value = list(value.values())
    if isinstance(value, list):
        if not value:
            return None
        return json.dumps(value, ensure_ascii=False)
    s = str(value).strip()
    if not s or s.lower() in ("n/a", "null", "none"):
        return None
    return json.dumps([s], ensure_ascii=False)


def iter_objects(raw_objects):
    """Itera sobre objetos do campo 'objects' — trata dict (1 VM) ou list (N VMs)."""
    if raw_objects is None:
        return
    if isinstance(raw_objects, dict):
        # PS ConvertTo-Json colapsou o array de 1 elemento — entrega direto
        yield raw_objects
    elif isinstance(raw_objects, list):
        for o in raw_objects:
            yield o


def _content_hash(client_id: int, job_id: Optional[str], job_name: str,
                  collected_at: Optional[str]) -> str:
    parts = [str(client_id), job_id or "", job_name or "", collected_at or ""]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def _update_status(import_id: int, message: str, status: str = "processing") -> None:
    from app.database import SessionLocal
    from app.models import JobConfigImport
    db = SessionLocal()
    try:
        imp = db.query(JobConfigImport).get(import_id)
        if imp:
            imp.status         = status
            imp.status_message = message
            imp.updated_at     = datetime.utcnow()
            db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


# ── Mapeador: linha NDJSON → dict de colunas do snapshot ──────────────────────

def _map_snapshot(rec: dict, client_id: int, import_id: int) -> dict:
    j   = rec.get("job", {}) or {}
    sch = rec.get("schedule", {}) or {}
    rpo = rec.get("repository", {}) or {}
    prx = rec.get("backup_proxy", {}) or {}
    gip = rec.get("guest_interaction_proxy", {}) or {}
    aap = rec.get("application_aware_processing", {}) or {}
    stl = rec.get("sql_transaction_log", {}) or {}
    ret = rec.get("retention", {}) or {}
    adv = rec.get("advanced_settings", {}) or {}
    sto = rec.get("storage_settings", {}) or {}

    job_id       = _str(j.get("job_id"))
    job_name     = _str(j.get("job_name")) or "(sem nome)"
    collected_at_raw = _str(rec.get("collected_at"))
    collected_dt = parse_iso(collected_at_raw)

    # proxy selected names: lista de strings ou dict vazio
    proxy_names = prx.get("selected_proxy_names")
    if isinstance(proxy_names, dict) and not proxy_names:
        proxy_names = None

    transport_modes = prx.get("transport_modes")
    if isinstance(transport_modes, dict) and not transport_modes:
        transport_modes = None

    # dias da semana: lista → string compacta
    days = sch.get("days_of_week")
    if isinstance(days, list):
        days = ",".join(str(d) for d in days) if days else None
    elif isinstance(days, dict):
        days = None
    else:
        days = _str(days)

    # retenção — a EFETIVA depende de storage_retention_type (Days vs Cycles).
    # restore_points_to_keep (SimpleRetentionRestorePoints) é default secundário.
    ret_points   = _to_int(_str(ret.get("restore_points_to_keep")))
    ret_cycles_v = _to_int(_str(ret.get("retention_cycles")))
    ret_days_v   = _to_int(_str(ret.get("retention_days")))
    ret_storage  = _str(ret.get("storage_retention_type"))

    # GFS — só guarda o contador do nível se ele estiver HABILITADO.
    # (KeepBackupsForNumberOf* tem default 1 mesmo desabilitado → não exibir.)
    def _gfs(count_key, enabled_key):
        return _to_int(_str(ret.get(count_key))) if _to_bool(ret.get(enabled_key)) else None
    gfs_weekly_v  = _gfs("gfs_weekly",  "gfs_weekly_enabled")
    gfs_monthly_v = _gfs("gfs_monthly", "gfs_monthly_enabled")
    gfs_yearly_v  = _gfs("gfs_yearly",  "gfs_yearly_enabled")

    # storage — compression e block size ficam em storage_settings ou advanced
    compression = _str(sto.get("compression_level") or adv.get("compression_level"))
    block_size  = _str(sto.get("storage_block_size") or adv.get("block_size"))
    dedup       = _to_bool(sto.get("enable_deduplication") if "enable_deduplication" in sto
                           else adv.get("deduplication_enabled"))
    encryption  = _to_bool(sto.get("storage_encryption_enabled") if "storage_encryption_enabled" in sto
                           else adv.get("encryption_enabled"))
    cbt         = _to_bool(adv.get("use_change_tracking") or adv.get("cbt_enabled"))

    # Full sintético / active / compact — QUANDO cada um roda.
    # Mesmo tratamento do GFS: os campos de agendamento (kind/days/monthly) têm
    # valor default mesmo com o tipo DESLIGADO (ex.: compact_full_schedule vem
    # "Monthly" com enable_compact_full=False). Guardar isso faria a tela mentir,
    # então só persistimos o agendamento quando o tipo está habilitado.
    def _adv(key):
        """Campo de storage/advanced — o coletor emite os dois blocos iguais."""
        return adv.get(key) if key in adv else sto.get(key)

    def _full(dst: str, src: str, kind_key: str) -> dict:
        on = _to_bool(_adv(f"{src}_enabled"))
        keep = (lambda key: _str(_adv(key))) if on else (lambda key: None)
        return {
            f"{dst}_enabled": on,
            f"{dst}_kind":    keep(kind_key),
            f"{dst}_days":    keep(f"{src}_days"),
            f"{dst}_monthly": keep(f"{src}_monthly"),
        }

    full_cols = {
        "full_algorithm":         _str(_adv("backup_algorithm")),
        "transform_to_rollbacks": _to_bool(_adv("transform_to_rollbacks")),
    }
    full_cols.update(_full("synth_full",   "synthetic_full", "synthetic_full_kind"))
    full_cols.update(_full("active_full",  "active_full",    "active_full_kind"))
    # compact: o coletor usa a chave legada "compact_full_schedule" para o kind
    full_cols.update(_full("compact_full", "compact_full",   "compact_full_schedule"))

    chash = _content_hash(client_id, job_id, job_name, collected_at_raw)

    return dict(
        client_id            = client_id,
        job_config_import_id = import_id,
        job_id               = job_id,
        job_name             = job_name,
        job_name_normalized  = _str(j.get("job_name_normalized")),
        job_type             = _str(j.get("job_type")),
        platform             = _str(j.get("platform")),
        backup_type          = _str(j.get("backup_type")),
        job_description      = _str(j.get("job_description")),
        is_enabled           = _to_bool(j.get("is_enabled")),
        is_schedule_enabled  = _to_bool(j.get("is_schedule_enabled")),
        next_run             = _str(j.get("next_run")),

        collected_at  = collected_dt,
        vbr_server    = _str(rec.get("vbr_server")),
        computer_name = _str(rec.get("computer_name")),
        script_version= _str(rec.get("script_version")),

        sched_daily_enabled        = _to_bool(sch.get("daily_enabled")),
        sched_daily_time           = _str(sch.get("daily_time")),
        sched_daily_kind           = _str(sch.get("daily_kind")),
        sched_days_of_week         = days,
        sched_periodically_enabled = _to_bool(sch.get("periodically_enabled")),
        sched_periodically_every   = _to_int(_str(sch.get("periodically_every"))),
        sched_periodically_unit    = _str(sch.get("periodically_unit")),
        sched_retry_enabled        = _to_bool(sch.get("retry_enabled")),
        sched_retry_count          = _to_int(_str(sch.get("retry_count"))),
        sched_chain_job_name       = _str(sch.get("chain_job_name")),

        repo_name              = _str(rpo.get("repository_name")),
        repo_id                = _str(rpo.get("repository_id")),
        repo_type              = _str(rpo.get("repository_type")),
        repo_is_sobr           = _to_bool(rpo.get("is_sobr")),
        repo_sobr_name         = _str(rpo.get("sobr_name")),
        repo_immutability      = _to_bool(rpo.get("immutability_supported")),
        repo_is_linux_hardened = _to_bool(rpo.get("is_linux_hardened")),
        repo_per_vm_files      = _to_bool(rpo.get("per_vm_backup_files")),
        repo_extent_count        = _to_int(_str(rpo.get("extent_count"))),
        repo_extent_names        = _json_list(rpo.get("extent_names")),
        repo_capacity_name       = _str(rpo.get("capacity_tier_name")),
        repo_capacity_immutable  = _to_bool(rpo.get("capacity_tier_immutable")),
        repo_capacity_immut_days = _to_int(_str(rpo.get("capacity_tier_immut_days"))),

        proxy_mode            = _str(prx.get("proxy_selection_mode")),
        proxy_automatic       = _to_bool(prx.get("automatic_proxy_selection")),
        proxy_selected_names  = _json_list(proxy_names),
        proxy_transport_modes = _json_list(transport_modes),

        gip_mode      = _str(gip.get("guest_interaction_proxy_mode")),
        gip_automatic = _to_bool(gip.get("automatic_guest_interaction_proxy")),

        aap_enabled         = _to_bool(aap.get("application_aware_enabled")),
        aap_mode            = _str(aap.get("application_aware_mode")),
        aap_require_success = _to_bool(aap.get("require_success")),
        vmtools_quiesce     = _to_bool(aap.get("vmware_tools_quiescence_enabled")),

        sql_detected            = _to_bool(stl.get("detected")),
        sql_processing_enabled  = _to_bool(stl.get("sql_processing_enabled")),
        sql_tx_mode             = _str(stl.get("transaction_log_mode")),
        sql_log_backup_enabled  = _to_bool(stl.get("transaction_log_backup_enabled")),
        sql_log_freq_min        = _to_int(_str(stl.get("transaction_log_frequency_minutes"))),
        sql_log_retention_days  = _to_int(_str(stl.get("log_retention_days"))),
        sql_per_object_detected = _to_bool(stl.get("per_object_log_backup_detected")),

        ret_type           = _str(ret.get("retention_type")),
        ret_storage_type   = ret_storage,
        ret_restore_points = ret_points,
        ret_cycles         = ret_cycles_v,
        ret_days           = ret_days_v,
        ret_gfs_enabled    = _to_bool(ret.get("gfs_enabled")),
        ret_gfs_weekly     = gfs_weekly_v,
        ret_gfs_monthly    = gfs_monthly_v,
        ret_gfs_yearly     = gfs_yearly_v,

        stg_compression   = compression,
        stg_block_size    = block_size,
        stg_dedup_enabled = dedup,
        stg_encryption    = encryption,
        stg_cbt_enabled   = cbt,

        **full_cols,

        content_hash = chash,
    )


def _map_object(o: dict, client_id: int, snapshot_id: int) -> Optional[dict]:
    obj_name = _str(o.get("object_name"))
    if not obj_name:
        return None
    sql = o.get("sql_transaction_log", {}) or {}
    return dict(
        client_id               = client_id,
        job_config_snapshot_id  = snapshot_id,
        object_id               = _str(o.get("object_id")),
        object_name             = obj_name,
        object_type             = _str(o.get("object_type")),
        object_path             = _str(o.get("path")),
        approx_size             = _str(o.get("approx_size")),
        guest_processing_enabled    = _to_bool(o.get("guest_processing_enabled")),
        application_aware_enabled   = _to_bool(o.get("application_aware_enabled")),
        sql_mode                = _str(sql.get("mode")),
        sql_log_backup_enabled  = _to_bool(sql.get("transaction_log_backup_enabled")),
        sql_log_freq_min        = _to_int(_str(sql.get("frequency_minutes"))),
        sql_log_retain_days     = _to_int(_str(sql.get("retain_days"))),
        sql_use_db_retention    = _to_bool(sql.get("use_db_backup_retention")),
        sql_truncate_enabled    = _to_bool(sql.get("truncate_logs_enabled")),
    )


# ── Ingestão principal ─────────────────────────────────────────────────────────

def ingest_ndjson(
    db,
    *,
    client_id: int,
    file_path: str,
    original_filename: Optional[str] = None,
    uploaded_by: Optional[str] = None,
    import_id: Optional[int] = None,
) -> dict:
    """Importa NDJSON de auditoria de configuração de forma idempotente."""
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from app.models import JobConfigImport, JobConfigSnapshot, JobConfigObject

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
        imp = JobConfigImport(
            client_id=client_id, filename=stored_name,
            original_filename=original_filename or stored_name,
            file_hash=file_hash, file_size_bytes=file_size,
            status="processing", status_message="Iniciando ingestão...",
            uploaded_by=uploaded_by,
        )
        db.add(imp); db.commit(); db.refresh(imp)
        import_id = imp.id
    else:
        imp = db.query(JobConfigImport).get(import_id)
        if imp:
            imp.file_hash       = file_hash
            imp.file_size_bytes = file_size
            imp.status_message  = "Arquivo recebido. Iniciando análise..."
            imp.updated_at      = datetime.utcnow()
            db.commit()

    total_lines = ignored_lines = failed_lines = 0
    inserted_jobs = updated_jobs = duplicate_jobs = 0
    inserted_objects = total_objects_count = 0
    min_collected = max_collected = None
    vbr_server_seen = script_version_seen = None
    snapshot_rows: list[dict] = []
    # Para objetos: acumulamos separado e inserimos após flush dos snapshots
    objects_by_hash: list[tuple[str, list[dict]]] = []  # (content_hash, [obj_dicts])
    seen_hashes: set[str] = set()

    _update_status(import_id, "Lendo linhas do arquivo...")

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

                job_name = (rec.get("job", {}) or {}).get("job_name")
                if not job_name:
                    ignored_lines += 1
                    continue

                snap_row = _map_snapshot(rec, client_id, import_id)
                chash = snap_row["content_hash"]

                # dedup intra-arquivo
                if chash in seen_hashes:
                    duplicate_jobs += 1
                    continue
                seen_hashes.add(chash)

                # Coleta de metadados do arquivo
                if not vbr_server_seen:
                    vbr_server_seen = snap_row.get("vbr_server")
                if not script_version_seen:
                    script_version_seen = snap_row.get("script_version")
                col_dt = snap_row.get("collected_at")
                if col_dt:
                    if min_collected is None or col_dt < min_collected:
                        min_collected = col_dt
                    if max_collected is None or col_dt > max_collected:
                        max_collected = col_dt

                # Coleta objetos brutos para associar ao snapshot depois
                raw_objs = rec.get("objects")
                obj_list = list(iter_objects(raw_objs)) if raw_objs else []

                snapshot_rows.append(snap_row)
                objects_by_hash.append((chash, obj_list))

        _update_status(import_id, f"{total_lines} linhas lidas. Inserindo snapshots...")

        now = datetime.utcnow()

        # Inserir snapshots em chunks, ON CONFLICT DO NOTHING (idempotente)
        chunk_size = 100
        for i in range(0, len(snapshot_rows), chunk_size):
            chunk = snapshot_rows[i:i + chunk_size]
            stmt = (
                pg_insert(JobConfigSnapshot.__table__)
                .values(chunk)
                .on_conflict_do_nothing()
            )
            inserted_jobs += db.execute(stmt).rowcount
            db.commit()

        duplicate_jobs += len(snapshot_rows) - inserted_jobs

        _update_status(import_id, f"{inserted_jobs} snapshots inseridos. Inserindo objetos...")

        # Buscar IDs dos snapshots inseridos + pré-existentes para associar objetos
        # Usamos o content_hash como chave de lookup
        all_hashes = [r["content_hash"] for r in snapshot_rows]
        snap_by_hash: dict[str, int] = {}
        for i in range(0, len(all_hashes), 500):
            batch = all_hashes[i:i + 500]
            rows = (db.query(JobConfigSnapshot.content_hash, JobConfigSnapshot.id)
                    .filter(JobConfigSnapshot.client_id == client_id,
                            JobConfigSnapshot.content_hash.in_(batch))
                    .all())
            for h, sid in rows:
                snap_by_hash[h] = sid

        # Também snapshots sem content_hash — fallback por (client_id, job_id, collected_at)
        for snap_row in snapshot_rows:
            chash = snap_row["content_hash"]
            if chash in snap_by_hash:
                continue
            jid = snap_row.get("job_id")
            col_dt = snap_row.get("collected_at")
            if jid and col_dt:
                row = (db.query(JobConfigSnapshot.id)
                       .filter(JobConfigSnapshot.client_id == client_id,
                               JobConfigSnapshot.job_id == jid,
                               JobConfigSnapshot.collected_at == col_dt)
                       .first())
                if row:
                    snap_by_hash[chash] = row[0]

        # Inserir objetos
        obj_rows = []
        for chash, obj_list in objects_by_hash:
            snap_id = snap_by_hash.get(chash)
            if snap_id is None:
                continue
            for o in obj_list:
                mapped = _map_object(o, client_id, snap_id)
                if mapped:
                    total_objects_count += 1
                    obj_rows.append(mapped)

        for i in range(0, len(obj_rows), chunk_size):
            chunk = obj_rows[i:i + chunk_size]
            stmt = (
                pg_insert(JobConfigObject.__table__)
                .values(chunk)
                .on_conflict_do_nothing()
            )
            inserted_objects += db.execute(stmt).rowcount
            db.commit()

        status_msg = (
            f"Carga concluída. {inserted_jobs} rotinas novas"
            + (f", {duplicate_jobs} duplicadas" if duplicate_jobs else "")
            + f". {inserted_objects}/{total_objects_count} objetos inseridos"
            + (f". {failed_lines} linhas com erro" if failed_lines else "")
            + "."
        )

        imp = db.query(JobConfigImport).get(import_id)
        imp.status           = "completed"
        imp.status_message   = status_msg
        imp.total_lines      = total_lines
        imp.inserted_jobs    = inserted_jobs
        imp.updated_jobs     = updated_jobs
        imp.duplicate_jobs   = duplicate_jobs
        imp.ignored_lines    = ignored_lines
        imp.failed_lines     = failed_lines
        imp.total_objects    = total_objects_count
        imp.inserted_objects = inserted_objects
        imp.script_version   = script_version_seen
        imp.vbr_server       = vbr_server_seen
        imp.min_collected_at = min_collected
        imp.max_collected_at = max_collected
        imp.updated_at       = now
        db.commit()

        return {
            "import_id": import_id, "status": "completed", "status_message": status_msg,
            "total_lines": total_lines, "inserted_jobs": inserted_jobs,
            "duplicate_jobs": duplicate_jobs, "ignored_lines": ignored_lines,
            "failed_lines": failed_lines, "total_objects": total_objects_count,
            "inserted_objects": inserted_objects,
        }

    except Exception as exc:
        db.rollback()
        try:
            imp = db.query(JobConfigImport).get(import_id)
            if imp:
                imp.status = "failed"
                imp.status_message = f"Erro na ingestão: {exc}"
                imp.updated_at = datetime.utcnow()
                db.commit()
        except Exception:
            db.rollback()
        raise
