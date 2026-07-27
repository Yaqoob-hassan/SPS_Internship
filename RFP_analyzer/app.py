import json
import streamlit as st
from pdf_reader import extract_text_from_pdf
from ai_engine import analyze_rfp, RfpAnalysisError
from pdf_report import generate_pdf_report, generate_deliverables_pdf
from history import save_analysis, list_history, load_analysis

st.set_page_config(page_title="RFP Intelligence — Go/No-Go Analyzer", page_icon="📄", layout="wide")

VERDICT_STYLE = {
    "GO":          {"color": "#1a7f37", "bg": "#e6f4ea", "label": "GO"},
    "CONDITIONAL": {"color": "#b8860b", "bg": "#fdf3e0", "label": "PROCEED WITH CAUTION"},
    "NO-GO":       {"color": "#b42318", "bg": "#fdeaea", "label": "NO-GO"},
}

STATUS_STYLE = {
    "MET":    {"icon": "✅", "color": "#1a7f37", "label": "MET"},
    "GAP":    {"icon": "❌", "color": "#b42318", "label": "GAP"},
    "REVIEW": {"icon": "❓", "color": "#b8860b", "label": "REVIEW"},
}

DEFAULT_PROFILE = """Company: Example Civic Tech Solutions
Annual revenue: ~$8M/year (under $10M)
Insurance: Commercial General Liability $2,000,000 per occurrence; Cyber Liability $2,000,000 per occurrence
Certifications: SOC 2 Type II, ISO 27001
Hosting: AWS GovCloud (US-based), cloud SaaS only, no on-prem offering
Security: AES-256 at rest, TLS 1.2+ in transit, SSO/SAML 2.0 integration with Azure AD
APIs: Documented, versioned REST APIs for integrations
Past performance: 4 completed municipal/county digital-services projects in the last 5 years
In-house capabilities: Web portals, workflow engines, payment gateway integrations, reporting dashboards
Not in-house: Native mobile app development, GIS/ArcGIS integration (would require a subcontractor)
Standard payment terms accepted: Net 30 (City may require Net 45)
"""

# ---------- session state ----------
if "ready" not in st.session_state:
    st.session_state.ready = False
if "result" not in st.session_state:
    st.session_state.result = None      # single combined analysis dict
if "combined_label" not in st.session_state:
    st.session_state.combined_label = ""  # display name for the combined run
if "history_id" not in st.session_state:
    st.session_state.history_id = None    # unique id of the currently-shown result
if "loaded_from_history" not in st.session_state:
    st.session_state.loaded_from_history = False

# ---------- sidebar ----------
with st.sidebar:
    st.header("🏢 Company Profile")
    st.caption("This is compared against the uploaded RFPs to compute fit score and compliance gaps.")
    company_profile = st.text_area("Edit your company profile", value=DEFAULT_PROFILE, height=300)

    st.divider()
    st.header("🔎 Load Saved Analysis")
    st.caption("Search by ID or filename — loads instantly from disk, no AI call.")

    history_records = list_history()

    if history_records:
        options = ["— New analysis —"] + [r["id"] for r in history_records]
        labels = {
            r["id"]: f"{r['id']}  ·  {r['fname']}  ·  {r.get('verdict', '?')} ({r.get('fit_score', '?')})"
            for r in history_records
        }

        def _fmt(option_id):
            return "— New analysis —" if option_id == "— New analysis —" else labels.get(option_id, option_id)

        chosen_id = st.selectbox(
            "Type to search saved analyses by ID or filename",
            options=options,
            format_func=_fmt,
            index=0,
            key="history_select",
        )

        if chosen_id != "— New analysis —" and chosen_id != st.session_state.history_id:
            record = load_analysis(chosen_id)
            st.session_state.result = record["result"]
            st.session_state.combined_label = record.get("fname", chosen_id)
            st.session_state.history_id = chosen_id
            st.session_state.loaded_from_history = True
            st.session_state.ready = False
            st.rerun()
    else:
        st.caption("No saved analyses yet — run one below and it will appear here.")

# ---------- main ----------
st.title("📄 RFP Intelligence — Go/No-Go Analyzer")
st.write(
    "Upload one or more RFPs. All documents are combined and analysed together, "
    "producing a single unified deliverable list, compliance checklist, and Go/No-Go verdict."
)

uploaded_files = st.file_uploader(
    "Upload RFPs (PDF only — upload as many as you like)",
    type=["pdf"],
    accept_multiple_files=True,
)

if uploaded_files:
    st.success(
        f"{len(uploaded_files)} file(s) ready: " + ", ".join(f.name for f in uploaded_files)
    )

if st.button("Analyze", type="primary"):
    if not uploaded_files:
        st.error("Please upload at least one PDF file.")
    else:
        st.session_state.ready = True
        st.session_state.loaded_from_history = False
        st.session_state.history_id = None
        st.rerun()

# ---------- pipeline ----------
if st.session_state.ready:
    st.session_state.ready = False
    st.session_state.result = None

    # ── Step 1: extract text from every uploaded PDF ──────────────────────
    progress = st.progress(0.0, text="Reading PDFs...")
    total = len(uploaded_files)
    combined_parts = []   # list of strings, one per PDF
    failures = []

    for idx, f in enumerate(uploaded_files, start=1):
        progress.progress((idx - 1) / total, text=f"Reading {f.name} ({idx}/{total})...")
        try:
            text = extract_text_from_pdf(f)
        except Exception as e:
            failures.append((f.name, f"Failed to read PDF: {e}"))
            continue

        if not text.strip():
            failures.append((f.name, "No text found — this PDF may be a scanned image."))
            continue

        # wrap each document with a clear separator so the AI knows which
        # document each section of text came from
        combined_parts.append(
            f"{'=' * 60}\n"
            f"DOCUMENT {idx} OF {total}: {f.name}\n"
            f"{'=' * 60}\n"
            f"{text.strip()}"
        )

    progress.progress(1.0, text="All PDFs read.")

    if failures:
        with st.expander(f"⚠️ {len(failures)} file(s) could not be read", expanded=True):
            for fname, msg in failures:
                st.error(f"**{fname}**: {msg}")

    if not combined_parts:
        st.error("No readable text was found in any of the uploaded files. Cannot proceed.")
        st.stop()

    # ── Step 2: combine all texts into one and send a SINGLE AI call ──────
    combined_text = "\n\n".join(combined_parts)
    fnames = [f.name for f in uploaded_files if f.name not in [x[0] for x in failures]]
    label = fnames[0] if len(fnames) == 1 else f"Combined ({len(fnames)} RFPs)"
    st.session_state.combined_label = label

    char_count = len(combined_text)
    st.info(f"📄 {len(combined_parts)} document(s) combined — {char_count:,} characters total. Sending to AI...")

    status_box = st.status("Analyzing combined RFPs against your company profile...", expanded=False)

    def handle_retry(attempt, wait_seconds, reason):
        status_box.update(
            label=f"Hit a {reason} (attempt {attempt}/3) — retrying in {wait_seconds:.0f}s...",
            state="running",
        )

    try:
        with status_box:
            st.session_state.result = analyze_rfp(
                combined_text,
                company_profile,
                on_retry=handle_retry,
                num_documents=len(combined_parts),
            )
        status_box.update(label="Analysis complete.", state="complete")
    except RfpAnalysisError as e:
        status_box.update(label="Analysis failed.", state="error")
        st.error(str(e))
        st.stop()
    except Exception as e:
        status_box.update(label="Analysis failed.", state="error")
        st.error(f"AI analysis failed: {e}")
        st.stop()

    # ── Step 3: persist to history so it can be reloaded later without AI ──
    try:
        new_id = save_analysis(label, st.session_state.result)
        st.session_state.history_id = new_id
        st.session_state.loaded_from_history = False
    except Exception as e:
        st.warning(f"Analysis succeeded but could not be saved to history: {e}")

# ---------- render result ----------
result = st.session_state.result
label  = st.session_state.combined_label


def total_deliverable_weeks(deliverables):
    total = 0
    for d in deliverables:
        children = d.get("children", [])
        if children:
            total += sum(c.get("weeks_estimate", 0) for c in children)
        else:
            total += d.get("weeks_estimate", 0)
    return total


if result:
    st.divider()

    if st.session_state.history_id:
        if st.session_state.loaded_from_history:
            st.caption(f"📂 Loaded from saved history — ID: `{st.session_state.history_id}` (no AI call made)")
        else:
            st.caption(f"💾 Saved — ID: `{st.session_state.history_id}` (search for it in the sidebar anytime)")

    verdict  = result.get("verdict", "CONDITIONAL")
    vstyle   = VERDICT_STYLE.get(verdict, VERDICT_STYLE["CONDITIONAL"])
    fit_score = result.get("fit_score", 0)

    deliverables = result.get("deliverables", [])
    total_weeks  = total_deliverable_weeks(deliverables)
    total_items  = sum(len(d.get("children", [])) or 1 for d in deliverables)

    compliance = result.get("compliance", {})
    all_items  = [item for dept_items in compliance.values() for item in dept_items]
    met_count  = sum(1 for i in all_items if i.get("status") == "MET")
    gap_count  = sum(1 for i in all_items if i.get("status") == "GAP")

    # ── hero card ─────────────────────────────────────────────────────────
    st.markdown(
        f"""
        <div style="border:1px solid #ddd; border-radius:10px; padding:24px; background:{vstyle['bg']};">
            <div style="display:flex; justify-content:space-between; align-items:center; gap:24px; flex-wrap:wrap;">
                <div style="flex:2; min-width:280px;">
                    <div style="color:{vstyle['color']}; font-weight:700; font-size:1.1rem; letter-spacing:0.5px;">
                        {vstyle['label']}
                    </div>
                    <p style="margin-top:8px; color:#333;">{result.get('headline_summary', '')}</p>
                </div>
                <div style="flex:1; min-width:160px; text-align:center;">
                    <div style="font-size:3rem; font-weight:800; color:{vstyle['color']};">{fit_score}</div>
                    <div style="color:#666; font-size:0.85rem;">FIT / 100</div>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.write("")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Deliverables", total_items)
    c2.metric("Est. Weeks Total", total_weeks)
    c3.metric("Requirements Met", met_count)
    c4.metric("Compliance Gaps", gap_count)

    st.divider()

    tabs = st.tabs([
        "📦 Deliverables", "📊 Evaluation Criteria", "✅ Compliance Checklist",
        "📅 Dates & Budget", "🎯 Opportunity Assessment", "⬇ Downloads",
    ])

    # ── Deliverables ──────────────────────────────────────────────────────
    with tabs[0]:
        for i, d in enumerate(deliverables, 1):
            tag      = "🔴 MANDATORY" if d.get("mandatory") else "🟢 OPTIONAL"
            children = d.get("children", [])
            parent_wk = (
                sum(c.get("weeks_estimate", 0) for c in children)
                if children else d.get("weeks_estimate", "?")
            )
            st.markdown(f"**{i}. {d.get('title', '')}**")
            if d.get("description"):
                st.caption(d.get("description"))
            st.caption(f"{tag} · {parent_wk} wk estimate (total)")

            for j, c in enumerate(children, 1):
                ctag = "🔴 MANDATORY" if c.get("mandatory") else "🟢 OPTIONAL"
                st.markdown(
                    f"<div style='margin-left:28px; border-left:2px solid #e0e0e0;"
                    f"padding-left:14px; margin-bottom:10px;'>"
                    f"<b>{i}.{j} {c.get('title', '')}</b><br>"
                    f"<span style='color:#555;'>{c.get('description', '')}</span><br>"
                    f"<span style='color:#888; font-size:0.85rem;'>"
                    f"{ctag} · {c.get('weeks_estimate', '?')} wk estimate</span>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
            st.divider()

    # ── Evaluation Criteria ───────────────────────────────────────────────
    with tabs[1]:
        for c in result.get("evaluation_criteria", []):
            col_a, col_b = st.columns([4, 1])
            col_a.markdown(f"**{c.get('name', '')}**  \n{c.get('description', '')}")
            col_b.markdown(f"### {c.get('weight_pct', '?')}%")
            st.divider()

    # ── Compliance Checklist ──────────────────────────────────────────────
    with tabs[2]:
        for dept in ["Legal", "Accounting", "Technical", "Operations"]:
            items = compliance.get(dept, [])
            if not items:
                continue
            st.markdown(f"#### {dept}")
            for item in items:
                s = STATUS_STYLE.get(item.get("status", "REVIEW"), STATUS_STYLE["REVIEW"])
                st.markdown(
                    f"{s['icon']} **{item.get('requirement', '')}**  "
                    f"<span style='color:{s['color']}; font-weight:600; float:right;'>{s['label']}</span>",
                    unsafe_allow_html=True,
                )
                st.caption(item.get("note", ""))
            st.write("")

    # ── Dates & Budget ────────────────────────────────────────────────────
    with tabs[3]:
        kb = result.get("key_dates_budget", {})
        labels = {
            "submission_deadline":    "Submission Deadline",
            "pre_proposal_conference": "Pre-proposal Conference",
            "qa_deadline":            "Q&A Deadline",
            "project_timeline":       "Project Timeline",
            "total_budget":           "Total Budget",
            "bond_requirements":      "Bond Requirements",
        }
        for key, lbl in labels.items():
            st.markdown(f"**{lbl}:** {kb.get(key, 'Not specified.')}")

    # ── Opportunity Assessment ────────────────────────────────────────────
    with tabs[4]:
        oa = result.get("opportunity_assessment", {})
        st.markdown(f"**Key reasons behind the {vstyle['label']} call:**")
        for r in oa.get("key_reasons", []):
            st.markdown(f"- {r}")
        disqualifiers = oa.get("potential_disqualifiers", [])
        if disqualifiers:
            st.markdown("**Potential disqualifiers:**")
            for d in disqualifiers:
                st.markdown(f"- :red[{d}]")

    # ── Downloads ─────────────────────────────────────────────────────────
    with tabs[5]:
        base = label.replace(" ", "_").replace("(", "").replace(")", "").replace(",", "")
        id_suffix = f"_{st.session_state.history_id}" if st.session_state.history_id else ""

        dcol1, dcol2, dcol3 = st.columns(3)

        with dcol1:
            try:
                deliv_pdf = generate_deliverables_pdf(deliverables, label)
                st.download_button(
                    "⬇ Deliverables (PDF)",
                    deliv_pdf,
                    file_name=f"deliverables_{base}.pdf",
                    mime="application/pdf",
                    use_container_width=True,
                )
            except Exception as e:
                st.error(f"Could not generate deliverables PDF: {e}")

        with dcol2:
            try:
                full_pdf = generate_pdf_report(result, label)
                st.download_button(
                    "⬇ Full Report (PDF)",
                    full_pdf,
                    file_name=f"rfp_analysis_{base}.pdf",
                    mime="application/pdf",
                    use_container_width=True,
                    type="primary",
                )
            except Exception as e:
                st.error(f"Could not generate full PDF: {e}")

        with dcol3:
            try:
                json_bytes = json.dumps(result, ensure_ascii=False, indent=2).encode("utf-8")
                st.download_button(
                    "⬇ Full Report (JSON)",
                    json_bytes,
                    file_name=f"rfp_analysis_{base}{id_suffix}.json",
                    mime="application/json",
                    use_container_width=True,
                )
            except Exception as e:
                st.error(f"Could not generate JSON export: {e}")

        if st.session_state.history_id:
            st.caption(
                f"This analysis is saved under ID **{st.session_state.history_id}** — "
                f"search for it in the sidebar to reload it instantly, no AI call needed."
            )