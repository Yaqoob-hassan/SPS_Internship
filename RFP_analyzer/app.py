import streamlit as st
from pdf_reader import extract_text_from_pdf
from ai_engine import analyze_rfp, RfpAnalysisError

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
if "fname" not in st.session_state:
    st.session_state.fname = None
if "result" not in st.session_state:
    st.session_state.result = None

# ---------- sidebar: company profile ----------
with st.sidebar:
    st.header("🏢 Company Profile")
    st.caption("This is compared against the RFP to compute fit score and compliance gaps.")
    company_profile = st.text_area("Edit your company profile", value=DEFAULT_PROFILE, height=420)

# ---------- main ----------
st.title("📄 RFP Intelligence — Go/No-Go Analyzer")
st.write("Upload an RFP and get an automated pre-bid case file: fit score, deliverables, evaluation criteria, and a department-by-department compliance checklist.")

uploaded_file = st.file_uploader("Upload your RFP (PDF only)", type=["pdf"])

if uploaded_file is not None:
    st.success(f"File uploaded: {uploaded_file.name}")

if st.button("Analyze RFP", type="primary"):
    if uploaded_file is None:
        st.error("Please upload a PDF file first.")
    else:
        st.session_state.ready = True
        st.session_state.fname = uploaded_file.name
        st.rerun()

if st.session_state.ready:
    st.session_state.ready = False

    with st.spinner("Reading PDF..."):
        try:
            rfp_text = extract_text_from_pdf(uploaded_file)
        except Exception as e:
            st.error(f"Failed to read PDF: {e}")
            st.stop()

    if not rfp_text.strip():
        st.error("No text found. This PDF may be a scanned image.")
        st.stop()

    st.info(f"Extracted {len(rfp_text):,} characters.")

    status_box = st.status("Analyzing fit against your company profile...", expanded=False)

    def handle_retry(attempt, wait_seconds, reason):
        status_box.update(
            label=f"Hit a {reason} (attempt {attempt}/3) — retrying in {wait_seconds:.0f}s...",
            state="running",
        )

    try:
        with status_box:
            st.session_state.result = analyze_rfp(rfp_text, company_profile, on_retry=handle_retry)
        status_box.update(label="Analysis complete.", state="complete")
    except RfpAnalysisError as e:
        status_box.update(label="Analysis failed.", state="error")
        st.error(str(e))
        st.stop()
    except Exception as e:
        status_box.update(label="Analysis failed.", state="error")
        st.error(f"AI analysis failed: {e}")
        st.stop()

result = st.session_state.result

if result:
    st.divider()

    verdict = result.get("verdict", "CONDITIONAL")
    vstyle = VERDICT_STYLE.get(verdict, VERDICT_STYLE["CONDITIONAL"])
    fit_score = result.get("fit_score", 0)

    deliverables = result.get("deliverables", [])
    total_weeks = sum(d.get("weeks_estimate", 0) for d in deliverables)
    compliance = result.get("compliance", {})
    all_items = [item for dept_items in compliance.values() for item in dept_items]
    met_count = sum(1 for i in all_items if i.get("status") == "MET")
    gap_count = sum(1 for i in all_items if i.get("status") == "GAP")

    # ---------- hero card ----------
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
    c1.metric("Deliverables", len(deliverables))
    c2.metric("Est. Weeks Total", total_weeks)
    c3.metric("Requirements Met", met_count)
    c4.metric("Compliance Gaps", gap_count)

    st.divider()

    tabs = st.tabs([
        "📦 Deliverables", "📊 Evaluation Criteria", "✅ Compliance Checklist",
        "📅 Dates & Budget", "🎯 Opportunity Assessment",
    ])

    # --- Deliverables ---
    with tabs[0]:
        for i, d in enumerate(deliverables, 1):
            tag = "🔴 MANDATORY" if d.get("mandatory") else "🟢 OPTIONAL"
            st.markdown(f"**{i:02d}. {d.get('title', '')}**  \n{d.get('description', '')}")
            st.caption(f"{tag} · {d.get('weeks_estimate', '?')} wk estimate")
            st.divider()

    # --- Evaluation Criteria ---
    with tabs[1]:
        for c in result.get("evaluation_criteria", []):
            col_a, col_b = st.columns([4, 1])
            col_a.markdown(f"**{c.get('name', '')}**  \n{c.get('description', '')}")
            col_b.markdown(f"### {c.get('weight_pct', '?')}%")
            st.divider()

    # --- Compliance Checklist ---
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

    # --- Dates & Budget ---
    with tabs[3]:
        kb = result.get("key_dates_budget", {})
        labels = {
            "submission_deadline": "Submission Deadline",
            "pre_proposal_conference": "Pre-proposal Conference",
            "qa_deadline": "Q&A Deadline",
            "project_timeline": "Project Timeline",
            "total_budget": "Total Budget",
            "bond_requirements": "Bond Requirements",
        }
        for key, label in labels.items():
            st.markdown(f"**{label}:** {kb.get(key, 'Not specified.')}")

    # --- Opportunity Assessment ---
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

    # ---------- downloadable report ----------
    def fmt_list(items):
        return "\n".join(f"- {i}" for i in items) if items else "_None._"

    def fmt_deliverables():
        out = []
        for i, d in enumerate(deliverables, 1):
            tag = "MANDATORY" if d.get("mandatory") else "OPTIONAL"
            out.append(f"{i}. **{d.get('title','')}** ({tag}, {d.get('weeks_estimate','?')} wk)\n   {d.get('description','')}")
        return "\n\n".join(out)

    def fmt_eval():
        out = []
        for c in result.get("evaluation_criteria", []):
            out.append(f"- **{c.get('name','')}** — {c.get('weight_pct','?')}%\n  {c.get('description','')}")
        return "\n".join(out)

    def fmt_compliance():
        out = []
        for dept in ["Legal", "Accounting", "Technical", "Operations"]:
            items = compliance.get(dept, [])
            if not items:
                continue
            out.append(f"### {dept}\n")
            for item in items:
                out.append(f"- [{item.get('status','REVIEW')}] {item.get('requirement','')}\n  {item.get('note','')}")
        return "\n".join(out)

    kb = result.get("key_dates_budget", {})
    report = f"""# RFP Analysis: {st.session_state.fname}

**Verdict:** {vstyle['label']} — Fit Score: {fit_score}/100

{result.get('headline_summary', '')}

---

## 1. DELIVERABLES & TIME ESTIMATE

{fmt_deliverables()}

---

## 2. EVALUATION CRITERIA

{fmt_eval()}

---

## 3. DEPARTMENT COMPLIANCE CHECKLIST

{fmt_compliance()}

---

## 4. KEY DATES & BUDGET

- Submission Deadline: {kb.get('submission_deadline', 'Not specified.')}
- Pre-proposal Conference: {kb.get('pre_proposal_conference', 'Not specified.')}
- Q&A Deadline: {kb.get('qa_deadline', 'Not specified.')}
- Project Timeline: {kb.get('project_timeline', 'Not specified.')}
- Total Budget: {kb.get('total_budget', 'Not specified.')}
- Bond Requirements: {kb.get('bond_requirements', 'Not specified.')}

---

## 5. OPPORTUNITY ASSESSMENT

**Key reasons:**
{fmt_list(result.get('opportunity_assessment', {}).get('key_reasons', []))}

**Potential disqualifiers:**
{fmt_list(result.get('opportunity_assessment', {}).get('potential_disqualifiers', []))}
"""

    st.divider()
    st.download_button("⬇ Download Full Report", report, "rfp_analysis.md", "text/markdown")