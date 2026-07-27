"""
Builds a formatted PDF version of the RFP Go/No-Go analysis, mirroring the
sections shown in the Streamlit dashboard (hero verdict, deliverables,
evaluation criteria, compliance checklist, dates/budget, opportunity
assessment).
"""

import io

from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    PageBreak,
    HRFlowable,
)

VERDICT_COLORS = {
    "GO": {"bg": colors.HexColor("#e6f4ea"), "hex": "#1a7f37", "label": "GO"},
    "CONDITIONAL": {"bg": colors.HexColor("#fdf3e0"), "hex": "#b8860b", "label": "PROCEED WITH CAUTION"},
    "NO-GO": {"bg": colors.HexColor("#fdeaea"), "hex": "#b42318", "label": "NO-GO"},
}

STATUS_COLORS = {
    "MET": "#1a7f37",
    "GAP": "#b42318",
    "REVIEW": "#b8860b",
}

STATUS_ICON = {"MET": "OK", "GAP": "GAP", "REVIEW": "REVIEW"}


def _styles():
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        name="ReportTitle", parent=styles["Title"], fontSize=20, spaceAfter=4,
    ))
    styles.add(ParagraphStyle(
        name="VerdictLabel", parent=styles["Normal"], fontSize=14, leading=18,
        fontName="Helvetica-Bold",
    ))
    styles.add(ParagraphStyle(
        name="FitScore", parent=styles["Normal"], fontSize=28, leading=32,
        fontName="Helvetica-Bold", alignment=TA_CENTER,
    ))
    styles.add(ParagraphStyle(
        name="SectionHeading", parent=styles["Heading1"], fontSize=14,
        spaceBefore=18, spaceAfter=8, textColor=colors.HexColor("#222222"),
    ))
    styles.add(ParagraphStyle(
        name="SubHeading", parent=styles["Heading2"], fontSize=11.5,
        spaceBefore=10, spaceAfter=4, textColor=colors.HexColor("#333333"),
    ))
    styles.add(ParagraphStyle(
        name="ChildItem", parent=styles["Normal"], fontSize=9.5,
        leftIndent=18, spaceAfter=6, textColor=colors.HexColor("#333333"),
    ))
    styles.add(ParagraphStyle(
        name="Body", parent=styles["Normal"], fontSize=10, leading=14,
    ))
    styles.add(ParagraphStyle(
        name="Caption", parent=styles["Normal"], fontSize=8.5,
        textColor=colors.HexColor("#666666"),
    ))
    styles.add(ParagraphStyle(
        name="TableCell", parent=styles["Normal"], fontSize=9, leading=12,
    ))
    styles.add(ParagraphStyle(
        name="TableCellBold", parent=styles["Normal"], fontSize=9, leading=12,
        fontName="Helvetica-Bold",
    ))
    return styles


def _p(text, style):
    """Paragraph wrapper that tolerates missing/None text."""
    return Paragraph(text if text else "&nbsp;", style)


def _total_deliverable_weeks(deliverables):
    total = 0
    for d in deliverables:
        children = d.get("children", [])
        if children:
            total += sum(c.get("weeks_estimate", 0) for c in children)
        else:
            total += d.get("weeks_estimate", 0)
    return total


def generate_deliverables_pdf(deliverables: list, source_filename: str = "RFP") -> bytes:
    """
    Build a standalone PDF containing ONLY the deliverables section (with
    nested children and weeks estimates) — used by the "Download Deliverables
    (PDF)" button on the Deliverables tab.
    """
    styles = _styles()
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        topMargin=0.75 * inch,
        bottomMargin=0.75 * inch,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
        title=f"Deliverables - {source_filename}",
    )

    story = []
    total_weeks = _total_deliverable_weeks(deliverables)
    total_items = sum(len(d.get("children", [])) or 1 for d in deliverables)

    story.append(_p(f"Deliverables: {source_filename}", styles["ReportTitle"]))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#dddddd")))
    story.append(_p(f"{total_items} items &middot; {total_weeks} weeks estimated total", styles["Caption"]))
    story.append(Spacer(1, 12))

    if not deliverables:
        story.append(_p("No deliverables were extracted from this RFP.", styles["Body"]))

    for i, d in enumerate(deliverables, 1):
        tag = "MANDATORY" if d.get("mandatory") else "OPTIONAL"
        children = d.get("children", [])
        parent_wk = sum(c.get("weeks_estimate", 0) for c in children) if children else d.get("weeks_estimate", "?")
        story.append(_p(f"{i}. {d.get('title', '')}", styles["SubHeading"]))
        if d.get("description"):
            story.append(_p(d.get("description"), styles["Body"]))
        story.append(_p(f"{tag} &middot; {parent_wk} wk estimate (total)", styles["Caption"]))
        for j, c in enumerate(children, 1):
            ctag = "MANDATORY" if c.get("mandatory") else "OPTIONAL"
            story.append(_p(
                f"<b>{i}.{j} {c.get('title', '')}</b><br/>"
                f"{c.get('description', '')}<br/>"
                f"<font size='8' color='#888888'>{ctag} &middot; {c.get('weeks_estimate', '?')} wk estimate</font>",
                styles["ChildItem"],
            ))
        story.append(Spacer(1, 8))

    doc.build(story)
    pdf_bytes = buffer.getvalue()
    buffer.close()
    return pdf_bytes


def generate_pdf_report(result: dict, source_filename: str = "RFP") -> bytes:
    """
    Build the full PDF report from the analysis result dict (same shape
    produced by ai_engine.analyze_rfp) and return it as raw PDF bytes,
    ready to hand to st.download_button.
    """
    styles = _styles()
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        topMargin=0.75 * inch,
        bottomMargin=0.75 * inch,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
        title=f"RFP Analysis - {source_filename}",
    )

    story = []

    verdict = result.get("verdict", "CONDITIONAL")
    vstyle = VERDICT_COLORS.get(verdict, VERDICT_COLORS["CONDITIONAL"])
    fit_score = result.get("fit_score", 0)

    deliverables = result.get("deliverables", [])
    total_weeks = _total_deliverable_weeks(deliverables)
    total_items = sum(len(d.get("children", [])) or 1 for d in deliverables)

    compliance = result.get("compliance", {})
    all_items = [item for dept_items in compliance.values() for item in dept_items]
    met_count = sum(1 for i in all_items if i.get("status") == "MET")
    gap_count = sum(1 for i in all_items if i.get("status") == "GAP")

    # ---------- Title ----------
    story.append(_p(f"RFP Analysis: {source_filename}", styles["ReportTitle"]))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#dddddd")))
    story.append(Spacer(1, 10))

    # ---------- Hero verdict card ----------
    hero_table = Table(
        [[
            Paragraph(f'<font color="{vstyle["hex"]}"><b>{vstyle["label"]}</b></font>'
                      f"<br/><br/>" + (result.get("headline_summary", "") or ""), styles["Body"]),
            Paragraph(f'<font size="26" color="{vstyle["hex"]}"><b>{fit_score}</b></font>'
                      f'<br/><font size="8" color="#666666">FIT / 100</font>', styles["FitScore"]),
        ]],
        colWidths=[4.6 * inch, 1.6 * inch],
    )
    hero_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), vstyle["bg"]),
        ("BOX", (0, 0), (-1, -1), 0.75, colors.HexColor("#dddddd")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (1, 0), (1, 0), "CENTER"),
        ("LEFTPADDING", (0, 0), (-1, -1), 14),
        ("RIGHTPADDING", (0, 0), (-1, -1), 14),
        ("TOPPADDING", (0, 0), (-1, -1), 14),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 14),
    ]))
    story.append(hero_table)
    story.append(Spacer(1, 12))

    # ---------- Metrics row ----------
    metrics_table = Table(
        [[
            Paragraph(f"<b>{total_items}</b><br/><font size='8' color='#666666'>Deliverables</font>", styles["Body"]),
            Paragraph(f"<b>{total_weeks}</b><br/><font size='8' color='#666666'>Est. Weeks Total</font>", styles["Body"]),
            Paragraph(f"<b>{met_count}</b><br/><font size='8' color='#666666'>Requirements Met</font>", styles["Body"]),
            Paragraph(f"<b>{gap_count}</b><br/><font size='8' color='#666666'>Compliance Gaps</font>", styles["Body"]),
        ]],
        colWidths=[1.55 * inch] * 4,
    )
    metrics_table.setStyle(TableStyle([
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#eeeeee")),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#eeeeee")),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    story.append(metrics_table)

    # ---------- Deliverables ----------
    story.append(_p("1. Deliverables & Time Estimate", styles["SectionHeading"]))
    for i, d in enumerate(deliverables, 1):
        tag = "MANDATORY" if d.get("mandatory") else "OPTIONAL"
        children = d.get("children", [])
        parent_wk = sum(c.get("weeks_estimate", 0) for c in children) if children else d.get("weeks_estimate", "?")
        story.append(_p(f"{i}. {d.get('title', '')}", styles["SubHeading"]))
        if d.get("description"):
            story.append(_p(d.get("description"), styles["Body"]))
        story.append(_p(f"{tag} &middot; {parent_wk} wk estimate (total)", styles["Caption"]))
        for j, c in enumerate(children, 1):
            ctag = "MANDATORY" if c.get("mandatory") else "OPTIONAL"
            story.append(_p(
                f"<b>{i}.{j} {c.get('title', '')}</b><br/>"
                f"{c.get('description', '')}<br/>"
                f"<font size='8' color='#888888'>{ctag} &middot; {c.get('weeks_estimate', '?')} wk estimate</font>",
                styles["ChildItem"],
            ))
        story.append(Spacer(1, 6))

    # ---------- Evaluation Criteria ----------
    story.append(PageBreak())
    story.append(_p("2. Evaluation Criteria", styles["SectionHeading"]))
    eval_rows = [[Paragraph("<b>Criterion</b>", styles["TableCellBold"]),
                  Paragraph("<b>Weight</b>", styles["TableCellBold"])]]
    for c in result.get("evaluation_criteria", []):
        eval_rows.append([
            Paragraph(f"<b>{c.get('name', '')}</b><br/>{c.get('description', '')}", styles["TableCell"]),
            Paragraph(f"{c.get('weight_pct', '?')}%", styles["TableCell"]),
        ])
    if len(eval_rows) > 1:
        eval_table = Table(eval_rows, colWidths=[5.0 * inch, 1.0 * inch])
        eval_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f5f5f5")),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#dddddd")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ]))
        story.append(eval_table)
    else:
        story.append(_p("No evaluation criteria extracted.", styles["Body"]))

    # ---------- Compliance Checklist ----------
    story.append(PageBreak())
    story.append(_p("3. Department Compliance Checklist", styles["SectionHeading"]))
    for dept in ["Legal", "Accounting", "Technical", "Operations"]:
        items = compliance.get(dept, [])
        if not items:
            continue
        story.append(_p(dept, styles["SubHeading"]))
        rows = [[Paragraph("<b>Requirement</b>", styles["TableCellBold"]),
                 Paragraph("<b>Status</b>", styles["TableCellBold"]),
                 Paragraph("<b>Note</b>", styles["TableCellBold"])]]
        row_colors = [None]
        for item in items:
            status = item.get("status", "REVIEW")
            color = STATUS_COLORS.get(status, STATUS_COLORS["REVIEW"])
            rows.append([
                Paragraph(item.get("requirement", ""), styles["TableCell"]),
                Paragraph(f'<font color="{color}"><b>{STATUS_ICON.get(status, status)}</b></font>', styles["TableCell"]),
                Paragraph(item.get("note", ""), styles["TableCell"]),
            ])
        t = Table(rows, colWidths=[2.6 * inch, 0.8 * inch, 2.6 * inch])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f5f5f5")),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#dddddd")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ]))
        story.append(t)
        story.append(Spacer(1, 10))

    # ---------- Dates & Budget ----------
    story.append(PageBreak())
    story.append(_p("4. Key Dates & Budget", styles["SectionHeading"]))
    kb = result.get("key_dates_budget", {})
    labels = {
        "submission_deadline": "Submission Deadline",
        "pre_proposal_conference": "Pre-proposal Conference",
        "qa_deadline": "Q&A Deadline",
        "project_timeline": "Project Timeline",
        "total_budget": "Total Budget",
        "bond_requirements": "Bond Requirements",
    }
    kb_rows = []
    for key, label in labels.items():
        kb_rows.append([
            Paragraph(f"<b>{label}</b>", styles["TableCellBold"]),
            Paragraph(kb.get(key, "Not specified."), styles["TableCell"]),
        ])
    kb_table = Table(kb_rows, colWidths=[1.8 * inch, 4.2 * inch])
    kb_table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#dddddd")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
    ]))
    story.append(kb_table)

    # ---------- Opportunity Assessment ----------
    story.append(Spacer(1, 16))
    story.append(_p("5. Opportunity Assessment", styles["SectionHeading"]))
    oa = result.get("opportunity_assessment", {})
    story.append(_p("Key reasons behind the call:", styles["SubHeading"]))
    for r in oa.get("key_reasons", []):
        story.append(_p(f"&bull; {r}", styles["Body"]))
    disqualifiers = oa.get("potential_disqualifiers", [])
    if disqualifiers:
        story.append(_p("Potential disqualifiers:", styles["SubHeading"]))
        for d in disqualifiers:
            story.append(_p(f'&bull; <font color="#b42318">{d}</font>', styles["Body"]))

    doc.build(story)
    pdf_bytes = buffer.getvalue()
    buffer.close()
    return pdf_bytes