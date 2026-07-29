import streamlit as st
import os
import json
import tempfile
import base64
import re
import uuid
from dotenv import load_dotenv
from utils.document_processor import (
    RFPProcessor,
    extract_raw_pages_from_pdf,
    get_highlighted_page_image,
)
from datetime import datetime
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
import io
import plotly.graph_objects as go

load_dotenv()

st.set_page_config(
    page_title="RFP Document Processor",
    page_icon="RFP",
    layout="wide"
)

# ============================================================
# THEME SYSTEM (cream/lilac light mode, greyish dark mode)
# ============================================================
THEMES = {
    "Light": {
        "bg": "#FBF4E8",
        "bg_secondary": "#F3E7F7",
        "card": "#EFE1F6",
        "card_border": "#B98CD9",
        "text": "#3A2E45",
        "text_muted": "#7A6E85",
        "accent": "#A66FC9",
        "accent_dark": "#8C51B3",
        "go": "#4C9A6B",
        "nogo": "#C4576A",
        "escalate": "#D8A23A",
        "table_header": "#B98CD9",
        "table_row_a": "#F7EEFB",
        "table_row_b": "#EFDFF6",
    },
    "Dark": {
        "bg": "#2A2930",
        "bg_secondary": "#34333B",
        "card": "#3B3944",
        "card_border": "#9B7EBD",
        "text": "#EDEAF2",
        "text_muted": "#B7B0C4",
        "accent": "#B79CDB",
        "accent_dark": "#9A7BC4",
        "go": "#6FBE8E",
        "nogo": "#E08A96",
        "escalate": "#E8C170",
        "table_header": "#5B4C72",
        "table_row_a": "#3B3944",
        "table_row_b": "#34313D",
    },
}

def inject_theme_css(theme: dict):
    st.markdown(f"""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Baloo+2:wght@500;600;700&family=Quicksand:wght@400;500;600&display=swap');

    html, body, [class*="css"] {{
        font-family: 'Quicksand', sans-serif;
    }}

    .stApp {{
        background-color: {theme['bg']};
        color: {theme['text']};
    }}

    section[data-testid="stSidebar"] {{
        background-color: {theme['bg_secondary']};
    }}

    h1, h2, h3, h4, .heading-font {{
        font-family: 'Baloo 2', cursive !important;
        color: {theme['text']} !important;
    }}

    p, span, label {{
        color: {theme['text']};
    }}

    .stMarkdown, .stMarkdown p, .stMarkdown li, .stMarkdown span,
    .stCaption, [data-testid="stCaptionContainer"],
    [data-testid="stMetricValue"], [data-testid="stMetricLabel"],
    .stTextInput label, .stRadio label, .stFileUploader label,
    .streamlit-expanderHeader, [data-testid="stExpander"] summary {{
        color: {theme['text']} !important;
    }}
    div.stButton > button p,
    div.stButton > button span,
    button[kind="secondary"],
    button[kind="primary"],
    button[data-testid="baseButton-secondary"],
    button[data-testid="baseButton-primary"] {{
        background-color: {theme['accent']} !important;
        color: #FFFFFF !important;
        border-radius: 10px !important;
        border: none !important;
        font-family: 'Quicksand', sans-serif !important;
        font-weight: 600 !important;
    }}
    div.stButton > button:hover,
    button[kind="secondary"]:hover,
    button[kind="primary"]:hover {{
        background-color: {theme['accent_dark']} !important;
        color: #FFFFFF !important;
    }}
    div.stButton > button:disabled,
    div.stButton > button:disabled p,
    button:disabled {{
        background-color: {theme['text_muted']} !important;
        color: #FFFFFF !important;
        opacity: 0.6 !important;
    }}
    div.stDownloadButton > button {{
        background-color: {theme['accent']} !important;
        color: #FFFFFF !important;
        border-radius: 10px !important;
        border: none !important;
        font-weight: 600 !important;
    }}
    div.stDownloadButton > button:hover {{
        background-color: {theme['accent_dark']} !important;
    }}

    .rfp-card {{
        background: {theme['card']};
        border-left: 6px solid {theme['card_border']};
        border-radius: 14px;
        padding: 14px 20px;
        margin: 14px 0 10px 0;
    }}

    .rfp-card-title {{
        font-family: 'Baloo 2', cursive;
        font-size: 20px;
        font-weight: 700;
        color: {theme['accent_dark']};
    }}

    .rfp-card-sub {{
        font-size: 13px;
        color: {theme['text_muted']};
        margin-top: 2px;
    }}

    .rfp-item-row {{
        padding: 8px 0 8px 12px;
        border-bottom: 1px solid rgba(0,0,0,0.06);
    }}

    .rfp-badge {{
        font-size: 11px;
        color: {theme['accent_dark']};
        background: rgba(166,111,201,0.15);
        padding: 2px 10px;
        border-radius: 10px;
        border: 1px solid rgba(166,111,201,0.25);
        white-space: nowrap;
    }}

    .rfp-reason {{
        font-size: 13px;
        color: {theme['text_muted']};
        margin-top: 3px;
        padding-left: 8px;
        font-style: italic;
    }}

    .status-pill {{
        display: inline-block;
        padding: 3px 12px;
        border-radius: 12px;
        font-weight: 600;
        font-size: 12px;
    }}
    .status-go {{ background: rgba(76,154,107,0.18); color: {theme['go']}; }}
    .status-nogo {{ background: rgba(196,87,106,0.18); color: {theme['nogo']}; }}
    .status-escalate {{ background: rgba(216,162,58,0.18); color: {theme['escalate']}; }}

    .decision-box {{
        background: {theme['card']};
        border: 3px solid {theme['card_border']};
        border-radius: 18px;
        padding: 26px;
        text-align: center;
        margin: 16px 0;
    }}
    .decision-title {{
        font-family: 'Baloo 2', cursive;
        font-size: 40px;
        font-weight: 700;
    }}
    .decision-score {{
        font-size: 18px;
        color: {theme['text_muted']};
        margin-top: 6px;
    }}
    .decision-summary {{
        font-size: 15px;
        color: {theme['text']};
        margin-top: 10px;
    }}

    .rfp-table {{
        width: 100%;
        border-collapse: collapse;
        border-radius: 12px;
        overflow: hidden;
        font-size: 13px;
    }}
    .rfp-table th {{
        background: {theme['table_header']};
        color: white;
        font-family: 'Baloo 2', cursive;
        padding: 8px 10px;
        text-align: left;
        font-size: 14px;
    }}
    .rfp-table td {{
        padding: 8px 10px;
        vertical-align: top;
        color: {theme['text']};
    }}
    .rfp-table tr:nth-child(even) {{ background: {theme['table_row_b']}; }}
    .rfp-table tr:nth-child(odd) {{ background: {theme['table_row_a']}; }}

    div.stButton > button {{
        background-color: {theme['accent']};
        color: white;
        border-radius: 10px;
        border: none;
        font-family: 'Quicksand', sans-serif;
        font-weight: 600;
    }}
    div.stButton > button:hover {{
        background-color: {theme['accent_dark']};
        color: white;
    }}
    </style>
    """, unsafe_allow_html=True)

def get_theme():
    mode = st.session_state.get("theme_mode", "Light")
    return THEMES[mode]

# ============================================================
# STORAGE FUNCTIONS
# ============================================================
RESULTS_DIR = "analysis_results"

def ensure_results_dir():
    if not os.path.exists(RESULTS_DIR):
        os.makedirs(RESULTS_DIR)

def generate_analysis_id():
    date_str = datetime.now().strftime("%Y%m%d")
    uid = str(uuid.uuid4())[:8]
    return f"RFP-{date_str}-{uid}"

def save_analysis_results(analysis_id, results, pdf_files_bytes=None, pdf_raw_pages=None, combined_text=None):
    """
    Save the analysis results as a JSON file. Also embeds the original PDF
    bytes (base64), their raw per-page text, and the combined extracted text
    used for analysis — so "View Source" and "Add More Files" both keep
    working when this analysis is reloaded later by ID, with no re-upload
    of the original files required.
    """
    ensure_results_dir()
    filepath = os.path.join(RESULTS_DIR, f"{analysis_id}.json")
    results_to_save = results.copy()
    results_to_save['_metadata'] = {
        'analysis_id': analysis_id,
        'timestamp': datetime.now().isoformat(),
        'version': '1.2'
    }

    if pdf_files_bytes:
        results_to_save['_pdf_files'] = {
            name: base64.b64encode(data).decode('utf-8')
            for name, data in pdf_files_bytes.items()
        }
    if pdf_raw_pages:
        results_to_save['_pdf_raw_pages'] = pdf_raw_pages
    if combined_text is not None:
        results_to_save['_combined_text'] = combined_text

    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(results_to_save, f, indent=2, ensure_ascii=False)

def load_analysis_results(analysis_id):
    filepath = os.path.join(RESULTS_DIR, f"{analysis_id}.json")
    if not os.path.exists(filepath):
        return None
    with open(filepath, 'r', encoding='utf-8') as f:
        return json.load(f)

def get_all_analysis_ids():
    ensure_results_dir()
    files = os.listdir(RESULTS_DIR)
    return [f.replace('.json', '') for f in files if f.endswith('.json')]

def hydrate_pdf_state_from_results(results):
    """Pull '_pdf_files' / '_pdf_raw_pages' / '_combined_text' back out of a loaded
    results dict (if present) and put them into session_state, decoding base64
    back to bytes."""
    pdf_files_b64 = results.get('_pdf_files', {})
    pdf_files_bytes = {
        name: base64.b64decode(b64) for name, b64 in pdf_files_b64.items()
    }
    st.session_state['pdf_files_bytes'] = pdf_files_bytes
    st.session_state['pdf_raw_pages'] = results.get('_pdf_raw_pages', {})
    st.session_state['combined_text'] = results.get('_combined_text', '')

def load_analysis_into_session(analysis_id):
    """Fetch a saved analysis by ID and fully restore it into session_state:
    the results, the original PDFs (for View Source), and the original
    combined text (so 'Add More Files' can append to it later)."""
    fetched = load_analysis_results(analysis_id)
    if not fetched:
        return False
    st.session_state['results'] = fetched
    st.session_state['processed'] = True
    st.session_state['analysis_id'] = analysis_id
    hydrate_pdf_state_from_results(fetched)
    clear_pdf_view()
    return True

# ============================================================
# "VIEW SOURCE" STATE HELPERS
# ============================================================

def request_pdf_view(source_file, quote, item_name):
    st.session_state['pdf_view_request'] = {
        "source_file": source_file,
        "quote": quote,
        "item_name": item_name,
    }

def clear_pdf_view():
    st.session_state['pdf_view_request'] = None

def render_pdf_viewer(theme):
    """Render the highlighted PDF page for the currently requested deliverable, if any."""
    request = st.session_state.get('pdf_view_request')
    if not request:
        return

    source_file = request.get('source_file')
    quote = request.get('quote')
    item_name = request.get('item_name')

    pdf_files_bytes = st.session_state.get('pdf_files_bytes', {})
    pdf_raw_pages = st.session_state.get('pdf_raw_pages', {})

    st.markdown("---")
    header_col, close_col = st.columns([6, 1])
    with header_col:
        st.markdown(f"#### Source location: *{item_name}*")
    with close_col:
        if st.button("Close", key="close_pdf_viewer"):
            clear_pdf_view()
            st.rerun()

    pdf_bytes = pdf_files_bytes.get(source_file)

    if not pdf_bytes:
        st.warning(
            f"The original PDF for **{source_file}** isn't available in this session "
            f"(it may have been a non-PDF file, or this analysis was saved before this "
            f"feature was added). Re-upload the file and re-process to enable this view."
        )
        return

    if not quote or not quote.strip():
        st.warning("No source quote was captured for this deliverable, so it can't be located automatically.")
        return

    raw_pages = pdf_raw_pages.get(source_file)

    with st.spinner("Locating the exact page and highlighting the source text..."):
        result = get_highlighted_page_image(pdf_bytes, quote, raw_pages)

    if not result:
        st.warning(
            f"Couldn't locate this exact text inside **{source_file}**. "
            f"The quoted snippet may have been paraphrased too heavily by the AI."
        )
        st.caption(f"AI-provided quote: _{quote}_")
        return

    match_type = result["match_type"]
    page_num = result["page_num"]

    if match_type == "exact":
        st.success(f"Found on page {page_num + 1} of **{source_file}** (exact match)")
    elif match_type == "partial":
        st.info(f"Found on page {page_num + 1} of **{source_file}** (partial match — the AI's quote drifted slightly)")
    elif match_type == "fuzzy":
        st.info(f"Best approximate match on page {page_num + 1} of **{source_file}**")
    else:
        st.info(f"Likely on page {page_num + 1} of **{source_file}**, but the exact phrase couldn't be highlighted precisely")

    st.image(result["image_bytes"], use_container_width=True, caption=f"Page {page_num + 1} of {source_file}")

# ============================================================
# RENDER: Deliverables
# ============================================================
def render_deliverables(deliverables, source_files_available=None):
    """Render deliverables with clean cream/lilac cards + a 'View Source' button per item."""
    if not deliverables:
        st.info("No deliverables found in this RFP.")
        return

    source_files_available = source_files_available or set()

    if isinstance(deliverables, list) and len(deliverables) > 0 and isinstance(deliverables[0], str):
        deliverables = [{"category": "General", "items": deliverables}]

    category_counter = 1
    for cat_group in deliverables:
        category = cat_group.get('category', 'Uncategorized')
        items = cat_group.get('items', [])

        if not items:
            continue

        st.markdown(f"""
        <div class="rfp-card">
            <div class="rfp-card-title">{category_counter}. {category}</div>
            <div class="rfp-card-sub">{len(items)} deliverable(s) identified</div>
        </div>
        """, unsafe_allow_html=True)

        item_counter = 1
        for item in items:
            if isinstance(item, dict):
                item_name = item.get('name', 'Unknown')
                section_ref = item.get('section_ref', 'N/A')
                reason = item.get('reason', 'Required by RFP')
                source_file = item.get('source_file', 'Unknown')
                quote = item.get('quote', '')
            else:
                item_name = item
                section_ref = 'N/A'
                reason = 'Required by RFP'
                source_file = 'Unknown'
                quote = ''

            section_ref_clean = re.sub(r'<[^>]+>', '', str(section_ref))
            section_ref_clean = section_ref_clean.replace('&lt;', '<').replace('&gt;', '>')
            section_display = section_ref_clean if section_ref_clean and section_ref_clean != 'N/A' else ""

            if source_file and source_file != 'Unknown' and source_file != 'Unknown file':
                source_display = source_file.replace('", "', ', ').replace('"', '')
                file_display = f"[From: {source_display}]" if ',' in source_display else f"[From: {source_file}]"
                full_reason = f"{file_display} {reason}"
            else:
                full_reason = reason

            row_col1, row_col2 = st.columns([9, 1])

            with row_col1:
                st.markdown(f"""
                <div class="rfp-item-row">
                    <div style="display:flex; align-items:baseline; flex-wrap:wrap; gap:8px;">
                        <span style="font-weight:600; min-width:50px;">{category_counter}.{item_counter}</span>
                        <span style="flex:1;">{item_name}</span>
                        <span class="rfp-badge">{section_display}</span>
                    </div>
                    <div class="rfp-reason">{full_reason}</div>
                </div>
                """, unsafe_allow_html=True)

            with row_col2:
                can_view = source_file in source_files_available and bool(quote)
                if st.button(
                    "View Source",
                    key=f"view_pdf_{category_counter}_{item_counter}",
                    disabled=not can_view,
                    help="Open the exact PDF page and highlight this deliverable"
                    if can_view else "No PDF / source quote available for this item",
                    use_container_width=True,
                ):
                    request_pdf_view(source_file, quote, item_name)
                    st.rerun()

            item_counter += 1

        category_counter += 1

# ============================================================
# RENDER: Go/No-Go Dashboard (decision card + pie chart)
# ============================================================
def render_go_no_go_dashboard(go_no_go, theme):
    if not go_no_go:
        st.warning("No Go/No-Go analysis available.")
        return

    decision = go_no_go.get('overall_decision', 'UNDECIDED')
    score = go_no_go.get('overall_score', 0)
    go_count = go_no_go.get('go_count', 0)
    no_go_count = go_no_go.get('no_go_count', 0)
    escalate_count = go_no_go.get('escalate_count', 0)

    if decision == "GO":
        border_color = theme['go']
    elif decision == "NO-GO":
        border_color = theme['nogo']
    elif decision in ["CONDITIONAL", "CONSIDER"]:
        border_color = theme['escalate']
    else:
        border_color = theme['text_muted']

    col_left, col_right = st.columns([1.1, 1])

    with col_left:
        st.markdown(f"""
        <div class="decision-box" style="border-color:{border_color};">
            <div class="decision-title" style="color:{border_color};">{decision}</div>
            <div class="decision-score">Score: {min(100, round(score))} / 100</div>
            <div class="decision-summary">{go_no_go.get('summary', '')}</div>
        </div>
        """, unsafe_allow_html=True)

        m1, m2, m3 = st.columns(3)
        with m1:
            st.metric("GO Items", go_count)
        with m2:
            st.metric("NO-GO Items", no_go_count)
        with m3:
            st.metric("Conditional", escalate_count)

    with col_right:
        total = go_count + no_go_count + escalate_count
        if total > 0:
            fig = go.Figure(data=[go.Pie(
                labels=["GO", "NO-GO", "Conditional"],
                values=[go_count, no_go_count, escalate_count],
                hole=0.55,
                marker=dict(colors=[theme['go'], theme['nogo'], theme['escalate']]),
                textinfo="label+percent",
                sort=False,
            )])
            fig.update_layout(
                showlegend=False,
                margin=dict(t=10, b=10, l=10, r=10),
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                font=dict(family="Quicksand, sans-serif", color=theme['text']),
                height=300,
            )
            st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
        else:
            st.caption("No checklist items to chart yet.")

# ============================================================
# RENDER: Checklist (collapsible reason/evidence per item)
# ============================================================
def render_checklist_section(checklist, theme):
    if not checklist:
        st.warning("No checklist items were analyzed. Please try again.")
        return

    categories = {}
    for item in checklist:
        cat = item.get('category', 'Other')
        categories.setdefault(cat, []).append(item)

    for category, items in categories.items():
        st.markdown(f"""
        <div class="rfp-card">
            <div class="rfp-card-title">{category} Department</div>
            <div class="rfp-card-sub">{len(items)} item(s) evaluated</div>
        </div>
        """, unsafe_allow_html=True)

        for item in items:
            status = item.get('status', 'UNKNOWN')
            if status == "GO":
                status_class, status_label = "status-go", "GO"
            elif status == "NO-GO":
                status_class, status_label = "status-nogo", "NO-GO"
            elif status in ["CONDITIONAL", "CONSIDER", "ESCALATE"]:
                status_class, status_label = "status-escalate", "CONDITIONAL"
            else:
                status_class, status_label = "status-escalate", status

            with st.expander(f"{item.get('item', 'Unknown')}   [{status_label}]"):
                st.markdown(
                    f'<span class="status-pill {status_class}">{status_label}</span>',
                    unsafe_allow_html=True
                )
                st.markdown(f"**Reason:** {item.get('reason', '')}")
                st.markdown(f"**Evidence from RFP:** {item.get('evidence', '')}")

    go_count = sum(1 for i in checklist if i.get('status') == 'GO')
    no_go_count = sum(1 for i in checklist if i.get('status') == 'NO-GO')
    conditional_count = sum(1 for i in checklist if i.get('status') not in ('GO', 'NO-GO'))

    if no_go_count == 0 and go_count > 0:
        st.success(f"GO Decision — all {go_count} items passed. Recommend bidding on this RFP.")
    elif no_go_count > 0:
        st.error(f"NO-GO Decision — {no_go_count} item(s) failed. Recommend NOT bidding on this RFP.")
    else:
        st.warning(f"CONDITIONAL Decision — {conditional_count} item(s) need review. Proceed with caution.")

# ============================================================
# RENDER: Evaluation Criteria (capped + expandable)
# ============================================================
def render_evaluation_criteria_section(criteria, theme, max_shown=6, max_chars=90):
    if not criteria:
        st.info("No evaluation criteria extracted.")
        return

    if isinstance(criteria, str):
        st.write(criteria)
        return

    def _truncate(text, limit):
        text = str(text)
        return text if len(text) <= limit else text[:limit].rstrip() + "..."

    shown = criteria[:max_shown]
    remaining = criteria[max_shown:]

    for i, item in enumerate(shown, 1):
        st.markdown(f"**{i}.** {_truncate(item, max_chars)}")

    if remaining:
        with st.expander(f"Show {len(remaining)} more criteria"):
            for i, item in enumerate(remaining, max_shown + 1):
                st.markdown(f"**{i}.** {item}")

# ============================================================
# RENDER: Compliance Checklist as a Department Table
# ============================================================
def render_compliance_table_section(compliance, theme, max_rows=6, max_chars=70):
    if not compliance:
        st.info("No compliance checklist extracted.")
        return

    departments = ["Legal", "Accounting", "Technical", "Operations", "HR"]

    def _truncate(text, limit):
        text = str(text)
        return text if len(text) <= limit else text[:limit].rstrip() + "..."

    columns_data = {}
    overflow_notes = []
    for dept in departments:
        tasks = compliance.get(dept, [])
        if isinstance(tasks, str):
            tasks = [tasks]
        shown_tasks = tasks[:max_rows]
        if len(tasks) > max_rows:
            overflow_notes.append(f"{dept} (+{len(tasks) - max_rows} more)")
        columns_data[dept] = [_truncate(t, max_chars) for t in shown_tasks]

    row_count = max((len(v) for v in columns_data.values()), default=0)
    row_count = min(row_count, max_rows)

    header_html = "".join(f"<th>{dept}</th>" for dept in departments)
    rows_html = ""
    for r in range(row_count):
        cells = ""
        for dept in departments:
            cell_val = columns_data[dept][r] if r < len(columns_data[dept]) else "-"
            cells += f"<td>{cell_val}</td>"
        rows_html += f"<tr>{cells}</tr>"

    table_html = f"""
    <table class="rfp-table">
        <thead><tr>{header_html}</tr></thead>
        <tbody>{rows_html}</tbody>
    </table>
    """
    st.markdown(table_html, unsafe_allow_html=True)

    if overflow_notes:
        st.caption("Additional tasks not shown here (see full JSON download): " + "; ".join(overflow_notes))

# ============================================================
# PDF GENERATION FUNCTIONS (downloadable reports)
# ============================================================
def generate_deliverables_pdf(deliverables, file_name=None):
    if not deliverables:
        return None

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, topMargin=40, bottomMargin=40)
    styles = getSampleStyleSheet()
    story = []

    title_style = ParagraphStyle(
        'CustomTitle', parent=styles['Heading1'], fontSize=20,
        textColor=colors.HexColor('#2c3e50'), alignment=TA_CENTER, spaceAfter=20
    )
    story.append(Paragraph("Deliverables Required by RFP", title_style))
    story.append(Spacer(1, 10))

    if isinstance(deliverables, list) and len(deliverables) > 0 and isinstance(deliverables[0], str):
        deliverables = [{"category": "General", "items": deliverables}]

    category_style = ParagraphStyle(
        'Category', parent=styles['Heading2'], fontSize=14,
        textColor=colors.HexColor('#6c5ce7'), spaceAfter=8, spaceBefore=15
    )
    item_style = ParagraphStyle(
        'Item', parent=styles['Normal'], fontSize=11,
        textColor=colors.HexColor('#2c3e50'), leftIndent=20, spaceAfter=2
    )
    section_style = ParagraphStyle(
        'Section', parent=styles['Normal'], fontSize=9,
        textColor=colors.HexColor('#6c5ce7'), leftIndent=40, spaceAfter=2, fontName='Helvetica-Oblique'
    )
    reason_style = ParagraphStyle(
        'Reason', parent=styles['Normal'], fontSize=9,
        textColor=colors.HexColor('#555555'), leftIndent=40, spaceAfter=6, fontName='Helvetica'
    )

    category_counter = 1
    for cat_group in deliverables:
        category = cat_group.get('category', 'Uncategorized')
        items = cat_group.get('items', [])
        if not items:
            continue

        story.append(Paragraph(f"{category_counter}. {category}", category_style))

        item_counter = 1
        for item in items:
            if isinstance(item, dict):
                item_name = item.get('name', 'Unknown')
                section_ref = item.get('section_ref', 'N/A')
                reason = item.get('reason', 'Required by RFP')
                source_file = item.get('source_file', 'Unknown')
            else:
                item_name = item
                section_ref = 'N/A'
                reason = 'Required by RFP'
                source_file = 'Unknown'

            item_name = item_name.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            section_ref = section_ref.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            reason = reason.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            source_file = source_file.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

            if source_file and source_file != 'Unknown' and source_file != 'Unknown file':
                source_display = source_file.replace('", "', ', ').replace('"', '')
                file_display = f"[From: {source_display}]" if ',' in source_display else f"[From: {source_file}]"
                full_reason = f"{file_display} {reason}"
            else:
                full_reason = reason

            story.append(Paragraph(f"{category_counter}.{item_counter} <b>{item_name}</b>", item_style))
            story.append(Paragraph(f"<font color='#6c5ce7'>Section: {section_ref}</font>", section_style))
            story.append(Paragraph(full_reason, reason_style))
            story.append(Spacer(1, 2))
            item_counter += 1

        category_counter += 1
        story.append(Spacer(1, 8))

    footer_style = ParagraphStyle(
        'Footer', parent=styles['Normal'], fontSize=8,
        textColor=colors.HexColor('#999999'), alignment=TA_CENTER, spaceBefore=20
    )
    story.append(Spacer(1, 15))
    story.append(Paragraph(f"Generated on {datetime.now().strftime('%B %d, %Y at %I:%M %p')}", footer_style))

    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()

def generate_full_results_pdf(results, file_name=None):
    if not results:
        return None

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, topMargin=40, bottomMargin=40)
    styles = getSampleStyleSheet()
    story = []

    title_style = ParagraphStyle(
        'MainTitle', parent=styles['Heading1'], fontSize=22,
        textColor=colors.HexColor('#1a1a2e'), alignment=TA_CENTER, spaceAfter=20
    )
    story.append(Paragraph("RFP Analysis Report", title_style))
    story.append(Spacer(1, 10))

    summary = results.get('project_summary', 'No summary available')
    summary_style = ParagraphStyle(
        'Summary', parent=styles['Normal'], fontSize=11,
        textColor=colors.HexColor('#333333'), spaceAfter=15
    )
    story.append(Paragraph("<b>Project Summary</b>", styles['Heading2']))
    story.append(Paragraph(summary, summary_style))
    story.append(Spacer(1, 10))

    go_no_go = results.get('go_no_go', {})
    if go_no_go:
        decision = go_no_go.get('overall_decision', 'UNDECIDED')
        score = go_no_go.get('overall_score', 0)

        story.append(Paragraph("<b>Go/No-Go Decision</b>", styles['Heading2']))
        decision_color = '#28a745' if decision == 'GO' else '#dc3545' if decision == 'NO-GO' else '#ffc107'
        story.append(Paragraph(f"<font color='{decision_color}' size='18'><b>{decision}</b></font>", styles['Normal']))
        story.append(Paragraph(f"Score: {min(100, round(score))}/100", styles['Normal']))
        story.append(Paragraph(f"<i>{go_no_go.get('summary', '')}</i>", styles['Normal']))
        story.append(Spacer(1, 10))

        go_count = go_no_go.get('go_count', 0)
        no_go_count = go_no_go.get('no_go_count', 0)
        escalate_count = go_no_go.get('escalate_count', 0)

        data = [
            ['Status', 'Count'],
            ['GO', str(go_count)],
            ['NO-GO', str(no_go_count)],
            ['ESCALATE', str(escalate_count)]
        ]
        table = Table(data, colWidths=[150, 100])
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#6c5ce7')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 12),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
            ('BACKGROUND', (0, 1), (-1, -1), colors.HexColor('#f8f9fa')),
            ('TEXTCOLOR', (0, 1), (-1, -1), colors.HexColor('#333333')),
            ('GRID', (0, 0), (-1, -1), 1, colors.HexColor('#dddddd')),
            ('FONTSIZE', (0, 1), (-1, -1), 10),
        ]))
        story.append(table)
        story.append(Spacer(1, 15))

    checklist = go_no_go.get('checklist', []) if go_no_go else []
    if checklist:
        story.append(Paragraph("<b>Checklist Evaluation</b>", styles['Heading2']))
        categories = {}
        for item in checklist:
            cat = item.get('category', 'Other')
            categories.setdefault(cat, []).append(item)

        for category, items in categories.items():
            story.append(Paragraph(f"{category} Department", styles['Heading3']))
            data = [['Checklist Item', 'Decision', 'Reason', 'Evidence']]
            for item in items:
                status = item.get('status', 'UNKNOWN')
                status_display = 'GO' if status == 'GO' else 'NO-GO' if status == 'NO-GO' else 'CONDITIONAL'
                data.append([
                    item.get('item', 'Unknown'),
                    status_display,
                    item.get('reason', ''),
                    item.get('evidence', '')[:50] + '...' if len(item.get('evidence', '')) > 50 else item.get('evidence', '')
                ])
            table = Table(data, colWidths=[120, 70, 150, 150])
            table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#6c5ce7')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 10),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 6),
                ('BACKGROUND', (0, 1), (-1, -1), colors.HexColor('#f8f9fa')),
                ('TEXTCOLOR', (0, 1), (-1, -1), colors.HexColor('#333333')),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#cccccc')),
                ('FONTSIZE', (0, 1), (-1, -1), 8),
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ]))
            story.append(table)
            story.append(Spacer(1, 10))

        total_go = go_no_go.get('go_count', 0)
        total_no_go = go_no_go.get('no_go_count', 0)
        total_conditional = go_no_go.get('conditional_count', 0)

        if total_no_go == 0 and total_go > 0:
            story.append(Paragraph(f"<font color='#28a745'><b>GO Decision.</b> All {total_go} items passed.</font>", styles['Normal']))
        elif total_no_go > 0:
            story.append(Paragraph(f"<font color='#dc3545'><b>NO-GO Decision.</b> {total_no_go} items failed.</font>", styles['Normal']))
        else:
            story.append(Paragraph(f"<font color='#ffc107'><b>CONDITIONAL Decision.</b> {total_conditional} items need review.</font>", styles['Normal']))
        story.append(Spacer(1, 15))

    deliverables = results.get('deliverables', [])
    if deliverables:
        story.append(Paragraph("<b>Deliverables Required by RFP</b>", styles['Heading2']))
        if isinstance(deliverables, list) and len(deliverables) > 0 and isinstance(deliverables[0], str):
            deliverables = [{"category": "General", "items": deliverables}]

        category_counter = 1
        for cat_group in deliverables:
            category = cat_group.get('category', 'Uncategorized')
            items = cat_group.get('items', [])
            if not items:
                continue

            story.append(Paragraph(f"{category_counter}. {category}", styles['Heading3']))
            item_counter = 1
            for item in items:
                if isinstance(item, dict):
                    item_name = item.get('name', 'Unknown')
                    section_ref = item.get('section_ref', 'N/A')
                    reason = item.get('reason', 'Required by RFP')
                    source_file = item.get('source_file', 'Unknown')
                else:
                    item_name = item
                    section_ref = 'N/A'
                    reason = 'Required by RFP'
                    source_file = 'Unknown'

                item_name = item_name.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                section_ref = section_ref.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                reason = reason.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                source_file = source_file.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

                if source_file and source_file != 'Unknown' and source_file != 'Unknown file':
                    source_display = source_file.replace('", "', ', ').replace('"', '')
                    file_display = f"[From: {source_display}]" if ',' in source_display else f"[From: {source_file}]"
                    full_reason = f"{file_display} {reason}"
                else:
                    full_reason = reason

                story.append(Paragraph(f"{category_counter}.{item_counter} <b>{item_name}</b>", styles['Normal']))
                story.append(Paragraph(f"Section: {section_ref}", styles['Normal']))
                story.append(Paragraph(full_reason, styles['Normal']))
                story.append(Spacer(1, 4))
                item_counter += 1

            category_counter += 1
            story.append(Spacer(1, 8))

    criteria = results.get('evaluation_criteria', [])
    if criteria:
        story.append(Paragraph("<b>Evaluation Criteria</b>", styles['Heading2']))
        for i, criterion in enumerate(criteria, 1):
            story.append(Paragraph(f"{i}. {criterion}", styles['Normal']))
        story.append(Spacer(1, 10))

    compliance = results.get('compliance_checklist', {})
    if compliance:
        story.append(Paragraph("<b>Compliance Checklist</b>", styles['Heading2']))
        for dept, tasks in compliance.items():
            story.append(Paragraph(f"{dept} Department", styles['Heading3']))
            for task in tasks:
                story.append(Paragraph(f"- {task}", styles['Normal']))
            story.append(Spacer(1, 5))

    footer_style = ParagraphStyle(
        'Footer', parent=styles['Normal'], fontSize=8,
        textColor=colors.HexColor('#999999'), alignment=TA_CENTER, spaceBefore=20
    )
    story.append(Spacer(1, 20))
    story.append(Paragraph(f"Generated on {datetime.now().strftime('%B %d, %Y at %I:%M %p')}", footer_style))

    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()

# ============================================================
# MAIN FUNCTION
# ============================================================
def main():
    if 'theme_mode' not in st.session_state:
        st.session_state['theme_mode'] = "Light"
    if 'pdf_view_request' not in st.session_state:
        st.session_state['pdf_view_request'] = None

    theme = get_theme()
    inject_theme_css(theme)

    st.title("RFP Document Processor")
    st.markdown("---")

    st.markdown("""
    #### AI-Powered RFP Analysis with Company Checklist
    Upload your RFP document(s) or paste text directly.
    """)

    with st.sidebar:
        st.header("Appearance")
        chosen_mode = st.radio("Theme", ["Light", "Dark"], index=["Light", "Dark"].index(st.session_state['theme_mode']))
        if chosen_mode != st.session_state['theme_mode']:
            st.session_state['theme_mode'] = chosen_mode
            st.rerun()

        st.markdown("---")
        st.header("Configuration")

        api_key = st.text_input(
            "Gemini API Key",
            type="password",
            value=os.getenv("GEMINI_API_KEY", ""),
            help="Enter your Google Gemini API key"
        )

        if api_key:
            st.success("API Key provided")
        else:
            st.warning("Please provide your Gemini API key")

        st.markdown("---")
        st.markdown("""
        #### Instructions
        1. Upload one or multiple files (PDF, DOCX, TXT)
        2. OR paste text below
        3. Click "Process Document"
        4. View the Go/No-Go decision with detailed checklist
        5. Click **View Source** next to any deliverable to jump to its exact page, highlighted
        6. Scroll to **Functional / Non-Functional Requirements** to see the classification
        """)

        st.markdown("---")
        st.subheader("Fetch Analysis by ID")
        fetch_id = st.text_input("Enter Analysis ID:", placeholder="e.g., RFP-20260727-a1b2c3d4")
        if st.button("Fetch Results", use_container_width=True):
            if fetch_id:
                if load_analysis_into_session(fetch_id):
                    st.success("Results loaded successfully")
                    st.rerun()
                else:
                    st.error("Analysis ID not found. Please check the ID.")

        st.markdown("---")
        st.subheader("Saved Analyses")
        saved_ids = get_all_analysis_ids()
        if saved_ids:
            recent_ids = saved_ids[-5:][::-1]
            older_ids = saved_ids[:-5][::-1]

            for aid in recent_ids:
                if st.button(aid, key=f"saved_id_{aid}", use_container_width=True):
                    if load_analysis_into_session(aid):
                        st.rerun()

            if older_ids:
                with st.expander(f"Show {len(older_ids)} more"):
                    for aid in older_ids:
                        if st.button(aid, key=f"saved_id_more_{aid}", use_container_width=True):
                            if load_analysis_into_session(aid):
                                st.rerun()
        else:
            st.caption("No saved analyses yet.")

    input_method = st.radio(
        "Choose input method:",
        ["Upload Files", "Paste Text"],
        horizontal=True
    )

    if input_method == "Upload Files":
        uploaded_files = st.file_uploader(
            "Choose one or more RFP documents",
            type=['pdf', 'docx', 'txt'],
            help="Supported formats: PDF, DOCX, TXT",
            accept_multiple_files=True
        )

        if uploaded_files:
            st.success(f"{len(uploaded_files)} document(s) uploaded")

            file_names = []
            total_size = 0
            for f in uploaded_files:
                file_names.append(f"{f.name} ({f.size/1024:.1f} KB)")
                total_size += f.size

            st.write(", ".join(file_names))
            st.info(f"Total size: {total_size/1024:.1f} KB")

            pdf_files_bytes = {}
            for f in uploaded_files:
                if f.name.lower().endswith('.pdf'):
                    f.seek(0)
                    pdf_files_bytes[f.name] = f.read()
                    f.seek(0)

            st.session_state['pdf_files_bytes'] = pdf_files_bytes
            st.session_state['uploaded_file_names'] = [f.name for f in uploaded_files]

            if st.button("Process Documents", type="primary"):
                if not api_key:
                    st.error("Please provide your Gemini API key in the sidebar")
                    return

                try:
                    combined_text = ""
                    file_paths = []
                    file_name_list = []

                    for uploaded_file in uploaded_files:
                        uploaded_file.seek(0)
                        file_name_list.append(uploaded_file.name)
                        with tempfile.NamedTemporaryFile(delete=False, suffix=f".{uploaded_file.name.split('.')[-1]}") as tmp_file:
                            tmp_file.write(uploaded_file.read())
                            file_paths.append(tmp_file.name)

                    processor = RFPProcessor(api_key)
                    pdf_raw_pages = {}

                    with st.spinner(f"Processing {len(uploaded_files)} document(s) with Gemini (6 agents running in parallel)..."):
                        for idx, file_path in enumerate(file_paths):
                            text = processor.extract_text(file_path)
                            file_label = file_name_list[idx]

                            if file_label.lower().endswith('.pdf'):
                                try:
                                    pdf_raw_pages[file_label] = extract_raw_pages_from_pdf(file_path)
                                except Exception:
                                    pdf_raw_pages[file_label] = []

                            combined_text += f"\n\n========================================\n"
                            combined_text += f"FILE: {file_label}\n"
                            combined_text += f"========================================\n\n"
                            combined_text += text + "\n\n"
                            os.unlink(file_path)

                        results = processor.run_full_analysis(combined_text)

                        analysis_id = generate_analysis_id()
                        save_analysis_results(analysis_id, results, pdf_files_bytes, pdf_raw_pages, combined_text)
                        st.session_state['analysis_id'] = analysis_id
                        st.session_state['results'] = results
                        st.session_state['processed'] = True
                        st.session_state['combined_text'] = combined_text
                        st.session_state['uploaded_file_names'] = file_name_list
                        st.session_state['pdf_raw_pages'] = pdf_raw_pages
                        clear_pdf_view()

                    st.success(f"All {len(uploaded_files)} document(s) processed successfully")
                    st.info(f"Analysis ID: `{analysis_id}`  \nShare this ID to let others fetch the results without re-uploading.")

                except Exception as e:
                    st.error(f"Error: {str(e)}")
                    for path in file_paths:
                        try:
                            os.unlink(path)
                        except:
                            pass

    else:
        pasted_text = st.text_area(
            "Paste your RFP text here:",
            height=300,
            placeholder="Paste the RFP content here..."
        )

        if pasted_text:
            st.info(f"{len(pasted_text)} characters pasted")
            st.session_state['text_input'] = pasted_text
            st.session_state['uploaded_files'] = None
            st.session_state['pdf_files_bytes'] = {}
            st.session_state['pdf_raw_pages'] = {}
            st.session_state['uploaded_file_names'] = ["Pasted Text"]

            if st.button("Process Document", type="primary"):
                if not api_key:
                    st.error("Please provide your Gemini API key in the sidebar")
                    return

                try:
                    processor = RFPProcessor(api_key)

                    with st.spinner("Processing text with Gemini (6 agents running in parallel)..."):
                        results = processor.run_full_analysis(pasted_text)

                        analysis_id = generate_analysis_id()
                        save_analysis_results(analysis_id, results)
                        st.session_state['analysis_id'] = analysis_id
                        st.session_state['results'] = results
                        st.session_state['processed'] = True
                        clear_pdf_view()

                    st.success("Document processed successfully")
                    st.info(f"Analysis ID: `{analysis_id}`  \nShare this ID to let others fetch the results without re-uploading.")
                    st.caption("Note: pasted text has no source PDF, so 'View Source' isn't available for these deliverables.")

                except Exception as e:
                    st.error(f"Error: {str(e)}")

    # ============================================================
    # DISPLAY RESULTS
    # ============================================================

    if 'processed' in st.session_state and st.session_state['processed']:
        results = st.session_state['results']
        analysis_id = st.session_state.get('analysis_id', 'unknown')

        if 'error' in results:
            st.error(f"Analysis error: {results['error']}")
            if st.button("Try Again"):
                st.session_state['processed'] = False
                st.session_state['results'] = None
                st.rerun()
            return

        st.markdown("---")
        st.subheader("Go/No-Go Decision Dashboard")
        render_go_no_go_dashboard(results.get('go_no_go', {}), theme)

        st.markdown("---")
        st.markdown("#### Checklist Evaluation")
        st.caption("Click any item to see the reasoning and RFP evidence behind its decision.")
        render_checklist_section(results.get('go_no_go', {}).get('checklist', []), theme)

        agent_meta = results.get('_agent_meta')
        if agent_meta:
            with st.expander("Multi-Agent Pipeline Performance", expanded=False):
                total_time = agent_meta.get('total_elapsed_seconds', 0)
                per_agent = agent_meta.get('per_agent_seconds', {})
                errors = agent_meta.get('errors', {})

                st.markdown(f"**Total wall-clock time:** {total_time}s (agents ran concurrently)")

                if per_agent:
                    sum_sequential = sum(per_agent.values())
                    st.caption(
                        f"If run sequentially, this would have taken ~{sum_sequential:.1f}s. "
                        f"Running in parallel saved ~{max(0, sum_sequential - total_time):.1f}s."
                    )
                    agent_labels = {
                        "summary": "Summary Agent",
                        "deliverables": "Deliverables Agent",
                        "evaluation_criteria": "Evaluation Criteria Agent",
                        "compliance_checklist": "Compliance Checklist Agent",
                        "go_no_go": "Go/No-Go Agent",
                    }
                    for agent_name, seconds in per_agent.items():
                        status = "error (used fallback)" if agent_name in errors else "succeeded"
                        st.write(f"{agent_labels.get(agent_name, agent_name)}: {seconds}s — {status}")

                if errors:
                    st.warning("Some agents fell back to default values after an error:")
                    for agent_name, err in errors.items():
                        st.caption(f"{agent_name}: {err}")

        st.markdown("---")
        st.subheader("Project Summary")
        st.info(results.get('project_summary', 'No summary available'))

        st.subheader("Deliverables Required by RFP")
        deliverables = results.get('deliverables', [])

        pdf_files_bytes = st.session_state.get('pdf_files_bytes', {})
        source_files_available = set(pdf_files_bytes.keys())

        render_deliverables(deliverables, source_files_available)
        render_pdf_viewer(theme)

        st.markdown("---")
        st.subheader("Evaluation Criteria")
        render_evaluation_criteria_section(results.get('evaluation_criteria', []), theme)

        st.markdown("---")
        st.subheader("Compliance Checklist")
        render_compliance_table_section(results.get('compliance_checklist', {}), theme)

        st.markdown("---")
        st.subheader("Add More Files to This Analysis")
        st.caption(
            "If the client sent additional documents, or an updated version of this RFP, "
            "upload them here. The AI will re-analyze using the combined content of the "
            "original file(s) plus these new ones, under the same Analysis ID."
        )
        additional_files = st.file_uploader(
            "Upload additional PDF, DOCX, or TXT file(s)",
            type=['pdf', 'docx', 'txt'],
            accept_multiple_files=True,
            key="additional_files_uploader"
        )
        if additional_files:
            if st.button("Add Files & Re-run Analysis", type="primary", key="add_files_btn"):
                if not api_key:
                    st.error("Please provide your Gemini API key in the sidebar")
                else:
                    add_file_paths = []
                    try:
                        processor = RFPProcessor(api_key)
                        existing_combined_text = st.session_state.get('combined_text', '')
                        existing_pdf_bytes = dict(st.session_state.get('pdf_files_bytes', {}))
                        existing_raw_pages = dict(st.session_state.get('pdf_raw_pages', {}))

                        new_text_sections = ""
                        add_file_name_list = []

                        for uploaded_file in additional_files:
                            uploaded_file.seek(0)
                            add_file_name_list.append(uploaded_file.name)
                            with tempfile.NamedTemporaryFile(delete=False, suffix=f".{uploaded_file.name.split('.')[-1]}") as tmp_file:
                                tmp_file.write(uploaded_file.read())
                                add_file_paths.append(tmp_file.name)
                            if uploaded_file.name.lower().endswith('.pdf'):
                                uploaded_file.seek(0)
                                existing_pdf_bytes[uploaded_file.name] = uploaded_file.read()

                        with st.spinner(f"Processing {len(additional_files)} new file(s) and re-analyzing the combined RFP..."):
                            for idx, file_path in enumerate(add_file_paths):
                                text = processor.extract_text(file_path)
                                file_label = add_file_name_list[idx]

                                if file_label.lower().endswith('.pdf'):
                                    try:
                                        existing_raw_pages[file_label] = extract_raw_pages_from_pdf(file_path)
                                    except Exception:
                                        existing_raw_pages[file_label] = []

                                new_text_sections += "\n\n========================================\n"
                                new_text_sections += f"FILE: {file_label}\n"
                                new_text_sections += "========================================\n\n"
                                new_text_sections += text + "\n\n"
                                os.unlink(file_path)

                            merged_combined_text = existing_combined_text + new_text_sections
                            new_results = processor.run_full_analysis(merged_combined_text)

                            save_analysis_results(
                                analysis_id, new_results, existing_pdf_bytes,
                                existing_raw_pages, merged_combined_text
                            )

                            st.session_state['results'] = new_results
                            st.session_state['combined_text'] = merged_combined_text
                            st.session_state['pdf_files_bytes'] = existing_pdf_bytes
                            st.session_state['pdf_raw_pages'] = existing_raw_pages
                            clear_pdf_view()

                        st.success(
                            f"Analysis updated with {len(additional_files)} new file(s) "
                            f"under the same Analysis ID: `{analysis_id}`"
                        )
                        st.rerun()

                    except Exception as e:
                        st.error(f"Error while adding files: {str(e)}")
                        for path in add_file_paths:
                            try:
                                os.unlink(path)
                            except:
                                pass

        st.markdown("---")
        st.subheader("Download Reports")

        col1, col2 = st.columns(2)
        with col1:
            if deliverables:
                pdf_data = generate_deliverables_pdf(deliverables)
                if pdf_data:
                    st.download_button(
                        label="Download Deliverables PDF",
                        data=pdf_data,
                        file_name=f"deliverables_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf",
                        mime="application/pdf",
                        use_container_width=True
                    )
        with col2:
            full_pdf_data = generate_full_results_pdf(results)
            if full_pdf_data:
                st.download_button(
                    label="Download Full Report PDF",
                    data=full_pdf_data,
                    file_name=f"full_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf",
                    mime="application/pdf",
                    use_container_width=True
                )

        st.markdown("---")
        st.subheader("Download Raw Data")

        col1, col2 = st.columns(2)
        with col1:
            results_for_download = {
                k: v for k, v in results.items()
                if not k.startswith('_pdf') and k != '_combined_text'
            }
            json_str = json.dumps(results_for_download, indent=2, ensure_ascii=False)
            st.download_button(
                label="Download Full Analysis (JSON)",
                data=json_str,
                file_name=f"analysis_{analysis_id}.json",
                mime="application/json",
                use_container_width=True
            )
        with col2:
            if st.button("Process New Document", use_container_width=True):
                st.session_state['processed'] = False
                st.session_state['results'] = None
                st.session_state['pdf_files_bytes'] = {}
                st.session_state['pdf_raw_pages'] = {}
                clear_pdf_view()
                st.rerun()

        st.markdown("---")
        st.caption(f"Analysis ID: `{analysis_id}`")

if __name__ == "__main__":
    main()