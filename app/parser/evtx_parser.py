"""
Parser para arquivos .evtx (Windows Event Log binário).

Utiliza a biblioteca `evtx` (pure-Python) para extrair eventos e os converte
para o formato ParsedEvent unificado, reutilizando a lógica do xml_parser.

Cada evento é armazenado com o XML bruto completo (raw_event_xml) para
permitir reprocessamento futuro sem necessidade de re-upload dos arquivos.
"""
import hashlib
from typing import List

from app.parser.common import (
    AUDIT_EVENT_IDS, VEEAM_JOB_EVENT_IDS,
    ParsedEvent, PARSER_VERSION,
)
from app.parser.xml_parser import _parse_backup_element, _parse_audit_element, _get_event_id


def parse_evtx_file(filepath: str) -> List[ParsedEvent]:
    """
    Parse completo de um arquivo .evtx.
    Retorna lista unificada com eventos de backup E auditoria.
    """
    try:
        import evtx
    except ImportError:
        raise RuntimeError("Biblioteca 'evtx' não instalada. Execute: pip install evtx")

    import xml.etree.ElementTree as ET

    events: List[ParsedEvent] = []

    try:
        parser = evtx.PyEvtxParser(filepath)
        for record in parser.records():
            # Library yields RuntimeError objects for corrupt records instead of raising
            if isinstance(record, Exception):
                continue

            raw_xml = record.get("data", "")
            if not raw_xml:
                continue

            try:
                elem = ET.fromstring(raw_xml)
            except Exception:
                continue

            content_hash = hashlib.sha256(
                raw_xml.encode("utf-8", errors="replace")
            ).hexdigest()

            eid = _get_event_id(elem)
            if eid is None:
                continue

            if eid in AUDIT_EVENT_IDS:
                ev = _parse_audit_element(elem, raw_xml)
            elif eid in VEEAM_JOB_EVENT_IDS:
                ev = _parse_backup_element(elem, raw_xml)
            else:
                continue

            if ev is not None:
                ev.content_hash   = content_hash
                ev.parser_version = PARSER_VERSION
                ev.record_id      = record.get("event_record_id")
                events.append(ev)

    except Exception as e:
        raise ValueError(f"Erro ao ler arquivo EVTX: {e}")

    return events
