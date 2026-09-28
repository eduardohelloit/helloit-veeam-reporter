"""
disk_usage_service.py — Ingestao de uso de disco + capacidade de repositorio.

Le dois NDJSON do provedor (mesma coleta / mesmo ambiente):
  • collect_disk_backups.ps1  -> disk_backup_points (1 por ponto fisico)
  • collect_repo_capacity.ps1 -> repo_capacity_samples (1 por extent/repo)

Dedup: o mesmo ponto aparece em 2 backups -> deduplica por rp_id DENTRO da carga
(mantem a 1a ocorrencia). Calcula o fator de calibracao logico/real:
  logical_bytes = soma BackupSize (dedup)
  real_used     = soma 'used' dos extents PERFORMANCE
  calib_factor  = logical / real   (block cloning infla o logico)
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Optional

from app.services.offload_service import parse_iso


def _to_int(v) -> Optional[int]:
    if v is None:
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _to_bool(v) -> Optional[bool]:
    if isinstance(v, bool):
        return v
    if v is None:
        return None
    s = str(v).strip().lower()
    if s in ("true", "1", "yes", "sim"):
        return True
    if s in ("false", "0", "no", "nao", "n/d"):
        return False
    return None


def _update_status(import_id: int, message: str, status: str = "processing") -> None:
    from app.database import SessionLocal
    from app.models import DiskUsageImport
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


def _ingest_capacity(db, client_id: int, import_id: int, path: str) -> tuple[int, int]:
    """Insere repo_capacity_samples. Retorna (extents_count, real_used_performance)."""
    from app.models import RepoCapacitySample
    if not path or not os.path.exists(path):
        return 0, 0
    real_used = 0
    count = 0
    with open(path, "r", encoding="utf-8-sig") as fh:
        for raw in fh:
            line = raw.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            total = _to_int(rec.get("total_bytes"))
            free  = _to_int(rec.get("free_bytes"))
            used  = _to_int(rec.get("used_bytes"))
            # -1 = "sem limite reportado" (config backup / object storage) -> None
            if total is not None and total < 0:
                total = None
            if free is not None and free < 0:
                free = None
            if used is not None and used < 0:
                used = None
            tier = (rec.get("tier") or "").strip() or None
            db.add(RepoCapacitySample(
                client_id=client_id, disk_usage_import_id=import_id,
                tier=tier, sobr_name=(rec.get("sobr_name") or None),
                name=(rec.get("name") or None), repo_type=(rec.get("repo_type") or None),
                total_bytes=total, free_bytes=free, used_bytes=used,
                path=(rec.get("path") or None),
            ))
            count += 1
            if tier == "Performance" and used:
                real_used += used
    db.commit()
    return count, real_used


def ingest(
    db,
    *,
    client_id: int,
    disk_path: str,
    capacity_path: Optional[str] = None,
    disk_original: Optional[str] = None,
    capacity_original: Optional[str] = None,
    uploaded_by: Optional[str] = None,
    import_id: Optional[int] = None,
) -> dict:
    """Ingere o NDJSON de disco (obrigatorio) + capacidade (opcional)."""
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from app.models import DiskUsageImport, DiskBackupPoint

    if import_id is None:
        imp = DiskUsageImport(
            client_id=client_id,
            disk_filename=os.path.basename(disk_path),
            capacity_filename=os.path.basename(capacity_path) if capacity_path else None,
            status="processing", status_message="Iniciando ingestao...",
            uploaded_by=uploaded_by,
        )
        db.add(imp); db.commit(); db.refresh(imp)
        import_id = imp.id

    try:
        # ── capacidade primeiro (poucas linhas) -> real_used p/ calibracao ────
        _update_status(import_id, "Lendo capacidade dos repositorios...")
        extents_count, real_used = _ingest_capacity(db, client_id, import_id, capacity_path)

        # ── pontos de disco (dedup por rp_id dentro da carga) ─────────────────
        # O MESMO ponto fisico aparece em 2 backups (o agregado do job e o
        # per-VM "- Imutavel"). Ao deduplicar, PREFERIMOS a ocorrencia mais util:
        #   1) a que TEM immutable_until (nao perder a data de imutabilidade);
        #   2) a do backup cujo nome contem o nome da VM (o per-VM que o operador
        #      reconhece e move no Veeam) em vez do agregado.
        _update_status(import_id, "Lendo pontos de restauracao...")
        best_by_rp: dict = {}
        rows_no_id: list[dict] = []
        total_lines = failed_lines = duplicate = 0
        now = datetime.utcnow()

        def _better(new: dict, old: dict) -> bool:
            n_imm, o_imm = bool(new["immutable_until"]), bool(old["immutable_until"])
            if n_imm != o_imm:
                return n_imm                       # prefere quem tem imutabilidade
            vm = new["vm_name"] or ""
            if vm:
                n_has = vm in (new["backup_name"] or "")
                o_has = vm in (old["backup_name"] or "")
                if n_has != o_has:
                    return n_has                   # prefere o backup per-VM
            return False

        with open(disk_path, "r", encoding="utf-8-sig") as fh:
            for raw in fh:
                line = raw.strip()
                if not line:
                    continue
                total_lines += 1
                try:
                    rec = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    failed_lines += 1
                    continue

                rp_id = rec.get("rp_id")
                rp_id = str(rp_id).strip() if rp_id else None
                tier = (rec.get("tier") or "").strip() or None
                if tier == "n/d":
                    tier = None

                row = {
                    "client_id":            client_id,
                    "disk_usage_import_id": import_id,
                    "rp_id":                rp_id,
                    "job_name":             (rec.get("job_name") or None),
                    "backup_name":          (rec.get("backup_name") or None),
                    "vm_name":              (rec.get("vm_name") or None),
                    "repository":           (rec.get("repository") or None),
                    "extent_name":          (rec.get("extent_name") or None),
                    "tier":                 tier,
                    "restore_point_at":     parse_iso(rec.get("restore_point")),
                    "is_full":              _to_bool(rec.get("is_full")),
                    "backup_type":          (rec.get("type") or None),
                    "gfs_period":           rec.get("gfs_period") or None,
                    "size_bytes":           _to_int(rec.get("size_bytes")),
                    "data_size_bytes":      _to_int(rec.get("data_size_bytes")),
                    "dedup_ratio":          _to_int(rec.get("dedup_ratio")),
                    "compress_ratio":       _to_int(rec.get("compress_ratio")),
                    "is_immutable":         _to_bool(rec.get("is_immutable")),
                    "immutable_until":      parse_iso(rec.get("immutable_until")),
                    "in_capacity_tier":     _to_bool(rec.get("in_capacity_tier")),
                    "created_at":           now,
                }

                if rp_id:
                    prev = best_by_rp.get(rp_id)
                    if prev is None:
                        best_by_rp[rp_id] = row
                    else:
                        duplicate += 1
                        if _better(row, prev):
                            best_by_rp[rp_id] = row
                else:
                    rows_no_id.append(row)

        rows = list(best_by_rp.values()) + rows_no_id

        # totais calculados sobre os pontos JA deduplicados
        logical_bytes = sum((r["size_bytes"] or 0) for r in rows)
        pt_dates = [r["restore_point_at"] for r in rows if r["restore_point_at"]]
        min_at = min(pt_dates) if pt_dates else None
        max_at = max(pt_dates) if pt_dates else None

        _update_status(import_id, f"{len(rows)} pontos fisicos. Inserindo no banco...")

        inserted = 0
        for i in range(0, len(rows), 500):
            chunk = rows[i:i + 500]
            stmt = pg_insert(DiskBackupPoint.__table__).values(chunk).on_conflict_do_nothing()
            inserted += db.execute(stmt).rowcount
            db.commit()

        calib = (logical_bytes / real_used) if (logical_bytes and real_used) else None

        msg = (f"Carga concluida. {inserted} pontos fisicos inseridos"
               + (f" ({duplicate} duplicados por rp_id ignorados)" if duplicate else "")
               + (f", {extents_count} extents/repos" if extents_count else "")
               + (f", fator de calibracao {calib:.2f}x" if calib else "")
               + (f", {failed_lines} linhas com erro" if failed_lines else "") + ".")

        imp = db.query(DiskUsageImport).get(import_id)
        imp.status = "completed"
        imp.status_message = msg
        imp.total_lines = total_lines
        imp.inserted_points = inserted
        imp.duplicate_points = duplicate
        imp.failed_lines = failed_lines
        imp.extents_count = extents_count
        imp.logical_bytes = logical_bytes or None
        imp.real_used_bytes = real_used or None
        imp.calib_factor = calib
        imp.min_point_at = min_at
        imp.max_point_at = max_at
        imp.updated_at = datetime.utcnow()
        db.commit()

        return {"import_id": import_id, "status": "completed", "status_message": msg,
                "inserted_points": inserted, "extents_count": extents_count,
                "calib_factor": calib}

    except Exception as exc:
        db.rollback()
        try:
            imp = db.query(DiskUsageImport).get(import_id)
            if imp:
                imp.status = "failed"
                imp.status_message = f"Erro na ingestao: {exc}"
                imp.updated_at = datetime.utcnow()
                db.commit()
        except Exception:
            db.rollback()
        raise
