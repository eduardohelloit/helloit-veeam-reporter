"""
Parse Windows Event Log XML exports (.xml) do Event Viewer.

Retorna lista unificada de ParsedEvent cobrindo:
  - Eventos de backup (EV150, EV190-200)  → event_category='backup' | 'log_backup'
  - Eventos de auditoria (EV23010+)       → event_category='audit'
"""
import hashlib
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import List, Optional

from app.parser.common import (
    AUDIT_EVENT_IDS, AUDIT_EVENT_MAP,
    VEEAM_JOB_EVENT_IDS,
    ParsedEvent, PARSER_VERSION,
    extract_job_name_from_message, extract_result_from_message,
    is_log_backup, normalize_job_name,
    parse_job_result, parse_will_be_retried,
)

NS = "http://schemas.microsoft.com/win/2004/08/events/event"

_EV190_IDX_JOB_NAME = 6
_EV190_IDX_MESSAGE  = 19
_EV150_IDX_JOB_DESC = 8
_EV150_IDX_VM_NAME  = 11
_EV150_IDX_MESSAGE  = 19


def _tag(name: str) -> str:
    return f"{{{NS}}}{name}"


def _parse_timestamp(st: str) -> Optional[datetime]:
    st = st.rstrip("Z")
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(st[:26], fmt)
        except ValueError:
            continue
    return None


def _looks_like_guid(s: str) -> bool:
    import re
    return bool(re.match(r"^[0-9a-f]{8}-[0-9a-f]{4}-", s, re.IGNORECASE))


# ── Classificação de valores de campos de auditoria ───────────────────────────

import re as _re

_DOMAIN_USER_RE = _re.compile(r"^[\w\.\-]+\\[\w\.\-@]+$")
_UPN_RE         = _re.compile(r"^[\w\.\-]+@[\w\.\-]+\.[a-z]+$", _re.IGNORECASE)
_FQDN_RE        = _re.compile(r"^[\w\-]+(?:\.[\w\-]+){1,}$")
_SKIP_VALUES    = {"n\\a", "n/a", "na", "null", "none", "-", "", "0"}


def _classify_value(s: str) -> str:
    if not s:
        return "skip"
    sl = s.strip().lower()
    if sl in _SKIP_VALUES or _looks_like_guid(s):
        return "skip"
    if s.strip().lstrip("-").replace(".", "").isdigit():
        return "skip"
    if _DOMAIN_USER_RE.match(s.strip()):
        return "operator"
    if _UPN_RE.match(s.strip()):
        return "operator"
    if _FQDN_RE.match(s.strip()) and "." in s:
        return "hostname"
    return "value"


# ── Parsers de elementos individuais ─────────────────────────────────────────

def _parse_backup_element(elem: ET.Element, raw_xml: str) -> Optional[ParsedEvent]:
    """Converte um elemento <Event> de backup em ParsedEvent."""
    ev = ParsedEvent(raw_event_xml=raw_xml, parser_version=PARSER_VERSION)

    system = elem.find(_tag("System"))
    if system is None:
        return None

    # EventID
    eid_el = system.find(_tag("EventID"))
    if eid_el is None or not eid_el.text:
        return None
    try:
        ev.event_id = int(eid_el.text.strip())
    except ValueError:
        return None

    if ev.event_id not in VEEAM_JOB_EVENT_IDS:
        return None

    # TimeCreated
    tc_el = system.find(_tag("TimeCreated"))
    if tc_el is not None:
        st = tc_el.get("SystemTime", "")
        if st:
            ev.time_created = _parse_timestamp(st)

    # Computer → vbr_hostname
    comp_el = system.find(_tag("Computer"))
    if comp_el is not None and comp_el.text:
        ev.vbr_hostname = comp_el.text.strip()

    # Provider
    prov_el = system.find(_tag("Provider"))
    if prov_el is not None:
        ev.provider_name = prov_el.get("Name") or prov_el.get("EventSourceName")

    # Channel
    ch_el = system.find(_tag("Channel"))
    if ch_el is not None and ch_el.text:
        ev.channel = ch_el.text.strip()

    # RecordID
    rec_el = system.find(_tag("EventRecordID"))
    if rec_el is not None and rec_el.text:
        try:
            ev.record_id = int(rec_el.text.strip())
        except ValueError:
            pass

    # EventData
    event_data = elem.find(_tag("EventData"))
    if event_data is not None:
        named = {
            d.get("Name"): (d.text or "").strip()
            for d in event_data.findall(_tag("Data"))
            if d.get("Name")
        }
        unnamed = [
            (d.text or "").strip()
            for d in event_data.findall(_tag("Data"))
            if not d.get("Name")
        ]

        if named:
            ev.job_name = named.get("JobName") or named.get("jobName")
            raw_result = named.get("JobResult") or named.get("jobResult")
            if raw_result is not None:
                ev.job_result = parse_job_result(raw_result)
            raw_retry = named.get("WillBeRetried") or named.get("willBeRetried")
            if raw_retry is not None:
                ev.will_be_retried = parse_will_be_retried(raw_retry)
        elif unnamed:
            if ev.event_id == 150:
                _parse_positional_150(ev, unnamed)
            else:
                _parse_positional(ev, unnamed)

    # RenderingInfo fallback
    rendering = elem.find(_tag("RenderingInfo"))
    if rendering is not None:
        msg_el = rendering.find(_tag("Message"))
        if msg_el is not None and msg_el.text:
            msg = msg_el.text.strip()
            ev.original_message = ev.original_message or msg
            if not ev.job_name:
                ev.job_name = extract_job_name_from_message(msg)
            if ev.job_result is None:
                ev.job_result = extract_result_from_message(msg)

    # job_type final
    if ev.event_id == 150:
        if ev.job_type == "backup":
            desc = ""
            event_data = elem.find(_tag("EventData"))
            if event_data is not None:
                unnamed_vals = [
                    (d.text or "").strip()
                    for d in event_data.findall(_tag("Data"))
                    if not d.get("Name")
                ]
                if len(unnamed_vals) > _EV150_IDX_JOB_DESC:
                    desc = unnamed_vals[_EV150_IDX_JOB_DESC]
            if is_log_backup(desc, ev.job_name):
                ev.job_type = "log_backup"
    else:
        ev.job_type = "backup"

    ev.event_category = ev.job_type
    ev.job_name_normalized = normalize_job_name(ev.job_name)

    if not ev.job_name:
        return None
    return ev


def _parse_positional_150(ev: ParsedEvent, vals: list):
    ev.job_type = "backup"
    desc = vals[_EV150_IDX_JOB_DESC].strip() if len(vals) > _EV150_IDX_JOB_DESC else ""
    if is_log_backup(desc):
        ev.job_type = "log_backup"

    vm_name = vals[_EV150_IDX_VM_NAME].strip() if len(vals) > _EV150_IDX_VM_NAME else ""
    if vm_name:
        ev.job_name = vm_name

    msg = vals[_EV150_IDX_MESSAGE].strip() if len(vals) > _EV150_IDX_MESSAGE else ""
    if msg:
        ev.original_message = msg
        ev.job_result = extract_result_from_message(msg)
    if len(vals) > 14:
        ev.will_be_retried = parse_will_be_retried(vals[14])


def _parse_positional(ev: ParsedEvent, vals: list):
    if len(vals) > _EV190_IDX_JOB_NAME:
        candidate = vals[_EV190_IDX_JOB_NAME].strip()
        if candidate and not _looks_like_guid(candidate):
            ev.job_name = candidate

    msg = None
    if len(vals) > _EV190_IDX_MESSAGE:
        msg = vals[_EV190_IDX_MESSAGE].strip()
    if not msg:
        for v in reversed(vals):
            if len(v) > 20 and ("finished" in v.lower() or "job" in v.lower()):
                msg = v.strip()
                break
    if not msg:
        for v in reversed(vals):
            v = v.strip()
            if v and not _looks_like_guid(v) and not v.replace(".", "").isdigit():
                msg = v
                break

    if msg:
        ev.original_message = msg
        ev.job_result = extract_result_from_message(msg)
        if ev.job_name is None:
            ev.job_name = extract_job_name_from_message(msg)
        ev.will_be_retried = msg.strip().lower().startswith("retry of")


def _parse_audit_element(elem: ET.Element, raw_xml: str) -> Optional[ParsedEvent]:
    """Converte um elemento <Event> de auditoria em ParsedEvent com event_category='audit'."""
    try:
        system = elem.find(_tag("System"))
        if system is None:
            return None

        eid_el = system.find(_tag("EventID"))
        if eid_el is None or not eid_el.text:
            return None
        try:
            eid = int(eid_el.text.strip())
        except ValueError:
            return None
        if eid not in AUDIT_EVENT_IDS:
            return None

        ev = ParsedEvent(raw_event_xml=raw_xml, parser_version=PARSER_VERSION)
        ev.event_id       = eid
        ev.event_category = "audit"
        ev.job_type       = "backup"  # não aplicável, mas precisa de valor padrão
        ev.audit_event_type, ev.audit_event_label = AUDIT_EVENT_MAP[eid]

        tc_el = system.find(_tag("TimeCreated"))
        if tc_el is not None:
            st = tc_el.get("SystemTime", "")
            if st:
                ev.time_created = _parse_timestamp(st)

        comp = system.find(_tag("Computer"))
        if comp is not None and comp.text:
            ev.vbr_hostname = comp.text.strip()

        prov_el = system.find(_tag("Provider"))
        if prov_el is not None:
            ev.provider_name = prov_el.get("Name")

        ch_el = system.find(_tag("Channel"))
        if ch_el is not None and ch_el.text:
            ev.channel = ch_el.text.strip()

        rec_el = system.find(_tag("EventRecordID"))
        if rec_el is not None and rec_el.text:
            try:
                ev.record_id = int(rec_el.text.strip())
            except ValueError:
                pass

        event_data = elem.find(_tag("EventData"))
        if event_data is not None:
            data_els = event_data.findall(_tag("Data"))
            for d in data_els:
                name_attr = d.get("Name", "").lower()
                val = (d.text or "").strip()
                if not val:
                    continue
                if name_attr in ("jobname", "job_name", "name", "objectname", "taskname"):
                    ev.job_name = ev.job_name or val
                elif name_attr in ("username", "user", "operator", "account",
                                   "initiatedby", "initiator", "modifiedby", "userid"):
                    ev.operator = ev.operator or val
                elif name_attr in ("servername", "server", "computer", "vbrserver", "hostname"):
                    ev.vbr_hostname = ev.vbr_hostname or val

            extra: list = []
            for d in data_els:
                val = (d.text or "").strip()
                kind = _classify_value(val)
                if kind == "skip":
                    continue
                if kind == "operator" and not ev.operator:
                    ev.operator = val
                elif kind == "hostname" and not ev.vbr_hostname:
                    ev.vbr_hostname = val
                elif kind == "value":
                    if not ev.job_name:
                        ev.job_name = val
                    elif val != ev.job_name:
                        extra.append(val)
            if extra:
                ev.audit_details = " | ".join(extra[:4])

        ev.job_name_normalized = normalize_job_name(ev.job_name)
        return ev

    except Exception:
        return None


# ── Função principal (ponto de entrada unificado) ─────────────────────────────

def parse_xml_file(filepath: str) -> List[ParsedEvent]:
    """
    Parse completo de um arquivo XML do Event Viewer.
    Retorna lista unificada com eventos de backup E auditoria.
    """
    try:
        tree = ET.parse(filepath)
        root = tree.getroot()
    except ET.ParseError as e:
        raise ValueError(f"Arquivo XML inválido: {e}")

    events: List[ParsedEvent] = []
    local_name = root.tag.split("}")[-1] if "}" in root.tag else root.tag

    elements = [root] if local_name == "Event" else list(root.iter(_tag("Event")))

    for elem in elements:
        raw_xml = ET.tostring(elem, encoding="unicode")
        content_hash = hashlib.sha256(raw_xml.encode("utf-8", errors="replace")).hexdigest()

        # Determinar tipo pelo EventID
        eid = _get_event_id(elem)
        if eid in AUDIT_EVENT_IDS:
            ev = _parse_audit_element(elem, raw_xml)
        elif eid in VEEAM_JOB_EVENT_IDS:
            ev = _parse_backup_element(elem, raw_xml)
        else:
            continue

        if ev is not None:
            ev.content_hash = content_hash
            events.append(ev)

    return events


def _get_event_id(elem: ET.Element) -> Optional[int]:
    system = elem.find(_tag("System"))
    if system is None:
        return None
    eid_el = system.find(_tag("EventID"))
    if eid_el is None or not eid_el.text:
        return None
    try:
        return int(eid_el.text.strip())
    except ValueError:
        return None
