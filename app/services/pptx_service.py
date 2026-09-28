"""
pptx_service.py — gera o Relatório Semanal no padrão visual HelloIT.

Fluxo:
  1. Carrega app/static/template.pptx (arquivo de referência)
  2. Mantém o slide 1 (capa) e o último slide (encerramento)
  3. Exclui todos os slides intermediários (2 … n-1)
  4. Insere 1 slide de conteúdo moderno na posição 2
  5. Retorna os bytes do PPTX resultante

Design do slide de conteúdo:
  ┌───────────── 10" ──────────────────────────────────────────────────── ┐
  │ ●● HelloIT brand   …                                          0.32"  │
  │▌ título slide | cliente | período                                      │
  │  ─ teal separator ───────────────────────────────────────────────────  │
  │  [VMs Prot.]  [Transact.Log]  [Jobs Backup]       ← rounded cards     │
  │  ─────────────────────────────────────────────────────────────────     │
  │  donut + %, mini-KPIs  │  tabela 4 colunas (+ coluna Ticket)           │
  │─────────────────────────────── rodapé teal ─────────────────── 0.12" │
  └────────────────────────────────────────────────────────────── 5.625" ─┘
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Optional

from pptx import Presentation
from pptx.util import Inches, Pt, Emu
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN
from pptx.oxml.ns import qn
from lxml import etree

# ── Caminhos ──────────────────────────────────────────────────────────────────
TEMPLATE_PATH = Path(__file__).parent.parent / "static" / "template.pptx"


def _hex_to_rgb(hex_color: str) -> RGBColor:
    """Converte '#7C3AED' em RGBColor."""
    try:
        h = hex_color.lstrip("#")
        return RGBColor(int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    except Exception:
        return RGBColor(0x7C, 0x3A, 0xED)  # fallback HelloIT green

# ── Dimensões (padrão HelloIT 10" × 5.625") ──────────────────────────────────
SW       = Inches(10.0)
SH       = Inches(5.625)
FOOTER_Y = Inches(5.42)

# ── Paleta HelloIT ───────────────────────────────────────────────────────────
TEAL        = RGBColor(0xA7, 0x8B, 0xFA)
GREEN_DOT   = RGBColor(0x7C, 0x3A, 0xED)
ORANGE_DOT  = RGBColor(0xF9, 0x73, 0x16)
GRAY_HDR    = RGBColor(0xF2, 0xF2, 0xF2)
C_TEXT      = RGBColor(0x33, 0x33, 0x33)
C_MUTED     = RGBColor(0x71, 0x80, 0x96)
C_WHITE     = RGBColor(0xFF, 0xFF, 0xFF)
C_GRAY_LINE = RGBColor(0xE8, 0xEA, 0xED)

# ── Paleta vibrante (cards modernos) ─────────────────────────────────────────
C_BLUE   = RGBColor(0x36, 0x8C, 0xFF)   # VMs protegidas
C_ORANGE = RGBColor(0xFF, 0x7B, 0x00)   # Transaction Logs
C_TEAL_V = RGBColor(0x06, 0xD6, 0xA0)   # Jobs de backup / sucesso
C_RED    = RGBColor(0xEF, 0x23, 0x3C)   # Falha

# Fundos neutros (tints muito suaves) dos cards — visual executivo
C_BG_BLUE   = RGBColor(0xF4, 0xF7, 0xFF)   # era EAF3FF
C_BG_ORANGE = RGBColor(0xFD, 0xF6, 0xEF)   # era FFF3E0
C_BG_TEAL   = RGBColor(0xF2, 0xFB, 0xF8)   # era E0FBF4
C_BG_RED    = RGBColor(0xFE, 0xF4, 0xF5)   # era FEECEE
C_BG_GREEN  = RGBColor(0xF2, 0xFB, 0xF4)   # era E0F8EA
C_BG_TICKET = RGBColor(0xF2, 0xF3, 0xF5)   # cinza neutro (era amarelo FFFFE8)

# Cabeçalho de tabela — tom claro executivo com texto escuro
C_TBL_HDR      = RGBColor(0xE4, 0xEB, 0xF3)   # azul-acinzentado claro (era 3D5A80)
C_TBL_HDR_TEXT = RGBColor(0x2C, 0x3E, 0x55)   # azul-escuro discreto para texto do header
C_ROW_ALT      = RGBColor(0xF8, 0xF9, 0xFA)

# Namespace chart/drawing
C_NS = "http://schemas.openxmlformats.org/drawingml/2006/chart"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"


# ─────────────────────────────────────────────────────────────────────────────
# Primitivas de desenho
# ─────────────────────────────────────────────────────────────────────────────

def _rounded_rect(slide, x, y, w, h,
                  fill: RGBColor,
                  border: Optional[RGBColor] = None,
                  border_pt: float = 0.4,
                  rounding: int = 5000,
                  shadow: bool = False) -> object:
    """Retângulo com cantos arredondados e shadow opcional."""
    shp = slide.shapes.add_shape(5, x, y, w, h)   # 5 = roundedRect
    shp.fill.solid()
    shp.fill.fore_color.rgb = fill
    if border:
        shp.line.color.rgb = border
        shp.line.width = Pt(border_pt)
    else:
        shp.line.fill.background()
    # Definir raio de arredondamento
    try:
        prstGeom = shp._element.spPr.find(qn("a:prstGeom"))
        if prstGeom is not None:
            avLst = prstGeom.find(qn("a:avLst"))
            if avLst is None:
                avLst = etree.SubElement(prstGeom, qn("a:avLst"))
            for gd in avLst.findall(qn("a:gd")):
                avLst.remove(gd)
            gd = etree.SubElement(avLst, qn("a:gd"))
            gd.set("name", "adj")
            gd.set("fmla", f"val {rounding}")
    except Exception:
        pass
    # Shadow sutil
    if shadow:
        try:
            spPr = shp._element.spPr
            effectLst = etree.SubElement(spPr, qn("a:effectLst"))
            outerShdw = etree.SubElement(effectLst, qn("a:outerShdw"))
            outerShdw.set("blurRad", "25400")    # 2 pt (mais discreta)
            outerShdw.set("dist",    "6350")     # 0.5 pt
            outerShdw.set("dir",     "5400000")  # para baixo
            outerShdw.set("rotWithShape", "0")
            clr = etree.SubElement(outerShdw, qn("a:srgbClr"))
            clr.set("val", "000000")
            alpha = etree.SubElement(clr, qn("a:alpha"))
            alpha.set("val", "8000")   # 8 % de opacidade (era 15 %)
        except Exception:
            pass
    return shp


def _rect(slide, x, y, w, h, fill: RGBColor, shape_id: int = 1) -> object:
    """Retângulo simples (ou oval) sem borda."""
    shp = slide.shapes.add_shape(shape_id, x, y, w, h)
    shp.fill.solid()
    shp.fill.fore_color.rgb = fill
    shp.line.fill.background()
    return shp


def _oval(slide, x, y, d, fill: RGBColor):
    return _rect(slide, x, y, d, d, fill, shape_id=9)


def _tb(slide, text: str, x, y, w, h,
        size: float,
        bold: bool = False,
        italic: bool = False,
        color: RGBColor = C_TEXT,
        align=PP_ALIGN.LEFT,
        wrap: bool = True) -> None:
    """Caixa de texto simples sem fundo."""
    box = slide.shapes.add_textbox(x, y, w, h)
    tf  = box.text_frame
    tf.word_wrap     = wrap
    tf.margin_left   = Pt(0)
    tf.margin_right  = Pt(0)
    tf.margin_top    = Pt(0)
    tf.margin_bottom = Pt(0)
    p = tf.paragraphs[0]
    p.alignment = align
    run = p.add_run()
    run.text           = text
    run.font.size      = Pt(size)
    run.font.bold      = bold
    run.font.italic    = italic
    run.font.color.rgb = color


# ─────────────────────────────────────────────────────────────────────────────
# Frame HelloIT
# ─────────────────────────────────────────────────────────────────────────────

def _add_frame(slide, page_num: int, branding: Optional[dict] = None) -> None:
    """Adiciona os elementos fixos do template HelloIT (topo + rodapé)."""
    _teal   = _hex_to_rgb(branding["accent_color"])  if branding else TEAL
    _green  = _hex_to_rgb(branding["primary_color"]) if branding else GREEN_DOT
    _footer = branding.get("footer_text", "Infraestrutura, Redes e Cibersegurança  |  helloit.com.br") \
              if branding else "Infraestrutura, Redes e Cibersegurança  |  helloit.com.br"

    # Barra cinza do header
    HDR_H  = Inches(0.32)
    HDR_CY = HDR_H / 2               # centro vertical da barra = 0.16"
    _rect(slide, Inches(0), Inches(0), SW, HDR_H, GRAY_HDR)
    # Bolinhas coloridas — centralizadas verticalmente no header
    D      = Inches(0.12)
    dot_y  = HDR_CY - D / 2          # 0.16" - 0.06" = 0.10"
    _oval(slide, Inches(0.22), dot_y, D, _green)
    _oval(slide, Inches(0.38), dot_y, D, ORANGE_DOT)
    # Texto da marca — mesmo eixo vertical das bolinhas
    txt_h  = Inches(0.14)
    txt_y  = HDR_CY - txt_h / 2      # 0.16" - 0.07" = 0.09"
    _tb(slide, _footer,
        Inches(0.55), txt_y, Inches(7.5), txt_h,
        size=8, color=C_TEXT)
    # Barra accent esquerda (marca lateral)
    _rect(slide, Inches(0), Inches(0.32), Inches(0.18), Inches(0.62), _teal)
    # Barra accent rodapé
    _rect(slide, Inches(0), FOOTER_Y, SW, Inches(0.12), _teal)
    # Número de página
    _tb(slide, str(page_num),
        Inches(9.20), Inches(5.25), Inches(0.70), Inches(0.18),
        size=7, color=C_MUTED, align=PP_ALIGN.RIGHT)


# ─────────────────────────────────────────────────────────────────────────────
# Gráfico donut nativo (vetor — sem pixelação)
# ─────────────────────────────────────────────────────────────────────────────

def _add_donut_chart(slide, x, y, w, h,
                     failed_n: int, success_n: int) -> None:
    """Cria um gráfico rosca nativo no slide (qualidade vetorial)."""
    from pptx.chart.data import ChartData
    from pptx.enum.chart import XL_CHART_TYPE

    cd = ChartData()
    cd.categories = ["Com Falha", "Com Sucesso"]
    cd.add_series("", (failed_n, success_n))

    frame = slide.shapes.add_chart(XL_CHART_TYPE.DOUGHNUT, x, y, w, h, cd)
    chart = frame.chart
    chart.has_title  = False
    chart.has_legend = False

    # Remover borda do frame do gráfico
    try:
        frame.line.fill.background()
    except Exception:
        pass

    plot_el = chart.plots[0]._element
    ser_el  = chart.plots[0].series[0]._element

    # Tamanho do furo (65%)
    holeSize = plot_el.find(qn("c:holeSize"))
    if holeSize is None:
        holeSize = etree.SubElement(plot_el, qn("c:holeSize"))
    holeSize.set("val", "65")

    # Desabilitar variação automática de cor (usaremos manual)
    varyColors = plot_el.find(qn("c:varyColors"))
    if varyColors is not None:
        varyColors.set("val", "0")

    # Cores dos fatias: vermelho (falha) e verde-teal (sucesso)
    SLICES = [
        ("EF233C", "FFFFFF"),   # falha — vermelho vibrante
        ("06D6A0", "FFFFFF"),   # sucesso — teal verde
    ]

    # Encontrar ponto de inserção (antes de c:cat / c:val)
    cat_el = ser_el.find(qn("c:cat"))
    if cat_el is None:
        cat_el = ser_el.find(qn("c:val"))
    ins = list(ser_el).index(cat_el) if cat_el is not None else len(ser_el)

    for i, (fill_hex, ln_hex) in enumerate(SLICES):
        # NB: nada de <c:bubble3D> aqui — o python-pptx nativo não o gera para
        # rosca/pizza e o PowerPoint marca o arquivo como "necessita reparo".
        dPt = (
            f'<c:dPt xmlns:c="{C_NS}" xmlns:a="{A_NS}">'
            f'<c:idx val="{i}"/>'
            f'<c:spPr>'
            f'<a:solidFill><a:srgbClr val="{fill_hex}"/></a:solidFill>'
            f'<a:ln w="38100"><a:solidFill>'
            f'<a:srgbClr val="{ln_hex}"/></a:solidFill></a:ln>'
            f'</c:spPr>'
            f'</c:dPt>'
        )
        ser_el.insert(ins + i, etree.fromstring(dPt))

    # Remover rótulos de dados (dLbls) caso existam
    dLbls = ser_el.find(qn("c:dLbls"))
    if dLbls is not None:
        ser_el.remove(dLbls)


# ─────────────────────────────────────────────────────────────────────────────
# Cards modernos
# ─────────────────────────────────────────────────────────────────────────────

def _kpi_card(slide, x, y, w, h,
              value: str, label: str, desc: str,
              accent: RGBColor, bg: RGBColor) -> None:
    """Card KPI compacto — cantos sutis, sombra discreta, faixa lateral esquerda.
    h esperado: Inches(0.67)
    """
    _rounded_rect(slide, x, y, w, h, bg, shadow=True, rounding=5000)
    # Faixa accent fina na borda esquerda (substituindo barra no topo)
    STRIP_W = Inches(0.04)
    _rect(slide, x, y, STRIP_W, h, accent)

    # Número grande (recuado da faixa lateral)
    _tb(slide, value,
        x + STRIP_W + Inches(0.04), y + Inches(0.05),
        w - STRIP_W - Inches(0.10), Inches(0.30),
        size=18, bold=True, color=accent, align=PP_ALIGN.CENTER)

    # Rótulo em negrito
    _tb(slide, label,
        x + STRIP_W + Inches(0.02), y + Inches(0.37),
        w - STRIP_W - Inches(0.06), Inches(0.15),
        size=7.5, bold=True, color=C_TEXT, align=PP_ALIGN.CENTER)

    # Descrição (opcional), itálica e discreta
    if desc:
        _tb(slide, desc,
            x + STRIP_W + Inches(0.02), y + Inches(0.51),
            w - STRIP_W - Inches(0.06), Inches(0.14),
            size=6, italic=True, color=C_MUTED, align=PP_ALIGN.CENTER)


def _mini_kpi(slide, x, y, w, h,
              value: str, label: str,
              accent: RGBColor, bg: RGBColor) -> None:
    """Card KPI mini compacto — cantos sutis, faixa lateral esquerda.
    h esperado: Inches(0.52)
    """
    _rounded_rect(slide, x, y, w, h, bg, shadow=True, rounding=5000)
    # Faixa accent fina na borda esquerda
    STRIP_W = Inches(0.035)
    _rect(slide, x, y, STRIP_W, h, accent)
    _tb(slide, value,
        x + STRIP_W + Inches(0.04), y + Inches(0.04),
        w - STRIP_W - Inches(0.08), Inches(0.27),
        size=15, bold=True, color=accent, align=PP_ALIGN.CENTER)
    _tb(slide, label,
        x + STRIP_W + Inches(0.03), y + Inches(0.30),
        w - STRIP_W - Inches(0.06), Inches(0.20),
        size=7, color=C_MUTED, align=PP_ALIGN.CENTER)


# ─────────────────────────────────────────────────────────────────────────────
# Tabela de falhas (4 colunas: Job | Tipo | Última Execução | Ticket)
# ─────────────────────────────────────────────────────────────────────────────

def _build_failed_table(slide, x, y, w, avail_h, failed_jobs: list,
                        tbl_hdr_color: Optional[RGBColor] = None) -> int:
    """Tabela executiva compacta com altura de linha fixa.

    Alturas fixas via XML:
      - Cabeçalho: HDR_H  = 0.28"
      - Dado:       ROW_H  = 0.23"
    O número de linhas visíveis é limitado ao espaço disponível (avail_h).
    Coluna Ticket permanece em branco para preenchimento manual.

    Retorna a altura real (EMU) usada pela tabela, para posicionamento preciso
    dos elementos abaixo dela.
    """
    HDR_H     = Inches(0.30)   # header row
    ROW_H     = Inches(0.26)   # data rows — mais espaço para melhor legibilidade
    MAX_ROWS  = min(13, int((avail_h - HDR_H) / ROW_H))
    TYPE_LBLS = {"log_backup": "Transact. Log", "backup": "Backup Regular"}
    show      = failed_jobs[:MAX_ROWS]

    if not show:
        _tb(slide, "✓  Nenhum job com falha no período.",
            x, y + Inches(0.40), w, Inches(0.35),
            size=10, bold=True, color=C_TEAL_V, align=PP_ALIGN.CENTER)
        return int(Inches(0.45))

    n_rows   = 1 + len(show)
    actual_h = int(HDR_H) + len(show) * int(ROW_H)
    tbl      = slide.shapes.add_table(n_rows, 4, x, y, w, actual_h).table

    # Largura das colunas
    tbl.columns[0].width = int(w * 0.47)   # Rotina de Backup
    tbl.columns[1].width = int(w * 0.17)   # Tipo
    tbl.columns[2].width = int(w * 0.21)   # Última Execução
    tbl.columns[3].width = int(w * 0.15)   # Ticket

    # Alturas fixas das linhas via python-pptx API
    for i, row in enumerate(tbl.rows):
        row.height = int(HDR_H) if i == 0 else int(ROW_H)

    def _c(ri, ci, text,
           bold=False, size=7.5,
           color: RGBColor = C_TEXT,
           bg: Optional[RGBColor] = None,
           align=PP_ALIGN.LEFT):
        cell = tbl.cell(ri, ci)
        if bg:
            cell.fill.solid()
            cell.fill.fore_color.rgb = bg
        cell.margin_left   = Pt(4)
        cell.margin_right  = Pt(4)
        cell.margin_top    = Pt(1.5)
        cell.margin_bottom = Pt(1.5)
        p = cell.text_frame.paragraphs[0]
        p.alignment = align
        run = p.add_run()
        run.text           = text
        run.font.size      = Pt(size)
        run.font.bold      = bold
        run.font.color.rgb = color

    # Cabeçalho — usa cor de branding ou padrão executivo
    _hdr_bg  = tbl_hdr_color if tbl_hdr_color else C_TBL_HDR
    _hdr_txt = C_WHITE if tbl_hdr_color else C_TBL_HDR_TEXT
    hdrs = ["Rotina de Backup", "Tipo", "Última Execução", "Ticket"]
    for ci, h_text in enumerate(hdrs):
        _c(0, ci, h_text, bold=True, color=_hdr_txt, bg=_hdr_bg,
           align=PP_ALIGN.CENTER)

    # Linhas de dados
    for ri, job in enumerate(show, start=1):
        row_bg = C_ROW_ALT if ri % 2 == 0 else C_WHITE
        name   = job.get("job_name", "")
        if len(name) > 40:
            name = name[:38] + "…"
        cols = [
            (name,
             PP_ALIGN.LEFT,   row_bg,      C_TEXT),
            (TYPE_LBLS.get(job.get("job_type", ""), job.get("job_type", "")),
             PP_ALIGN.CENTER, row_bg,      C_TEXT),
            (job.get("last_execution", "—"),
             PP_ALIGN.CENTER, row_bg,      C_TEXT),
            ("",
             PP_ALIGN.CENTER, C_BG_TICKET, C_MUTED),
        ]
        for ci, (val, al, bg, clr) in enumerate(cols):
            _c(ri, ci, val, color=clr, bg=bg, align=al)

    return actual_h


# ─────────────────────────────────────────────────────────────────────────────
# Slide de conteúdo principal
# ─────────────────────────────────────────────────────────────────────────────

def _build_content(slide,
                   client_name: str, period: str,
                   kpis: dict, protection_kpis: dict,
                   failed_jobs: list,
                   branding: Optional[dict] = None) -> None:
    """Monta o slide de conteúdo com design moderno."""
    # Resolve cores via branding ou usa padrão HelloIT
    _teal      = _hex_to_rgb(branding["accent_color"])   if branding else TEAL
    _green_dot = _hex_to_rgb(branding["primary_color"])  if branding else GREEN_DOT
    _tbl_hdr   = _hex_to_rgb(branding["table_header_color"]) if branding else C_TBL_HDR

    X0 = Inches(0.22)
    CW = SW - Inches(0.44)   # 9.56" — largura útil

    _add_frame(slide, page_num=2, branding=branding)

    # ── Cabeçalho ─────────────────────────────────────────────────────────────
    _tb(slide, "Relatório Semanal de Backup",
        X0, Inches(0.36), Inches(6.50), Inches(0.26),
        size=10.5, bold=True, color=C_TEXT)

    clabel = client_name if client_name else "Todos os clientes"
    _tb(slide, f"{clabel}  |  {period}",
        X0, Inches(0.63), Inches(9.40), Inches(0.20),
        size=8, color=C_MUTED)

    # Linha separadora accent
    _rect(slide, X0, Inches(0.84), CW, Inches(0.018), _teal)

    # ── Row 1: cards KPI de proteção de VMs ──────────────────────────────────
    # Altura reduzida (0.67") — elimina espaço morto dos cards
    KPI_Y = Inches(0.91)
    KPI_H = Inches(0.67)
    GAP   = Inches(0.09)
    KPI_W = (CW - 2 * GAP) // 3   # 3 cards iguais

    vm_cards = [
        (str(protection_kpis.get("protected_vms", 0)),
         "VMs Protegidas", "Máquinas com backup no período",
         C_BLUE, C_BG_BLUE),
        (str(protection_kpis.get("log_vms", 0)),
         "Transaction Logs", "VMs com backup de log SQL",
         C_ORANGE, C_BG_ORANGE),
        (str(protection_kpis.get("backup_jobs", 0)),
         "Jobs de Backup", "Rotinas de backup ativas",
         C_TEAL_V, C_BG_TEAL),
    ]
    for i, (val, lbl, dsc, acc, bg) in enumerate(vm_cards):
        _kpi_card(slide,
                  X0 + i * (KPI_W + GAP), KPI_Y, KPI_W, KPI_H,
                  val, lbl, dsc, acc, bg)

    # Separador fino — logo abaixo dos cards, sem gap excessivo
    SEP_Y = KPI_Y + KPI_H + Inches(0.06)   # ≈ 1.64"
    _rect(slide, X0, SEP_Y, CW, Inches(0.012), C_GRAY_LINE)

    # ── Área principal (painel esquerdo + painel direito) ─────────────────────
    MAIN_Y = SEP_Y + Inches(0.05)           # ≈ 1.69"

    LEFT_W  = Inches(3.26)
    RIGHT_X = X0 + LEFT_W + Inches(0.12)
    RIGHT_W = SW - RIGHT_X - Inches(0.12)   # ≈ 6.18"

    failed_n    = kpis.get("failed",      0)
    success_n   = kpis.get("success",     0)
    failed_pct  = kpis.get("failed_pct",  0.0)
    success_pct = kpis.get("success_pct", 0.0)

    # ── PAINEL ESQUERDO: Donut + legenda de cores ──────────────────────────────
    _tb(slide, "Distribuição dos Backups",
        X0, MAIN_Y + Inches(0.02), LEFT_W, Inches(0.20),
        size=9, bold=True, color=C_TEXT)
    _tb(slide, "Última execução por rotina de backup regular  •  EV190",
        X0, MAIN_Y + Inches(0.21), LEFT_W, Inches(0.14),
        size=6.5, italic=True, color=C_MUTED, wrap=False)

    # Donut nativo (vetor) — 2.30" centralizado no painel
    CHART_S = Inches(2.30)
    chart_x = X0 + (LEFT_W - CHART_S) // 2
    chart_y = MAIN_Y + Inches(0.38)          # ≈ 2.07"

    _add_donut_chart(slide, chart_x, chart_y, CHART_S, CHART_S,
                     failed_n, success_n)

    # Texto central sobreposto ao buraco do donut
    hole_x = chart_x + Inches(0.58)
    hole_y = chart_y + CHART_S / 2 - Inches(0.25)
    hole_w = CHART_S - Inches(1.16)
    _tb(slide, f"{failed_pct:.0f}%",
        hole_x, hole_y, hole_w, Inches(0.32),
        size=16, bold=True, color=C_RED, align=PP_ALIGN.CENTER)
    _tb(slide, "com falha",
        hole_x, hole_y + Inches(0.32), hole_w, Inches(0.18),
        size=7, color=C_MUTED, align=PP_ALIGN.CENTER)

    # Legenda de cores logo abaixo do donut (substitui mini-KPIs no esquerdo)
    # chart_bottom ≈ 2.07 + 2.30 = 4.37"
    LEG_Y   = chart_y + CHART_S + Inches(0.07)   # ≈ 4.44"
    LEG_H   = Inches(0.18)
    D       = Inches(0.10)
    half_w  = LEFT_W / 2

    _oval(slide, X0, LEG_Y + (LEG_H - D) / 2, D, C_RED)
    _tb(slide, f"Com Falha ({failed_n})",
        X0 + D + Inches(0.04), LEG_Y,
        half_w - D - Inches(0.06), LEG_H,
        size=7.5, color=C_TEXT)

    _oval(slide, X0 + half_w, LEG_Y + (LEG_H - D) / 2, D, C_TEAL_V)
    _tb(slide, f"Com Sucesso ({success_n})",
        X0 + half_w + D + Inches(0.04), LEG_Y,
        half_w - D - Inches(0.06), LEG_H,
        size=7.5, color=C_TEXT)
    # leg_bottom ≈ 4.62" — breathing room até rodapé (5.42") ✓

    # ── PAINEL DIREITO: Tabela + mini-KPIs + total ────────────────────────────
    _tb(slide, "Jobs com Falha na Última Execução",
        RIGHT_X, MAIN_Y + Inches(0.02), RIGHT_W, Inches(0.20),
        size=9, bold=True, color=C_TEXT)
    _tb(slide,
        "Preencha a coluna Ticket se houver chamado aberto.",
        RIGHT_X, MAIN_Y + Inches(0.21), RIGHT_W, Inches(0.14),
        size=6.5, italic=True, color=C_MUTED, wrap=False)

    # Tabela alinhada horizontalmente com o donut
    TABLE_Y = MAIN_Y + Inches(0.38)                    # ≈ 2.07"
    AVAIL_H = FOOTER_Y - TABLE_Y - Inches(0.18)        # margem inferior antes do rodapé

    tbl_actual_h = _build_failed_table(
        slide, RIGHT_X, TABLE_Y, RIGHT_W, AVAIL_H, failed_jobs,
        tbl_hdr_color=_tbl_hdr if branding else None,
    )
    # Posição exata onde a tabela termina (EMU, sem cálculo duplicado)
    tbl_bottom = TABLE_Y + tbl_actual_h

    # Mini-KPIs abaixo da tabela (Falha | Sucesso) — só se couber antes do rodapé
    MINI_H   = Inches(0.52)
    MINI_GAP = Inches(0.06)
    MINI_W   = (RIGHT_W - MINI_GAP) / 2
    mini_y   = tbl_bottom + Inches(0.25)   # gap explícito após a tabela

    if mini_y + MINI_H < FOOTER_Y - Inches(0.18):
        _mini_kpi(slide, RIGHT_X, mini_y, MINI_W, MINI_H,
                  str(failed_n), f"Com Falha ({failed_pct:.1f}%)",
                  C_RED, C_BG_RED)
        _mini_kpi(slide,
                  RIGHT_X + MINI_W + MINI_GAP, mini_y, MINI_W, MINI_H,
                  str(success_n), f"Com Sucesso ({success_pct:.1f}%)",
                  C_TEAL_V, C_BG_TEAL)

        # Linha de total abaixo dos mini-KPIs (se ainda couber)
        total_y = mini_y + MINI_H + Inches(0.08)
        total   = failed_n + success_n
        if total_y + Inches(0.18) < FOOTER_Y - Inches(0.12) and total > 0:
            _tb(slide,
                f"Total analisado no período: {total} jobs",
                RIGHT_X, total_y, RIGHT_W, Inches(0.18),
                size=7, italic=True, color=C_MUTED,
                align=PP_ALIGN.CENTER)


# ─────────────────────────────────────────────────────────────────────────────
# Atualização dinâmica do slide de capa
# ─────────────────────────────────────────────────────────────────────────────

def _update_cover_slide(slide, title: str, client_label: str, period: str,
                        branding: Optional[dict] = None) -> None:
    """
    Substitui o texto do slide de capa com título e subtítulo dinâmicos.
    Encontra a TextBox com a maior fonte e reescreve o conteúdo, preservando
    posição, tamanho e estilos visuais do template original.
    """
    # Encontrar TextBox com a maior fonte (é a TextBox principal da capa)
    cover_box = None
    max_sz    = 0
    for shape in slide.shapes:
        if not shape.has_text_frame:
            continue
        for para in shape.text_frame.paragraphs:
            for run in para.runs:
                if run.font.size and run.font.size > max_sz:
                    max_sz    = run.font.size
                    cover_box = shape

    if not cover_box:
        return

    txBody = cover_box.text_frame._txBody

    # Remover todos os <a:p> existentes, preservando bodyPr e lstStyle
    for p in list(txBody.findall(qn("a:p"))):
        txBody.remove(p)

    # Cor accent do branding para destaque do nome do cliente
    accent_hex: Optional[str] = None
    if branding:
        accent_hex = (branding.get("accent_color") or "#A78BFA").lstrip("#")

    # ── Helpers para construir elementos XML ──────────────────────────────────

    def _make_rPr(parent, sz: int, bold: bool,
                  scheme: str = "bg1", rgb: Optional[str] = None):
        rp = etree.SubElement(parent, qn("a:rPr"))
        rp.set("lang", "pt-BR")
        rp.set("sz", str(sz))
        if bold:
            rp.set("b", "1")
        rp.set("dirty", "0")
        fl = etree.SubElement(rp, qn("a:solidFill"))
        if rgb:
            cl = etree.SubElement(fl, qn("a:srgbClr"))
            cl.set("val", rgb)
        else:
            cl = etree.SubElement(fl, qn("a:schemeClr"))
            cl.set("val", scheme)
        return rp

    def _add_run(parent, text: str, sz: int, bold: bool,
                 scheme: str = "bg1", rgb: Optional[str] = None):
        r = etree.SubElement(parent, qn("a:r"))
        _make_rPr(r, sz, bold, scheme=scheme, rgb=rgb)
        t = etree.SubElement(r, qn("a:t"))
        t.text = text
        return r

    def _add_para_single(text: str, sz: int, bold: bool,
                         scheme: str = "bg1", rgb: Optional[str] = None):
        p = etree.SubElement(txBody, qn("a:p"))
        _add_run(p, text, sz, bold, scheme=scheme, rgb=rgb)
        return p

    def _add_empty_para():
        p   = etree.SubElement(txBody, qn("a:p"))
        epr = etree.SubElement(p, qn("a:endParaRPr"))
        epr.set("lang", "pt-BR")
        epr.set("b", "1")
        epr.set("dirty", "0")
        fl  = etree.SubElement(epr, qn("a:solidFill"))
        sc  = etree.SubElement(fl,  qn("a:schemeClr"))
        sc.set("val", "bg1")

    # ── Linha 1: Título principal (branco, 16 pt, negrito) ────────────────────
    _add_para_single(title, sz=1600, bold=True, scheme="bg1")

    # ── Linha 2: Espaçador vazio ──────────────────────────────────────────────
    _add_empty_para()

    # ── Linha 3: "Cliente  |  Período" ────────────────────────────────────────
    p_sub = etree.SubElement(txBody, qn("a:p"))

    # Nome do cliente em cor accent (destaque)
    _add_run(p_sub, client_label, sz=1400, bold=True,
             rgb=accent_hex, scheme="accent6")

    # Separador e período em branco
    _add_run(p_sub, f"  |  {period}", sz=1400, bold=False, scheme="bg1")

    epr = etree.SubElement(p_sub, qn("a:endParaRPr"))
    epr.set("lang", "pt-BR")
    epr.set("dirty", "0")
    fl  = etree.SubElement(epr, qn("a:solidFill"))
    sc  = etree.SubElement(fl,  qn("a:schemeClr"))
    sc.set("val", "bg1")


# ─────────────────────────────────────────────────────────────────────────────
# Slide de conteúdo — Proteção de VMs
# ─────────────────────────────────────────────────────────────────────────────

def _add_evolution_chart(slide, x, y, w, h, evolution: list,
                         branding: Optional[dict] = None) -> None:
    """Gráfico de linha da evolução de VMs protegidas por período.

    Série única 'VMs Protegidas' (vms total); linha suave com pontos marcados
    e rótulos acima de cada ponto. Sem legenda. Cores do branding.
    """
    from pptx.chart.data import CategoryChartData
    from pptx.enum.chart import XL_CHART_TYPE

    _c_line = _hex_to_rgb(branding["primary_color"]) if branding and branding.get("primary_color") else C_BLUE
    _c_hex  = branding["primary_color"].lstrip("#") if branding and branding.get("primary_color") else "368CFF"

    cd = CategoryChartData()
    cd.categories = [ep.get("label", "") for ep in evolution]
    cd.add_series("VMs Protegidas", [ep.get("vms", 0) for ep in evolution])

    frame = slide.shapes.add_chart(XL_CHART_TYPE.LINE_MARKERS, x, y, w, h, cd)
    chart = frame.chart
    chart.has_title  = False
    chart.has_legend = False

    # ── Eixos ────────────────────────────────────────────────────────────────
    try:
        chart.category_axis.tick_labels.font.size = Pt(6.5)
        va = chart.value_axis
        va.tick_labels.font.size = Pt(7)
        va.has_major_gridlines = True
        va.minimum_scale = 0
    except Exception:
        pass

    # ── Sem borda no frame ───────────────────────────────────────────────────
    try:
        frame.line.fill.background()
    except Exception:
        pass

    # ── Cor e espessura da linha + marcadores via XML ────────────────────────
    ser_el = chart.plots[0].series[0]._element

    # Remover spPr existente para recriar
    for old in ser_el.findall(qn("c:spPr")):
        ser_el.remove(old)

    line_w_emu = 25400  # ~2 pt
    spPr_xml = (
        f'<c:spPr xmlns:c="{C_NS}" xmlns:a="{A_NS}">'
        f'<a:ln w="{line_w_emu}">'
        f'<a:solidFill><a:srgbClr val="{_c_hex}"/></a:solidFill>'
        f'<a:round/>'
        f'</a:ln>'
        f'</c:spPr>'
    )
    # Inserir antes de c:marker se existir, senão ao final
    marker_el = ser_el.find(qn("c:marker"))
    if marker_el is not None:
        ser_el.insert(list(ser_el).index(marker_el), etree.fromstring(spPr_xml))
    else:
        ser_el.append(etree.fromstring(spPr_xml))

    # Marcador: círculo pequeno na cor do branding
    if marker_el is not None:
        ser_el.remove(marker_el)
    marker_xml = (
        f'<c:marker xmlns:c="{C_NS}" xmlns:a="{A_NS}">'
        f'<c:symbol val="circle"/>'
        f'<c:size val="5"/>'
        f'<c:spPr>'
        f'<a:solidFill><a:srgbClr val="{_c_hex}"/></a:solidFill>'
        f'<a:ln><a:solidFill><a:srgbClr val="{_c_hex}"/></a:solidFill></a:ln>'
        f'</c:spPr>'
        f'</c:marker>'
    )
    ser_el.append(etree.fromstring(marker_xml))

    # ── Linha suave (smooth) via XML no plotArea ─────────────────────────────
    try:
        lnChart_el = chart.plots[0]._element
        smooth_el  = lnChart_el.find(qn("c:smooth"))
        if smooth_el is None:
            smooth_el = etree.SubElement(lnChart_el, qn("c:smooth"))
        smooth_el.set("val", "1")
    except Exception:
        pass

    # ── Rótulos acima dos pontos via c:dLbls ────────────────────────────────
    # Remover dLbls existentes no plot (nível chart) se houver
    for old in ser_el.findall(qn("c:dLbls")):
        ser_el.remove(old)

    dLbls_xml = (
        f'<c:dLbls xmlns:c="{C_NS}" xmlns:a="{A_NS}">'
        f'<c:numFmt formatCode="General" sourceLinked="0"/>'
        f'<c:spPr>'
        f'<a:noFill/>'
        f'<a:ln><a:noFill/></a:ln>'
        f'</c:spPr>'
        f'<c:txPr>'
        f'<a:bodyPr/><a:lstStyle/>'
        f'<a:p><a:pPr><a:defRPr b="1" sz="700"/></a:pPr></a:p>'
        f'</c:txPr>'
        f'<c:dLblPos val="t"/>'
        f'<c:showLegendKey val="0"/>'
        f'<c:showVal val="1"/>'
        f'<c:showCatName val="0"/>'
        f'<c:showSerName val="0"/>'
        f'<c:showPercent val="0"/>'
        f'<c:showBubbleSize val="0"/>'
        f'</c:dLbls>'
    )
    # Inserir dLbls antes de c:marker (ou ao final)
    marker_new = ser_el.find(qn("c:marker"))
    if marker_new is not None:
        ser_el.insert(list(ser_el).index(marker_new), etree.fromstring(dLbls_xml))
    else:
        ser_el.append(etree.fromstring(dLbls_xml))

    # ── Grid leve: linha pontilhada cinza claro ──────────────────────────────
    try:
        va      = chart.value_axis
        mg_el   = va._element.find(qn("c:majorGridlines"))
        if mg_el is None:
            mg_el = etree.SubElement(va._element, qn("c:majorGridlines"))
        grid_spPr = mg_el.find(qn("c:spPr"))
        if grid_spPr is not None:
            mg_el.remove(grid_spPr)
        grid_xml = (
            f'<c:spPr xmlns:c="{C_NS}" xmlns:a="{A_NS}">'
            f'<a:ln w="6350">'
            f'<a:solidFill><a:srgbClr val="E0E0E0"/></a:solidFill>'
            f'<a:prstDash val="sysDot"/>'
            f'</a:ln>'
            f'</c:spPr>'
        )
        mg_el.append(etree.fromstring(grid_xml))
    except Exception:
        pass


def _build_protection_content(slide,
                               client_name: str, period: str,
                               kpis: dict, evolution: list, vm_list: list,
                               branding: Optional[dict] = None) -> None:
    """Slide-resumo: cards + gráfico de evolução + tabela 'Detalhe por Período'."""
    _teal    = _hex_to_rgb(branding["accent_color"])       if branding else TEAL
    _green   = _hex_to_rgb(branding["primary_color"])      if branding else GREEN_DOT
    _tbl_hdr = _hex_to_rgb(branding["table_header_color"]) if branding else C_TBL_HDR

    X0 = Inches(0.22)
    CW = SW - Inches(0.44)

    _add_frame(slide, page_num=2, branding=branding)

    # ── Cabeçalho ─────────────────────────────────────────────────────────────
    _tb(slide, "Relatório de Proteção de VMs",
        X0, Inches(0.36), Inches(6.50), Inches(0.26),
        size=10.5, bold=True, color=C_TEXT)

    clabel = client_name if client_name else "Todos os clientes"
    _tb(slide, f"{clabel}  |  {period}",
        X0, Inches(0.63), Inches(9.40), Inches(0.20),
        size=8, color=C_MUTED)

    _rect(slide, X0, Inches(0.84), CW, Inches(0.018), _teal)

    # ── KPI Cards ─────────────────────────────────────────────────────────────
    KPI_Y = Inches(0.91)
    KPI_H = Inches(0.67)
    GAP   = Inches(0.09)
    KPI_W = (CW - 2 * GAP) // 3

    vm_cards = [
        (str(kpis.get("protected_vms", 0)),
         "VMs Protegidas", "Máquinas com backup no período",
         C_BLUE, C_BG_BLUE),
        (str(kpis.get("log_vms", 0)),
         "Transaction Logs", "VMs com backup de log SQL",
         C_ORANGE, C_BG_ORANGE),
        (str(kpis.get("backup_jobs", 0)),
         "Jobs de Backup", "Rotinas de backup ativas",
         C_TEAL_V, C_BG_TEAL),
    ]
    for i, (val, lbl, dsc, acc, bg) in enumerate(vm_cards):
        _kpi_card(slide, X0 + i * (KPI_W + GAP), KPI_Y, KPI_W, KPI_H,
                  val, lbl, dsc, acc, bg)

    SEP_Y  = KPI_Y + KPI_H + Inches(0.06)
    _rect(slide, X0, SEP_Y, CW, Inches(0.012), C_GRAY_LINE)
    MAIN_Y = SEP_Y + Inches(0.05)

    # ── Layout full-width: gráfico de evolução + detalhe por período ─────────
    _hdr_bg  = _tbl_hdr if branding else C_TBL_HDR
    _hdr_txt = C_WHITE  if branding else C_TBL_HDR_TEXT

    # ── Gráfico de evolução (coluna empilhada nativa) ─────────────────────────
    _tb(slide, "Evolução de VMs Protegidas",
        X0, MAIN_Y + Inches(0.02), CW, Inches(0.20),
        size=9, bold=True, color=C_TEXT)
    _tb(slide, "VMs únicas com backup identificado no período, agrupadas conforme o filtro selecionado.",
        X0, MAIN_Y + Inches(0.21), CW, Inches(0.14),
        size=6.5, italic=True, color=C_MUTED, wrap=False)

    CHART_Y = MAIN_Y + Inches(0.37)
    CHART_H = Inches(1.66)
    if evolution:
        _add_evolution_chart(slide, X0, CHART_Y, CW, CHART_H, evolution, branding)
    else:
        _tb(slide, "Sem dados de evolução no período.",
            X0, CHART_Y + Inches(0.40), CW, Inches(0.30),
            size=9, color=C_MUTED, align=PP_ALIGN.CENTER)

    # ── Tabela 'Detalhe por Período' ─────────────────────────────────────────
    DET_TITLE_Y = CHART_Y + CHART_H + Inches(0.06)
    _tb(slide, "Detalhe por Período",
        X0, DET_TITLE_Y, CW, Inches(0.18),
        size=8.5, bold=True, color=C_TEXT)

    DET_Y     = DET_TITLE_Y + Inches(0.22)
    DET_HDR_H = Inches(0.26)
    DET_ROW_H = Inches(0.22)
    DET_AVAIL = FOOTER_Y - DET_Y - Inches(0.12)
    MAX_DET   = min(len(evolution), max(0, int((DET_AVAIL - DET_HDR_H) / DET_ROW_H)))

    if evolution and MAX_DET > 0:
        det_show = evolution[:MAX_DET]
        n_rows   = 1 + len(det_show)
        det_h    = int(DET_HDR_H) + len(det_show) * int(DET_ROW_H)
        tbl_det  = slide.shapes.add_table(n_rows, 5, X0, DET_Y, CW, det_h).table
        tbl_det.columns[0].width = int(CW * 0.30)
        tbl_det.columns[1].width = int(CW * 0.18)
        tbl_det.columns[2].width = int(CW * 0.18)
        tbl_det.columns[3].width = int(CW * 0.16)
        tbl_det.columns[4].width = int(CW) - int(CW * 0.30) - int(CW * 0.18) - int(CW * 0.18) - int(CW * 0.16)
        for i, row in enumerate(tbl_det.rows):
            row.height = int(DET_HDR_H) if i == 0 else int(DET_ROW_H)

        def _cd(ri, ci, text, bold=False, sz=7.5, color=C_TEXT, bg=None, align=PP_ALIGN.LEFT):
            cell = tbl_det.cell(ri, ci)
            if bg:
                cell.fill.solid(); cell.fill.fore_color.rgb = bg
            cell.margin_left = Pt(4); cell.margin_right = Pt(4)
            cell.margin_top  = Pt(1.5); cell.margin_bottom = Pt(1.5)
            p = cell.text_frame.paragraphs[0]; p.alignment = align
            run = p.add_run(); run.text = text
            run.font.size = Pt(sz); run.font.bold = bold; run.font.color.rgb = color

        for ci, h in enumerate(["Período", "VMs Protegidas", "Com Trans. Log", "Só Backup", "Variação"]):
            _cd(0, ci, h, bold=True, color=_hdr_txt, bg=_hdr_bg, align=PP_ALIGN.CENTER)

        prev_vms = None
        for ri, ep in enumerate(det_show, start=1):
            row_bg = C_ROW_ALT if ri % 2 == 0 else C_WHITE
            vms = ep.get("vms", 0)
            if prev_vms is None:
                var_txt, var_clr = "—", C_MUTED
            else:
                diff = vms - prev_vms
                if diff > 0:   var_txt, var_clr = f"+{diff} ▲", C_TEAL_V
                elif diff < 0: var_txt, var_clr = f"{diff} ▼", C_RED
                else:          var_txt, var_clr = "0 —", C_MUTED
            prev_vms = vms
            _cd(ri, 0, ep.get("label", ""),       bg=row_bg)
            _cd(ri, 1, str(vms),                  bg=row_bg, align=PP_ALIGN.CENTER, bold=True)
            _cd(ri, 2, str(ep.get("log_vms", 0)), bg=row_bg, align=PP_ALIGN.CENTER)
            _cd(ri, 3, str(ep.get("bkp_vms", 0)), bg=row_bg, align=PP_ALIGN.CENTER)
            _cd(ri, 4, var_txt, color=var_clr,    bg=row_bg, align=PP_ALIGN.CENTER, bold=True)

def _build_protection_vm_slide(slide,
                                client_name: str, period: str,
                                kpis: dict, vm_list: list,
                                branding: Optional[dict] = None) -> None:
    """Slide dedicado à lista de VMs protegidas no período (tabela full-width)."""
    _teal    = _hex_to_rgb(branding["accent_color"])       if branding else TEAL
    _tbl_hdr = _hex_to_rgb(branding["table_header_color"]) if branding else C_TBL_HDR
    X0 = Inches(0.22)
    CW = SW - Inches(0.44)

    _add_frame(slide, page_num=3, branding=branding)

    _tb(slide, "VMs Protegidas no Período",
        X0, Inches(0.36), Inches(6.50), Inches(0.26),
        size=10.5, bold=True, color=C_TEXT)
    clabel = client_name if client_name else "Todos os clientes"
    _tb(slide, f"{clabel}  |  {period}",
        X0, Inches(0.63), Inches(9.40), Inches(0.20),
        size=8, color=C_MUTED)
    _rect(slide, X0, Inches(0.84), CW, Inches(0.018), _teal)

    total_vms = kpis.get("protected_vms", 0)
    shown_vms = len(vm_list)
    vm_note   = (f"Mostrando {shown_vms} de {total_vms} — ver Excel para a lista completa."
                 if shown_vms < total_vms else f"Total: {total_vms} VMs.")
    _tb(slide, vm_note + "  Cada VM é contabilizada uma vez por período.",
        X0, Inches(0.92), CW, Inches(0.16),
        size=7, italic=True, color=C_MUTED, wrap=False)

    _hdr_bg  = _tbl_hdr if branding else C_TBL_HDR
    _hdr_txt = C_WHITE  if branding else C_TBL_HDR_TEXT

    VM_HDR_H = Inches(0.28)
    VM_ROW_H = Inches(0.225)
    VM_Y     = Inches(1.16)
    VM_AVAIL = FOOTER_Y - VM_Y - Inches(0.12)
    MAX_VMS  = min(len(vm_list), max(0, int((VM_AVAIL - VM_HDR_H) / VM_ROW_H)))

    RESULT_COLORS = {0: C_TEAL_V, 1: RGBColor(0xFF, 0xC1, 0x07), 2: C_RED}
    RESULT_LABELS = {0: "Sucesso", 1: "Warning", 2: "Falha"}
    PROT_LABELS   = {
        (True,  True):  "Backup + Log",
        (True,  False): "Só Log",
        (False, True):  "Só Backup",
        (False, False): "—",
    }

    if not (vm_list and MAX_VMS > 0):
        _tb(slide, "Nenhuma VM encontrada no período.",
            X0, VM_Y + Inches(0.20), CW, Inches(0.25),
            size=9, color=C_MUTED, align=PP_ALIGN.CENTER)
        return

    vm_show  = vm_list[:MAX_VMS]
    n_rows   = 1 + len(vm_show)
    vm_tbl_h = int(VM_HDR_H) + len(vm_show) * int(VM_ROW_H)
    tbl_vm   = slide.shapes.add_table(n_rows, 4, X0, VM_Y, CW, vm_tbl_h).table

    tbl_vm.columns[0].width = int(CW * 0.46)
    tbl_vm.columns[1].width = int(CW * 0.18)
    tbl_vm.columns[2].width = int(CW * 0.22)
    tbl_vm.columns[3].width = int(CW) - int(CW * 0.46) - int(CW * 0.18) - int(CW * 0.22)
    for i, row in enumerate(tbl_vm.rows):
        row.height = int(VM_HDR_H) if i == 0 else int(VM_ROW_H)

    def _cv(ri, ci, text, bold=False, sz=7.5, color=C_TEXT, bg=None, align=PP_ALIGN.LEFT):
        cell = tbl_vm.cell(ri, ci)
        if bg:
            cell.fill.solid(); cell.fill.fore_color.rgb = bg
        cell.margin_left = Pt(4); cell.margin_right = Pt(4)
        cell.margin_top  = Pt(1.5); cell.margin_bottom = Pt(1.5)
        p = cell.text_frame.paragraphs[0]; p.alignment = align
        run = p.add_run(); run.text = text
        run.font.size = Pt(sz); run.font.bold = bold; run.font.color.rgb = color

    for ci, h in enumerate(["Máquina Virtual", "Proteção", "Último Backup", "Status"]):
        _cv(0, ci, h, bold=True, color=_hdr_txt, bg=_hdr_bg, align=PP_ALIGN.CENTER)

    for ri, vm in enumerate(vm_show, start=1):
        row_bg     = C_ROW_ALT if ri % 2 == 0 else C_WHITE
        has_log    = bool(vm.get("has_log"))
        has_backup = bool(vm.get("has_backup"))
        result     = vm.get("job_result", 0)
        name       = str(vm.get("vm_name", ""))
        if len(name) > 52:
            name = name[:50] + "…"
        last_bkp = "—"
        if vm.get("last_backup"):
            try:
                lb       = vm["last_backup"]
                last_bkp = (lb.strftime("%d/%m/%Y %H:%M")
                            if hasattr(lb, "strftime") else str(lb)[:16])
            except Exception:
                last_bkp = str(vm.get("last_backup", ""))[:16]
        _cv(ri, 0, name, bg=row_bg)
        _cv(ri, 1, PROT_LABELS.get((has_log, has_backup), "—"), bg=row_bg, align=PP_ALIGN.CENTER)
        _cv(ri, 2, last_bkp, bg=row_bg, align=PP_ALIGN.CENTER)
        _cv(ri, 3, RESULT_LABELS.get(result, str(result)),
            bg=row_bg, color=RESULT_COLORS.get(result, C_TEXT), align=PP_ALIGN.CENTER)


# ─────────────────────────────────────────────────────────────────────────────
# Gestão dos slides do template
# ─────────────────────────────────────────────────────────────────────────────

def _delete_slide(prs: Presentation, idx: int) -> None:
    """Remove o slide no índice idx (mantém partes para não corromper arquivo)."""
    xml_slides = prs.slides._sldIdLst
    slide_elem = xml_slides[idx]
    rId = slide_elem.get(qn("r:id"))

    # Remover relacionamento (best-effort — não falha se indisponível)
    try:
        prs.slides.part.drop_rel(rId)
    except Exception:
        try:
            prs.slides.part._rels.pop(rId)
        except Exception:
            pass

    # Remover da lista de slides
    xml_slides.remove(slide_elem)


def _delete_middle_slides(prs: Presentation) -> None:
    """Mantém apenas o primeiro e o último slide da apresentação."""
    n = len(prs.slides)
    if n <= 2:
        return
    # Deleta de n-2 até 1 (reverso) para não deslocar índices já processados
    for i in range(n - 2, 0, -1):
        _delete_slide(prs, i)


def _move_to_idx(prs: Presentation, from_idx: int, to_idx: int) -> None:
    """Move slide de from_idx para to_idx na lista de slides."""
    xml_slides = prs.slides._sldIdLst
    elem = xml_slides[from_idx]
    xml_slides.remove(elem)
    xml_slides.insert(to_idx, elem)


# ─────────────────────────────────────────────────────────────────────────────
# API Pública
# ─────────────────────────────────────────────────────────────────────────────

def generate_weekly_pptx(
    client_name: str,
    period: str,
    kpis: dict,
    protection_kpis: dict,
    failed_jobs: list,
    chart_image_b64: Optional[str] = None,   # ignorado — usa gráfico nativo
    branding: Optional[dict] = None,
    branding_dir: Optional[str] = None,
) -> bytes:
    """
    Gera o PPTX semanal e retorna como bytes.

    Estrutura de slides:
      Slide 1 — Capa do template (HelloIT ou personalizado)
      Slide 2 — Conteúdo moderno gerado dinamicamente
      Slide N — Encerramento do template (HelloIT ou personalizado)

    Se o arquivo template.pptx não estiver disponível, gera apenas o slide
    de conteúdo em um PPTX standalone.

    Se branding["pptx_cover_path"] estiver definido, usa esse arquivo PPTX como
    template em vez do template HelloIT padrão.
    """
    # ── Determina qual template usar ──────────────────────────────────────────
    template_to_use: Optional[Path] = None

    if branding and branding.get("pptx_cover_path") and branding_dir:
        custom_tpl = Path(branding_dir) / branding["pptx_cover_path"]
        if custom_tpl.exists():
            template_to_use = custom_tpl

    if template_to_use is None and TEMPLATE_PATH.exists():
        template_to_use = TEMPLATE_PATH

    if template_to_use:
        prs = Presentation(str(template_to_use))

        # Manter apenas capa (índice 0) e encerramento (índice n-1)
        _delete_middle_slides(prs)

        # Atualizar capa com dados dinâmicos do relatório
        client_label = client_name if client_name else "Todos os clientes"
        _update_cover_slide(
            prs.slides[0],
            title="Relatório Semanal de Backup",
            client_label=client_label,
            period=period,
            branding=branding,
        )

        # Adicionar slide de conteúdo (inserido no fim — índice 2)
        blank = prs.slide_layouts[6]
        content_slide = prs.slides.add_slide(blank)
        _build_content(
            content_slide,
            client_name     = client_name,
            period          = period,
            kpis            = kpis,
            protection_kpis = protection_kpis,
            failed_jobs     = failed_jobs,
            branding        = branding,
        )

        # Mover para posição 1 (entre capa e encerramento)
        _move_to_idx(prs, len(prs.slides) - 1, 1)

    else:
        # Fallback: PPTX standalone sem template
        prs = Presentation()
        prs.slide_width  = SW
        prs.slide_height = SH
        blank = prs.slide_layouts[6]
        content_slide = prs.slides.add_slide(blank)
        _build_content(
            content_slide,
            client_name     = client_name,
            period          = period,
            kpis            = kpis,
            protection_kpis = protection_kpis,
            failed_jobs     = failed_jobs,
            branding        = branding,
        )

    buf = io.BytesIO()
    prs.save(buf)
    buf.seek(0)
    return buf.read()


# ─────────────────────────────────────────────────────────────────────────────
# Relatório MENSAL — conteúdo próprio (reaproveita as primitivas do semanal)
# ─────────────────────────────────────────────────────────────────────────────

def _build_monthly_failed_table(slide, x, y, w, avail_h, top_jobs: list,
                                tbl_hdr_color: Optional[RGBColor] = None) -> int:
    """Top-5 rotinas com erro: Rotina | Qtd. Falhas | Última Falha | Status Atual."""
    HDR_H = Inches(0.30)
    ROW_H = Inches(0.30)
    show  = (top_jobs or [])[:5]

    if not show:
        _tb(slide, "✓  Nenhuma rotina de backup regular apresentou falha no período.",
            x, y + Inches(0.40), w, Inches(0.40),
            size=10, bold=True, color=C_TEAL_V, align=PP_ALIGN.CENTER)
        return int(Inches(0.45))

    n_rows   = 1 + len(show)
    actual_h = int(HDR_H) + len(show) * int(ROW_H)
    tbl      = slide.shapes.add_table(n_rows, 4, x, y, w, actual_h).table

    tbl.columns[0].width = int(w * 0.46)   # Rotina de Backup
    tbl.columns[1].width = int(w * 0.17)   # Qtd. Falhas
    tbl.columns[2].width = int(w * 0.21)   # Última Falha
    tbl.columns[3].width = int(w * 0.16)   # Status Atual
    for i, row in enumerate(tbl.rows):
        row.height = int(HDR_H) if i == 0 else int(ROW_H)

    STATUS_CLR = {"Falha": C_RED, "Warning": C_ORANGE, "Sucesso": C_TEAL_V}

    def _c(ri, ci, text, bold=False, size=8, color=C_TEXT,
           bg: Optional[RGBColor] = None, align=PP_ALIGN.LEFT):
        cell = tbl.cell(ri, ci)
        if bg:
            cell.fill.solid(); cell.fill.fore_color.rgb = bg
        cell.margin_left = Pt(4); cell.margin_right = Pt(4)
        cell.margin_top  = Pt(1.5); cell.margin_bottom = Pt(1.5)
        p = cell.text_frame.paragraphs[0]; p.alignment = align
        run = p.add_run(); run.text = text
        run.font.size = Pt(size); run.font.bold = bold; run.font.color.rgb = color

    _hdr_bg  = tbl_hdr_color if tbl_hdr_color else C_TBL_HDR
    _hdr_txt = C_WHITE if tbl_hdr_color else C_TBL_HDR_TEXT
    for ci, h_text in enumerate(["Rotina de Backup", "Qtd. Falhas", "Última Falha", "Status Atual"]):
        _c(0, ci, h_text, bold=True, color=_hdr_txt, bg=_hdr_bg, align=PP_ALIGN.CENTER)

    for ri, job in enumerate(show, start=1):
        row_bg = C_ROW_ALT if ri % 2 == 0 else C_WHITE
        name   = job.get("job_name", "")
        if len(name) > 38:
            name = name[:36] + "…"
        status = job.get("current_status", "—")
        _c(ri, 0, name, bg=row_bg, align=PP_ALIGN.LEFT)
        _c(ri, 1, str(job.get("failed_count", 0)), bold=True, color=C_RED, bg=row_bg, align=PP_ALIGN.CENTER)
        _c(ri, 2, job.get("last_failure", "—"), bg=row_bg, align=PP_ALIGN.CENTER)
        _c(ri, 3, status, bold=True, color=STATUS_CLR.get(status, C_MUTED), bg=row_bg, align=PP_ALIGN.CENTER)

    return actual_h


def _build_monthly_content(slide,
                           client_name: str, period: str,
                           kpis: dict, protection_kpis: dict,
                           top_failed_jobs: list,
                           branding: Optional[dict] = None,
                           middle_kpi_label: str = "Transaction Logs",
                           middle_kpi_desc: str = "VMs com backup de log SQL",
                           subtitle: Optional[str] = None) -> None:
    """Slide de conteúdo do Relatório Mensal (mesma identidade visual do semanal)."""
    _teal    = _hex_to_rgb(branding["accent_color"])        if branding else TEAL
    _tbl_hdr = _hex_to_rgb(branding["table_header_color"])  if branding else C_TBL_HDR

    X0 = Inches(0.22)
    CW = SW - Inches(0.44)

    _add_frame(slide, page_num=2, branding=branding)

    _tb(slide, "Relatório Mensal de Backup",
        X0, Inches(0.36), Inches(6.50), Inches(0.26),
        size=10.5, bold=True, color=C_TEXT)
    clabel = client_name if client_name else "Todos os clientes"
    _tb(slide, f"{clabel}  |  {period}",
        X0, Inches(0.63), Inches(9.40), Inches(0.20),
        size=8, color=C_MUTED)
    _rect(slide, X0, Inches(0.84), CW, Inches(0.018), _teal)

    # Cards superiores (VMs Protegidas | Transaction Logs | Jobs de Backup)
    KPI_Y = Inches(0.91); KPI_H = Inches(0.67); GAP = Inches(0.09)
    KPI_W = (CW - 2 * GAP) // 3
    cards = [
        (str(protection_kpis.get("protected_vms", 0)),
         "VMs Protegidas", "Máquinas com backup no período", C_BLUE, C_BG_BLUE),
        (str(protection_kpis.get("transaction_log_vms", 0)),
         middle_kpi_label, middle_kpi_desc, C_ORANGE, C_BG_ORANGE),
        (str(protection_kpis.get("backup_jobs", 0)),
         "Jobs de Backup", "Rotinas de backup no período", C_TEAL_V, C_BG_TEAL),
    ]
    for i, (val, lbl, dsc, acc, bg) in enumerate(cards):
        _kpi_card(slide, X0 + i * (KPI_W + GAP), KPI_Y, KPI_W, KPI_H, val, lbl, dsc, acc, bg)

    SEP_Y = KPI_Y + KPI_H + Inches(0.06)
    _rect(slide, X0, SEP_Y, CW, Inches(0.012), C_GRAY_LINE)
    MAIN_Y = SEP_Y + Inches(0.05)

    LEFT_W  = Inches(3.26)
    RIGHT_X = X0 + LEFT_W + Inches(0.12)
    RIGHT_W = SW - RIGHT_X - Inches(0.12)

    failed_n    = kpis.get("failed", 0)
    success_n   = kpis.get("success", 0)
    failed_pct  = kpis.get("failed_pct", 0.0)
    success_pct = kpis.get("success_pct", 0.0)
    total_exec  = kpis.get("total", failed_n + success_n)

    # PAINEL ESQUERDO: donut sucesso × falha (por execução, exclui logs)
    _tb(slide, "Distribuição dos Backups",
        X0, MAIN_Y + Inches(0.02), LEFT_W, Inches(0.20),
        size=9, bold=True, color=C_TEXT)
    _tb(slide, subtitle or "Execuções regulares no período  •  Transaction Logs não considerados",
        X0, MAIN_Y + Inches(0.21), LEFT_W, Inches(0.14),
        size=6.5, italic=True, color=C_MUTED, wrap=False)

    CHART_S = Inches(2.30)
    chart_x = X0 + (LEFT_W - CHART_S) // 2
    chart_y = MAIN_Y + Inches(0.38)
    _add_donut_chart(slide, chart_x, chart_y, CHART_S, CHART_S, failed_n, success_n)

    hole_x = chart_x + Inches(0.58)
    hole_y = chart_y + CHART_S / 2 - Inches(0.25)
    hole_w = CHART_S - Inches(1.16)
    _tb(slide, f"{success_pct:.0f}%",
        hole_x, hole_y, hole_w, Inches(0.32),
        size=16, bold=True, color=C_TEAL_V, align=PP_ALIGN.CENTER)
    _tb(slide, "sucesso",
        hole_x, hole_y + Inches(0.32), hole_w, Inches(0.18),
        size=7, color=C_MUTED, align=PP_ALIGN.CENTER)

    LEG_Y = chart_y + CHART_S + Inches(0.07); LEG_H = Inches(0.18); D = Inches(0.10)
    half_w = LEFT_W / 2
    _oval(slide, X0, LEG_Y + (LEG_H - D) / 2, D, C_RED)
    _tb(slide, f"Com Falha ({failed_n})",
        X0 + D + Inches(0.04), LEG_Y, half_w - D - Inches(0.06), LEG_H,
        size=7.5, color=C_TEXT)
    _oval(slide, X0 + half_w, LEG_Y + (LEG_H - D) / 2, D, C_TEAL_V)
    _tb(slide, f"Com Sucesso ({success_n})",
        X0 + half_w + D + Inches(0.04), LEG_Y, half_w - D - Inches(0.06), LEG_H,
        size=7.5, color=C_TEXT)

    # PAINEL DIREITO: Top 5 rotinas com erro + mini-KPIs + total
    _tb(slide, "Top 5 rotinas com erro no período",
        RIGHT_X, MAIN_Y + Inches(0.02), RIGHT_W, Inches(0.20),
        size=9, bold=True, color=C_TEXT)
    _tb(slide, "Rotinas de backup regular com mais falhas no período.",
        RIGHT_X, MAIN_Y + Inches(0.21), RIGHT_W, Inches(0.14),
        size=6.5, italic=True, color=C_MUTED, wrap=False)

    TABLE_Y = MAIN_Y + Inches(0.38)
    AVAIL_H = FOOTER_Y - TABLE_Y - Inches(0.18)
    tbl_actual_h = _build_monthly_failed_table(
        slide, RIGHT_X, TABLE_Y, RIGHT_W, AVAIL_H, top_failed_jobs,
        tbl_hdr_color=_tbl_hdr if branding else None,
    )
    tbl_bottom = TABLE_Y + tbl_actual_h

    MINI_H = Inches(0.52); MINI_GAP = Inches(0.06)
    MINI_W = (RIGHT_W - MINI_GAP) / 2
    mini_y = tbl_bottom + Inches(0.25)
    if mini_y + MINI_H < FOOTER_Y - Inches(0.18):
        _mini_kpi(slide, RIGHT_X, mini_y, MINI_W, MINI_H,
                  str(failed_n), f"Com Falha ({failed_pct:.1f}%)", C_RED, C_BG_RED)
        _mini_kpi(slide, RIGHT_X + MINI_W + MINI_GAP, mini_y, MINI_W, MINI_H,
                  str(success_n), f"Com Sucesso ({success_pct:.1f}%)", C_TEAL_V, C_BG_TEAL)
        total_y = mini_y + MINI_H + Inches(0.08)
        if total_y + Inches(0.18) < FOOTER_Y - Inches(0.12):
            _tb(slide,
                f"Total de execuções analisadas no período: {total_exec}",
                RIGHT_X, total_y, RIGHT_W, Inches(0.18),
                size=7, italic=True, color=C_MUTED, align=PP_ALIGN.CENTER)


def generate_monthly_backup_pptx(
    client_name: str,
    period: str,
    kpis: dict,
    protection_kpis: dict,
    top_failed_jobs: list,
    branding: Optional[dict] = None,
    branding_dir: Optional[str] = None,
    middle_kpi_label: str = "Transaction Logs",
    middle_kpi_desc: str = "VMs com backup de log SQL",
    subtitle: Optional[str] = None,
) -> bytes:
    """Gera o PPTX mensal (mesma base visual do semanal; conteúdo mensal próprio)."""
    template_to_use: Optional[Path] = None
    if branding and branding.get("pptx_cover_path") and branding_dir:
        custom_tpl = Path(branding_dir) / branding["pptx_cover_path"]
        if custom_tpl.exists():
            template_to_use = custom_tpl
    if template_to_use is None and TEMPLATE_PATH.exists():
        template_to_use = TEMPLATE_PATH

    if template_to_use:
        prs = Presentation(str(template_to_use))
        _delete_middle_slides(prs)
        _update_cover_slide(
            prs.slides[0],
            title="Relatório Mensal de Backup",
            client_label=client_name if client_name else "Todos os clientes",
            period=period,
            branding=branding,
        )
        blank = prs.slide_layouts[6]
        content_slide = prs.slides.add_slide(blank)
        _build_monthly_content(
            content_slide,
            client_name=client_name, period=period,
            kpis=kpis, protection_kpis=protection_kpis,
            top_failed_jobs=top_failed_jobs, branding=branding,
            middle_kpi_label=middle_kpi_label, middle_kpi_desc=middle_kpi_desc, subtitle=subtitle,
        )
        _move_to_idx(prs, len(prs.slides) - 1, 1)
    else:
        prs = Presentation()
        prs.slide_width  = SW
        prs.slide_height = SH
        blank = prs.slide_layouts[6]
        content_slide = prs.slides.add_slide(blank)
        _build_monthly_content(
            content_slide,
            client_name=client_name, period=period,
            kpis=kpis, protection_kpis=protection_kpis,
            top_failed_jobs=top_failed_jobs, branding=branding,
            middle_kpi_label=middle_kpi_label, middle_kpi_desc=middle_kpi_desc, subtitle=subtitle,
        )

    buf = io.BytesIO()
    prs.save(buf)
    buf.seek(0)
    return buf.read()


# ─────────────────────────────────────────────────────────────────────────────
# API Pública — Proteção de VMs
# ─────────────────────────────────────────────────────────────────────────────

def generate_protection_pptx(
    client_name: str,
    period: str,
    kpis: dict,
    evolution: list,
    vm_list: list,
    branding: Optional[dict] = None,
    branding_dir: Optional[str] = None,
) -> bytes:
    """
    Gera o PPTX de Proteção de VMs e retorna como bytes.

    Estrutura de slides:
      Slide 1 — Capa dinâmica ("Relatório de Proteção de VMs")
      Slide 2 — Conteúdo: KPIs + evolução + lista de VMs
      Slide N — Encerramento do template
    """
    template_to_use: Optional[Path] = None

    if branding and branding.get("pptx_cover_path") and branding_dir:
        custom_tpl = Path(branding_dir) / branding["pptx_cover_path"]
        if custom_tpl.exists():
            template_to_use = custom_tpl

    if template_to_use is None and TEMPLATE_PATH.exists():
        template_to_use = TEMPLATE_PATH

    client_label = client_name if client_name else "Todos os clientes"

    if template_to_use:
        prs = Presentation(str(template_to_use))
        _delete_middle_slides(prs)

        # Atualizar capa com título de proteção de VMs
        _update_cover_slide(
            prs.slides[0],
            title="Relatório de Proteção de VMs",
            client_label=client_label,
            period=period,
            branding=branding,
        )

        # Slide de conteúdo
        blank         = prs.slide_layouts[6]
        content_slide = prs.slides.add_slide(blank)
        _build_protection_content(
            content_slide,
            client_name=client_name,
            period=period,
            kpis=kpis,
            evolution=evolution,
            vm_list=vm_list,
            branding=branding,
        )
        _move_to_idx(prs, len(prs.slides) - 1, 1)

        # Slide 3 — lista de VMs (separado, para não competir com o gráfico)
        if vm_list:
            vm_slide = prs.slides.add_slide(blank)
            _build_protection_vm_slide(
                vm_slide, client_name=client_name, period=period,
                kpis=kpis, vm_list=vm_list, branding=branding,
            )
            _move_to_idx(prs, len(prs.slides) - 1, 2)

    else:
        # Fallback: PPTX standalone sem template
        prs = Presentation()
        prs.slide_width  = SW
        prs.slide_height = SH
        blank         = prs.slide_layouts[6]
        content_slide = prs.slides.add_slide(blank)
        _build_protection_content(
            content_slide,
            client_name=client_name,
            period=period,
            kpis=kpis,
            evolution=evolution,
            vm_list=vm_list,
            branding=branding,
        )
        if vm_list:
            vm_slide = prs.slides.add_slide(blank)
            _build_protection_vm_slide(
                vm_slide, client_name=client_name, period=period,
                kpis=kpis, vm_list=vm_list, branding=branding,
            )

    buf = io.BytesIO()
    prs.save(buf)
    buf.seek(0)
    return buf.read()


# ─────────────────────────────────────────────────────────────────────────────
# Slide de auditoria standalone
# ─────────────────────────────────────────────────────────────────────────────

def _txt(shape, text, size_pt=11, bold=False, color=None, align=PP_ALIGN.LEFT):
    tf = shape.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    p.alignment = align
    run = p.add_run()
    run.text = text
    run.font.size = Pt(size_pt)
    run.font.bold = bold
    if color:
        run.font.color.rgb = color


def _add_label_value(slide, x, y, label, value, label_color=None, val_color=None):
    """Bloco label (topo, pequeno) + valor (embaixo, maior)."""
    w = Inches(1.6)
    lbl = slide.shapes.add_textbox(x, y, w, Inches(0.22))
    tf = lbl.text_frame; tf.word_wrap = False
    run = tf.paragraphs[0].add_run()
    run.text = label; run.font.size = Pt(8); run.font.bold = False
    run.font.color.rgb = label_color or C_MUTED

    val = slide.shapes.add_textbox(x, y + Inches(0.22), w, Inches(0.36))
    tf2 = val.text_frame; tf2.word_wrap = False
    run2 = tf2.paragraphs[0].add_run()
    run2.text = str(value); run2.font.size = Pt(22); run2.font.bold = True
    run2.font.color.rgb = val_color or C_TEXT


# ─────────────────────────────────────────────────────────────────────────────
# Relatório de Offload / Capacity Tier
# ─────────────────────────────────────────────────────────────────────────────

def _build_offload_content(slide,
                            client_name: str, period: str,
                            kpis: dict,
                            top_failed_jobs: list,
                            by_category: list,
                            branding: Optional[dict] = None) -> None:
    """Slide de conteúdo do Relatório de Offload (mesmo padrão visual do Semanal)."""
    _teal    = _hex_to_rgb(branding["accent_color"])        if branding else TEAL
    _tbl_hdr = _hex_to_rgb(branding["table_header_color"])  if branding else C_TBL_HDR
    _primary = _hex_to_rgb(branding["primary_color"])       if branding else C_BLUE

    X0 = Inches(0.22)
    CW = SW - Inches(0.44)

    _add_frame(slide, page_num=2, branding=branding)

    # ── Cabeçalho ─────────────────────────────────────────────────────────────
    _tb(slide, "Relatório de Offload / Capacity Tier",
        X0, Inches(0.36), Inches(7.0), Inches(0.26),
        size=10.5, bold=True, color=C_TEXT)
    clabel = client_name if client_name else "Todos os clientes"
    _tb(slide, f"{clabel}  |  {period}",
        X0, Inches(0.63), Inches(9.40), Inches(0.20),
        size=8, color=C_MUTED)
    _rect(slide, X0, Inches(0.84), CW, Inches(0.018), _teal)

    # ── 4 KPI cards ───────────────────────────────────────────────────────────
    KPI_Y = Inches(0.91); KPI_H = Inches(0.67); GAP = Inches(0.07)
    KPI_W = (CW - 3 * GAP) / 4

    total       = kpis.get("total", 0)
    unique_jobs = kpis.get("unique_jobs", 0)
    failed      = kpis.get("failed", 0)
    failed_pct  = kpis.get("failed_pct", 0.0)

    kpi_cards = [
        (str(total),       "Execuções",      "Total no período",        _primary,  C_BG_BLUE),
        (str(unique_jobs), "Rotinas",         "Rotinas únicas",          C_TEAL_V,  C_BG_TEAL),
        (str(failed),      "Falhas",          "Execuções com falha",     C_RED,     RGBColor(0xFF,0xEB,0xEE)),
        (f"{failed_pct:.1f}%", "Taxa de Falha", "Falhas / total",        C_ORANGE,  C_BG_ORANGE),
    ]
    for i, (val, lbl, dsc, acc, bg) in enumerate(kpi_cards):
        _kpi_card(slide, X0 + i * (KPI_W + GAP), KPI_Y, KPI_W, KPI_H, val, lbl, dsc, acc, bg)

    SEP_Y = KPI_Y + KPI_H + Inches(0.06)
    _rect(slide, X0, SEP_Y, CW, Inches(0.012), C_GRAY_LINE)
    MAIN_Y = SEP_Y + Inches(0.05)

    LEFT_W  = Inches(3.10)
    RIGHT_X = X0 + LEFT_W + Inches(0.12)
    RIGHT_W = SW - RIGHT_X - Inches(0.12)

    success_n   = kpis.get("success", 0) + kpis.get("warning", 0)
    failed_n    = kpis.get("failed", 0)
    success_pct = round(success_n / total * 100, 1) if total else 0.0
    f_pct       = round(failed_n  / total * 100, 1) if total else 0.0

    # ── PAINEL ESQUERDO: donut falha × sucesso ────────────────────────────────
    _tb(slide, "Distribuição das Execuções",
        X0, MAIN_Y + Inches(0.02), LEFT_W, Inches(0.20),
        size=9, bold=True, color=C_TEXT)
    _tb(slide, "Todas as execuções de Offload no período (Warning incluso no Sucesso)",
        X0, MAIN_Y + Inches(0.21), LEFT_W, Inches(0.14),
        size=6.5, italic=True, color=C_MUTED, wrap=False)

    CHART_S = Inches(2.10)
    chart_x = X0 + (LEFT_W - CHART_S) / 2
    chart_y = MAIN_Y + Inches(0.38)

    _add_donut_chart(slide, chart_x, chart_y, CHART_S, CHART_S, failed_n, success_n)

    # Mini KPIs abaixo do donut
    LBL_Y = chart_y + CHART_S + Inches(0.08)
    half  = LEFT_W / 2
    for j, (pct, label, clr) in enumerate([
        (f"{f_pct:.1f}%",       "Falha",   C_RED),
        (f"{success_pct:.1f}%", "Sucesso",  GREEN_DOT),
    ]):
        xi = X0 + j * half
        _tb(slide, pct,   xi, LBL_Y,              half, Inches(0.22), size=13, bold=True,  color=clr,   align=PP_ALIGN.CENTER)
        _tb(slide, label, xi, LBL_Y + Inches(0.22), half, Inches(0.14), size=7.5, bold=False, color=C_MUTED, align=PP_ALIGN.CENTER)

    # Aviso Warning (se houver)
    warning_n = kpis.get("warning", 0)
    if warning_n:
        _tb(slide, f"⚠ {warning_n} execuções com aviso (Warning) contabilizadas no Sucesso",
            X0, LBL_Y + Inches(0.38), LEFT_W, Inches(0.16),
            size=6, italic=True, color=C_ORANGE, align=PP_ALIGN.CENTER)

    # ── PAINEL DIREITO: top rotinas com falha ─────────────────────────────────
    show = top_failed_jobs[:8]

    _tb(slide, "Rotinas com Mais Falhas",
        RIGHT_X, MAIN_Y + Inches(0.02), RIGHT_W, Inches(0.20),
        size=9, bold=True, color=C_TEXT)
    _tb(slide, "Top rotinas por número de execuções com falha no período",
        RIGHT_X, MAIN_Y + Inches(0.21), RIGHT_W, Inches(0.14),
        size=6.5, italic=True, color=C_MUTED, wrap=False)

    TBL_Y   = MAIN_Y + Inches(0.38)
    TBL_AVH = FOOTER_Y - TBL_Y - Inches(0.04)

    if not show:
        _tb(slide, "✓ Nenhuma rotina com falha no período.",
            RIGHT_X, TBL_Y, RIGHT_W, Inches(0.22),
            size=8.5, color=GREEN_DOT)
    else:
        n_rows   = len(show) + 1  # +1 header
        row_h    = min(TBL_AVH / n_rows, Inches(0.30))
        tbl_h    = row_h * n_rows
        col_ws   = [RIGHT_W * p for p in (0.44, 0.12, 0.24, 0.20)]

        tbl = slide.shapes.add_table(n_rows, 4, RIGHT_X, TBL_Y,
                                     int(RIGHT_W), int(tbl_h)).table
        for ci, w in enumerate(col_ws):
            tbl.columns[ci].width = int(w)
        for ri in range(n_rows):
            tbl.rows[ri].height = int(row_h)

        def _c(ri, ci, text, bold=False, size=7.5, color=C_TEXT,
               bg: Optional[RGBColor] = None, align=PP_ALIGN.LEFT):
            cell = tbl.cell(ri, ci)
            if bg:
                cell.fill.solid(); cell.fill.fore_color.rgb = bg
            cell.margin_left = Pt(3); cell.margin_right = Pt(3)
            cell.margin_top  = Pt(1.5); cell.margin_bottom = Pt(1.5)
            p = cell.text_frame.paragraphs[0]; p.alignment = align
            run = p.add_run(); run.text = text
            run.font.size = Pt(size); run.font.bold = bold; run.font.color.rgb = color

        _hdr_bg  = _tbl_hdr
        _hdr_txt = C_WHITE
        for ci, h in enumerate(["Rotina", "Falhas", "Última Falha", "Categoria"]):
            _c(0, ci, h, bold=True, color=_hdr_txt, bg=_hdr_bg, align=PP_ALIGN.CENTER)

        for ri, job in enumerate(show, start=1):
            row_bg = C_ROW_ALT if ri % 2 == 0 else C_WHITE
            name = job.get("job_name", "")
            if len(name) > 40:
                name = name[:38] + "…"
            _c(ri, 0, name,                                          bg=row_bg)
            _c(ri, 1, str(job.get("failed_count", 0)), bold=True, color=C_RED, bg=row_bg, align=PP_ALIGN.CENTER)
            _c(ri, 2, job.get("last_failed", "—"),                  bg=row_bg, align=PP_ALIGN.CENTER)
            _c(ri, 3, job.get("category", "—"),                     bg=row_bg)


def _add_failures_area_chart(slide, x, y, w, h, daily_series: list,
                             branding: Optional[dict] = None) -> None:
    """Gráfico de área (onda) com a quantidade de falhas por dia.

    Usa a API do python-pptx para fill/linha (sem XML manual frágil) — evita o
    prompt de 'reparo' do PowerPoint.
    """
    from pptx.chart.data import CategoryChartData
    from pptx.enum.chart import XL_CHART_TYPE

    # Thinning de rótulos: no máx. ~24 labels visíveis (resto em branco) p/ não poluir
    n = len(daily_series)
    step = max(1, n // 24)
    labels = [ (d.get("label", "") if (i % step == 0) else "")
               for i, d in enumerate(daily_series) ]
    values = [ d.get("failed", 0) for d in daily_series ]

    cd = CategoryChartData()
    cd.categories = labels
    cd.add_series("Falhas", values)

    frame = slide.shapes.add_chart(XL_CHART_TYPE.AREA, x, y, w, h, cd)
    chart = frame.chart
    chart.has_title  = False
    chart.has_legend = False

    try:
        chart.category_axis.tick_labels.font.size = Pt(6)
        va = chart.value_axis
        va.tick_labels.font.size = Pt(7)
        va.minimum_scale = 0
        va.has_major_gridlines = True
    except Exception:
        pass

    try:
        frame.line.fill.background()
    except Exception:
        pass

    # Cores via API (sem XML manual): preenchimento vermelho claro + linha vermelha forte
    try:
        ser = chart.series[0]
        ser.format.fill.solid()
        ser.format.fill.fore_color.rgb = RGBColor(0xF8, 0xC9, 0xCE)
        ser.format.line.color.rgb      = RGBColor(0xDC, 0x35, 0x45)
        ser.format.line.width          = Pt(1.5)
    except Exception:
        pass


def _build_offload_wave_slide(slide, client_name: str, period: str,
                              daily_series: list,
                              branding: Optional[dict] = None) -> None:
    """Slide dedicado: onda de 'Falhas por Dia' no período."""
    _teal = _hex_to_rgb(branding["accent_color"]) if branding else TEAL

    X0 = Inches(0.22)
    CW = SW - Inches(0.44)

    _add_frame(slide, page_num=3, branding=branding)

    _tb(slide, "Falhas por Dia",
        X0, Inches(0.36), Inches(7.0), Inches(0.26),
        size=10.5, bold=True, color=C_TEXT)
    clabel = client_name if client_name else "Todos os clientes"
    total_fail = sum(d.get("failed", 0) for d in daily_series)
    peak = max(daily_series, key=lambda d: d.get("failed", 0)) if daily_series else None
    sub = f"{clabel}  |  {period}"
    if peak and peak.get("failed", 0) > 0:
        sub += f"   ·   {total_fail} falhas no período · pico {peak['failed']} em {peak.get('label','')}"
    _tb(slide, sub,
        X0, Inches(0.63), CW, Inches(0.20),
        size=8, color=C_MUTED, wrap=False)
    _rect(slide, X0, Inches(0.84), CW, Inches(0.018), _teal)

    CHART_Y = Inches(1.05)
    CHART_H = FOOTER_Y - CHART_Y - Inches(0.10)

    if not daily_series or total_fail == 0:
        _tb(slide, "✓ Nenhuma falha registrada no período.",
            X0, CHART_Y + Inches(0.4), CW, Inches(0.3),
            size=11, color=GREEN_DOT, align=PP_ALIGN.CENTER)
        return

    _add_failures_area_chart(slide, X0, CHART_Y, CW, CHART_H, daily_series, branding)


def generate_offload_pptx(
    client_name: str,
    period: str,
    kpis: dict,
    top_failed_jobs: list,
    by_category: list,
    daily_series: Optional[list] = None,
    branding: Optional[dict] = None,
    branding_dir: Optional[str] = None,
) -> bytes:
    """
    Gera o PPTX de Relatório de Offload e retorna como bytes.

    Estrutura de slides:
      Slide 1 — Capa (template HelloIT ou customizado)
      Slide 2 — Conteúdo: KPIs + donut + top rotinas
      Slide N — Encerramento
    """
    template_to_use: Optional[Path] = None

    if branding and branding.get("pptx_cover_path") and branding_dir:
        custom_tpl = Path(branding_dir) / branding["pptx_cover_path"]
        if custom_tpl.exists():
            template_to_use = custom_tpl

    if template_to_use is None and TEMPLATE_PATH.exists():
        template_to_use = TEMPLATE_PATH

    if template_to_use:
        prs = Presentation(str(template_to_use))
        _delete_middle_slides(prs)
        client_label = client_name if client_name else "Todos os clientes"
        _update_cover_slide(
            prs.slides[0],
            title="Relatório de Offload / Capacity Tier",
            client_label=client_label,
            period=period,
            branding=branding,
        )
        blank = prs.slide_layouts[6]
        content_slide = prs.slides.add_slide(blank)
        _build_offload_content(
            content_slide,
            client_name=client_name, period=period,
            kpis=kpis, top_failed_jobs=top_failed_jobs,
            by_category=by_category, branding=branding,
        )
        _move_to_idx(prs, len(prs.slides) - 1, 1)
        # Slide de onda (Falhas por Dia) — inserido após o conteúdo, antes do encerramento
        if daily_series:
            wave_slide = prs.slides.add_slide(blank)
            _build_offload_wave_slide(
                wave_slide, client_name=client_name, period=period,
                daily_series=daily_series, branding=branding,
            )
            _move_to_idx(prs, len(prs.slides) - 1, 2)
    else:
        prs = Presentation()
        prs.slide_width  = SW
        prs.slide_height = SH
        blank = prs.slide_layouts[6]
        content_slide = prs.slides.add_slide(blank)
        _build_offload_content(
            content_slide,
            client_name=client_name, period=period,
            kpis=kpis, top_failed_jobs=top_failed_jobs,
            by_category=by_category, branding=branding,
        )
        if daily_series:
            wave_slide = prs.slides.add_slide(blank)
            _build_offload_wave_slide(
                wave_slide, client_name=client_name, period=period,
                daily_series=daily_series, branding=branding,
            )

    buf = io.BytesIO()
    prs.save(buf)
    buf.seek(0)
    return buf.read()


def _build_backup_content(slide, client_name: str, period: str, kpis: dict,
                          top_slowest: list, branding: Optional[dict] = None) -> None:
    """Slide de conteúdo do Relatório de Performance de Backups."""
    _teal    = _hex_to_rgb(branding["accent_color"])       if branding else TEAL
    _tbl_hdr = _hex_to_rgb(branding["table_header_color"]) if branding else C_TBL_HDR
    _primary = _hex_to_rgb(branding["primary_color"])      if branding else C_BLUE

    X0 = Inches(0.22); CW = SW - Inches(0.44)
    _add_frame(slide, page_num=2, branding=branding)

    _tb(slide, "Relatório de Performance de Backups",
        X0, Inches(0.36), Inches(7.0), Inches(0.26), size=10.5, bold=True, color=C_TEXT)
    clabel = client_name if client_name else "Todos os clientes"
    _tb(slide, f"{clabel}  |  {period}", X0, Inches(0.63), Inches(9.40), Inches(0.20),
        size=8, color=C_MUTED)
    _rect(slide, X0, Inches(0.84), CW, Inches(0.018), _teal)

    KPI_Y = Inches(0.91); KPI_H = Inches(0.67); GAP = Inches(0.07)
    KPI_W = (CW - 3 * GAP) / 4
    total = kpis.get("total", 0); routines = kpis.get("unique_routines", 0)
    failed = kpis.get("failed", 0); failed_pct = kpis.get("failed_pct", 0.0)
    kpi_cards = [
        (str(total),    "Execuções", "Total no período",     _primary, C_BG_BLUE),
        (str(routines), "Rotinas",   "VMs distintas",        C_TEAL_V, C_BG_TEAL),
        (str(failed),   "Falhas",    "Execuções com falha",  C_RED,    RGBColor(0xFF,0xEB,0xEE)),
        (f"{failed_pct:.1f}%", "Taxa de Falha", "Falhas / total", C_ORANGE, C_BG_ORANGE),
    ]
    for i, (val, lbl, dsc, acc, bg) in enumerate(kpi_cards):
        _kpi_card(slide, X0 + i * (KPI_W + GAP), KPI_Y, KPI_W, KPI_H, val, lbl, dsc, acc, bg)

    SEP_Y = KPI_Y + KPI_H + Inches(0.06)
    _rect(slide, X0, SEP_Y, CW, Inches(0.012), C_GRAY_LINE)
    MAIN_Y = SEP_Y + Inches(0.05)

    LEFT_W  = Inches(3.10)
    RIGHT_X = X0 + LEFT_W + Inches(0.12)
    RIGHT_W = SW - RIGHT_X - Inches(0.12)

    success_n = kpis.get("success", 0) + kpis.get("warning", 0)
    failed_n  = kpis.get("failed", 0)
    success_pct = round(success_n / total * 100, 1) if total else 0.0
    f_pct       = round(failed_n  / total * 100, 1) if total else 0.0

    _tb(slide, "Distribuição das Execuções", X0, MAIN_Y + Inches(0.02), LEFT_W, Inches(0.20),
        size=9, bold=True, color=C_TEXT)
    _tb(slide, f"Duração média: {kpis.get('avg_duration_fmt', '—')}  ·  Warning incluso no Sucesso",
        X0, MAIN_Y + Inches(0.21), LEFT_W, Inches(0.14), size=6.5, italic=True, color=C_MUTED, wrap=False)

    CHART_S = Inches(2.10)
    chart_x = X0 + (LEFT_W - CHART_S) / 2
    chart_y = MAIN_Y + Inches(0.38)
    _add_donut_chart(slide, chart_x, chart_y, CHART_S, CHART_S, failed_n, success_n)

    LBL_Y = chart_y + CHART_S + Inches(0.08); half = LEFT_W / 2
    for j, (pct, label, clr) in enumerate([
        (f"{f_pct:.1f}%", "Falha", C_RED), (f"{success_pct:.1f}%", "Sucesso", GREEN_DOT)]):
        xi = X0 + j * half
        _tb(slide, pct,   xi, LBL_Y, half, Inches(0.22), size=13, bold=True, color=clr, align=PP_ALIGN.CENTER)
        _tb(slide, label, xi, LBL_Y + Inches(0.22), half, Inches(0.14), size=7.5, color=C_MUTED, align=PP_ALIGN.CENTER)

    # ── PAINEL DIREITO: rotinas mais lentas ───────────────────────────────────
    show = top_slowest[:8]
    _tb(slide, "Rotinas Mais Lentas", RIGHT_X, MAIN_Y + Inches(0.02), RIGHT_W, Inches(0.20),
        size=9, bold=True, color=C_TEXT)
    _tb(slide, "Top VMs por duração mediana de backup no período",
        RIGHT_X, MAIN_Y + Inches(0.21), RIGHT_W, Inches(0.14), size=6.5, italic=True, color=C_MUTED, wrap=False)

    TBL_Y = MAIN_Y + Inches(0.38); TBL_AVH = FOOTER_Y - TBL_Y - Inches(0.04)
    if not show:
        _tb(slide, "Sem dados de duração no período.", RIGHT_X, TBL_Y, RIGHT_W, Inches(0.22),
            size=8.5, color=C_MUTED)
    else:
        n_rows = len(show) + 1
        row_h  = min(TBL_AVH / n_rows, Inches(0.30)); tbl_h = row_h * n_rows
        col_ws = [RIGHT_W * p for p in (0.46, 0.20, 0.18, 0.16)]
        tbl = slide.shapes.add_table(n_rows, 4, RIGHT_X, TBL_Y, int(RIGHT_W), int(tbl_h)).table
        for ci, w in enumerate(col_ws): tbl.columns[ci].width = int(w)
        for ri in range(n_rows): tbl.rows[ri].height = int(row_h)

        def _c(ri, ci, text, bold=False, size=7.5, color=C_TEXT, bg=None, align=PP_ALIGN.LEFT):
            cell = tbl.cell(ri, ci)
            if bg: cell.fill.solid(); cell.fill.fore_color.rgb = bg
            cell.margin_left = Pt(3); cell.margin_right = Pt(3)
            cell.margin_top = Pt(1.5); cell.margin_bottom = Pt(1.5)
            p = cell.text_frame.paragraphs[0]; p.alignment = align
            run = p.add_run(); run.text = text
            run.font.size = Pt(size); run.font.bold = bold; run.font.color.rgb = color

        for ci, h in enumerate(["Rotina (VM)", "Mediana", "Máx.", "Exec."]):
            _c(0, ci, h, bold=True, color=C_WHITE, bg=_tbl_hdr, align=PP_ALIGN.CENTER)
        for ri, rt in enumerate(show, start=1):
            row_bg = C_ROW_ALT if ri % 2 == 0 else C_WHITE
            name = rt.get("vm_name", "")
            if len(name) > 28: name = name[:26] + "…"
            _c(ri, 0, name, bg=row_bg)
            _c(ri, 1, rt.get("median_fmt", "—"), bold=True, color=_primary, bg=row_bg, align=PP_ALIGN.CENTER)
            _c(ri, 2, rt.get("max_fmt", "—"), bg=row_bg, align=PP_ALIGN.CENTER)
            _c(ri, 3, str(rt.get("executions", 0)), bg=row_bg, align=PP_ALIGN.CENTER)


def generate_backup_pptx(
    client_name: str, period: str, kpis: dict,
    top_slowest: list, top_failed: list,
    daily_series: Optional[list] = None,
    branding: Optional[dict] = None, branding_dir: Optional[str] = None,
) -> bytes:
    """PPTX do Relatório de Performance de Backups (capa + conteúdo + onda + encerramento)."""
    template_to_use: Optional[Path] = None
    if branding and branding.get("pptx_cover_path") and branding_dir:
        custom_tpl = Path(branding_dir) / branding["pptx_cover_path"]
        if custom_tpl.exists():
            template_to_use = custom_tpl
    if template_to_use is None and TEMPLATE_PATH.exists():
        template_to_use = TEMPLATE_PATH

    if template_to_use:
        prs = Presentation(str(template_to_use))
        _delete_middle_slides(prs)
        _update_cover_slide(prs.slides[0], title="Relatório de Performance de Backups",
                            client_label=client_name or "Todos os clientes",
                            period=period, branding=branding)
        blank = prs.slide_layouts[6]
        cs = prs.slides.add_slide(blank)
        _build_backup_content(cs, client_name=client_name, period=period, kpis=kpis,
                              top_slowest=top_slowest, branding=branding)
        _move_to_idx(prs, len(prs.slides) - 1, 1)
        if daily_series:
            ws = prs.slides.add_slide(blank)
            _build_offload_wave_slide(ws, client_name=client_name, period=period,
                                      daily_series=daily_series, branding=branding)
            _move_to_idx(prs, len(prs.slides) - 1, 2)
    else:
        prs = Presentation(); prs.slide_width = SW; prs.slide_height = SH
        blank = prs.slide_layouts[6]
        cs = prs.slides.add_slide(blank)
        _build_backup_content(cs, client_name=client_name, period=period, kpis=kpis,
                              top_slowest=top_slowest, branding=branding)
        if daily_series:
            ws = prs.slides.add_slide(blank)
            _build_offload_wave_slide(ws, client_name=client_name, period=period,
                                      daily_series=daily_series, branding=branding)

    buf = io.BytesIO(); prs.save(buf); buf.seek(0)
    return buf.read()


def generate_audit_pptx(
    client_name: str,
    period: str,
    kpis: dict,
    top_operators: list,   # [(name, count), ...]
    sensitive_items: list, # [{"job_name", "event_type", "operator", "time", "impact"}, ...]
    top_jobs: list,        # [(job_name, count), ...]
    branding: Optional[dict] = None,
    branding_dir: Optional[str] = None,
) -> bytes:
    """Gera um slide executivo de auditoria de jobs no padrão HelloIT (ou branding configurado)."""
    _green = _hex_to_rgb(branding["primary_color"]) if branding else GREEN_DOT
    _teal  = _hex_to_rgb(branding["accent_color"])  if branding else TEAL
    _company = branding.get("company_name", "HelloIT") if branding else "HelloIT"

    prs = Presentation()
    prs.slide_width  = SW
    prs.slide_height = SH
    blank = prs.slide_layouts[6]
    slide = prs.slides.add_slide(blank)

    # ── Fundo branco ──────────────────────────────────────────────────────────
    bg = _rounded_rect(slide, 0, 0, SW, SH, fill=C_WHITE, rounding=0)

    # ── Topo colorido ─────────────────────────────────────────────────────────
    header_h = Inches(0.55)
    _rounded_rect(slide, 0, 0, SW, header_h, fill=_green, rounding=0)

    # Título
    t = slide.shapes.add_textbox(Inches(0.25), Inches(0.06), Inches(7), Inches(0.44))
    _txt(t, f"Auditoria de Jobs  |  {client_name}  |  {period}",
         size_pt=13, bold=True, color=C_WHITE)

    # Subtítulo direito
    sub = slide.shapes.add_textbox(Inches(7.8), Inches(0.1), Inches(2.1), Inches(0.35))
    _txt(sub, f"{_company}  ●  Veeam Backup", size_pt=9, color=_teal, align=PP_ALIGN.RIGHT)

    # ── KPI cards (linha 1) ───────────────────────────────────────────────────
    kpi_items = [
        ("Total de eventos",    kpis.get("total", 0),           _green),
        ("Jobs Criados",        kpis.get("job_created", 0),     _green),
        ("Config. Alteradas",   kpis.get("job_updated", 0),     RGBColor(0xF9, 0x73, 0x16)),
        ("Jobs Excluídos",      kpis.get("job_deleted", 0),     RGBColor(0xDC, 0x35, 0x45)),
        ("Obj. Adicionados",    kpis.get("objects_added", 0),   RGBColor(0xA7, 0x8B, 0xFA)),
        ("Obj. Removidos",      kpis.get("objects_deleted", 0), RGBColor(0xFD, 0x7E, 0x14)),
        ("Operadores",          kpis.get("operators_count", 0), RGBColor(0x6F, 0x42, 0xC1)),
    ]
    card_y = Inches(0.64)
    card_h = Inches(0.72)
    card_w = Inches(1.3)
    gap    = Inches(0.08)
    for i, (lbl, val, color) in enumerate(kpi_items):
        cx = Inches(0.18) + i * (card_w + gap)
        bg_card = _rounded_rect(slide, cx, card_y, card_w, card_h,
                                fill=RGBColor(0xF8, 0xF9, 0xFA),
                                border=color, border_pt=0.8, rounding=4000)
        # Label
        lt = slide.shapes.add_textbox(cx + Inches(0.08), card_y + Inches(0.06), card_w - Inches(0.12), Inches(0.2))
        _txt(lt, lbl, size_pt=7, color=C_MUTED)
        # Value
        vt = slide.shapes.add_textbox(cx + Inches(0.08), card_y + Inches(0.26), card_w - Inches(0.12), Inches(0.36))
        _txt(vt, str(val), size_pt=20, bold=True, color=color)

    # ── Separator ─────────────────────────────────────────────────────────────
    sep_y = Inches(1.45)
    _rounded_rect(slide, Inches(0.18), sep_y, SW - Inches(0.36), Pt(1), fill=TEAL, rounding=0)

    # ── Secção esquerda: Top Jobs ─────────────────────────────────────────────
    col_left_x = Inches(0.18)
    col_left_w = Inches(4.6)
    sec_y = sep_y + Inches(0.12)

    hdr_jobs = slide.shapes.add_textbox(col_left_x, sec_y, col_left_w, Inches(0.22))
    _txt(hdr_jobs, "Jobs com mais alterações", size_pt=9, bold=True, color=GREEN_DOT)

    bar_area_y = sec_y + Inches(0.25)
    bar_area_h = Inches(1.5)
    max_cnt = top_jobs[0][1] if top_jobs else 1

    for i, (jname, cnt) in enumerate(top_jobs[:6]):
        ry = bar_area_y + i * Inches(0.24)
        # label
        jl = slide.shapes.add_textbox(col_left_x, ry, Inches(2.8), Inches(0.22))
        _txt(jl, jname[:35], size_pt=8, color=C_TEXT)
        # bar bg
        _rounded_rect(slide, col_left_x + Inches(2.9), ry + Inches(0.04),
                      Inches(1.5), Inches(0.13),
                      fill=RGBColor(0xE8, 0xEA, 0xED), rounding=2000)
        # bar fill
        bar_pct = cnt / max_cnt if max_cnt else 0
        if bar_pct > 0:
            _rounded_rect(slide, col_left_x + Inches(2.9), ry + Inches(0.04),
                          Inches(1.5 * bar_pct), Inches(0.13),
                          fill=TEAL, rounding=2000)
        # count
        nc = slide.shapes.add_textbox(col_left_x + Inches(4.45), ry, Inches(0.3), Inches(0.22))
        _txt(nc, str(cnt), size_pt=8, bold=True, color=GREEN_DOT, align=PP_ALIGN.RIGHT)

    # ── Secção direita: Alterações Sensíveis ──────────────────────────────────
    col_right_x = Inches(5.0)
    col_right_w = SW - col_right_x - Inches(0.18)

    hdr_sens = slide.shapes.add_textbox(col_right_x, sec_y, col_right_w, Inches(0.22))
    _txt(hdr_sens, "Alterações Sensíveis", size_pt=9, bold=True, color=RGBColor(0xDC, 0x35, 0x45))

    EVENT_LABELS = {
        "job_created": "Criação", "job_updated": "Alteração",
        "job_deleted": "Exclusão", "objects_added": "Obj+",
        "objects_changed": "Obj±", "objects_deleted": "Obj−",
    }

    if not sensitive_items:
        ns = slide.shapes.add_textbox(col_right_x, sec_y + Inches(0.28), col_right_w, Inches(0.3))
        _txt(ns, "Nenhuma alteração sensível no período.", size_pt=9, color=C_MUTED)
    else:
        for i, ev in enumerate(sensitive_items[:6]):
            ey = sec_y + Inches(0.25) + i * Inches(0.24)
            etype = EVENT_LABELS.get(ev.get("event_type", ""), "—")
            jname = (ev.get("job_name") or "—")[:30]
            op    = (ev.get("operator") or "—")[:22]
            dt    = (ev.get("time") or "—")[:16]
            impact= ev.get("impact", "—")

            # Impact badge color
            imp_color = (
                RGBColor(0xDC, 0x35, 0x45) if impact == "Alto" else
                RGBColor(0xFF, 0xC1, 0x07) if impact == "Médio" else
                GREEN_DOT
            )

            # Small colored badge for event type
            badge_w = Inches(0.55)
            badge_h = Inches(0.16)
            _rounded_rect(slide, col_right_x, ey + Inches(0.03), badge_w, badge_h,
                          fill=RGBColor(0xE8, 0xEA, 0xED), rounding=3000)
            bt = slide.shapes.add_textbox(col_right_x, ey + Inches(0.02), badge_w, Inches(0.2))
            _txt(bt, etype, size_pt=7, color=C_TEXT, align=PP_ALIGN.CENTER)

            # Job name
            jt = slide.shapes.add_textbox(col_right_x + badge_w + Inches(0.06), ey, Inches(2.5), Inches(0.22))
            _txt(jt, jname, size_pt=8, color=C_TEXT)

            # Operator
            ot = slide.shapes.add_textbox(col_right_x + badge_w + Inches(0.06), ey + Inches(0.12), Inches(2.0), Inches(0.18))
            _txt(ot, op, size_pt=7, color=C_MUTED)

            # Impact badge
            imp_x = col_right_x + col_right_w - Inches(0.55)
            _rounded_rect(slide, imp_x, ey + Inches(0.03), Inches(0.5), badge_h,
                          fill=imp_color, rounding=3000)
            it = slide.shapes.add_textbox(imp_x, ey + Inches(0.02), Inches(0.5), Inches(0.2))
            _txt(it, impact, size_pt=7, bold=True, color=C_WHITE, align=PP_ALIGN.CENTER)

    # ── Operadores (linha de rodapé) ──────────────────────────────────────────
    op_y = Inches(3.3)
    _rounded_rect(slide, Inches(0.18), op_y, SW - Inches(0.36), Pt(1), fill=C_GRAY_LINE, rounding=0)
    op_hdr = slide.shapes.add_textbox(Inches(0.18), op_y + Inches(0.08), Inches(2.5), Inches(0.22))
    _txt(op_hdr, "Operadores — volume de alterações", size_pt=9, bold=True, color=GREEN_DOT)

    if not top_operators:
        no_op = slide.shapes.add_textbox(Inches(0.18), op_y + Inches(0.32), Inches(4), Inches(0.25))
        _txt(no_op, "Nenhum operador identificado.", size_pt=9, color=C_MUTED)
    else:
        max_op = top_operators[0][1] if top_operators else 1
        for i, (op_name, op_cnt) in enumerate(top_operators[:5]):
            ox = Inches(0.18) + i * Inches(1.9)
            oy = op_y + Inches(0.32)
            # Name
            nt = slide.shapes.add_textbox(ox, oy, Inches(1.8), Inches(0.22))
            _txt(nt, op_name[:22], size_pt=8, color=C_TEXT)
            # bar
            bar_pct = op_cnt / max_op if max_op else 0
            _rounded_rect(slide, ox, oy + Inches(0.24), Inches(1.7), Inches(0.12),
                          fill=RGBColor(0xE8, 0xEA, 0xED), rounding=2000)
            if bar_pct > 0:
                _rounded_rect(slide, ox, oy + Inches(0.24), Inches(1.7 * bar_pct), Inches(0.12),
                              fill=GREEN_DOT, rounding=2000)
            # count
            ct = slide.shapes.add_textbox(ox + Inches(1.75), oy + Inches(0.2), Inches(0.3), Inches(0.2))
            _txt(ct, str(op_cnt), size_pt=8, bold=True, color=GREEN_DOT, align=PP_ALIGN.RIGHT)

    # ── Rodapé ────────────────────────────────────────────────────────────────
    footer = slide.shapes.add_textbox(Inches(0.18), FOOTER_Y, SW - Inches(0.36), Inches(0.2))
    _txt(footer, f"Gerado em {__import__('datetime').datetime.now().strftime('%d/%m/%Y %H:%M')}  |  HelloIT Veeam Reporter",
         size_pt=7.5, color=C_MUTED)

    buf = io.BytesIO()
    prs.save(buf)
    buf.seek(0)
    return buf.read()
