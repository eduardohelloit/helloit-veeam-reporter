"""
import_detect.py — Detecta o TIPO de um NDJSON de coletor PowerShell.

Prioriza o marcador explícito `collection_type` (coletores atualizados); se
ausente (arquivos antigos), cai para a assinatura de campos do 1º registro.

Tipos: job_config | disk_backups | repo_capacity | backup_perf | offload
"""
from __future__ import annotations

import json

# Marcadores explícitos emitidos pelos coletores (campo collection_type).
MARKERS = {
    "veeam_job_configuration_audit": "job_config",
    "veeam_disk_backups":            "disk_backups",
    "veeam_repo_capacity":           "repo_capacity",
    "veeam_backup_performance":      "backup_perf",
    "veeam_offload":                 "offload",
}

TYPE_LABELS = {
    "job_config":    "Config de Rotinas",
    "disk_backups":  "Backups em Disco",
    "repo_capacity": "Capacidade do Repositório",
    "backup_perf":   "Performance de Backup",
    "offload":       "Offload",
}


def first_record(path: str) -> dict | None:
    """Primeiro registro JSON válido do arquivo (ignora linhas vazias)."""
    try:
        with open(path, encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    return obj if isinstance(obj, dict) else None
                except ValueError:
                    return None
    except OSError:
        return None
    return None


def detect_type(path: str) -> str | None:
    """Retorna o tipo detectado ou None se não reconhecer."""
    rec = first_record(path)
    if not isinstance(rec, dict):
        return None

    # 1) marcador explícito
    ct = rec.get("collection_type")
    if ct and ct in MARKERS:
        return MARKERS[ct]

    # 2) assinatura de campos (arquivos sem marcador)
    if "job" in rec and "schedule" in rec:
        return "job_config"
    if "rp_id" in rec or ("vm_name" in rec and "size_bytes" in rec):
        return "disk_backups"
    if ("total_bytes" in rec or "free_bytes" in rec or "used_bytes" in rec) and "tier" in rec:
        return "repo_capacity"
    if "task_session_id" in rec or "processed_gb" in rec or "read_gb" in rec:
        return "backup_perf"
    if "state" in rec or "source" in rec or "session_id" in rec:
        return "offload"
    return None
