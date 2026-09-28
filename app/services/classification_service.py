"""Classificação de status e badges CSS para jobs e relatórios."""


SITUATION_CSS = {
    "Superado":      "bg-success",
    "Em observação": "bg-warning text-dark",
    "Instável":      "bg-warning text-dark",
    "Ativo":         "bg-danger",
    "Indefinido":    "bg-secondary",
}

RESULT_LABEL = {
    0: "Sucesso",
    1: "Warning",
    2: "Falha",
    None: "—",
}

RESULT_CSS = {
    0: "success",
    1: "warning",
    2: "danger",
    None: "secondary",
}


def situation_badge_class(situation: str) -> str:
    """Retorna classe CSS Bootstrap para o badge de situação."""
    return SITUATION_CSS.get(situation, "bg-secondary")


def result_label(job_result) -> str:
    return RESULT_LABEL.get(job_result, "—")


def result_css(job_result) -> str:
    return RESULT_CSS.get(job_result, "secondary")


def auto_situation(failed_count: int, total_executions: int) -> str:
    """
    Sugestão automática de situação baseada na taxa de falhas.
    O usuário sempre pode sobrescrever manualmente.
    """
    if total_executions == 0:
        return "Indefinido"
    rate = failed_count / total_executions
    if rate == 0:
        return "Superado"
    if rate < 0.2:
        return "Em observação"
    if rate < 0.5:
        return "Instável"
    return "Ativo"
