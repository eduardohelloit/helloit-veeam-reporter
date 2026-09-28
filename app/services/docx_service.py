"""
docx_service.py — Relatório de Cliente (rotina) em Word (.docx).

Dossiê mensal de UMA rotina de backup (= um cliente final do provedor), no padrão
visual do relatório mensal (capa com logo, controle de versão, sumário, barras de
seção na cor da marca, gráficos, rodapé com página e marcação TLP em todas as páginas).

REGRA DE NEGÓCIO: nomes de infraestrutura interna do provedor (proxies, extents)
NUNCA aparecem — o payload já vem higienizado (main._client_report_payload).

O serviço é "burro": recebe strings prontas e monta o documento.
"""
from __future__ import annotations

import io
from pathlib import Path
from typing import Optional

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor, Twips

# ── TLP (Traffic Light Protocol) ───────────────────────────────────────────────
TLP_COLORS = {
    "RED":   RGBColor(0xFF, 0x2B, 0x2B),
    "AMBER": RGBColor(0xFF, 0xC0, 0x00),
    "GREEN": RGBColor(0x33, 0xA0, 0x2C),
    "CLEAR": RGBColor(0x70, 0x70, 0x70),
}

# Página A4 EXPLÍCITA (o default do python-docx é Letter 8,5" — em Word
# configurado para A4 as tabelas estouravam a margem direita).
# Geometria em TWIPS (1/1440") — a MESMA unidade que o Word usa no XML,
# para que tabelas, grade de colunas e margens batam exatamente.
A4_W_TW, A4_H_TW = 11906, 16838            # A4 (210 × 297 mm) em twips
MARGIN_TW        = 1152                     # 0,8" de margem esq./dir.
TABLE_TWIPS      = A4_W_TW - 2 * MARGIN_TW  # 9602 — largura útil exata
TABLE_W          = TABLE_TWIPS / 1440       # idem em polegadas (p/ imagens)
CELL_MAR_TW      = 108                      # margem interna padrão da célula
# QUIRK do Word: a borda esquerda da tabela é desenhada em
# (tblInd − margem interna esquerda da célula). Com tblInd=0 a tabela invade
# a margem em 108 twips e desalinha das barras de seção — por isso o recuo
# da tabela precisa ser IGUAL à margem interna da célula (medido em render:
# barras e tabelas devem alinhar no mesmo x).
TABLE_IND_TW     = CELL_MAR_TW

C_SUCCESS = "#2E9E5B"
C_WARNING = "#F0AD4E"
C_FAILED  = "#D9534F"


# ═══════════════════════════════════ helpers ═══════════════════════════════════

def _hex_to_rgb(hex_str: Optional[str]) -> RGBColor:
    try:
        h = (hex_str or "").lstrip("#")
        return RGBColor(int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    except Exception:
        return RGBColor(0x5B, 0x21, 0xB6)


def _rgb_hex(color: RGBColor) -> str:
    return f"{color:06X}" if not isinstance(color, str) else color


def _shade(cell, hex_fill: str) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:fill"), hex_fill)
    tc_pr.append(shd)


def _cell_text(cell, text, *, bold=False, size=9, color: Optional[RGBColor] = None,
               align=None) -> None:
    cell.text = ""
    p = cell.paragraphs[0]
    if align is not None:
        p.alignment = align
    run = p.add_run("" if text is None else str(text))
    run.font.size = Pt(size)
    run.font.bold = bold
    if color is not None:
        run.font.color.rgb = color


def _setup_page(doc) -> None:
    """Papel A4 explícito + margens — geometria idêntica em qualquer Word."""
    for section in doc.sections:
        section.page_width    = Twips(A4_W_TW)
        section.page_height   = Twips(A4_H_TW)
        section.top_margin    = Inches(0.8)
        section.bottom_margin = Inches(0.7)
        section.left_margin   = Twips(MARGIN_TW)
        section.right_margin  = Twips(MARGIN_TW)


def _fix_table(table, widths) -> None:
    """
    Geometria COMPLETA da tabela em twips: layout fixo + tblW + indent 0 +
    grade de colunas (tblGrid) + largura por célula (tcW), ciente de células
    MESCLADAS (gridSpan). As larguras informadas são tratadas como PROPORÇÕES
    e normalizadas para a largura útil da página (TABLE_TWIPS) com soma
    exata — por construção, toda tabela alinha nas duas margens.
    """
    total = float(sum(widths))
    cols = [max(1, int(w * TABLE_TWIPS / total)) for w in widths]
    cols[-1] += TABLE_TWIPS - sum(cols)          # soma EXATA = largura útil

    table.autofit = False
    table.alignment = WD_TABLE_ALIGNMENT.LEFT
    tbl = table._tbl
    tbl_pr = tbl.tblPr

    def _tbl_prop(tag, successors, **attrs):
        """Cria/atualiza um filho de tblPr respeitando a ordem do schema."""
        el = tbl_pr.find(qn(tag))
        if el is None:
            el = OxmlElement(tag)
            tbl_pr.insert_element_before(el, *successors)
        for k, v in attrs.items():
            el.set(qn(k), v)

    _tbl_prop("w:tblW",
              ("w:jc", "w:tblCellSpacing", "w:tblInd", "w:tblBorders",
               "w:shd", "w:tblLayout", "w:tblCellMar", "w:tblLook"),
              **{"w:w": str(TABLE_TWIPS), "w:type": "dxa"})
    _tbl_prop("w:tblInd",
              ("w:tblBorders", "w:shd", "w:tblLayout", "w:tblCellMar",
               "w:tblLook"),
              **{"w:w": str(TABLE_IND_TW), "w:type": "dxa"})
    _tbl_prop("w:tblLayout", ("w:tblCellMar", "w:tblLook"),
              **{"w:type": "fixed"})

    # Margens internas da célula EXPLÍCITAS (a compensação do tblInd acima
    # depende delas — não podem ficar à mercê do estilo).
    mar = tbl_pr.find(qn("w:tblCellMar"))
    if mar is None:
        mar = OxmlElement("w:tblCellMar")
        tbl_pr.insert_element_before(mar, "w:tblLook")
    for side, val in (("w:top", 0), ("w:left", CELL_MAR_TW),
                      ("w:bottom", 0), ("w:right", CELL_MAR_TW)):
        el = mar.find(qn(side))
        if el is None:
            el = OxmlElement(side)
            mar.append(el)
        el.set(qn("w:w"), str(val))
        el.set(qn("w:type"), "dxa")

    # Grade de colunas reconstruída com as larguras exatas (o python-docx
    # deixa a grade da criação, que não bate com as larguras desejadas).
    grid = tbl.find(qn("w:tblGrid"))
    if grid is None:
        grid = OxmlElement("w:tblGrid")
        tbl_pr.addnext(grid)
    else:
        for gc in list(grid):
            grid.remove(gc)
    for c in cols:
        gc = OxmlElement("w:gridCol")
        gc.set(qn("w:w"), str(c))
        grid.append(gc)

    # Largura célula a célula — célula mesclada recebe a SOMA das colunas
    # que ela atravessa (não a largura de uma coluna só).
    for row in table.rows:
        idx = 0
        for tc in row._tr.tc_lst:
            span = tc.grid_span
            width = sum(cols[idx:idx + span]) or TABLE_TWIPS
            tc_pr = tc.get_or_add_tcPr()
            tcw = tc_pr.find(qn("w:tcW"))
            if tcw is None:
                tcw = OxmlElement("w:tcW")
                tc_pr.insert(0, tcw)
            tcw.set(qn("w:w"), str(width))
            tcw.set(qn("w:type"), "dxa")
            idx += span


def _header_row(table, labels, fill="1D3A5F") -> None:
    for i, lbl in enumerate(labels):
        _cell_text(table.rows[0].cells[i], lbl, bold=True, size=9,
                   color=RGBColor(0xFF, 0xFF, 0xFF), align=WD_ALIGN_PARAGRAPH.CENTER)
        _shade(table.rows[0].cells[i], fill)


def _tlp_run(paragraph, tlp: str, size=11) -> None:
    run = paragraph.add_run(f"TLP:{tlp}")
    run.font.bold = True
    run.font.size = Pt(size)
    run.font.color.rgb = TLP_COLORS.get(tlp, TLP_COLORS["CLEAR"])


def _kv_table(doc, items):
    t = doc.add_table(rows=0, cols=2)
    t.style = "Table Grid"
    for label, value in items:
        row = t.add_row()
        _cell_text(row.cells[0], label, bold=True, size=9)
        _shade(row.cells[0], "EEF2F7")
        _cell_text(row.cells[1], value, size=9)
    _fix_table(t, [2.4, TABLE_W - 2.4])
    return t


def _section_bar(doc, text: str, fill_hex: str):
    """
    Título de seção com barra colorida (estilo do relatório mensal em PDF).
    Usa estilo Heading 1 (para o Sumário/TOC enxergar) + shading + texto branco.
    """
    p = doc.add_paragraph(style="Heading 1")
    p_pr = p._p.get_or_add_pPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:fill"), fill_hex)
    p_pr.append(shd)
    run = p.add_run("  " + text)
    run.font.bold = True
    run.font.size = Pt(11)
    run.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
    p.paragraph_format.space_before = Pt(14)
    p.paragraph_format.space_after = Pt(8)
    return p


def _add_toc(doc) -> None:
    """Campo TOC do Word (o leitor atualiza com F9 / 'Atualizar campo')."""
    p = doc.add_paragraph()
    fld = OxmlElement("w:fldSimple")
    fld.set(qn("w:instr"), 'TOC \\o "1-2" \\h \\z \\u')
    r = OxmlElement("w:r")
    t = OxmlElement("w:t")
    t.text = ("Sumário — clique com o botão direito → 'Atualizar campo' (ou F9) "
              "para preencher com as seções e páginas.")
    r.append(t)
    fld.append(r)
    p._p.append(fld)


def _vcenter(cell) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    v = OxmlElement("w:vAlign")
    v.set(qn("w:val"), "center")
    tc_pr.append(v)


def _framed_box(doc, title: str, fill_hex: str, cols: int = 1):
    """Caixa com moldura (Table Grid): linha de título sombreada + linha de conteúdo."""
    t = doc.add_table(rows=2, cols=cols)
    t.style = "Table Grid"
    if cols > 1:
        t.rows[0].cells[0].merge(t.rows[0].cells[cols - 1])
    _cell_text(t.rows[0].cells[0], title, bold=True, size=9.5,
               color=RGBColor(0xFF, 0xFF, 0xFF), align=WD_ALIGN_PARAGRAPH.CENTER)
    _shade(t.rows[0].cells[0], fill_hex)
    return t


def _job_subtitle(doc, job_name: str, brand_color: RGBColor) -> None:
    """Sub-título 'Rotina: X' para separar blocos por rotina (multi-job)."""
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(8); p.paragraph_format.space_after = Pt(2)
    r = p.add_run(f"Rotina: {job_name}")
    r.font.size = Pt(9.5); r.font.bold = True; r.font.color.rgb = brand_color


def _render_configs(doc, payload, brand_color: RGBColor, *, source_prefix: str) -> None:
    """
    Seção de configuração vigente: se houver >1 rotina, um bloco por rotina
    (com sub-título); se 1, idêntico ao layout single-job. `configs` = lista
    de {job_name, items, collected_at}.
    """
    configs = payload.get("configs")
    if not configs:                       # fallback compat: config única
        cfg = payload.get("config")
        if cfg:
            _kv_table(doc, cfg)
            note = doc.add_paragraph()
            r = note.add_run(source_prefix
                             + (payload.get("config_collected_at") or "—") + ".")
            r.font.size = Pt(8); r.italic = True; r.font.color.rgb = RGBColor(0x88, 0x88, 0x88)
        else:
            p = doc.add_paragraph()
            r = p.add_run("Configuração não disponível — nenhuma coleta importada.")
            r.font.size = Pt(9); r.italic = True
        return

    multi = len(configs) > 1
    for c in configs:
        if multi:
            _job_subtitle(doc, c["job_name"], brand_color)
        _kv_table(doc, c["items"])
        note = doc.add_paragraph()
        r = note.add_run(source_prefix + (c.get("collected_at") or "—") + ".")
        r.font.size = Pt(8); r.italic = True; r.font.color.rgb = RGBColor(0x88, 0x88, 0x88)


def _stat_line(cell, label: str, value: str, *, color: Optional[RGBColor] = None,
               first: bool = False) -> None:
    p = cell.paragraphs[0] if first and not cell.paragraphs[0].runs else cell.add_paragraph()
    r1 = p.add_run(f"{label}:  ")
    r1.font.size = Pt(9); r1.font.color.rgb = RGBColor(0x66, 0x66, 0x66)
    r2 = p.add_run(str(value))
    r2.font.size = Pt(10.5); r2.font.bold = True
    if color is not None:
        r2.font.color.rgb = color


def _render_drift_table(doc, payload, brand_hex: str) -> None:
    """Fallback da seção de alterações: config-drift entre coletas da Config Audit."""
    intro = doc.add_paragraph()
    r = intro.add_run(
        "Alterações na configuração da rotina identificadas pela auditoria de "
        "configuração (comparação entre coletas) dentro do período."
    )
    r.font.size = Pt(8.5); r.italic = True; r.font.color.rgb = RGBColor(0x66, 0x66, 0x66)
    changes = payload.get("config_changes") or []
    cols = ["Data", "Configuração", "De", "Para"]
    tc = doc.add_table(rows=1, cols=len(cols))
    tc.style = "Table Grid"
    _header_row(tc, cols, fill=brand_hex)
    for ch in changes:
        row = tc.add_row()
        _cell_text(row.cells[0], ch.get("date"), size=8.5,
                   align=WD_ALIGN_PARAGRAPH.CENTER)
        _cell_text(row.cells[1], ch.get("field"), size=8.5, bold=True)
        _cell_text(row.cells[2], ch.get("from"), size=8.5,
                   color=RGBColor(0xC0, 0x39, 0x2B))
        _cell_text(row.cells[3], ch.get("to"), size=8.5,
                   color=RGBColor(0x2E, 0x7D, 0x32))
    _fix_table(tc, [0.95, 1.65, 2.15, 2.15])
    nt = doc.add_paragraph()
    r = nt.add_run(f"Total: {len(changes)} alteração(ões) identificada(s) no período.")
    r.font.size = Pt(8.5); r.font.bold = True


def _cell_lines(cell, lines, size=8.5) -> None:
    """Escreve várias linhas dentro de uma célula (um parágrafo por linha)."""
    cell.text = ""
    first = True
    for ln in lines:
        p = cell.paragraphs[0] if first else cell.add_paragraph()
        first = False
        r = p.add_run(str(ln))
        r.font.size = Pt(size)


def _caption(doc, text: str) -> None:
    cap = doc.add_paragraph(); cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = cap.add_run(text)
    r.font.size = Pt(8); r.italic = True; r.font.bold = True
    r.font.color.rgb = RGBColor(0x2E, 0x7D, 0x32)


def _add_page_field(paragraph, size=7.5, color="888888") -> None:
    """Campo PAGE com fonte própria (sem rPr ele herda 11pt e fica gigante)."""
    fld = OxmlElement("w:fldSimple")
    fld.set(qn("w:instr"), "PAGE")
    r = OxmlElement("w:r")
    rpr = OxmlElement("w:rPr")
    sz = OxmlElement("w:sz"); sz.set(qn("w:val"), str(int(size * 2)))   # meia-pts
    rpr.append(sz)
    col = OxmlElement("w:color"); col.set(qn("w:val"), color); rpr.append(col)
    r.append(rpr)
    t = OxmlElement("w:t")
    t.text = "1"
    r.append(t)
    fld.append(r)
    paragraph._p.append(fld)


def _setup_footer(footer_p, tlp: str, center_text: str) -> None:
    """
    Rodapé em 3 partes via tabulações: conteúdo (TLP + doc_ref + provedor)
    CENTRALIZADO na largura útil e a página cravada no CANTO direito, com a
    mesma fonte 7,5pt cinza do resto.
    """
    footer_p.alignment = WD_ALIGN_PARAGRAPH.LEFT
    tabs = footer_p.paragraph_format.tab_stops
    tabs.add_tab_stop(Twips(TABLE_TWIPS // 2), WD_TAB_ALIGNMENT.CENTER)
    tabs.add_tab_stop(Twips(TABLE_TWIPS), WD_TAB_ALIGNMENT.RIGHT)

    footer_p.add_run("\t")                       # → tab de centro
    _tlp_run(footer_p, tlp, size=8)
    cr = footer_p.add_run(center_text)
    cr.font.size = Pt(7.5); cr.font.color.rgb = RGBColor(0x88, 0x88, 0x88)

    footer_p.add_run("\t")                       # → tab de direita (canto)
    pr = footer_p.add_run("pág. ")
    pr.font.size = Pt(7.5); pr.font.color.rgb = RGBColor(0x88, 0x88, 0x88)
    _add_page_field(footer_p)


# ═══════════════════════════════════ gráficos ══════════════════════════════════

def _chart_donut(success: int, warning: int, failed: int) -> Optional[io.BytesIO]:
    total = success + warning + failed
    if total == 0:
        return None
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    vals, colors, labels = [], [], []
    for v, c, l in ((success, C_SUCCESS, f"Sucesso ({success})"),
                    (warning, C_WARNING, f"Aviso ({warning})"),
                    (failed,  C_FAILED,  f"Falha ({failed})")):
        if v > 0:
            vals.append(v); colors.append(c); labels.append(l)

    fig, ax = plt.subplots(figsize=(3.4, 3.0), dpi=160)
    ax.pie(vals, colors=colors, startangle=90, counterclock=False,
           wedgeprops={"width": 0.34, "edgecolor": "white"})
    pct = success / total * 100
    ax.text(0, 0.06, f"{pct:.0f}%", ha="center", va="center",
            fontsize=22, fontweight="bold", color=C_SUCCESS)
    ax.text(0, -0.22, "sucesso", ha="center", va="center", fontsize=9, color="#666666")
    ax.legend(labels, loc="lower center", bbox_to_anchor=(0.5, -0.18),
              ncol=len(labels), frameon=False, fontsize=7.5)
    ax.set_aspect("equal")
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", transparent=True)
    plt.close(fig)
    buf.seek(0)
    return buf


def _chart_daily(daily: list) -> Optional[io.BytesIO]:
    """Barras: execuções por dia, com as falhas destacadas em vermelho."""
    if not daily:
        return None
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    days  = [d["label"] for d in daily]
    execs = [d["execs"] for d in daily]
    fails = [d["fails"] for d in daily]

    fig, ax = plt.subplots(figsize=(9.4, 2.6), dpi=160)
    ax.bar(days, execs, color=C_SUCCESS, width=0.72, label="Execuções")
    ax.bar(days, fails, color=C_FAILED, width=0.72, label="Com falha")
    ax.spines[["top", "right"]].set_visible(False)
    ax.tick_params(axis="x", labelsize=6.5, rotation=0)
    ax.tick_params(axis="y", labelsize=7)
    ax.legend(loc="upper right", frameon=False, fontsize=7.5)
    ax.margins(x=0.01)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", transparent=True)
    plt.close(fig)
    buf.seek(0)
    return buf


# ═══════════════════════════════════ documento ═════════════════════════════════

def generate_client_report_docx(payload: dict, branding: Optional[dict] = None,
                                branding_dir: Optional[str] = None) -> bytes:
    tlp = (payload.get("tlp") or "AMBER").upper()
    if tlp not in TLP_COLORS:
        tlp = "AMBER"
    brand_color = _hex_to_rgb((branding or {}).get("primary_color"))
    brand_hex   = f"{brand_color}"  # RGBColor.__str__ = hex sem '#'
    provider    = (branding or {}).get("company_name") or ""

    doc = Document()
    _setup_page(doc)

    # ── Cabeçalho/rodapé (TLP em todas as páginas + nº de página) ─────────────
    header_p = doc.sections[0].header.paragraphs[0]
    header_p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    _tlp_run(header_p, tlp, size=9)
    hr = header_p.add_run(f"   |   {payload.get('report_name','')}")
    hr.font.size = Pt(8); hr.font.color.rgb = RGBColor(0x88, 0x88, 0x88)

    footer_p = doc.sections[0].footer.paragraphs[0]
    _setup_footer(footer_p, tlp,
                  f"   •   {payload.get('doc_ref','')}"
                  + (f"   •   {provider}" if provider else ""))

    # ── CAPA ──────────────────────────────────────────────────────────────────
    p = doc.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    _tlp_run(p, tlp, size=14)

    doc.add_paragraph(); doc.add_paragraph()

    # logo do branding (se existir arquivo)
    logo_rel = (branding or {}).get("logo_main_path")
    if logo_rel and branding_dir:
        logo_path = Path(branding_dir) / logo_rel
        if logo_path.exists():
            lp = doc.add_paragraph(); lp.alignment = WD_ALIGN_PARAGRAPH.CENTER
            try:
                lp.add_run().add_picture(str(logo_path), width=Inches(2.3))
            except Exception:
                pass

    doc.add_paragraph(); doc.add_paragraph()

    t = doc.add_paragraph(); t.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = t.add_run(payload.get("report_name", ""))
    r.font.size = Pt(30); r.font.bold = True

    st = doc.add_paragraph(); st.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = st.add_run(f"RELATÓRIO MENSAL DE BACKUP — {payload.get('period_label','').upper()}")
    r.font.size = Pt(13); r.font.bold = True; r.font.color.rgb = brand_color
    st2 = doc.add_paragraph(); st2.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = st2.add_run("AMBIENTE DE BACKUP VEEAM BACKUP & REPLICATION")
    r.font.size = Pt(10.5); r.font.color.rgb = brand_color

    doc.add_paragraph()
    meta = doc.add_paragraph(); meta.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _jobs = payload.get("job_names") or [payload.get("job_name", "")]
    _jobs_label = ("Rotinas de backup" if len(_jobs) > 1 else "Rotina de backup")
    r = meta.add_run(
        f"Período: {payload.get('period','')}\n"
        f"{_jobs_label}: {'; '.join(_jobs)}\n"
        f"Documento: {payload.get('doc_ref','')}"
    )
    r.font.size = Pt(10.5); r.font.color.rgb = RGBColor(0x55, 0x55, 0x55)

    doc.add_page_break()

    # ── SUMÁRIO + CONTROLE DE VERSÃO ─────────────────────────────────────────
    _section_bar(doc, "SUMÁRIO", brand_hex)
    _add_toc(doc)

    vc = doc.add_paragraph()
    vc.paragraph_format.space_before = Pt(10)
    vc.paragraph_format.space_after = Pt(4)
    r = vc.add_run("CONTROLE DE VERSÃO DO RELATÓRIO TÉCNICO")
    r.font.size = Pt(9); r.font.bold = True; r.font.color.rgb = RGBColor(0x44, 0x44, 0x44)
    tv = doc.add_table(rows=1, cols=4)
    tv.style = "Table Grid"
    _header_row(tv, ["Data", "Versão", "Descrição", "Autor"], fill="E8E8E8")
    for c in tv.rows[0].cells:                       # header cinza com texto escuro
        c.paragraphs[0].runs[0].font.color.rgb = RGBColor(0x33, 0x33, 0x33)
    row = tv.add_row()
    _cell_text(row.cells[0], payload.get("generated_date", ""), size=9,
               align=WD_ALIGN_PARAGRAPH.CENTER)
    _cell_text(row.cells[1], "1.0", size=9, align=WD_ALIGN_PARAGRAPH.CENTER)
    _cell_text(row.cells[2], "Elaboração do Documento", size=9,
               align=WD_ALIGN_PARAGRAPH.CENTER)
    _cell_text(row.cells[3], payload.get("author", "—"), size=9,
               align=WD_ALIGN_PARAGRAPH.CENTER)
    _fix_table(tv, [1.2, 0.9, 2.9, 1.9])

    doc.add_page_break()

    # ── 1. SUMÁRIO EXECUTIVO ──────────────────────────────────────────────────
    _section_bar(doc, "1. SUMÁRIO EXECUTIVO", brand_hex)
    k = payload.get("kpis", {})
    _kv_table(doc, [
        ("VMs protegidas",             k.get("protected_vms", "—")),
        ("Execuções da rotina no mês", k.get("executions", "—")),
        ("Execuções com sucesso",      k.get("success", "—")),
        ("Execuções com aviso",        k.get("warning", "—")),
        ("Execuções com falha",        k.get("failed", "—")),
        ("Taxa de sucesso",            k.get("success_rate", "—")),
        ("Duração média da execução",  k.get("avg_duration", "—")),
        ("Maior duração no período",   k.get("max_duration", "—")),
        ("Dados processados (média por execução)", k.get("avg_processed", "—")),
    ])

    # ── Gráficos EMOLDURADOS: donut + painel de estatísticas | execuções/dia ──
    succ = int(k.get("success", 0) or 0)
    warn = int(k.get("warning", 0) or 0)
    fail = int(k.get("failed", 0) or 0)
    total = succ + warn + fail

    donut = _chart_donut(succ, warn, fail)
    if donut:
        doc.add_paragraph().paragraph_format.space_after = Pt(2)
        box = _framed_box(doc, f"DISTRIBUIÇÃO DAS EXECUÇÕES — {payload.get('period_label','')}",
                          brand_hex, cols=2)
        left, right = box.rows[1].cells
        lp = left.paragraphs[0]; lp.alignment = WD_ALIGN_PARAGRAPH.CENTER
        lp.add_run().add_picture(donut, width=Inches(TABLE_W / 2 - 0.55))
        _vcenter(left); _vcenter(right)
        fail_pct = f"{fail/total*100:.1f}%" if total else "—"
        _stat_line(right, "Total de execuções", str(total), first=True)
        _stat_line(right, "Com sucesso", f"{succ}  ({k.get('success_rate','—')})",
                   color=RGBColor(0x2E, 0x7D, 0x32))
        _stat_line(right, "Com aviso", str(warn), color=RGBColor(0xB8, 0x86, 0x0B))
        _stat_line(right, "Com falha", f"{fail}  ({fail_pct})",
                   color=RGBColor(0xC0, 0x39, 0x2B))
        _stat_line(right, "Duração média", k.get("avg_duration", "—"))
        _stat_line(right, "Maior duração", k.get("max_duration", "—"))
        wd = payload.get("worst_day")
        if wd:
            _stat_line(right, "Dia mais crítico",
                       f"{wd['label']}  ({wd['fails']} falha(s))",
                       color=RGBColor(0xC0, 0x39, 0x2B))
        _fix_table(box, [3.45, 3.45])
        _caption(doc, "Legenda 1 — Distribuição das execuções da rotina no período")

    daily = _chart_daily(payload.get("daily") or [])
    if daily:
        doc.add_paragraph().paragraph_format.space_after = Pt(2)
        box2 = _framed_box(doc, f"EXECUÇÕES POR DIA — {payload.get('period_label','')}",
                           brand_hex, cols=1)
        cell = box2.rows[1].cells[0]
        cp = cell.paragraphs[0]; cp.alignment = WD_ALIGN_PARAGRAPH.CENTER
        cp.add_run().add_picture(daily, width=Inches(TABLE_W - 0.35))
        _fix_table(box2, [TABLE_W])
        _caption(doc, "Legenda 2 — Execuções por dia (falhas destacadas em vermelho)")

    # ── 2. CONFIGURAÇÃO DE PROTEÇÃO ──────────────────────────────────────────
    _section_bar(doc, "2. CONFIGURAÇÃO DE PROTEÇÃO", brand_hex)
    _render_configs(doc, payload, brand_color,
                    source_prefix="Fonte: auditoria de configuração coletada em ")

    # ── 3. ALTERAÇÕES NA ROTINA NO PERÍODO ───────────────────────────────────
    # Fonte primária: trilha de AUDITORIA do Veeam (quem/quando/o quê, de→para).
    # Fallback: config-drift entre coletas de auditoria de configuração.
    _section_bar(doc, "3. ALTERAÇÕES NA ROTINA NO PERÍODO", brand_hex)

    audit = payload.get("audit_changes") or []
    if audit:
        intro_a = doc.add_paragraph()
        r = intro_a.add_run(
            "Registro de alterações efetuadas na rotina de backup no período, "
            "conforme a trilha de auditoria do Veeam Backup & Replication."
        )
        r.font.size = Pt(8.5); r.italic = True; r.font.color.rgb = RGBColor(0x66, 0x66, 0x66)

        multi = bool(payload.get("multi_job"))
        if multi:
            cols   = ["Data/Hora", "Rotina", "Ação", "Alterações", "Operador"]
            widths = [1.0, 1.15, 1.1, 2.65, 1.1]
        else:
            cols   = ["Data/Hora", "Ação", "Alterações", "Operador"]
            widths = [1.05, 1.15, 3.55, 1.15]
        ta = doc.add_table(rows=1, cols=len(cols))
        ta.style = "Table Grid"
        _header_row(ta, cols, fill=brand_hex)
        for ch in audit:
            row = ta.add_row()
            i = 0
            _cell_text(row.cells[i], ch.get("dt"), size=8.5,
                       align=WD_ALIGN_PARAGRAPH.CENTER); i += 1
            if multi:
                _cell_text(row.cells[i], ch.get("job"), size=8,
                           align=WD_ALIGN_PARAGRAPH.CENTER); i += 1
            _cell_text(row.cells[i], ch.get("action"), size=8.5, bold=True,
                       align=WD_ALIGN_PARAGRAPH.CENTER); i += 1
            _cell_lines(row.cells[i], ch.get("lines") or ["—"], size=8.5); i += 1
            _cell_text(row.cells[i], ch.get("operator"), size=8.5,
                       align=WD_ALIGN_PARAGRAPH.CENTER)
        _fix_table(ta, widths)
        more = payload.get("audit_changes_more") or 0
        nt = doc.add_paragraph()
        r = nt.add_run(
            f"Total: {len(audit)} alteração(ões) registrada(s) no período."
            + (f" (+{more} alteração(ões) adicionais não exibidas)" if more else "")
        )
        r.font.size = Pt(8.5); r.font.bold = True
    elif payload.get("config_changes"):
        _render_drift_table(doc, payload, brand_hex)
    else:
        p = doc.add_paragraph()
        r = p.add_run("✔ Nenhuma alteração de configuração identificada no período.")
        r.font.size = Pt(10); r.font.bold = True
        r.font.color.rgb = RGBColor(0x2E, 0x7D, 0x32)

    # ── 4. DETALHE POR MÁQUINA VIRTUAL ───────────────────────────────────────
    _section_bar(doc, "4. DETALHE POR MÁQUINA VIRTUAL", brand_hex)
    intro = doc.add_paragraph()
    r = intro.add_run(
        "RPO observado = intervalo real entre backups bem-sucedidos de cada VM no período. "
        "“Maior intervalo” é a maior janela sem ponto de restauração novo."
    )
    r.font.size = Pt(8.5); r.italic = True; r.font.color.rgb = RGBColor(0x66, 0x66, 0x66)

    vms = payload.get("vms", [])
    if vms:
        cols = ["VM", "Execuções", "Sucesso", "Último backup OK",
                "RPO médio", "Maior intervalo", "Duração média", "Dados (média)"]
        tvm = doc.add_table(rows=1, cols=len(cols))
        tvm.style = "Table Grid"
        _header_row(tvm, cols, fill=brand_hex)
        for v in vms:
            row = tvm.add_row()
            vals = [v.get("vm"), v.get("executions"), v.get("successes"),
                    v.get("last_success"), v.get("rpo_avg"), v.get("rpo_max"),
                    v.get("avg_dur"), v.get("avg_gb")]
            for i, val in enumerate(vals):
                _cell_text(row.cells[i], val, size=8.5,
                           align=WD_ALIGN_PARAGRAPH.CENTER if i > 0 else None)
        _fix_table(tvm, [1.45, 0.60, 0.60, 1.05, 0.75, 0.85, 0.80, 0.80])
    else:
        doc.add_paragraph("Nenhuma execução por VM registrada no período.")

    # ── 4. FALHAS NO PERÍODO ──────────────────────────────────────────────────
    _section_bar(doc, "5. FALHAS NO PERÍODO", brand_hex)
    failures = payload.get("failures", [])
    if failures:
        cols = ["Data/Hora", "VMs afetadas", "Motivo"]
        tf = doc.add_table(rows=1, cols=len(cols))
        tf.style = "Table Grid"
        _header_row(tf, cols, fill="7A1F1F")
        for f in failures:
            row = tf.add_row()
            _cell_text(row.cells[0], f.get("date"), size=8.5)
            _cell_text(row.cells[1], f.get("vms"), size=8.5)
            _cell_text(row.cells[2], f.get("reason"), size=8.5)
        _fix_table(tf, [1.15, 1.75, TABLE_W - 1.15 - 1.75])
        row_note = doc.add_paragraph()
        r = row_note.add_run(f"Total: {len(failures)} execução(ões) com falha no período.")
        r.font.size = Pt(8.5); r.font.bold = True
    else:
        p = doc.add_paragraph()
        r = p.add_run("✔ Nenhuma falha de execução registrada no período.")
        r.font.size = Pt(10); r.font.bold = True
        r.font.color.rgb = RGBColor(0x2E, 0x7D, 0x32)

    # ── 5. OBSERVAÇÕES ────────────────────────────────────────────────────────
    _section_bar(doc, "6. OBSERVAÇÕES", brand_hex)
    obs = doc.add_paragraph()
    r = obs.add_run("(Espaço reservado para observações do time técnico antes do envio.)")
    r.font.size = Pt(9); r.italic = True; r.font.color.rgb = RGBColor(0x99, 0x99, 0x99)

    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    return buf.read()


# ═══════════════════════ Política de Backup (PSI) ══════════════════════════════

# Cores de status da matriz de conformidade (Anexo A)
_POLICY_STATUS = {
    "CONFORME":     ("2E7D32", RGBColor(0xFF, 0xFF, 0xFF)),
    "ATENÇÃO":      ("F0AD4E", RGBColor(0x33, 0x33, 0x33)),
    "NÃO CONFORME": ("C0392B", RGBColor(0xFF, 0xFF, 0xFF)),
    "N/D":          ("9E9E9E", RGBColor(0xFF, 0xFF, 0xFF)),
}


def _policy_par(doc, text: str, *, size=9.5, bold=False, italic=False,
                color: Optional[RGBColor] = None):
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    r = p.add_run(text)
    r.font.size = Pt(size); r.font.bold = bold; r.italic = italic
    if color is not None:
        r.font.color.rgb = color
    return p


def _policy_bullets(doc, items, *, size=9.5) -> None:
    for it in items:
        p = doc.add_paragraph(style="List Bullet")
        p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        r = p.add_run(it)
        r.font.size = Pt(size)


def generate_backup_policy_docx(payload: dict, branding: Optional[dict] = None,
                                branding_dir: Optional[str] = None) -> bytes:
    """
    Política de Backup e Recuperação de Dados (PSI) de UMA rotina (= um cliente
    final do provedor). Documento normativo + Anexo A com matriz de conformidade
    automática (configuração real × controles ISO 27001/27040, NIST CSF, CIS v8).

    REGRA DE NEGÓCIO: nomes de infraestrutura interna do provedor (proxies,
    extents) NUNCA aparecem — o payload já vem higienizado.
    """
    tlp = (payload.get("tlp") or "AMBER").upper()
    if tlp not in TLP_COLORS:
        tlp = "AMBER"
    brand_color = _hex_to_rgb((branding or {}).get("primary_color"))
    brand_hex   = f"{brand_color}"
    provider    = (branding or {}).get("company_name") or "o Provedor"
    client_name = payload.get("client_name", "")
    resp_client = payload.get("responsible_client") or "A designar pelo cliente"

    doc = Document()
    _setup_page(doc)

    # ── Cabeçalho/rodapé (TLP em todas as páginas + nº de página) ─────────────
    header_p = doc.sections[0].header.paragraphs[0]
    header_p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    _tlp_run(header_p, tlp, size=9)
    hr = header_p.add_run(f"   |   Política de Backup — {client_name}")
    hr.font.size = Pt(8); hr.font.color.rgb = RGBColor(0x88, 0x88, 0x88)

    footer_p = doc.sections[0].footer.paragraphs[0]
    _setup_footer(footer_p, tlp,
                  f"   •   {payload.get('doc_ref','')}"
                  + (f"   •   {provider}" if provider != "o Provedor" else ""))

    # ── CAPA ──────────────────────────────────────────────────────────────────
    p = doc.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    _tlp_run(p, tlp, size=14)
    doc.add_paragraph(); doc.add_paragraph()

    logo_rel = (branding or {}).get("logo_main_path")
    if logo_rel and branding_dir:
        logo_path = Path(branding_dir) / logo_rel
        if logo_path.exists():
            lp = doc.add_paragraph(); lp.alignment = WD_ALIGN_PARAGRAPH.CENTER
            try:
                lp.add_run().add_picture(str(logo_path), width=Inches(2.3))
            except Exception:
                pass

    doc.add_paragraph(); doc.add_paragraph()
    t = doc.add_paragraph(); t.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = t.add_run(client_name)
    r.font.size = Pt(30); r.font.bold = True

    st = doc.add_paragraph(); st.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = st.add_run("POLÍTICA DE BACKUP E RECUPERAÇÃO DE DADOS")
    r.font.size = Pt(14); r.font.bold = True; r.font.color.rgb = brand_color
    st2 = doc.add_paragraph(); st2.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = st2.add_run("POLÍTICA DE SEGURANÇA DA INFORMAÇÃO — PROTEÇÃO DE DADOS EM NUVEM")
    r.font.size = Pt(10.5); r.font.color.rgb = brand_color

    doc.add_paragraph()
    meta = doc.add_paragraph(); meta.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _pjobs = payload.get("job_names") or [payload.get("job_name", "")]
    _pjobs_label = ("Rotinas de backup" if len(_pjobs) > 1 else "Rotina de backup")
    r = meta.add_run(
        f"{_pjobs_label}: {'; '.join(_pjobs)}\n"
        f"Documento: {payload.get('doc_ref','')}   •   Versão {payload.get('doc_version','1.0')}\n"
        f"Emitido em: {payload.get('generated_date','')}"
    )
    r.font.size = Pt(10.5); r.font.color.rgb = RGBColor(0x55, 0x55, 0x55)

    doc.add_page_break()

    # ── SUMÁRIO + CONTROLE DE VERSÃO ─────────────────────────────────────────
    _section_bar(doc, "SUMÁRIO", brand_hex)
    _add_toc(doc)

    vc = doc.add_paragraph()
    vc.paragraph_format.space_before = Pt(10)
    vc.paragraph_format.space_after = Pt(4)
    r = vc.add_run("CONTROLE DE VERSÃO DO DOCUMENTO")
    r.font.size = Pt(9); r.font.bold = True; r.font.color.rgb = RGBColor(0x44, 0x44, 0x44)
    tv = doc.add_table(rows=1, cols=4)
    tv.style = "Table Grid"
    _header_row(tv, ["Data", "Versão", "Descrição", "Autor"], fill="E8E8E8")
    for c in tv.rows[0].cells:
        c.paragraphs[0].runs[0].font.color.rgb = RGBColor(0x33, 0x33, 0x33)
    row = tv.add_row()
    _cell_text(row.cells[0], payload.get("generated_date", ""), size=9,
               align=WD_ALIGN_PARAGRAPH.CENTER)
    _cell_text(row.cells[1], payload.get("doc_version", "1.0"), size=9,
               align=WD_ALIGN_PARAGRAPH.CENTER)
    _cell_text(row.cells[2], "Elaboração do Documento", size=9,
               align=WD_ALIGN_PARAGRAPH.CENTER)
    _cell_text(row.cells[3], payload.get("author", "—"), size=9,
               align=WD_ALIGN_PARAGRAPH.CENTER)
    _fix_table(tv, [1.2, 0.9, 2.9, 1.9])

    doc.add_page_break()

    # ── 1. OBJETIVO E ESCOPO ─────────────────────────────────────────────────
    _section_bar(doc, "1. OBJETIVO E ESCOPO", brand_hex)
    _policy_par(doc,
        f"Esta política estabelece as diretrizes, responsabilidades e parâmetros "
        f"para a proteção de dados (backup e recuperação) do ambiente de "
        f"{client_name}, hospedado e operado por {provider} em plataforma "
        f"Veeam Backup & Replication. O objetivo é assegurar a disponibilidade, "
        f"a integridade e a capacidade de recuperação das informações, em "
        f"alinhamento às boas práticas de segurança da informação (ISO/IEC 27001, "
        f"ISO/IEC 27040, NIST CSF 2.0 e CIS Controls v8).")
    scope = payload.get("scope") or {}
    _pjobs = payload.get("job_names") or [payload.get("job_name", "—")]
    items = [
        ("Cliente",              client_name),
        ("Rotina(s) de backup" if len(_pjobs) > 1 else "Rotina de backup",
                                 "; ".join(_pjobs)),
        ("Plataforma",           scope.get("platform") or "Veeam Backup & Replication"),
        ("Máquinas virtuais no escopo", str(scope.get("vm_count", "—"))),
    ]
    if scope.get("vms"):
        items.append(("Ativos protegidos", ", ".join(scope["vms"])))
    _kv_table(doc, items)
    _policy_par(doc,
        "Novas máquinas virtuais do cliente somente são consideradas protegidas "
        "após inclusão formal no escopo da rotina de backup.",
        size=8.5, italic=True, color=RGBColor(0x66, 0x66, 0x66))

    # ── 2. TERMOS E DEFINIÇÕES ───────────────────────────────────────────────
    _section_bar(doc, "2. TERMOS E DEFINIÇÕES", brand_hex)
    _kv_table(doc, [
        ("RPO (Recovery Point Objective)",
         "Perda máxima de dados tolerada, medida em tempo — determina a "
         "frequência mínima dos backups."),
        ("RTO (Recovery Time Objective)",
         "Tempo máximo tolerado para restabelecer o serviço/dado após um incidente."),
        ("Ponto de restauração",
         "Cópia íntegra dos dados em um instante no tempo, disponível para recuperação."),
        ("Retenção",
         "Período/quantidade de pontos de restauração mantidos antes do descarte."),
        ("GFS (Grandfather-Father-Son)",
         "Retenção estendida com pontos semanais, mensais e/ou anuais para "
         "recuperação de longo prazo."),
        ("Imutabilidade",
         "Proteção que impede alteração ou exclusão dos backups durante o período "
         "definido, inclusive por credenciais administrativas (defesa anti-ransomware)."),
        ("Regra 3-2-1",
         "Boa prática: 3 cópias dos dados, em 2 mídias distintas, sendo 1 fora do "
         "ambiente primário."),
    ])

    # ── 3. REFERÊNCIAS NORMATIVAS ────────────────────────────────────────────
    _section_bar(doc, "3. REFERÊNCIAS NORMATIVAS", brand_hex)
    tn = doc.add_table(rows=1, cols=2)
    tn.style = "Table Grid"
    _header_row(tn, ["Norma / Framework", "Aplicação nesta política"], fill=brand_hex)
    for ref, desc in [
        ("ISO/IEC 27001:2022 — Anexo A, controle A.8.13",
         "Backup da informação: cópias mantidas e testadas conforme política definida."),
        ("ISO/IEC 27002:2022",
         "Diretrizes de implementação do controle de backup (escopo, frequência, "
         "retenção, testes)."),
        ("ISO/IEC 27040",
         "Segurança de armazenamento: proteção dos dados de backup em repouso "
         "(imutabilidade, criptografia)."),
        ("ISO 22301 / NIST SP 800-34",
         "Continuidade de negócios: definição e monitoramento de RPO e RTO."),
        ("NIST CSF 2.0 — PR.DS-11",
         "Backups criados, protegidos, mantidos e testados."),
        ("CIS Controls v8 — Controle 11",
         "Recuperação de dados: processo, cópia isolada e testes de restauração."),
        ("LGPD (Lei nº 13.709/2018)",
         "Tratamento de dados pessoais eventualmente contidos nos backups."),
    ]:
        row = tn.add_row()
        _cell_text(row.cells[0], ref, size=8.5, bold=True)
        _cell_text(row.cells[1], desc, size=8.5)
    _fix_table(tn, [2.7, TABLE_W - 2.7])

    # ── 4. PAPÉIS E RESPONSABILIDADES ────────────────────────────────────────
    _section_bar(doc, "4. PAPÉIS E RESPONSABILIDADES", brand_hex)
    tr_ = doc.add_table(rows=1, cols=2)
    tr_.style = "Table Grid"
    _header_row(tr_, ["Atividade", "Responsável"], fill=brand_hex)
    for act, resp in [
        ("Execução e operação das rotinas de backup", provider),
        ("Monitoramento das execuções e tratamento de falhas", provider),
        ("Manutenção da infraestrutura de backup (repositórios, imutabilidade)", provider),
        ("Emissão do relatório mensal de backup", provider),
        ("Execução de restaurações solicitadas", provider),
        ("Testes periódicos de restauração", provider),
        ("Definição e revisão do escopo de proteção (VMs)", f"{client_name} ({resp_client})"),
        ("Solicitação formal de restaurações", f"{client_name} ({resp_client})"),
        ("Comunicação de mudanças relevantes no ambiente", f"{client_name} ({resp_client})"),
        ("Aprovação desta política e de suas revisões", f"{provider} e {client_name}"),
    ]:
        row = tr_.add_row()
        _cell_text(row.cells[0], act, size=8.5)
        _cell_text(row.cells[1], resp, size=8.5, bold=True)
    _fix_table(tr_, [4.1, TABLE_W - 4.1])

    # ── 5. DIRETRIZES DE PROTEÇÃO DE DADOS ───────────────────────────────────
    _section_bar(doc, "5. DIRETRIZES DE PROTEÇÃO DE DADOS", brand_hex)
    _policy_bullets(doc, [
        "Os backups devem ser executados de forma automática, conforme o "
        "agendamento definido, sem dependência de intervenção manual.",
        "Os pontos de restauração devem respeitar a retenção definida nesta "
        "política, incluindo retenção estendida (GFS) quando configurada.",
        "Os dados de backup devem ser protegidos contra alteração e exclusão "
        "indevidas (imutabilidade), como defesa contra ransomware.",
        "Deve ser mantida cópia dos dados em camada secundária de armazenamento, "
        "em aderência à regra 3-2-1.",
        "Falhas de execução devem ser tratadas pela operação do provedor e "
        "refletidas no relatório mensal entregue ao cliente.",
        "Alterações na configuração da rotina são registradas em trilha de "
        "auditoria e reportadas no relatório mensal.",
    ])
    _pjobs = payload.get("job_names") or []
    _policy_par(doc,
                ("Parâmetros vigentes por rotina de backup:" if len(_pjobs) > 1
                 else "Parâmetros vigentes da rotina de backup:"),
                bold=True, size=9.5)
    _render_configs(doc, payload, brand_color,
                    source_prefix="Fonte: auditoria de configuração coletada em ")
    _policy_par(doc,
        "Os parâmetros acima constituem o padrão vigente; alterações devem ser "
        "acordadas entre as partes.", size=8, italic=True,
        color=RGBColor(0x88, 0x88, 0x88))

    # ── 6. OBJETIVOS DE RECUPERAÇÃO ──────────────────────────────────────────
    _section_bar(doc, "6. OBJETIVOS DE RECUPERAÇÃO (RPO / RTO)", brand_hex)
    rpo_target = payload.get("rpo_target")
    rpo_by_job = payload.get("rpo_by_job") or []
    if rpo_target or len(rpo_by_job) <= 1:
        # RPO único (rotina única, ou várias com o mesmo agendamento)
        _kv_table(doc, [
            ("RPO objetivo", rpo_target
                             or (rpo_by_job[0]["value"] if rpo_by_job else "—")),
            ("RTO acordado", payload.get("rto") or "Conforme contrato"),
        ])
    else:
        # RPO difere entre rotinas → uma linha por rotina + RTO
        _kv_table(doc,
            [(f"RPO objetivo — {r['job_name']}", r["value"]) for r in rpo_by_job]
            + [("RTO acordado", payload.get("rto") or "Conforme contrato")])
    _policy_par(doc,
        "O RPO objetivo decorre da frequência de execução da rotina de backup. "
        "O RPO efetivamente observado é apurado mensalmente por máquina virtual "
        "no relatório mensal de backup. O RTO depende do volume de dados, do tipo "
        "de restauração e da infraestrutura de destino.",
        size=8.5, italic=True, color=RGBColor(0x66, 0x66, 0x66))

    # ── 7. TESTES DE RESTAURAÇÃO ─────────────────────────────────────────────
    _section_bar(doc, "7. TESTES DE RESTAURAÇÃO", brand_hex)
    _policy_par(doc,
        f"Testes de restauração devem ser realizados com periodicidade "
        f"{(payload.get('restore_test_freq') or 'Trimestral').lower()}, "
        f"contemplando ao menos uma máquina virtual ou conjunto de arquivos do "
        f"escopo protegido, com registro do resultado. Testes adicionais podem "
        f"ser realizados sob demanda mediante solicitação de {client_name}.")
    _policy_par(doc,
        "Referências: ISO/IEC 27001 A.8.13 (backups testados), NIST CSF PR.DS-11, "
        "CIS v8 11.5.", size=8.5, italic=True, color=RGBColor(0x66, 0x66, 0x66))

    # ── 8. MONITORAMENTO E RELATÓRIOS ────────────────────────────────────────
    _section_bar(doc, "8. MONITORAMENTO E RELATÓRIOS", brand_hex)
    _policy_bullets(doc, [
        "As execuções da rotina são monitoradas continuamente pela operação do "
        "provedor, com tratamento de falhas e reexecuções quando aplicável.",
        "Mensalmente é emitido o Relatório de Backup do cliente, contendo taxa de "
        "sucesso, RPO observado por máquina virtual, falhas ocorridas e alterações "
        "efetuadas na rotina no período (trilha de auditoria).",
        "Divergências entre o configurado e o definido nesta política devem ser "
        "sinalizadas e tratadas entre as partes.",
    ])

    # ── 9. RETENÇÃO LEGAL E LGPD ─────────────────────────────────────────────
    _section_bar(doc, "9. RETENÇÃO LEGAL E PRIVACIDADE (LGPD)", brand_hex)
    legal = (payload.get("legal_retention") or "").strip()
    if legal:
        _policy_par(doc, legal)
    _policy_par(doc,
        "Os backups podem conter dados pessoais tratados pelo cliente. O provedor "
        "atua como operador desses dados, restringindo o acesso aos backups à "
        "equipe técnica responsável e utilizando-os exclusivamente para fins de "
        "proteção e recuperação. O descarte ocorre automaticamente ao fim do "
        "período de retenção definido nesta política. Requisitos adicionais de "
        "retenção legal devem ser formalizados pelo cliente.")

    # ── 10. REVISÃO DA POLÍTICA ──────────────────────────────────────────────
    _section_bar(doc, "10. REVISÃO DA POLÍTICA", brand_hex)
    _policy_par(doc,
        f"Esta política deve ser revisada com periodicidade "
        f"{(payload.get('review_cycle') or 'Anual').lower()}, ou antes disso "
        f"sempre que houver mudança relevante no ambiente protegido, no contrato "
        f"ou nos requisitos de negócio de {client_name}. As revisões devem ser "
        f"registradas no controle de versão deste documento.")

    doc.add_page_break()

    # ── ANEXO A — MATRIZ DE CONFORMIDADE ─────────────────────────────────────
    _section_bar(doc, "ANEXO A — MATRIZ DE CONFORMIDADE", brand_hex)
    _policy_par(doc,
        "Avaliação automática da configuração vigente frente aos controles de "
        "referência. Gerada a partir da auditoria de configuração coletada em "
        + (payload.get("config_collected_at") or "—") + ".",
        size=8.5, italic=True, color=RGBColor(0x66, 0x66, 0x66))

    # uma matriz por rotina (com sub-título quando >1); single-job idêntico.
    comp_by_job = payload.get("compliance_by_job")
    if not comp_by_job:
        comp_by_job = [{"job_name": (payload.get("job_names") or [""])[0],
                        "rows": payload.get("compliance") or []}]
    multi = len(comp_by_job) > 1

    def _render_matrix(rows):
        cols = ["Controle de referência", "Requisito", "Situação atual", "Status"]
        tc = doc.add_table(rows=1, cols=len(cols))
        tc.style = "Table Grid"
        _header_row(tc, cols, fill=brand_hex)
        for item in rows:
            row = tc.add_row()
            _cell_text(row.cells[0], item.get("ref"), size=8)
            _cell_text(row.cells[1], item.get("req"), size=8)
            _cell_text(row.cells[2], item.get("current"), size=8)
            status = item.get("status") or "N/D"
            fill, fg = _POLICY_STATUS.get(status, _POLICY_STATUS["N/D"])
            _cell_text(row.cells[3], status, size=8, bold=True, color=fg,
                       align=WD_ALIGN_PARAGRAPH.CENTER)
            _shade(row.cells[3], fill)
            _vcenter(row.cells[3])
        _fix_table(tc, [1.55, 1.95, 2.30, 1.10])
        n_ok = sum(1 for i in rows if i.get("status") == "CONFORME")
        nt = doc.add_paragraph()
        r = nt.add_run(f"Resultado: {n_ok} de {len(rows)} controles avaliados como "
                       f"CONFORME na configuração vigente.")
        r.font.size = Pt(8.5); r.font.bold = True

    if any(j.get("rows") for j in comp_by_job):
        for j in comp_by_job:
            if multi:
                _job_subtitle(doc, j["job_name"], brand_color)
            _render_matrix(j.get("rows") or [])
    else:
        _policy_par(doc, "Matriz indisponível — nenhuma coleta de configuração "
                         "importada para esta rotina.", italic=True, size=9)

    # ── ANEXO B — APROVAÇÃO ──────────────────────────────────────────────────
    _section_bar(doc, "ANEXO B — APROVAÇÃO", brand_hex)
    tap = doc.add_table(rows=1, cols=3)
    tap.style = "Table Grid"
    _header_row(tap, ["Papel", "Nome / Área", "Assinatura e data"], fill="E8E8E8")
    for c in tap.rows[0].cells:
        c.paragraphs[0].runs[0].font.color.rgb = RGBColor(0x33, 0x33, 0x33)
    for papel, nome in [
        (f"Elaborado por ({provider})", payload.get("author", "—")),
        (f"Aprovado por ({provider})", "—"),
        (f"Aprovado por ({client_name})", resp_client),
    ]:
        row = tap.add_row()
        _cell_text(row.cells[0], papel, size=9, bold=True)
        _cell_text(row.cells[1], nome, size=9)
        _cell_text(row.cells[2], "", size=9)
        row.height = Inches(0.45)
    _fix_table(tap, [2.4, 2.3, 2.2])

    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    return buf.read()
