# 📄 RFP Analyzer

**AI-powered Go/No-Go decision support tool for Request for Proposal (RFP) documents.**

Upload an RFP, compare it against your company's profile, and get an instant fit score, verdict, and department-by-department compliance breakdown — instead of manually reading 20+ pages to figure out whether you should even bid.

---

## 🚀 What It Does

Most RFPs bury the things that actually matter — insurance minimums, bond requirements, mandatory deliverables, evaluation weights — inside dense procurement language. RFP Analyzer reads the document, compares it against a configurable company profile, and surfaces:

- ✅ **Fit Score (0–100)** and a clear verdict — `GO` / `CONDITIONAL` / `NO-GO`
- 📦 **Deliverables** — mandatory vs. optional, with effort estimates
- 📊 **Evaluation Criteria** — weighted scoring breakdown as defined in the RFP
- 🧾 **Compliance Checklist** — split by department (**Legal**, **Accounting**, **Technical**, **Operations**), each item flagged `MET` / `GAP` / `REVIEW`
- 📅 **Key Dates & Budget** — deadlines, contract value, bonding requirements
- ⚠️ **Risk Assessment** — top risks and a go/no-go recommendation with reasoning

Export the result as a clean **Markdown report** or a styled **PDF case file**.

---

## 🛠️ Tech Stack

| Layer | Tool |
|---|---|
| UI | [Streamlit](https://streamlit.io/) |
| PDF text extraction | [pdfplumber](https://github.com/jsvine/pdfplumber) |
| AI analysis | [Google Gemini](https://ai.google.dev/) (`gemini-2.5-flash`, structured JSON output) |
| PDF report generation | [ReportLab](https://www.reportlab.com/) |
| Config | `python-dotenv` |

---

## 📂 Project Structure

```
RFP_analyzer/
├── app.py            # Streamlit UI — upload, company profile, results, downloads
├── ai_engine.py       # Gemini prompt, structured JSON parsing, retry/backoff logic
├── pdf_reader.py       # PDF text extraction (pdfplumber)
├── pdf_report.py       # Generates the styled downloadable PDF report
└── .env                 # GEMINI_API_KEY (not committed)
```

---

## ⚙️ How It Works

1. **Upload** an RFP PDF through the Streamlit interface.
2. **Extract** text from every page (`pdf_reader.py`).
3. **Define your company profile** — insurance coverage, certifications, revenue, capabilities — editable directly in the sidebar.
4. **Analyze** — the RFP text and company profile are sent to Gemini with a strict JSON schema, so the model returns structured data (not free-text) covering deliverables, evaluation criteria, compliance status per department, dates/budget, and an overall fit assessment.
5. **Review** results in-app across sectioned tabs, with a verdict card and key metrics (deliverables count, total weeks, requirements met, compliance gaps) up top.
6. **Export** the full analysis as Markdown or PDF.

---

## 🧠 Key Design Decisions

- **Structured JSON over free-text parsing** — early versions asked the model for markdown and sliced it by header position, which was fragile. Forcing strict JSON output (`response_mime_type: application/json`) makes scoring, counting, and status badges computable rather than guessed.
- **Retry with backoff, but quota-aware** — transient errors (429 rate limits, 500/503 server errors) are retried automatically with exponential/linear backoff. Daily quota exhaustion is detected separately and fails fast with a clear message, since retrying a daily cap is pointless.
- **Two-step "set flag, then rerun" pattern** — Streamlit reruns the whole script on every interaction, so the upload/analyze flow uses `st.session_state` to avoid re-triggering expensive AI calls on unrelated UI interactions.

---

## ▶️ Running Locally

```bash
git clone https://github.com/Yaqoob-hassan/SPS_Internship.git
cd SPS_Internship/RFP_analyzer

pip install streamlit pdfplumber google-generativeai python-dotenv reportlab

# Add your Gemini API key
echo "GEMINI_API_KEY=your_key_here" > .env

streamlit run app.py
```

---

## 📌 Notes

- Built and tested against a synthetic sample RFP (a fictional city government permitting-system procurement) designed to exercise every analysis path — mandatory/optional deliverables, weighted criteria, and a deliberate insurance compliance gap.
- Gemini's free tier caps requests at 20/day per project — for heavier testing, enable billing on the underlying Google Cloud project.
- This project was built as part of the **SPS Internship** under [Yaqoob-hassan/SPS_Internship](https://github.com/Yaqoob-hassan/SPS_Internship).

---

*Part of [@Yaqoob-hassan](https://github.com/Yaqoob-hassan)'s internship project portfolio.*
