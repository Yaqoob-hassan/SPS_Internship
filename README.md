<h1 align="center">📄 RFP Go/No-Go Analyzer</h1>
<h3 align="center">AI-Powered Multi-Agent RFP Analysis Platform</h3>

<p align="center">
  <img src="https://readme-typing-svg.demolab.com?font=Fira+Code&size=20&pause=1000&color=2F81F7&center=true&vCenter=true&width=650&lines=Upload+an+RFP+%E2%86%92+Get+an+Instant+Go%2FNo-Go+Decision;5+AI+Agents+Running+in+Parallel;Deliverables+%7C+Checklist+%7C+Evaluation+Criteria;Built+as+a+Team+SPS+Internship+Project" alt="Typing SVG" />
</p>

---

### 👥 Contributors

This project is built and maintained by:

<p align="left">
  <a href="https://github.com/Yaqoob-hassan" target="_blank">
    <img src="https://img.shields.io/badge/Muhammad%20Yaqoob%20Hassan-181717?style=for-the-badge&logo=github&logoColor=white" />
  </a>
  <a href="https://github.com/MominaAsim-dev" target="_blank">
    <img src="https://img.shields.io/badge/Momina%20Asim-181717?style=for-the-badge&logo=github&logoColor=white" />
  </a>
  <a href="https://github.com/NimraAkhlaq" target="_blank">
    <img src="https://img.shields.io/badge/Nimra%20Akhlaq-181717?style=for-the-badge&logo=github&logoColor=white" />
  </a>
</p>

> Click either badge above to visit that contributor's GitHub profile.

> **Note:** Only the [`RFP_Team_Project`](./RFP_Team_Project) folder in this repository is the joint work of the whole team. Other folders in this repo belong to individual work and are not part of this collaboration.

---

### 🚀 About the Project

This is an **AI-powered RFP (Request for Proposal) analysis tool** built with Streamlit and Google Gemini, developed as part of our SPS internship. The idea is simple: upload an RFP document, and instead of a person spending hours reading it manually, our system:

- 📥 Ingests one or more RFP files (PDF, DOCX, or pasted text)
- 🤖 Runs the document through **5 independent AI agents in parallel**
- ✅ Returns a clear **GO / NO-GO / CONDITIONAL** decision against a company checklist
- 📌 Extracts **deliverables**, **evaluation criteria**, and a **department-wise compliance checklist**
- 🔍 Lets you jump straight to the **exact page and highlighted text** in the original PDF for any deliverable

---

### 🧠 Multi-Agent Architecture

Instead of one giant prompt trying to do everything at once, the analysis is split into **single-responsibility agents** that run concurrently via a thread pool:

| Agent | Responsibility |
|---|---|
| 📝 **Summary Agent** | Generates a concise 2–3 sentence project summary |
| 📦 **Deliverables Agent** | Extracts deliverables by category, with section references and exact source quotes |
| 📊 **Evaluation Criteria Agent** | Extracts the criteria the client will use to judge proposals |
| ✅ **Compliance Checklist Agent** | Builds a department-wise checklist (Legal, Accounting, Technical, Operations, HR) |
| 🚦 **Go/No-Go Agent** | Scores the RFP against our company checklist and returns a bid/no-bid decision |

**Why this matters:** if one agent fails or returns a bad response, it falls back to a safe default — it does **not** crash or block the rest of the analysis. Every other agent's results still come through normally, so a single failure never takes down the whole pipeline.

---

### ✨ Key Features

- 📁 **Multi-file upload** — combine multiple RFP documents (PDF, DOCX, TXT) into a single analysis
- ⚡ **Parallel processing** — all 5 agents run concurrently, cutting total analysis time dramatically compared to a sequential pipeline
- 🎯 **Go/No-Go Dashboard** — visual decision card + pie chart breakdown of GO / NO-GO / Conditional items
- 🔦 **View Source** — click any deliverable to jump to the exact page of the original PDF, with the matching text automatically highlighted
- 🆔 **Analysis IDs** — every analysis is saved and retrievable later by a shareable ID, without re-uploading files
- ➕ **Add More Files** — append additional documents to an existing analysis and re-run it under the same ID
- 📑 **Downloadable Reports** — export a Deliverables PDF, a Full Report PDF, or the raw analysis as JSON
- 🌗 **Light/Dark Theme** — clean cream-and-lilac light mode and a soft greyish dark mode
- 🛠️ **Non-functional resilience** — per-agent error isolation, timing metadata, and graceful fallbacks throughout

---

### 🛠️ Tech Stack

**Core**

<p align="left">
  <img src="https://img.shields.io/badge/Python-3776AB?style=for-the-badge&logo=python&logoColor=white" />
  <img src="https://img.shields.io/badge/Streamlit-FF4B4B?style=for-the-badge&logo=streamlit&logoColor=white" />
  <img src="https://img.shields.io/badge/Google%20Gemini-8E75B2?style=for-the-badge&logo=google&logoColor=white" />
</p>

**Document Processing**

<p align="left">
  <img src="https://img.shields.io/badge/PyPDF2-000000?style=for-the-badge&logo=adobeacrobatreader&logoColor=white" />
  <img src="https://img.shields.io/badge/PyMuPDF-4B8BBE?style=for-the-badge&logo=python&logoColor=white" />
  <img src="https://img.shields.io/badge/python--docx-2B579A?style=for-the-badge&logo=microsoftword&logoColor=white" />
  <img src="https://img.shields.io/badge/ReportLab-333333?style=for-the-badge&logo=readthedocs&logoColor=white" />
</p>

**Data & Visualization**

<p align="left">
  <img src="https://img.shields.io/badge/Plotly-3F4F75?style=for-the-badge&logo=plotly&logoColor=white" />
  <img src="https://img.shields.io/badge/JSON-000000?style=for-the-badge&logo=json&logoColor=white" />
</p>

---

### 📂 Project Structure

```
RFP_Team_Project/
├── app.py                  # Streamlit UI, theming, dashboards, PDF/JSON export
├── utils/
│   ├── document_processor.py   # Text extraction (PDF/DOCX/TXT) + PDF source-highlighting
│   └── agents.py                # Multi-agent pipeline (Summary, Deliverables, Criteria, Compliance, Go/No-Go)
├── SRS_document.py          # Software Requirements Specification
├── requirement.txt          # Project dependencies
├── test_models.py           # Model/agent testing
└── analysis_results/        # Saved analyses (retrievable by Analysis ID)
```

---

### ⚙️ Getting Started

```bash
# Clone the repository
git clone https://github.com/Yaqoob-hassan/SPS_Internship.git
cd SPS_Internship/RFP_Team_Project

# Install dependencies
pip install -r requirement.txt

# Add your Gemini API key (via .env or the app's sidebar)
# GEMINI_API_KEY=your_key_here

# Run the app
streamlit run app.py
```

---

<h3 align="center">💡 "Turning a manual, hours-long RFP review into a few minutes of AI-assisted decision-making."</h3>
