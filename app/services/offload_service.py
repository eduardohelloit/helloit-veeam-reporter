"""
offload_service.py — Ingestão e classificação de sessões de Offload (Capacity Tier).

Responsabilidades:
  • ler arquivo NDJSON gerado pelo coletor PowerShell;
  • normalizar nome de rotina (job_name_normalized);
  • criar/atualizar rotinas (offload_jobs) por client_id + job_name_normalized;
  • normalizar o motivo bruto (mascarar IP/porta e [AP] (<ID>));
  • classificar o motivo em categorias por regras configuráveis;
  • inserir sessões de forma idempotente (dedup por session_id / content_hash);
  • registrar resumo da carga em offload_imports.

Princípios:
  job_name_normalized identifica a ROTINA.   session_id identifica a EXECUÇÃO.
  job_id do arquivo é apenas origem → source_job_id (nunca agrupa execuções).
  reason_raw é preservado; normalização/classificação são derivados reprocessáveis.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
from datetime import datetime
from typing import Optional

from app.services.offender_service import classify_message  # motor de match genérico

# ── Helper de status (para atualização mid-thread) ────────────────────────────

def _update_offload_status(import_id: int, message: str, status: str = "processing") -> None:
    """Abre uma sessão própria de DB para atualizar o status da importação."""
    from app.database import SessionLocal
    from app.models import OffloadImport
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

# ── Normalização de nome de rotina ────────────────────────────────────────────

def normalize_job_name(name: Optional[str]) -> str:
    """
    Gera a chave de agrupamento da rotina.
      • trim + colapsa espaços múltiplos;
      • padroniza espaços ao redor de hífens ('JOB- Imutavel' → 'JOB - Imutavel');
      • uppercase para comparação consistente.
    Exemplos que convergem para a mesma chave:
      'GRUPO - SRV-EXEMPLO- Imutavel Offload'
      'GRUPO - SRV-EXEMPLO - Imutavel Offload'
    """
    if not name:
        return ""
    s = re.sub(r"\s+", " ", name).strip()
    s = re.sub(r"\s*-\s*", " - ", s)        # espaços consistentes ao redor de hífen
    s = re.sub(r"\s+", " ", s).strip()
    return s.upper()


# ── Normalização de motivo (mascara variáveis voláteis) ───────────────────────

_IP_PORT_RE = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}:\d+\b")
_IP_PORT_CAP_RE = re.compile(r"\b(\d{1,3}(?:\.\d{1,3}){3}):(\d+)\b")
_AP_ID_RE = re.compile(r"\[AP\]\s*\([0-9a-fA-F]+\)")


def normalize_reason(reason_raw: Optional[str]) -> tuple[Optional[str], Optional[str], Optional[int]]:
    """
    Retorna (reason_normalized, target_ip, target_port).
    Mascara '10.1.2.3:6162' → '<IP>:<PORT>' e '[AP] (cb282ae7)' → '[AP] (<ID>)'.
    """
    if not reason_raw:
        return None, None, None

    target_ip: Optional[str] = None
    target_port: Optional[int] = None
    m = _IP_PORT_CAP_RE.search(reason_raw)
    if m:
        target_ip = m.group(1)
        try:
            target_port = int(m.group(2))
        except ValueError:
            target_port = None

    s = _IP_PORT_RE.sub("<IP>:<PORT>", reason_raw)
    s = _AP_ID_RE.sub("[AP] (<ID>)", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s, target_ip, target_port


# ── Cache de regras de classificação de motivo ────────────────────────────────

_lock: threading.Lock = threading.Lock()
_rules_cache: Optional[list[dict]] = None


def invalidate_cache() -> None:
    global _rules_cache
    with _lock:
        _rules_cache = None


def _load_rules(db) -> list[dict]:
    from app.models import OffloadReasonCategory, OffloadReasonRule

    rows = (
        db.query(OffloadReasonRule, OffloadReasonCategory.priority.label("cat_prio"))
        .join(OffloadReasonCategory)
        .filter(
            OffloadReasonRule.is_active.is_(True),
            OffloadReasonCategory.is_active.is_(True),
        )
        .order_by(
            OffloadReasonCategory.priority.desc(),
            OffloadReasonRule.priority.desc(),
            OffloadReasonRule.id,
        )
        .all()
    )
    return [
        {
            "category_id":    r.category_id,
            "rule_id":        r.id,
            "pattern":        r.pattern,
            "match_type":     r.match_type,
            "case_sensitive": r.case_sensitive,
        }
        for r, _ in rows
    ]


def get_rules(db) -> list[dict]:
    global _rules_cache
    with _lock:
        if _rules_cache is not None:
            return _rules_cache
    rules = _load_rules(db)
    with _lock:
        _rules_cache = rules
    return rules


# ── Hash de deduplicação (fallback) ───────────────────────────────────────────

def compute_content_hash(
    client_id: int,
    job_name_normalized: str,
    started_at_raw: Optional[str],
    ended_at_raw: Optional[str],
    result: Optional[str],
    reason_raw: Optional[str],
) -> str:
    """sha256 estável a partir dos campos brutos (usado como dedup fallback)."""
    parts = [
        str(client_id),
        job_name_normalized or "",
        started_at_raw or "",
        ended_at_raw or "",
        result or "",
        reason_raw or "",
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


# ── Parsing de datas ISO 8601 (com offset e frações de qualquer tamanho) ──────

def parse_iso(value: Optional[str]) -> Optional[datetime]:
    """
    Converte '2025-01-15T14:08:35.9300000-03:00' em datetime naive (wall-clock).
    Trunca frações > 6 dígitos e remove tzinfo (colunas são TIMESTAMP sem tz).
    """
    if not value:
        return None
    txt = value.strip()
    txt = re.sub(r"(\.\d{6})\d+", r"\1", txt)  # frações a no máx. 6 dígitos
    dt: Optional[datetime] = None
    for candidate in (txt, re.sub(r"\.\d+", "", txt)):
        try:
            dt = datetime.fromisoformat(candidate)
            break
        except ValueError:
            dt = None
    if dt is None:
        return None
    if dt.tzinfo is not None:
        dt = dt.replace(tzinfo=None)
    return dt


def _to_int(value) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ── Ingestão ──────────────────────────────────────────────────────────────────

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
    """
    Importa um arquivo NDJSON de offload de forma idempotente.

    Se `import_id` for fornecido, usa o registro existente em offload_imports
    (criado previamente pelo router para suportar redirect-before-processing).
    Caso contrário, cria o registro internamente (modo CLI).

    Retorna um dict com o resumo (também persistido em offload_imports):
      total_lines, inserted_sessions, duplicate_sessions, ignored_lines,
      failed_lines, success_count, warning_count, failed_count,
      min_started_at, max_started_at, import_id, status.
    """
    import os
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from app.models import OffloadImport, OffloadJob, OffloadSession

    file_size = None
    file_hash = None
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
        # Modo CLI: cria o registro aqui mesmo
        imp = OffloadImport(
            client_id=client_id,
            filename=stored_name,
            original_filename=original_filename or stored_name,
            file_hash=file_hash,
            file_size_bytes=file_size,
            status="processing",
            status_message="Iniciando ingestão...",
            uploaded_by=uploaded_by,
        )
        db.add(imp)
        db.commit()
        db.refresh(imp)
        import_id = imp.id
    else:
        # Modo async: registro já existe — apenas atualiza hash/tamanho detectados
        imp = db.query(OffloadImport).get(import_id)
        if imp:
            imp.file_hash      = file_hash
            imp.file_size_bytes = file_size
            imp.status_message = "Arquivo recebido. Iniciando análise..."
            imp.updated_at     = datetime.utcnow()
            db.commit()

    rules = get_rules(db)
    job_cache: dict[str, int] = {}          # job_name_normalized → offload_job_id
    job_bounds: dict[str, list] = {}        # norm → [min_started, max_started]
    seen_keys: set = set()                  # dedup intra-arquivo
    rows: list[dict] = []

    total_lines = ignored_lines = failed_lines = 0
    success_count = warning_count = failed_count = 0
    duplicate_sessions = 0
    min_started: Optional[datetime] = None
    max_started: Optional[datetime] = None
    now = datetime.utcnow()

    def _get_or_create_job(job_name: str, norm: str, started_dt: Optional[datetime]) -> int:
        if norm in job_cache:
            job_id = job_cache[norm]
        else:
            job = (
                db.query(OffloadJob)
                .filter(OffloadJob.client_id == client_id,
                        OffloadJob.job_name_normalized == norm)
                .first()
            )
            if job is None:
                job = OffloadJob(
                    client_id=client_id,
                    job_name=job_name,
                    job_name_normalized=norm,
                    first_seen_at=started_dt,
                    last_seen_at=started_dt,
                )
                db.add(job)
                db.flush()  # obtém job.id
            job_id = job.id
            job_cache[norm] = job_id
            job_bounds[norm] = [job.first_seen_at, job.last_seen_at]
        # acumula limites para atualizar first/last_seen no fim
        if started_dt is not None:
            b = job_bounds.setdefault(norm, [None, None])
            if b[0] is None or started_dt < b[0]:
                b[0] = started_dt
            if b[1] is None or started_dt > b[1]:
                b[1] = started_dt
        return job_id

    _update_offload_status(import_id, "Lendo e classificando linhas do arquivo...")

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

                job_name   = (rec.get("job_name") or "").strip() or None
                result     = (rec.get("result") or "").strip() or None
                started_raw = rec.get("started_at")

                # Campos mínimos obrigatórios
                if not job_name or not result or not started_raw:
                    ignored_lines += 1
                    continue

                norm = normalize_job_name(job_name)
                started_dt = parse_iso(started_raw)
                ended_dt   = parse_iso(rec.get("ended_at"))
                session_id = (rec.get("session_id") or None)
                if session_id is not None:
                    session_id = str(session_id).strip() or None
                source_job_id = rec.get("job_id")
                if source_job_id is not None:
                    source_job_id = str(source_job_id).strip() or None

                reason_raw = rec.get("reason_raw")
                if reason_raw is not None and not str(reason_raw).strip():
                    reason_raw = None

                # Duração: usa o valor do arquivo; calcula se ausente
                duration = _to_int(rec.get("duration_seconds"))
                if duration is None and started_dt and ended_dt:
                    duration = int((ended_dt - started_dt).total_seconds())

                content_hash = compute_content_hash(
                    client_id, norm, started_raw, rec.get("ended_at"), result, reason_raw,
                )

                # Dedup intra-arquivo
                key = ("sid", session_id) if session_id else ("hash", content_hash)
                if key in seen_keys:
                    duplicate_sessions += 1
                    continue
                seen_keys.add(key)

                # Contagens por resultado (sobre linhas válidas)
                if result == "Success":
                    success_count += 1
                elif result == "Warning":
                    warning_count += 1
                elif result == "Failed":
                    failed_count += 1

                # Período
                if started_dt is not None:
                    if min_started is None or started_dt < min_started:
                        min_started = started_dt
                    if max_started is None or started_dt > max_started:
                        max_started = started_dt

                # Classificação de motivo (apenas Failed/Warning têm motivo)
                reason_norm = cat_id = rule_id = matched = target_ip = target_port = None
                if reason_raw and result in ("Failed", "Warning"):
                    reason_norm, target_ip, target_port = normalize_reason(reason_raw)
                    cat_id, rule_id, matched = classify_message(reason_raw, rules)

                job_id = _get_or_create_job(job_name, norm, started_dt)

                rows.append({
                    "client_id":          client_id,
                    "offload_job_id":     job_id,
                    "offload_import_id":  import_id,
                    "session_id":         session_id,
                    "source_job_id":      source_job_id,
                    "job_name_snapshot":  job_name,
                    "result":             result,
                    "state":              (rec.get("state") or None),
                    "started_at":         started_dt,
                    "ended_at":           ended_dt,
                    "duration_seconds":   duration,
                    "reason_raw":         reason_raw,
                    "reason_normalized":  reason_norm,
                    "reason_category_id": cat_id,
                    "reason_rule_id":     rule_id,
                    "matched_pattern":    matched,
                    "target_ip":          target_ip,
                    "target_port":        target_port,
                    "content_hash":       content_hash,
                    "collected_at":       parse_iso(rec.get("collected_at")),
                    "imported_at":        now,
                    "source":             (rec.get("source") or None),
                })

        # Persiste as rotinas (first/last seen consolidados)
        for norm, job_id in job_cache.items():
            b = job_bounds.get(norm)
            if not b:
                continue
            job = db.query(OffloadJob).get(job_id)
            if job is None:
                continue
            if b[0] is not None and (job.first_seen_at is None or b[0] < job.first_seen_at):
                job.first_seen_at = b[0]
            if b[1] is not None and (job.last_seen_at is None or b[1] > job.last_seen_at):
                job.last_seen_at = b[1]
            job.updated_at = now
        db.commit()

        _update_offload_status(import_id, f"{total_lines} linhas lidas. Inserindo no banco...")

        # Insert idempotente das sessões (ON CONFLICT DO NOTHING cobre ambos os
        # índices únicos parciais: session_id e content_hash)
        inserted = 0
        chunk_size = 500
        for i in range(0, len(rows), chunk_size):
            chunk = rows[i:i + chunk_size]
            stmt = pg_insert(OffloadSession.__table__).values(chunk)
            stmt = stmt.on_conflict_do_nothing()
            result_proxy = db.execute(stmt)
            inserted += result_proxy.rowcount
            db.commit()

        cross_import_dups = len(rows) - inserted
        duplicate_sessions += cross_import_dups

        status = "completed"
        status_message = (
            f"Carga concluída. {inserted} sessões novas inseridas"
            + (f", {duplicate_sessions} duplicadas ignoradas" if duplicate_sessions else "")
            + (f", {ignored_lines} linhas ignoradas" if ignored_lines else "")
            + (f", {failed_lines} linhas com erro de parsing" if failed_lines else "")
            + "."
        )

        imp = db.query(OffloadImport).get(import_id)
        imp.status             = status
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
            "import_id":          import_id,
            "status":             status,
            "status_message":     status_message,
            "total_lines":        total_lines,
            "inserted_sessions":  inserted,
            "duplicate_sessions": duplicate_sessions,
            "ignored_lines":      ignored_lines,
            "failed_lines":       failed_lines,
            "success_count":      success_count,
            "warning_count":      warning_count,
            "failed_count":       failed_count,
            "min_started_at":     min_started,
            "max_started_at":     max_started,
        }

    except Exception as exc:
        db.rollback()
        try:
            imp = db.query(OffloadImport).get(import_id)
            if imp:
                imp.status = "failed"
                imp.status_message = f"Erro na ingestão: {exc}"
                imp.updated_at = datetime.utcnow()
                db.commit()
        except Exception:
            db.rollback()
        raise


# ── Dados pré-definidos para seed ─────────────────────────────────────────────
#
# Prioridade decrescente: categorias mais específicas primeiro.

DEFAULT_OFFLOAD_CATEGORIES: list[dict] = [
    {
        "name": "Rescan do SOBR necessário",
        "description": "Performance tier dessincronizado do capacity tier; exige rescan do Scale-out Backup Repository.",
        "severity": "high",
        "color": "#DC3545",
        "priority": 200,
        "rules": [
            "rescan is required",
            "version mismatch",
            "performance tier is not synchronized with capacity tier",
            "scale-out backup repository rescan is required",
        ],
    },
    {
        "name": "Conexão recusada / porta de agente",
        "description": "Veeam Agent/Data Mover inacessível (porta recusada).",
        "severity": "high",
        "color": "#0D6EFD",
        "priority": 190,
        "rules": [
            "target machine actively refused",
        ],
    },
    {
        "name": "Agente fechado / erro de agente",
        "description": "Agent Provider encerrado ou em erro durante o offload.",
        "severity": "high",
        "color": "#FD7E14",
        "priority": 185,
        "rules": [
            "AgentClosedException",
            "agent is closed",
            "Can't run command because agent is closed",
            "[AP] (",
        ],
    },
    {
        "name": "Falha de certificado / Object Storage",
        "description": "Falha ao recuperar certificado ou acessar o object storage / capacity tier.",
        "severity": "medium",
        "color": "#20C997",
        "priority": 180,
        "rules": [
            "Failed to retrieve certificate",
            "retrieve certificate",
            "cloud-object-storage",
            "object storage",
            "s3.private",
        ],
    },
    {
        "name": "Timeout",
        "description": "Operação excedeu o tempo limite.",
        "severity": "medium",
        "color": "#FFC107",
        "priority": 175,
        "rules": [
            "The wait operation timed out",
            "timed out",
            "timeout",
        ],
    },
    {
        "name": "Falha de rede/transporte",
        "description": "Quebra de conexão de transporte durante a transferência.",
        "severity": "medium",
        "color": "#6F42C1",
        "priority": 170,
        "rules": [
            "Broken pipe",
            "End of file",
            "Connection reset by peer",
            "Bad file descriptor",
        ],
    },
    {
        "name": "Erro de stream/leitura",
        "description": "Falha ao ler dados do stream.",
        "severity": "medium",
        "color": "#6C757D",
        "priority": 165,
        "rules": [
            "Unable to read data from stream",
            "read data from stream",
        ],
    },
]
