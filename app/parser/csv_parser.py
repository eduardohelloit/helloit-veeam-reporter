"""
Parser para arquivos CSV exportados do Event Viewer ou de ferramentas Veeam.

Suporta mapeamento flexível de colunas — reconhece múltiplos nomes comuns para
os mesmos campos. O evento bruto da linha é preservado como JSON em raw_event_json.

Campos obrigatórios mínimos: event_id + (time_created | message).
Linhas sem esses campos são contadas como inválidas e ignoradas.
"""
import csv
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from app.parser.common import (
    AUDIT_EVENT_IDS, VEEAM_JOB_EVENT_IDS, AUDIT_EVENT_MAP,
    ParsedEvent, PARSER_VERSION,
    extract_job_name_from_message, extract_result_from_message,
    is_log_backup, normalize_job_name,
    parse_job_result, parse_will_be_retried,
)


# ── Mapeamentos de nomes de coluna (case-insensitive) ─────────────────────────

_TIME_COLS = {
    "date and time", "datetime", "time created", "timecreated",
    "time_created", "data/hora", "data e hora", "date", "time",
    "date/time", "logged",
}
_EVENT_ID_COLS = {
    "event id", "eventid", "event_id", "id", "event", "eventocode",
    "code", "event code",
}
_SOURCE_COLS = {
    "source", "provider", "providername", "provider name", "provider_name",
    "source name", "sourcename", "event source",
}
_LEVEL_COLS = {
    "level", "severity", "tipo", "type", "category", "resultado",
}
_MESSAGE_COLS = {
    "message", "general", "description", "descricao", "descr",
    "details", "mensagem", "msg",
}
_COMPUTER_COLS = {
    "computer", "computer name", "computername", "host", "hostname",
    "server", "servername", "vbr", "machine",
}
_TASK_COLS = {
    "task category", "task", "category", "taskname", "task name",
}
_JOB_NAME_COLS = {
    "job name", "jobname", "job_name", "nome do job", "backup job",
}
_JOB_RESULT_COLS = {
    "job result", "jobresult", "result", "status", "outcome", "estado",
}


# ── Formatos de timestamp aceitos ─────────────────────────────────────────────

_DATETIME_FMTS = [
    "%m/%d/%Y %I:%M:%S %p",    # 01/15/2025 10:30:00 AM
    "%m/%d/%Y %H:%M:%S",       # 01/15/2025 10:30:00
    "%Y-%m-%d %H:%M:%S",       # 2025-01-15 10:30:00
    "%Y-%m-%dT%H:%M:%S",       # 2025-01-15T10:30:00
    "%Y-%m-%dT%H:%M:%S.%f",    # 2025-01-15T10:30:00.000000
    "%d/%m/%Y %H:%M:%S",       # 15/01/2025 10:30:00
    "%d/%m/%Y %H:%M",          # 15/01/2025 10:30
    "%m/%d/%Y %H:%M",          # 01/15/2025 10:30
]


def _parse_datetime(s: str) -> Optional[datetime]:
    s = re.sub(r"\s+", " ", s.strip())
    for fmt in _DATETIME_FMTS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _parse_event_id(s: str) -> Optional[int]:
    try:
        return int(str(s).strip())
    except (ValueError, TypeError):
        return None


def _normalize_col(s: str) -> str:
    return s.strip().lower().replace("-", " ").replace("_", " ")


def _find_col(headers: list, candidates: set) -> Optional[int]:
    """Retorna o índice da primeira coluna que bate com o conjunto de candidatos."""
    for i, h in enumerate(headers):
        if _normalize_col(h) in candidates:
            return i
    return None


def _stable_hash(row: dict, client_id_placeholder: str = "csv") -> str:
    """Hash determinístico da linha para deduplicação cross-upload."""
    key_fields = (
        str(row.get("_event_id", "")),
        str(row.get("_time_created", "")),
        str(row.get("_computer", "")),
        str(row.get("_source", "")),
        (row.get("_message", "") or "")[:200],
    )
    raw = "|".join(key_fields)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ── Parser principal ──────────────────────────────────────────────────────────

class CSVColumnError(ValueError):
    """Raised when the CSV lacks enough columns to import any events."""


def parse_csv_file(filepath: str) -> List[ParsedEvent]:
    """
    Parse de arquivo CSV do Event Viewer.
    Levanta CSVColumnError se as colunas mínimas não forem encontradas.
    Linhas inválidas são silenciosamente ignoradas (contadas externamente).
    """
    path = Path(filepath)

    # Detectar delimitador (vírgula ou ponto-e-vírgula)
    sample = path.read_text(encoding="utf-8", errors="replace")[:4096]
    delimiter = ";" if sample.count(";") > sample.count(",") else ","

    events: List[ParsedEvent] = []
    invalid_count = 0

    with path.open(encoding="utf-8", errors="replace", newline="") as f:
        reader = csv.reader(f, delimiter=delimiter)

        try:
            raw_headers = next(reader)
        except StopIteration:
            raise CSVColumnError("Arquivo CSV vazio ou sem cabeçalho.")

        headers = [h.strip() for h in raw_headers]

        # ── Mapear colunas ────────────────────────────────────────────────────
        col_time     = _find_col(headers, _TIME_COLS)
        col_event_id = _find_col(headers, _EVENT_ID_COLS)
        col_source   = _find_col(headers, _SOURCE_COLS)
        col_level    = _find_col(headers, _LEVEL_COLS)
        col_message  = _find_col(headers, _MESSAGE_COLS)
        col_computer = _find_col(headers, _COMPUTER_COLS)
        col_task     = _find_col(headers, _TASK_COLS)
        col_job_name = _find_col(headers, _JOB_NAME_COLS)
        col_result   = _find_col(headers, _JOB_RESULT_COLS)

        # Validação mínima
        if col_event_id is None and col_message is None:
            raise CSVColumnError(
                "Colunas insuficientes. O CSV precisa ter pelo menos 'Event ID' e/ou 'Message'. "
                f"Colunas encontradas: {', '.join(headers)}"
            )

        source_filename = path.name

        for row_num, row in enumerate(reader, start=2):
            if not row or all(c.strip() == "" for c in row):
                continue

            def get(idx):
                return row[idx].strip() if idx is not None and idx < len(row) else ""

            raw_row = {headers[i]: row[i] for i in range(min(len(headers), len(row)))}
            raw_json = json.dumps(raw_row, ensure_ascii=False)

            # ── Extrair campos ────────────────────────────────────────────────
            time_str   = get(col_time)
            eid_str    = get(col_event_id)
            source     = get(col_source)
            level      = get(col_level)
            message    = get(col_message)
            computer   = get(col_computer)
            job_name_s = get(col_job_name)
            result_s   = get(col_result)

            time_created = _parse_datetime(time_str) if time_str else None
            event_id     = _parse_event_id(eid_str)

            # Sem event_id e sem timestamp: linha inválida
            if event_id is None and time_created is None:
                invalid_count += 1
                continue

            # ── Classificar evento ────────────────────────────────────────────
            if event_id in AUDIT_EVENT_IDS:
                ev = _build_audit_event(
                    event_id, time_created, computer, source, message,
                    raw_json, source_filename,
                )
            elif event_id in VEEAM_JOB_EVENT_IDS or event_id is None:
                ev = _build_backup_event(
                    event_id, time_created, computer, source, level,
                    message, job_name_s, result_s, raw_json, source_filename,
                )
            else:
                continue  # EventID não-Veeam, ignorar

            if ev is None:
                invalid_count += 1
                continue

            # Hash para deduplicação
            hash_input = {
                "_event_id":    str(event_id or ""),
                "_time_created": str(time_created or ""),
                "_computer":    computer,
                "_source":      source,
                "_message":     message[:200] if message else "",
            }
            ev.content_hash = _stable_hash(hash_input)
            events.append(ev)

    return events


def _build_backup_event(
    event_id, time_created, computer, source, level,
    message, job_name_s, result_s, raw_json, source_filename,
) -> Optional[ParsedEvent]:
    ev = ParsedEvent(
        raw_event_json=raw_json,
        source_filename=source_filename,
        parser_version=PARSER_VERSION,
    )
    ev.event_id      = event_id
    ev.time_created  = time_created
    ev.vbr_hostname  = computer or None
    ev.provider_name = source or None
    ev.original_message = message or None

    # Job name: coluna dedicada > extração da mensagem
    ev.job_name = job_name_s or None
    if not ev.job_name and message:
        ev.job_name = extract_job_name_from_message(message)

    if not ev.job_name:
        return None  # sem nome de job não dá para associar a backup

    # Resultado: coluna dedicada > extração da mensagem > level
    if result_s:
        ev.job_result = parse_job_result(result_s)
    if ev.job_result is None and message:
        ev.job_result = extract_result_from_message(message)
    if ev.job_result is None and level:
        level_lc = level.lower()
        if "error" in level_lc or "critical" in level_lc:
            ev.job_result = 2
        elif "warning" in level_lc:
            ev.job_result = 1
        elif "information" in level_lc or "success" in level_lc:
            ev.job_result = 0

    # Tipo
    if is_log_backup(None, ev.job_name):
        ev.job_type = ev.event_category = "log_backup"
    else:
        ev.job_type = ev.event_category = "backup"

    ev.job_name_normalized = normalize_job_name(ev.job_name)
    return ev


def _build_audit_event(
    event_id, time_created, computer, source, message,
    raw_json, source_filename,
) -> Optional[ParsedEvent]:
    ev = ParsedEvent(
        raw_event_json=raw_json,
        source_filename=source_filename,
        parser_version=PARSER_VERSION,
    )
    ev.event_id       = event_id
    ev.time_created   = time_created
    ev.vbr_hostname   = computer or None
    ev.provider_name  = source or None
    ev.original_message = message or None
    ev.event_category = "audit"
    ev.job_type       = "backup"

    if event_id in AUDIT_EVENT_MAP:
        ev.audit_event_type, ev.audit_event_label = AUDIT_EVENT_MAP[event_id]

    # Tentar extrair job name da mensagem
    if message:
        ev.job_name = extract_job_name_from_message(message)
        ev.audit_details = message[:500] if len(message) > 100 else None

    ev.job_name_normalized = normalize_job_name(ev.job_name)
    return ev
