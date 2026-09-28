"""
Geração de relatórios por cliente + período.

O relatório consulta a base histórica (ImportedEvent) para o intervalo
selecionado — sem nenhum vínculo com uploads específicos.

Fluxo:
  1. Filtrar ImportedEvent por client_id + data + categorias desejadas
  2. Agrupar por job_name_normalized
  3. Calcular stats: total, failed, warning, success, last3_summary
  4. Pré-popular campos de ação a partir de JobAction (persistência cross-relatório)
  5. Criar ReportSession + JobSummary records
  6. Retornar ReportSession
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from app.models import ImportedEvent, JobAction, JobSummary, ReportSession
from app.services.classification_service import auto_situation


RESULT_CHAR = {0: "S", 1: "W", 2: "F", None: "?"}


def build_report(
    db: Session,
    client_id: int,
    client_name: str,
    date_start: datetime,
    date_end: datetime,
    include_backup: bool = True,
    include_log_backup: bool = True,
    show_all_jobs: bool = False,
    created_by: Optional[str] = None,
    is_snapshot: bool = False,
) -> ReportSession:
    """
    Gera um ReportSession a partir da base histórica de ImportedEvents.
    Não exige upload_id — o relatório é sempre uma consulta por período.
    """
    # ── Consultar eventos do período ──────────────────────────────────────────
    q = (
        db.query(ImportedEvent)
        .filter(
            ImportedEvent.client_id == client_id,
            ImportedEvent.time_created >= date_start,
            ImportedEvent.time_created <= date_end,
            ImportedEvent.event_category.in_(["backup", "log_backup"]),
            # Excluir EV150 de sub-tarefas de VM (não são jobs de backup diretamente)
            ~(
                (ImportedEvent.event_id == 150)
                & (ImportedEvent.event_category == "backup")
            ),
        )
        .order_by(ImportedEvent.time_created.asc())
    )

    if not include_backup:
        q = q.filter(ImportedEvent.event_category != "backup")
    if not include_log_backup:
        q = q.filter(ImportedEvent.event_category != "log_backup")

    events = q.all()

    # ── Agrupar por job_name (usar normalizado como chave de agrupamento) ─────
    groups: dict[str, list[ImportedEvent]] = defaultdict(list)
    for ev in events:
        key = ev.job_name_normalized or ev.job_name or "(sem nome)"
        groups[key].append(ev)

    # ── Carregar ações persistentes para pré-popular JobSummary ───────────────
    job_actions: dict[str, JobAction] = {}
    if groups:
        actions = (
            db.query(JobAction)
            .filter(
                JobAction.client_id == client_id,
                JobAction.job_name_normalized.in_(list(groups.keys())),
            )
            .all()
        )
        job_actions = {ja.job_name_normalized: ja for ja in actions}

    # ── Criar ReportSession ───────────────────────────────────────────────────
    report = ReportSession(
        client_id=client_id,
        client_name=client_name,
        date_start=date_start,
        date_end=date_end,
        created_by=created_by,
        include_backup=include_backup,
        include_log_backup=include_log_backup,
        show_all_jobs=show_all_jobs,
        is_snapshot=is_snapshot,
    )
    db.add(report)
    db.flush()  # gera report.id

    # ── Criar JobSummary para cada job ────────────────────────────────────────
    summaries: list[JobSummary] = []

    for job_norm, job_events in groups.items():
        failed  = sum(1 for e in job_events if e.job_result == 2)
        warning = sum(1 for e in job_events if e.job_result == 1)
        success = sum(1 for e in job_events if e.job_result == 0)
        total   = len(job_events)

        # Ordenar por tempo para last3
        sorted_evs = sorted(
            job_events, key=lambda e: e.time_created or datetime.min, reverse=True
        )
        last3 = "".join(RESULT_CHAR.get(e.job_result) for e in sorted_evs[:3])
        last3_label = last3 if last3 else "Sem histórico suficiente"

        # Última mensagem de erro
        last_error = ""
        for ev in sorted_evs:
            if ev.job_result == 2 and ev.original_message:
                last_error = ev.original_message[:500]
                break

        # Nome canônico (o mais frequente no grupo)
        canonical_name = _most_common_name(job_events)

        # Tipo predominante
        types = [e.event_category for e in job_events]
        job_type = "log_backup" if types.count("log_backup") >= types.count("backup") else "backup"

        # Pré-popular com ação persistente, se houver
        ja = job_actions.get(job_norm)

        js = JobSummary(
            report_id=report.id,
            job_name=canonical_name,
            job_name_normalized=job_norm,
            job_type=job_type,
            total_executions=total,
            failed_count=failed,
            warning_count=warning,
            success_count=success,
            last3_summary=last3_label,
            last_error_message=last_error,
            # Pré-popular de JobAction (persistência cross-relatório)
            situation=ja.situation if ja else auto_situation(failed, total),
            action_ongoing=ja.action_ongoing if ja else "",
            technical_observation=ja.technical_observation if ja else "",
            responsible=ja.responsible if ja else "",
            ticket=ja.ticket if ja else "",
        )

        # Se show_all_jobs=False, ocultar jobs sem falha
        if not show_all_jobs and failed == 0:
            continue

        summaries.append(js)

    if summaries:
        db.add_all(summaries)

    db.commit()
    db.refresh(report)
    return report


def _most_common_name(events: list[ImportedEvent]) -> str:
    """Retorna o job_name mais frequente no grupo."""
    counts: dict[str, int] = defaultdict(int)
    for ev in events:
        if ev.job_name:
            counts[ev.job_name] += 1
    if not counts:
        return "(sem nome)"
    return max(counts, key=counts.get)
