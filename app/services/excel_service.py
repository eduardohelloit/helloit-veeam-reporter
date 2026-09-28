"""Generate Excel report with openpyxl."""
import os
from datetime import datetime
from typing import List

import openpyxl
from openpyxl.styles import (
    Font, PatternFill, Alignment, Border, Side, numbers
)
from openpyxl.utils import get_column_letter

from app.models import ReportSession, JobSummary, ImportedEvent


# --- Color palette ---
COLOR_HEADER_BG = "7C3AED"   # HelloIT green
COLOR_HEADER_FONT = "FFFFFF"
COLOR_META_BG = "F3EEFF"
COLOR_ALT_ROW = "F2F7FC"
COLOR_SUCCESS = "C6EFCE"
COLOR_WARNING = "FFEB9C"
COLOR_DANGER = "FFC7CE"
COLOR_NEUTRAL = "FFFFFF"

SITUATION_COLORS = {
    "Superado": COLOR_SUCCESS,
    "Em observação": COLOR_WARNING,
    "Instável": COLOR_WARNING,
    "Ativo": COLOR_DANGER,
    "Indefinido": COLOR_NEUTRAL,
}

RESULT_LABELS = {0: "Success", 1: "Warning", 2: "Failed", None: "—"}
RESULT_COLORS = {0: COLOR_SUCCESS, 1: COLOR_WARNING, 2: COLOR_DANGER, None: COLOR_NEUTRAL}


def _header_font() -> Font:
    return Font(bold=True, color=COLOR_HEADER_FONT, size=11)


def _header_fill() -> PatternFill:
    return PatternFill("solid", fgColor=COLOR_HEADER_BG)


def _cell_fill(color: str) -> PatternFill:
    return PatternFill("solid", fgColor=color)


def _thin_border() -> Border:
    thin = Side(style="thin", color="CCCCCC")
    return Border(left=thin, right=thin, top=thin, bottom=thin)


def _apply_header_row(ws, row_num: int, columns: list[str],
                      font_fn=None, fill_fn=None):
    for col_idx, header in enumerate(columns, 1):
        cell = ws.cell(row=row_num, column=col_idx, value=header)
        cell.font = (font_fn or _header_font)()
        cell.fill = (fill_fn or _header_fill)()
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = _thin_border()


def _auto_width(ws, min_w: int = 10, max_w: int = 60):
    for col in ws.columns:
        max_len = 0
        col_letter = get_column_letter(col[0].column)
        for cell in col:
            try:
                val = str(cell.value or "")
                max_len = max(max_len, len(val))
            except Exception:
                pass
        ws.column_dimensions[col_letter].width = max(min_w, min(max_w, max_len + 2))


def _period_totals(report: ReportSession, all_events: List[ImportedEvent]) -> dict:
    period_evs = [
        e for e in all_events
        if e.time_created and report.date_start <= e.time_created <= report.date_end
    ]
    return {
        "total":   len(period_evs),
        "success": sum(1 for e in period_evs if e.job_result == 0),
        "warning": sum(1 for e in period_evs if e.job_result == 1),
        "failed":  sum(1 for e in period_evs if e.job_result == 2),
        "backup_success":     sum(1 for e in period_evs if e.job_result == 0 and e.event_category == "backup"),
        "backup_total":       sum(1 for e in period_evs if e.event_category == "backup"),
        "log_backup_success": sum(1 for e in period_evs if e.job_result == 0 and e.event_category == "log_backup"),
        "log_backup_total":   sum(1 for e in period_evs if e.event_category == "log_backup"),
    }


def generate_excel(
    report: ReportSession,
    job_summaries: List[JobSummary],
    all_events: List[ImportedEvent],
    output_path: str,
    branding: dict = None,
):
    # Aplica cores de branding ou usa padrão HelloIT
    hdr_bg   = (branding.get("table_header_color", "#7C3AED") or "#7C3AED").lstrip("#") \
               if branding else COLOR_HEADER_BG
    meta_bg  = "F3EEFF"  # mantém o verde-claro como meta background
    company  = branding.get("company_name", "HelloIT") if branding else "HelloIT"
    sys_name = branding.get("system_name", "Veeam Reporter") if branding else "Veeam Reporter"

    def _hdr_fill():  return PatternFill("solid", fgColor=hdr_bg)
    def _hdr_font():  return Font(bold=True, color="FFFFFF", size=11)

    wb = openpyxl.Workbook()

    # ── Sheet 1: Resumo Semanal ──────────────────────────────────────────────
    ws1 = wb.active
    ws1.title = "Resumo Semanal"

    # Meta header block
    meta_fill = _cell_fill(COLOR_META_BG)
    meta_font_bold = Font(bold=True, size=11)

    ws1.merge_cells("A1:H1")
    title_cell = ws1["A1"]
    title_cell.value = f"{sys_name} — {company}"
    title_cell.font = Font(bold=True, size=14, color=hdr_bg)
    title_cell.alignment = Alignment(horizontal="center")

    meta_rows = [
        ("Cliente:", report.client_name or "—"),
        ("Período início:", report.date_start.strftime("%d/%m/%Y 00:00:00")),
        ("Período fim:", report.date_end.strftime("%d/%m/%Y 23:59:59")),
        ("Gerado em:", datetime.now().strftime("%d/%m/%Y %H:%M")),
    ]
    for i, (label, value) in enumerate(meta_rows, start=2):
        cell_a = ws1.cell(row=i, column=1, value=label)
        cell_a.font = meta_font_bold
        cell_a.fill = meta_fill
        cell_b = ws1.cell(row=i, column=2, value=value)
        cell_b.fill = meta_fill

    # Totals block
    t = _period_totals(report, all_events)
    pct_ok = f"{t['success']/t['total']*100:.1f}%" if t['total'] else "—"
    totals_rows = [
        ("Total de execuções no período:", f"{t['total']:,}".replace(",", ".")),
        ("Sucesso:",  f"{t['success']:,}".replace(",", ".") + f"  ({pct_ok})"),
        ("Warning:",  f"{t['warning']:,}".replace(",", ".")),
        ("Falhas:",   f"{t['failed']:,}".replace(",", ".")),
    ]
    if t["backup_total"]:
        totals_rows.append(("  Backups regulares — sucesso:", f"{t['backup_success']:,} de {t['backup_total']:,}".replace(",", ".")))
    if t["log_backup_total"]:
        totals_rows.append(("  Transaction logs — sucesso:", f"{t['log_backup_success']:,} de {t['log_backup_total']:,}".replace(",", ".")))

    totals_start = 2 + len(meta_rows) + 1
    for i, (label, value) in enumerate(totals_rows):
        cell_a = ws1.cell(row=totals_start + i, column=1, value=label)
        cell_a.font = meta_font_bold
        cell_b = ws1.cell(row=totals_start + i, column=2, value=value)
        if "Sucesso" in label:
            cell_a.fill = _cell_fill(COLOR_SUCCESS); cell_b.fill = _cell_fill(COLOR_SUCCESS)
        elif "Warning" in label:
            cell_a.fill = _cell_fill(COLOR_WARNING); cell_b.fill = _cell_fill(COLOR_WARNING)
        elif "Falhas" in label:
            cell_a.fill = _cell_fill(COLOR_DANGER);  cell_b.fill = _cell_fill(COLOR_DANGER)
        else:
            cell_a.fill = meta_fill; cell_b.fill = meta_fill

    # Disclaimer
    disclaimer_row = totals_start + len(totals_rows) + 1
    ws1.merge_cells(f"A{disclaimer_row}:H{disclaimer_row}")
    disc = ws1.cell(
        row=disclaimer_row,
        column=1,
        value=(
            "Relatório gerado com base nos eventos exportados do Windows Event Viewer do Veeam Backup & Replication. "
            "A precisão depende da integridade do arquivo exportado, da retenção dos eventos no servidor VBR "
            "e da presença dos eventos de finalização de job no período analisado."
        ),
    )
    disc.font = Font(italic=True, size=9, color="666666")
    disc.alignment = Alignment(wrap_text=True)
    ws1.row_dimensions[disclaimer_row].height = 40

    # Data table headers
    data_start_row = disclaimer_row + 2
    summary_columns = [
        "Rotina de Backup",
        "Erros na Semana",
        "Últimas 3 Execuções",
        "Situação Atual",
        "Ação em Andamento",
        "Observação Técnica",
        "Responsável",
        "Ticket",
        "Último Erro Identificado",
    ]
    _apply_header_row(ws1, data_start_row, summary_columns, font_fn=_hdr_font, fill_fn=_hdr_fill)

    # Data rows
    for row_offset, js in enumerate(job_summaries):
        r = data_start_row + 1 + row_offset
        row_fill_color = COLOR_ALT_ROW if row_offset % 2 == 0 else COLOR_NEUTRAL
        sit_color = SITUATION_COLORS.get(js.situation, COLOR_NEUTRAL)

        type_label = "[LOG] " if js.job_type == "log_backup" else ""
        values = [
            type_label + js.job_name,
            js.failed_count,
            js.last3_summary,
            js.situation,
            js.action_ongoing or "",
            js.technical_observation or "",
            js.responsible or "",
            js.ticket or "",
            (js.last_error_message or "")[:200],
        ]
        for col_idx, val in enumerate(values, 1):
            cell = ws1.cell(row=r, column=col_idx, value=val)
            cell.border = _thin_border()
            cell.alignment = Alignment(wrap_text=True, vertical="top")
            if col_idx == 4:  # Situação Atual
                cell.fill = _cell_fill(sit_color)
            elif col_idx == 1:
                cell.fill = _cell_fill(row_fill_color)
                cell.font = Font(bold=True)
            else:
                cell.fill = _cell_fill(row_fill_color)

        ws1.row_dimensions[r].height = 30

    _auto_width(ws1)
    ws1.freeze_panes = ws1.cell(row=data_start_row + 1, column=1)

    # ── Sheet 2: Eventos Detalhados ──────────────────────────────────────────
    ws2 = wb.create_sheet(title="Eventos Detalhados")

    detail_columns = [
        "Data/Hora",
        "Job",
        "Event ID",
        "Resultado",
        "Mensagem",
        "WillBeRetried",
        "VBR Host",
    ]
    _apply_header_row(ws2, 1, detail_columns, font_fn=_hdr_font, fill_fn=_hdr_fill)

    for row_offset, ev in enumerate(all_events):
        r = 2 + row_offset
        result_label = RESULT_LABELS.get(ev.job_result, "—")
        result_color = RESULT_COLORS.get(ev.job_result, COLOR_NEUTRAL)
        row_bg = COLOR_ALT_ROW if row_offset % 2 == 0 else COLOR_NEUTRAL

        values = [
            ev.time_created.strftime("%d/%m/%Y %H:%M:%S") if ev.time_created else "—",
            ev.job_name or "—",
            ev.event_id or "—",
            result_label,
            (ev.original_message or "")[:300],
            "Sim" if ev.will_be_retried else ("Não" if ev.will_be_retried is False else "—"),
            ev.vbr_hostname or "—",
        ]
        for col_idx, val in enumerate(values, 1):
            cell = ws2.cell(row=r, column=col_idx, value=val)
            cell.border = _thin_border()
            cell.alignment = Alignment(wrap_text=True, vertical="top")
            if col_idx == 4:  # Resultado
                cell.fill = _cell_fill(result_color)
            else:
                cell.fill = _cell_fill(row_bg)

    _auto_width(ws2)
    ws2.freeze_panes = ws2["A2"]

    wb.save(output_path)


# ── Auditoria ────────────────────────────────────────────────────────────────

_AUDIT_LABELS = {
    "job_created":     "Job Criado",
    "job_updated":     "Config. Alteradas",
    "job_deleted":     "Job Excluído",
    "objects_added":   "Objetos Adicionados",
    "objects_changed": "Objetos Alterados",
    "objects_deleted": "Objetos Removidos",
}

_AUDIT_TYPE_COLORS = {
    "job_created":     "C6EFCE",   # verde
    "job_updated":     "FFEB9C",   # amarelo
    "job_deleted":     "FFC7CE",   # vermelho claro
    "objects_added":   "DDEBF7",   # azul claro
    "objects_changed": "FCE4D6",   # laranja claro
    "objects_deleted": "F2DCDB",   # rosa
}

_IMPACT_COLORS = {
    "Alto":  "FFC7CE",
    "Médio": "FFEB9C",
    "Baixo": "C6EFCE",
}


def _audit_classify_impact(e, failed_job_norms: set) -> str:
    after_hours = bool(e.time_created and (e.time_created.hour < 7 or e.time_created.hour >= 19))
    has_failure = bool((e.job_name_normalized or "") in failed_job_norms)
    if e.audit_event_type in ("job_deleted", "objects_deleted") or has_failure:
        return "Alto"
    if e.audit_event_type in ("objects_added", "objects_changed", "job_updated") or after_hours:
        return "Médio"
    return "Baixo"


def _audit_is_sensitive(e, failed_job_norms: set) -> bool:
    if e.audit_event_type in ("job_deleted", "objects_deleted"):
        return True
    if e.time_created and (e.time_created.hour < 7 or e.time_created.hour >= 19):
        return True
    if (e.job_name_normalized or "") in failed_job_norms:
        return True
    return False


def generate_audit_excel(
    events: list,
    period_info: dict,
    failed_job_norms: set,
    output_path: str,
):
    """Gera Excel de auditoria com 3 planilhas:
    1. Resumo — KPIs e ranking de operadores
    2. Eventos de Auditoria — tabela completa
    3. Alterações Sensíveis — filtro de alto risco
    """
    wb = openpyxl.Workbook()

    # ── Cores e estilos ────────────────────────────────────────────────────────
    hdr_font  = Font(bold=True, color=COLOR_HEADER_FONT, size=10)
    hdr_fill  = PatternFill("solid", fgColor=COLOR_HEADER_BG)
    bold11    = Font(bold=True, size=11)
    meta_fill = _cell_fill(COLOR_META_BG)
    center    = Alignment(horizontal="center", vertical="center", wrap_text=True)
    left_top  = Alignment(horizontal="left",   vertical="top",    wrap_text=True)
    thin      = _thin_border()

    def _hdr(ws, row: int, cols: list):
        for ci, h in enumerate(cols, 1):
            c = ws.cell(row=row, column=ci, value=h)
            c.font = hdr_font; c.fill = hdr_fill
            c.alignment = center; c.border = thin

    def _data_cell(ws, row, col, val, bg=None, bold=False, wrap=True):
        c = ws.cell(row=row, column=col, value=val)
        c.border = thin
        c.alignment = Alignment(horizontal="left", vertical="top", wrap_text=wrap)
        if bg:
            c.fill = PatternFill("solid", fgColor=bg)
        if bold:
            c.font = Font(bold=True)
        return c

    # ── Computar estatísticas ──────────────────────────────────────────────────
    from collections import defaultdict
    type_counts: dict = defaultdict(int)
    op_counts:   dict = defaultdict(int)
    for e in events:
        type_counts[e.audit_event_type or "unknown"] += 1
        if e.operator:
            op_counts[e.operator] += 1

    sensitive = [e for e in events if _audit_is_sensitive(e, failed_job_norms)]

    # ══════════════════════════════════════════════════════════════════
    # Sheet 1: Resumo
    # ══════════════════════════════════════════════════════════════════
    ws1 = wb.active
    ws1.title = "Resumo"

    # Título
    ws1.merge_cells("A1:F1")
    t = ws1["A1"]
    t.value = "Auditoria de Jobs — Relatório de Alterações"
    t.font  = Font(bold=True, size=14, color=COLOR_HEADER_BG)
    t.alignment = Alignment(horizontal="center")

    # Meta
    meta_rows = [
        ("Cliente:",        period_info.get("client_name", "—")),
        ("Período início:", period_info.get("date_start", "—")),
        ("Período fim:",    period_info.get("date_end", "—")),
        ("Gerado em:",      datetime.now().strftime("%d/%m/%Y %H:%M")),
    ]
    for i, (lbl, val) in enumerate(meta_rows, start=2):
        ca = ws1.cell(row=i, column=1, value=lbl)
        ca.font = bold11; ca.fill = meta_fill
        cb = ws1.cell(row=i, column=2, value=val)
        cb.fill = meta_fill

    # KPI block
    kpi_row = 7
    ws1.cell(row=kpi_row, column=1, value="Tipo de evento").font = bold11
    ws1.cell(row=kpi_row, column=2, value="Quantidade").font = bold11
    for ci in (1, 2):
        ws1.cell(row=kpi_row, column=ci).fill = hdr_fill
        ws1.cell(row=kpi_row, column=ci).font = hdr_font
        ws1.cell(row=kpi_row, column=ci).border = thin

    kpi_data = [
        ("Total de eventos",         len(events),                         None),
        ("Jobs Criados",             type_counts["job_created"],           "C6EFCE"),
        ("Config. Alteradas",        type_counts["job_updated"],           "FFEB9C"),
        ("Jobs Excluídos",           type_counts["job_deleted"],           "FFC7CE"),
        ("Objetos Adicionados",      type_counts["objects_added"],         "DDEBF7"),
        ("Objetos Alterados",        type_counts["objects_changed"],       "FCE4D6"),
        ("Objetos Removidos",        type_counts["objects_deleted"],       "F2DCDB"),
        ("Operadores envolvidos",    len(op_counts),                       "EDE7F6"),
        ("Alterações sensíveis",     len(sensitive),                       "FFC7CE" if sensitive else None),
    ]
    for ri, (lbl, cnt, bg) in enumerate(kpi_data, start=kpi_row + 1):
        ca = ws1.cell(row=ri, column=1, value=lbl)
        cb = ws1.cell(row=ri, column=2, value=cnt)
        for c in (ca, cb):
            c.border = thin
            c.alignment = Alignment(horizontal="left", vertical="center")
            if bg:
                c.fill = PatternFill("solid", fgColor=bg)

    # Operador ranking
    op_hdr_row = kpi_row + len(kpi_data) + 2
    ws1.cell(row=op_hdr_row - 1, column=4, value="Ranking de Operadores").font = bold11
    ws1.cell(row=op_hdr_row,     column=4, value="Operador").font = hdr_font
    ws1.cell(row=op_hdr_row,     column=4).fill = hdr_fill
    ws1.cell(row=op_hdr_row,     column=4).border = thin
    ws1.cell(row=op_hdr_row,     column=5, value="Alterações").font = hdr_font
    ws1.cell(row=op_hdr_row,     column=5).fill = hdr_fill
    ws1.cell(row=op_hdr_row,     column=5).border = thin

    top_ops = sorted(op_counts.items(), key=lambda x: x[1], reverse=True)[:15]
    for ri, (op, cnt) in enumerate(top_ops, start=op_hdr_row + 1):
        ca = ws1.cell(row=ri, column=4, value=op)
        ca.border = thin
        cb = ws1.cell(row=ri, column=5, value=cnt)
        cb.border = thin
        bg = COLOR_ALT_ROW if ri % 2 == 0 else COLOR_NEUTRAL
        ca.fill = _cell_fill(bg); cb.fill = _cell_fill(bg)

    ws1.column_dimensions["A"].width = 32
    ws1.column_dimensions["B"].width = 18
    ws1.column_dimensions["D"].width = 40
    ws1.column_dimensions["E"].width = 14

    # ══════════════════════════════════════════════════════════════════
    # Sheet 2: Eventos de Auditoria
    # ══════════════════════════════════════════════════════════════════
    ws2 = wb.create_sheet("Eventos de Auditoria")

    cols2 = ["Data/Hora", "Tipo de Alteração", "Rotina de Backup",
             "Operador", "Servidor VBR", "Detalhes", "Impacto", "Event ID"]
    _hdr(ws2, 1, cols2)

    for ri, e in enumerate(events, start=2):
        impact  = _audit_classify_impact(e, failed_job_norms)
        etype   = e.audit_event_type or "—"
        type_bg = _AUDIT_TYPE_COLORS.get(etype, COLOR_NEUTRAL)
        imp_bg  = _IMPACT_COLORS.get(impact, COLOR_NEUTRAL)
        row_bg  = COLOR_ALT_ROW if ri % 2 == 0 else COLOR_NEUTRAL

        vals = [
            e.time_created.strftime("%d/%m/%Y %H:%M") if e.time_created else "—",
            _AUDIT_LABELS.get(etype, etype),
            e.job_name or "—",
            e.operator or "—",
            e.vbr_hostname or "—",
            (e.audit_details or "")[:300],
            impact,
            e.event_id or "—",
        ]
        bgs = [row_bg, type_bg, row_bg, row_bg, row_bg, row_bg, imp_bg, row_bg]
        for ci, (val, bg) in enumerate(zip(vals, bgs), start=1):
            _data_cell(ws2, ri, ci, val, bg=bg)

    _auto_width(ws2, max_w=50)
    ws2.freeze_panes = ws2["A2"]

    # ══════════════════════════════════════════════════════════════════
    # Sheet 3: Alterações Sensíveis
    # ══════════════════════════════════════════════════════════════════
    ws3 = wb.create_sheet("Alterações Sensíveis")

    ws3.merge_cells("A1:H1")
    t3 = ws3["A1"]
    t3.value = (
        "Alterações Sensíveis: exclusões de jobs/objetos, operações fora do horário comercial "
        "(antes das 7h ou após 19h) e jobs com falhas de backup no período."
    )
    t3.font      = Font(italic=True, size=9, color="666666")
    t3.alignment = Alignment(wrap_text=True)
    ws3.row_dimensions[1].height = 36

    cols3 = ["Data/Hora", "Tipo de Alteração", "Rotina de Backup",
             "Operador", "Servidor VBR", "Detalhes", "Impacto", "Motivo"]
    _hdr(ws3, 2, cols3)

    for ri, e in enumerate(sensitive, start=3):
        impact  = _audit_classify_impact(e, failed_job_norms)
        etype   = e.audit_event_type or "—"
        type_bg = _AUDIT_TYPE_COLORS.get(etype, COLOR_NEUTRAL)
        imp_bg  = _IMPACT_COLORS.get(impact, COLOR_NEUTRAL)
        row_bg  = COLOR_ALT_ROW if ri % 2 == 0 else COLOR_NEUTRAL

        # Reason flags
        reasons = []
        if etype in ("job_deleted", "objects_deleted"):
            reasons.append("Exclusão")
        if e.time_created and (e.time_created.hour < 7 or e.time_created.hour >= 19):
            reasons.append("Fora do horário")
        if (e.job_name_normalized or "") in failed_job_norms:
            reasons.append("Job com falha de backup")

        vals = [
            e.time_created.strftime("%d/%m/%Y %H:%M") if e.time_created else "—",
            _AUDIT_LABELS.get(etype, etype),
            e.job_name or "—",
            e.operator or "—",
            e.vbr_hostname or "—",
            (e.audit_details or "")[:300],
            impact,
            ", ".join(reasons) or "—",
        ]
        bgs = [row_bg, type_bg, row_bg, row_bg, row_bg, row_bg, imp_bg, "FFF3CD"]
        for ci, (val, bg) in enumerate(zip(vals, bgs), start=1):
            _data_cell(ws3, ri, ci, val, bg=bg)

    _auto_width(ws3, max_w=50)
    ws3.freeze_panes = ws3["A3"]

    wb.save(output_path)
