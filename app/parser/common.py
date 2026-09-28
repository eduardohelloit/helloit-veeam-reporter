"""Estruturas de dados e utilitários compartilhados pelos parsers."""
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

PARSER_VERSION = "2.0"


@dataclass
class ParsedEvent:
    """
    Evento Veeam unificado — cobre tanto eventos de backup/log quanto auditoria.
    event_category discrimina o tipo: 'backup' | 'log_backup' | 'audit'.

    Campos raw_*: preservados para reprocessamento futuro.
    Campos derivados: recalculados no reprocessamento sem necessidade de re-upload.
    """
    # ── Dados brutos (nunca alterados após importação) ────────────────────────
    raw_event_xml:    Optional[str] = None   # XML completo (EVTX / XML)
    raw_event_json:   Optional[str] = None   # JSON da linha (CSV)
    original_message: Optional[str] = None
    provider_name:    Optional[str] = None
    channel:          Optional[str] = None
    record_id:        Optional[int] = None
    source_filename:  Optional[str] = None

    # ── Campos indexados (maioria brutos, rápidos para filtrar) ───────────────
    event_id:     Optional[int]      = None
    time_created: Optional[datetime] = None
    vbr_hostname: Optional[str]      = None

    # ── Campos derivados (recalculáveis) ──────────────────────────────────────
    event_category:      str           = "backup"    # backup | log_backup | audit
    job_name:            Optional[str] = None
    job_name_normalized: Optional[str] = None
    job_type:            str           = "backup"    # backup | log_backup
    job_result:          Optional[int] = None        # 0=Sucesso 1=Warning 2=Falha
    will_be_retried:     Optional[bool] = None

    # Auditoria (somente para event_category='audit')
    operator:          Optional[str] = None
    audit_event_type:  Optional[str] = None
    audit_event_label: Optional[str] = None
    audit_details:     Optional[str] = None

    # Rastreabilidade
    content_hash:   Optional[str] = None
    parser_version: str           = PARSER_VERSION


# ── Mapeamento de eventos de auditoria ───────────────────────────────────────

AUDIT_EVENT_MAP: dict[int, tuple[str, str]] = {
    23010: ("job_created",     "Job Criado"),
    23050: ("job_updated",     "Configurações Alteradas"),
    23090: ("job_deleted",     "Job Excluído"),
    23110: ("objects_added",   "Objetos Adicionados"),
    23130: ("objects_changed", "Objetos Alterados"),
    32120: ("objects_deleted", "Objetos Removidos"),
}

AUDIT_EVENT_IDS: frozenset[int] = frozenset(AUDIT_EVENT_MAP.keys())

VEEAM_JOB_EVENT_IDS: frozenset[int] = frozenset({150, 190, 191, 192, 193, 194, 195, 196, 197, 198, 199, 200})

ALL_VEEAM_EVENT_IDS: frozenset[int] = VEEAM_JOB_EVENT_IDS | AUDIT_EVENT_IDS

_LOG_BACKUP_KEYWORDS = (
    "transaction log", "log backup", "postgresql log", "sql server log",
    "oracle log", "mysql log",
)


# ── Helpers ───────────────────────────────────────────────────────────────────

JOB_RESULT_MAP = {
    "0": 0, "success": 0, "succeeded": 0,
    "1": 1, "warning": 1,
    "2": 2, "failed": 2, "error": 2,
}


def parse_job_result(value: str) -> Optional[int]:
    if value is None:
        return None
    return JOB_RESULT_MAP.get(str(value).strip().lower())


def parse_will_be_retried(value: str) -> Optional[bool]:
    if value is None:
        return None
    return str(value).strip().lower() in ("true", "1", "yes")


def is_log_backup(job_desc: Optional[str], job_name: Optional[str] = None) -> bool:
    """Retorna True se o evento EV150 é de transaction log backup."""
    for text in (job_desc or "", job_name or ""):
        if any(kw in text.lower() for kw in _LOG_BACKUP_KEYWORDS):
            return True
    return False


def normalize_job_name(name: Optional[str]) -> str:
    """Normaliza o nome do job para comparação entre períodos."""
    if not name:
        return ""
    # Remove sufixo '(copy)' e similares
    name = re.sub(r"\s*\(copy\)\s*$", "", name, flags=re.IGNORECASE)
    # Normaliza espaços
    return re.sub(r"\s+", " ", name).strip()


def extract_job_name_from_message(message: str) -> Optional[str]:
    patterns = [
        r"[Bb]ackup job\s+'([^']+)'",
        r"[Jj]ob\s+'([^']+)'",
        r"[Jj]ob\s+\"([^\"]+)\"",
        r"[Jj]ob\s+\{([^}]+)\}",          # Job {nome} — formato Veeam comum
        r"[Jj]ob\s+name[:\s]+([^\n;]+)",
        r"JobName[:\s=]+([^\n;]+)",
        r"[Tt]ask\s+\{([^}]+)\}",          # Task {nome} — EV150
    ]
    for pattern in patterns:
        m = re.search(pattern, message)
        if m:
            return m.group(1).strip()
    return None


def extract_result_from_message(message: str) -> Optional[int]:
    m = re.search(r"finished with\s+'?(Success|Warning|Failed)'?", message, re.IGNORECASE)
    if m:
        r = m.group(1).lower()
        if r == "success": return 0
        if r == "warning": return 1
        if r == "failed":  return 2
    msg_lower = message.lower()
    if "failed" in msg_lower:  return 2
    if "warning" in msg_lower: return 1
    if "success" in msg_lower or "succeeded" in msg_lower or "completed" in msg_lower:
        return 0
    return None
