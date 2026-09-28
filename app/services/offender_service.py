"""
offender_service.py — Motor de classificação automática de ofensores de backup.

Aplica regras configuráveis (pattern matching) sobre o campo original_message
dos ImportedEvents para classificar erros de backup em categorias.

Hierarquia de prioridade (decrescente):
  1. Categoria com maior priority
  2. Regra com maior priority dentro da categoria
  3. Primeira regra ativa encontrada por ordem de id

Cache em memória invalidado explicitamente após qualquer alteração de regras.
"""
from __future__ import annotations

import re
import threading
from datetime import datetime
from typing import Optional

# ── Cache de regras ──────────────────────────────────────────────────────────

_lock: threading.Lock = threading.Lock()
_rules_cache: Optional[list[dict]] = None
# Cada entrada: { category_id, rule_id, pattern, match_type, case_sensitive }


def invalidate_cache() -> None:
    """Força recarga das regras na próxima classificação."""
    global _rules_cache
    with _lock:
        _rules_cache = None


def _load_rules(db) -> list[dict]:
    """
    Carrega regras ativas ordenadas por prioridade.
    Ordem: categoria.priority DESC → rule.priority DESC → rule.id ASC.
    """
    from app.models import OffenderCategory, OffenderRule

    rows = (
        db.query(OffenderRule, OffenderCategory.priority.label("cat_prio"))
        .join(OffenderCategory)
        .filter(
            OffenderRule.is_active.is_(True),
            OffenderCategory.is_active.is_(True),
        )
        .order_by(
            OffenderCategory.priority.desc(),
            OffenderRule.priority.desc(),
            OffenderRule.id,
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
    """Retorna regras ativas do cache (carrega sob demanda)."""
    global _rules_cache
    with _lock:
        if _rules_cache is not None:
            return _rules_cache

    rules = _load_rules(db)

    with _lock:
        _rules_cache = rules

    return rules


# ── Motor de classificação ────────────────────────────────────────────────────

def classify_message(
    message: str, rules: list[dict]
) -> tuple[Optional[int], Optional[int], Optional[str]]:
    """
    Retorna (category_id, rule_id, matched_pattern) para a primeira regra
    que fizer match, ou (None, None, None) se nenhuma regra corresponder.
    """
    if not message or not rules:
        return None, None, None

    for rule in rules:
        pattern    = rule["pattern"]
        match_type = rule["match_type"]

        if rule["case_sensitive"]:
            msg_cmp = message
            pat_cmp = pattern
        else:
            msg_cmp = message.lower()
            pat_cmp = pattern.lower()

        matched = False
        if match_type == "contains":
            matched = pat_cmp in msg_cmp
        elif match_type == "starts_with":
            matched = msg_cmp.startswith(pat_cmp)
        elif match_type == "ends_with":
            matched = msg_cmp.endswith(pat_cmp)
        elif match_type == "regex":
            try:
                flags   = 0 if rule["case_sensitive"] else re.IGNORECASE
                matched = bool(re.search(pattern, message, flags))
            except re.error:
                matched = False

        if matched:
            return rule["category_id"], rule["rule_id"], rule["pattern"]

    return None, None, None


# ── Processamento em lote ─────────────────────────────────────────────────────

def classify_events_batch(
    db,
    *,
    upload_session_id: Optional[int] = None,
    client_id: Optional[int] = None,
    reprocess_all: bool = False,
) -> int:
    """
    Classifica eventos de erro (job_result=2) em lote.

    Args:
        upload_session_id: Processa apenas eventos desse upload (pós-import).
        client_id:         Filtra por cliente.
        reprocess_all:     Se True, reclassifica mesmo eventos já classificados.

    Retorna a contagem de eventos que receberam uma categoria.

    Usa keyset pagination por ID para não perder registros em caso de commit
    parcial.
    """
    from app.models import ImportedEvent

    rules = get_rules(db)
    if not rules:
        return 0

    q = db.query(ImportedEvent).filter(
        ImportedEvent.job_result == 2,
        ImportedEvent.event_category.in_(["backup", "log_backup"]),
        ImportedEvent.original_message.isnot(None),
    )

    if client_id:
        q = q.filter(ImportedEvent.client_id == client_id)
    if upload_session_id:
        q = q.filter(ImportedEvent.upload_session_id == upload_session_id)
    elif not reprocess_all:
        # Apenas eventos ainda não classificados
        q = q.filter(ImportedEvent.offender_classified_at.is_(None))

    now       = datetime.utcnow()
    classified = 0
    last_id    = 0
    BATCH      = 500

    while True:
        batch = (
            q.filter(ImportedEvent.id > last_id)
            .order_by(ImportedEvent.id)
            .limit(BATCH)
            .all()
        )
        if not batch:
            break

        for ev in batch:
            cat_id, rule_id, pattern = classify_message(ev.original_message, rules)
            ev.offender_category_id   = cat_id
            ev.offender_rule_id       = rule_id
            ev.offender_match_pattern = pattern
            ev.offender_classified_at = now
            if cat_id:
                classified += 1

        last_id = batch[-1].id
        db.commit()

        if len(batch) < BATCH:
            break

    return classified


# ── Dados pré-definidos para seed ─────────────────────────────────────────────

DEFAULT_CATEGORIES: list[dict] = [
    {
        "name": "Repositório / Desempenho",
        "description": "Erros relacionados à indisponibilidade ou falha de repositórios de backup.",
        "severity": "high",
        "color": "#DC3545",
        "priority": 200,
        "rules": [
            "extent offline",
            "repository is unavailable",
            "backup repository is not available",
            "failed to connect to repository",
            "no space left on device",
            "failed to write data to the file",
            "storage is full",
            "unable to allocate processing resources",
            "the file exists",
            "disk quota exceeded",
        ],
    },
    {
        "name": "Conectividade / Proxy",
        "description": "Erros de rede, proxy ou acesso remoto ao guest.",
        "severity": "high",
        "color": "#0D6EFD",
        "priority": 190,
        "rules": [
            "RPC server is unavailable",
            "Failed to connect to guest agent",
            "No accessible proxies",
            "network path was not found",
            "connection timed out",
            "unable to establish connection",
            "failed to connect to",
            "No route to host",
            "connection refused",
        ],
    },
    {
        "name": "VSS",
        "description": "Erros de Volume Shadow Copy (VSS) durante snapshot da VM.",
        "severity": "high",
        "color": "#FD7E14",
        "priority": 185,
        "rules": [
            "VSS",
            "SqlServerWriter",
            "VSS_WS_FAILED_AT_PREPARE_SNAPSHOT",
            "Cannot create a shadow copy",
            "A VSS critical writer has failed",
            "Failed to freeze guest",
            "Failed to call RPC function 'Vss.Unfreeze'",
            "quiesce",
            "freeze guest file system",
        ],
    },
    {
        "name": "Object Storage / Capacity Tier",
        "description": "Erros relacionados a Object Storage, S3 ou Capacity Tier.",
        "severity": "medium",
        "color": "#20C997",
        "priority": 180,
        "rules": [
            "ServiceUnavailable",
            "object storage",
            "S3",
            "failed to read HTTP status line",
            "checkpoint removal",
            "REST.POST.OBJECT_MULTI_DELETE",
            "capacity tier",
            "offload",
        ],
    },
    {
        "name": "Datastore Produção",
        "description": "Erros relacionados a espaço insuficiente ou snapshot no datastore VMware.",
        "severity": "medium",
        "color": "#FFC107",
        "priority": 175,
        "rules": [
            "insufficient free disk space on datastore",
            "may run out of free disk space",
            "Skipping VM processing due to insufficient free disk space",
            "open snapshots",
            "datastore",
            "Production datastore",
        ],
    },
    {
        "name": "VMs Excluídas / Não Encontradas",
        "description": "VMs removidas, excluídas da política ou não localizadas no inventário.",
        "severity": "low",
        "color": "#6F42C1",
        "priority": 170,
        "rules": [
            "excluded from backup",
            "VM is excluded",
            "object was not found",
            "virtual machine was not found",
            "VM no longer exists",
            "is unavailable and will be skipped",
            "not found in vCenter",
        ],
    },
]
