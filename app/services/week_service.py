"""Formatação de datas e períodos para relatórios."""
from datetime import datetime


def format_week_label(date_start: datetime, date_end: datetime) -> str:
    """Formata o intervalo do relatório como string legível."""
    return f"{date_start.strftime('%d/%m/%Y')} — {date_end.strftime('%d/%m/%Y')}"


def format_period(date_start: datetime, date_end: datetime) -> str:
    """Alias para format_week_label."""
    return format_week_label(date_start, date_end)
