import google.generativeai as genai
import PyPDF2
import docx
import os
import json
import re
import difflib
from typing import Dict, Any, List, Optional

# fitz (PyMuPDF) is used ONLY for the "View in PDF" feature: raw per-page text
# extraction, locating a quote's coordinates, and rendering a highlighted page
# image. It is intentionally kept separate from the PyPDF2 extraction used for
# the Gemini prompt, because that text gets cleaned/normalized (whitespace
# collapsed, punctuation stripped) which would break exact text search.
import fitz

# 🆕 Multi-agent pipeline: splits analysis into independent, single-responsibility
# agents (summary / deliverables / evaluation criteria / compliance / go-no-go)
# run concurrently instead of one large sequential prompt.
from utils.agents import run_agents_parallel


class RFPProcessor:
    """Handles document extraction and AI analysis for RFP documents"""

    def __init__(self, api_key: str):
        """Initialize with Gemini API key"""
        genai.configure(api_key=api_key)

        # Auto-select first available model
        self.model = None
        for model in genai.list_models():
            if 'generateContent' in model.supported_generation_methods:
                self.model = genai.GenerativeModel(model.name)
                print(f"✅ Using model: {model.name}")
                break

        if self.model is None:
            raise Exception("No available Gemini model found.")

    def run_full_analysis(self, text: str) -> Dict[str, Any]:
        """
        🆕 Multi-agent analysis pipeline.

        Runs SummaryAgent, DeliverablesAgent, EvaluationCriteriaAgent,
        ComplianceChecklistAgent, and GoNoGoAgent CONCURRENTLY (thread pool)
        instead of the old approach of one big analyze_rfp() call followed by
        a separate sequential go_no_go_analysis() call.

        Returns the same shape the app already expects:
            {project_summary, deliverables, evaluation_criteria,
             compliance_checklist, go_no_go, _agent_meta}

        _agent_meta contains per-agent timing and any per-agent errors, purely
        for visibility/debugging in the UI -- it doesn't affect the analysis.

        NOTE: analyze_rfp() and go_no_go_analysis() below are kept as-is
        (unused by this method) in case you want the old single-call behavior
        for comparison or as a fallback.
        """
        return run_agents_parallel(self.model, text)

    def extract_text_from_pdf(self, file_path: str) -> str:
        text = ""
        try:
            with open(file_path, 'rb') as file:
                pdf_reader = PyPDF2.PdfReader(file)
                for page in pdf_reader.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text += page_text + "\n"

            if len(text.strip()) < 200:
                try:
                    import pdfplumber
                    with pdfplumber.open(file_path) as pdf:
                        for page in pdf.pages:
                            page_text = page.extract_text()
                            if page_text:
                                text += page_text + "\n"
                except ImportError:
                    pass

            if not text or len(text.strip()) < 50:
                raise Exception("No text could be extracted from PDF.")

            text = self._clean_extracted_text(text)
            return text

        except Exception as e:
            raise Exception(f"Error reading PDF: {str(e)}")

    def _clean_extracted_text(self, text: str) -> str:
        if not text:
            return text

        text = re.sub(r'\n\s*\d+\s*\n', '\n', text)
        text = re.sub(r'Page \d+', '', text)
        text = re.sub(r'\s+', ' ', text)
        text = re.sub(r'\n\s*\n', '\n\n', text)
        text = re.sub(r'[^\w\s\.\,\-\$\d\%]', ' ', text)
        text = re.sub(r'\$ (\d+)', r'$\1', text)
        text = re.sub(r'(\d+) \%', r'\1%', text)
        text = re.sub(r'(\d+) M', r'\1M', text)
        text = re.sub(r'(\d+) K', r'\1K', text)

        return text

    def extract_text_from_docx(self, file_path: str) -> str:
        try:
            doc = docx.Document(file_path)
            text = ""
            for paragraph in doc.paragraphs:
                text += paragraph.text + "\n"
            return text
        except Exception as e:
            raise Exception(f"Error reading DOCX: {str(e)}")

    def extract_text_from_txt(self, file_path: str) -> str:
        try:
            with open(file_path, 'r', encoding='utf-8') as file:
                return file.read()
        except Exception as e:
            raise Exception(f"Error reading TXT: {str(e)}")

    def extract_text(self, file_path: str) -> str:
        file_extension = os.path.splitext(file_path)[1].lower()

        if file_extension == '.pdf':
            return self.extract_text_from_pdf(file_path)
        elif file_extension == '.docx':
            return self.extract_text_from_docx(file_path)
        elif file_extension == '.txt':
            return self.extract_text_from_txt(file_path)
        else:
            raise ValueError(f"Unsupported file format: {file_extension}")

    def analyze_rfp(self, text: str) -> Dict[str, Any]:
        prompt = f"""
        You are an expert in analyzing Request for Proposal (RFP) documents.

        The text below contains content from MULTIPLE FILES. Each file is clearly marked with:
        "========================================"
        "FILE: [filename]"
        "========================================"

        **CRITICAL INSTRUCTION: For EVERY deliverable you identify, you MUST include the EXACT filename where it appears.**

        Look at the "FILE: [filename]" headers above each section of text to determine which file each deliverable came from.

        **CRITICAL INSTRUCTION #2: For EVERY deliverable, you MUST also include a "quote" field.**
        The "quote" MUST be an EXACT, VERBATIM phrase or sentence COPIED DIRECTLY from the RFP text
        (5 to 15 words). Do NOT paraphrase it. Do NOT summarize it. Copy the exact characters as they
        appear in the RFP TEXT above, including original spelling/punctuation. This exact quote will be
        used to programmatically locate and highlight the deliverable inside the source PDF, so it must
        be a real, direct substring of the document text, not a rewording.

        Total RFP text length: {len(text)} characters.

        RFP TEXT:
        {text}

        Extract the following information:

        1. "project_summary": A brief 2-3 sentence summary of the project

        2. "deliverables": Group deliverables into BUSINESS CATEGORIES (max 5-6 categories, max 5-6 items per category).
           For EACH deliverable, include:
           - "name": The deliverable name
           - "section_ref": The section number where it appears (e.g., "Section XI.B.1", "Article IV", "Section 3.2")
           - "reason": Why this deliverable is required (include the section reference)
           - "source_file": The EXACT filename where this deliverable was found (e.g., "doc1.txt", "ODU_RFP_Part1.txt")
           - "quote": An EXACT verbatim 5-15 word snippet copied directly from the RFP text near where this deliverable is described

        3. "evaluation_criteria": List of criteria the client will use to judge proposals (flat list)

        4. "compliance_checklist": An object with departments as keys and lists of tasks as values
           Departments: Legal, Accounting, Technical, Operations, HR

        Return ONLY valid JSON. DO NOT include any text outside the JSON.

        Example format:
        {{
            "project_summary": "Old Dominion University is seeking an AI-driven search solution...",
            "deliverables": [
                {{
                    "category": "Documentation & Forms",
                    "items": [
                        {{"name": "RFP Cover Sheet", "section_ref": "Section XI.B.1", "reason": "Requires the return of the RFP cover sheet", "source_file": "doc1.txt", "quote": "Offerors must return the completed RFP Cover Sheet"}},
                        {{"name": "W-9 Form", "section_ref": "Section XI.B.2", "reason": "Requires a completed Substitute W-9 Form", "source_file": "doc2.txt", "quote": "a completed Substitute W-9 Form must be submitted"}},
                        {{"name": "SWAM Plan", "section_ref": "Attachment D", "reason": "Requires Contractor's Proposed SWAM Plan", "source_file": "doc3.txt", "quote": "Contractor shall submit its Proposed SWAM Plan"}}
                    ]
                }},
                {{
                    "category": "Technical Requirements",
                    "items": [
                        {{"name": "IAM Software", "section_ref": "Section IV", "reason": "Requires IAM solution deployment", "source_file": "doc1.txt", "quote": "the Offeror shall deploy an Identity and Access Management solution"}}
                    ]
                }}
            ],
            "evaluation_criteria": ["Experience", "Capability"],
            "compliance_checklist": {{"Legal": ["NDA"], "Accounting": ["Insurance"]}}
        }}
        """

        try:
            response = self.model.generate_content(prompt)
            json_str = response.text.strip()

            if "```json" in json_str:
                json_str = json_str.split("```json")[1].split("```")[0].strip()
            elif "```" in json_str:
                json_str = json_str.split("```")[1].split("```")[0].strip()

            result = json.loads(json_str)

            # Ensure deliverables is in the new format with source_file + quote
            if 'deliverables' in result:
                if isinstance(result['deliverables'], list) and len(result['deliverables']) > 0:
                    # If flat list (old format), convert
                    if isinstance(result['deliverables'][0], str):
                        flat_list = [{"name": item, "section_ref": "N/A", "reason": "Required by RFP", "source_file": "Unknown", "quote": ""} for item in result['deliverables']]
                        result['deliverables'] = [{"category": "General", "items": flat_list}]
                    # If old format without section_ref / source_file / quote
                    elif isinstance(result['deliverables'][0], dict) and 'items' in result['deliverables'][0]:
                        for cat in result['deliverables']:
                            if 'items' in cat:
                                if len(cat['items']) > 0 and isinstance(cat['items'][0], str):
                                    cat['items'] = [{"name": item, "section_ref": "N/A", "reason": "Required by RFP", "source_file": "Unknown", "quote": ""} for item in cat['items']]
                                elif isinstance(cat['items'][0], dict) and 'name' in cat['items'][0]:
                                    # Ensure each item has section_ref, reason, source_file, and quote
                                    for item in cat['items']:
                                        if 'section_ref' not in item:
                                            item['section_ref'] = 'N/A'
                                        if 'reason' not in item:
                                            item['reason'] = 'Required by RFP'
                                        if 'source_file' not in item:
                                            item['source_file'] = 'Unknown'
                                        if 'quote' not in item:
                                            item['quote'] = ''
                else:
                    result['deliverables'] = []

            return result

        except Exception as e:
            return {
                "project_summary": "Error processing document",
                "deliverables": [],
                "evaluation_criteria": ["Unable to extract evaluation criteria"],
                "compliance_checklist": {
                    "Legal": ["Unable to extract compliance tasks"],
                    "Accounting": ["Unable to extract compliance tasks"],
                    "Technical": ["Unable to extract compliance tasks"],
                    "Operations": ["Unable to extract compliance tasks"],
                    "HR": ["Unable to extract compliance tasks"]
                },
                "error": str(e)
            }

    def go_no_go_analysis(self, text: str) -> Dict[str, Any]:
        """Perform Go/No-Go analysis based on company checklist - WITH NUMERIC EXTRACTION"""

        prompt = f"""
        You are a Bid/No-Bid decision expert. Analyze this RFP against our company checklist.

        **CRITICAL: You MUST read and extract ALL numeric values (payment terms, dollar amounts, dates, deadlines) from the RFP text.**

        Total RFP text length: {len(text)} characters.

        RFP TEXT (FULL DOCUMENT):
        {text}

        ========================================
        COMPANY CHECKLIST - Evaluate Each Item
        ========================================

        FINANCIAL CHECKLIST (Score each 0-10):
        1. "Payment Terms" - NET30 or better = 10, NET45 = 7, NET60 = 4, Not mentioned = 3
        2. "Insurance Requirements" - $5M or less = 10, $10M = 5, More = 0, Not mentioned = 3
        3. "Financial Stability" - We meet = 10, Partial = 7, Don't meet = 0
        4. "Profitability" - Budget known = 10, Vague = 5, Not mentioned = 3
        5. "Bid Bond" - Not required = 10, Can provide = 7, Can't = 0

        LEGAL CHECKLIST (Score each 0-10):
        6. "Eligibility Criteria" - Meet all = 10, Meet most = 7, Don't meet = 0
        7. "State Registration" - Not required = 10, Have it = 7, Don't have = 0
        8. "E-Verify" - Not required = 10, Have it = 7, Don't have = 0
        9. "Contract Terms" - Acceptable = 10, Review needed = 7, Major issues = 3
        10. "Legal Compliance" - Comply = 10, Mostly = 7, Don't = 0

        OPERATIONS CHECKLIST (Score each 0-10):
        11. "Required Forms" - All standard = 10, Some effort = 7, Extensive = 4
        12. "Submission Deadlines" - Feasible (30+ days) = 10, Tight (15-29 days) = 7, Very tight (<15 days) = 4
        13. "Signatory Authority" - Available = 10, Need approval = 7, Not available = 0
        14. "Vendor Registration" - Not required = 10, Have it = 7, Need to register = 3

        TECHNICAL CHECKLIST (Score each 0-10):
        15. "Scope Alignment" - Perfect = 10, Good fit = 7, Partial = 4
        16. "Technical Requirements" - Meet all = 10, Meet most = 7, Don't meet = 0
        17. "Industry Standards" - Comply = 10, Mostly = 7, Don't = 0
        18. "Security Requirements" - Meet = 10, Mostly = 7, Don't = 0
        19. "Integration Needs" - Can do = 10, With effort = 7, Can't = 0

        ========================================

        STATUS DEFINITIONS:
        - "GO" = Score 7-10 (We fully meet this)
        - "ESCALATE" = Score 3-6 (Missing info or needs management review)
        - "NO-GO" = Score 0-2 (Cannot meet this)

        Return JSON ONLY in this format:
        {{
            "checklist": [
                {{"category": "Financial", "item": "Payment Terms", "score": 10, "status": "GO", "reason": "NET30 terms found", "evidence": "Section 1: NET30"}},
                {{"category": "Financial", "item": "Insurance", "score": 5, "status": "ESCALATE", "reason": "$10M required, we have $5M", "evidence": "Section 2: $10M"}}
            ],
            "go_count": 10,
            "no_go_count": 0,
            "escalate_count": 2,
            "summary": "We should bid with escalation items"
        }}

        IMPORTANT: DO NOT include "overall_score" in your JSON. The score will be calculated automatically from the checklist scores.
        """

        try:
            response = self.model.generate_content(prompt)
            raw_text = response.text.strip()

            # Extract JSON
            json_match = re.search(r'(\{.*\})', raw_text, re.DOTALL)
            if json_match:
                json_str = json_match.group(1)
            else:
                json_str = raw_text

            # Clean up
            json_str = re.sub(r',\s*}', '}', json_str)
            json_str = re.sub(r',\s*]', ']', json_str)

            result = json.loads(json_str)

            # Ensure all required fields exist
            if 'checklist' not in result:
                result['checklist'] = []

            # ============================================================
            # ✅ CALCULATE SCORE ONLY FROM CHECKLIST ITEMS
            # ============================================================
            total_score = 0
            max_score = len(result.get('checklist', [])) * 10

            for item in result.get('checklist', []):
                total_score += item.get('score', 0)

            if max_score > 0:
                calculated_score = (total_score / max_score) * 100
                result['overall_score'] = round(min(100, calculated_score))
            else:
                result['overall_score'] = 50

            # Enforce strict score-based decision
            score = result.get('overall_score', 0)
            if score >= 71:
                result['overall_decision'] = 'GO'
            elif 51 <= score <= 70:
                result['overall_decision'] = 'ESCALATE'
            else:
                result['overall_decision'] = 'NO-GO'

            # Count statuses
            go_count = sum(1 for item in result.get('checklist', []) if item.get('status') == 'GO')
            no_go_count = sum(1 for item in result.get('checklist', []) if item.get('status') == 'NO-GO')
            escalate_count = sum(1 for item in result.get('checklist', []) if item.get('status') == 'ESCALATE')

            result['go_count'] = go_count
            result['no_go_count'] = no_go_count
            result['escalate_count'] = escalate_count
            result['conditional_count'] = escalate_count

            return result

        except Exception as e:
            return {
                "overall_decision": "NEEDS REVIEW",
                "overall_score": 50,
                "checklist": [
                    {"category": "Financial", "item": "Payment Terms", "score": 5, "status": "ESCALATE", "reason": "Could not analyze", "evidence": "Check RFP manually"},
                    {"category": "Legal", "item": "Eligibility", "score": 5, "status": "ESCALATE", "reason": "Could not analyze", "evidence": "Check RFP manually"},
                    {"category": "Operations", "item": "Deadlines", "score": 5, "status": "ESCALATE", "reason": "Could not analyze", "evidence": "Check RFP manually"},
                    {"category": "Technical", "item": "Scope", "score": 5, "status": "ESCALATE", "reason": "Could not analyze", "evidence": "Check RFP manually"}
                ],
                "go_count": 0,
                "no_go_count": 0,
                "escalate_count": 4,
                "conditional_count": 4,
                "summary": f"AI analysis encountered an error: {str(e)}. Please review the RFP manually."
            }


# ============================================================
# 🆕 "VIEW IN PDF" HELPERS (module-level, no Gemini/API key needed)
# ============================================================
# These are standalone so the Streamlit app can open/search/highlight a PDF
# without spinning up an RFPProcessor (which requires a valid Gemini key and
# makes a network call to list models). This matters most on the "load a
# saved analysis" path, which is supposed to make ZERO AI calls.

def extract_raw_pages_from_pdf(file_path: str) -> List[str]:
    """
    Extract UNMODIFIED per-page text using PyMuPDF. This is kept separate from
    RFPProcessor.extract_text_from_pdf(), whose output is cleaned/normalized
    for the AI prompt and is therefore NOT reliable for exact text search.
    """
    pages = []
    doc = fitz.open(file_path)
    try:
        for page in doc:
            pages.append(page.get_text())
    finally:
        doc.close()
    return pages


def extract_raw_pages_from_pdf_bytes(pdf_bytes: bytes) -> List[str]:
    """Same as extract_raw_pages_from_pdf but works directly on bytes in memory."""
    pages = []
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        for page in doc:
            pages.append(page.get_text())
    finally:
        doc.close()
    return pages


def _word_overlap_ratio(quote: str, page_text: str) -> float:
    """Fraction of the quote's words that also appear in the page's text."""
    quote_words = set(re.findall(r'\w+', quote.lower()))
    if not quote_words:
        return 0.0
    page_words = set(re.findall(r'\w+', page_text.lower()))
    overlap = quote_words & page_words
    return len(overlap) / len(quote_words)


def _best_fuzzy_sentence(quote: str, page_text: str) -> Optional[str]:
    """Find the sentence in page_text most similar to quote, for fallback highlighting."""
    sentences = re.split(r'(?<=[.!?])\s+', page_text)
    sentences = [s.strip() for s in sentences if len(s.strip()) > 10]
    if not sentences:
        return None
    matches = difflib.get_close_matches(quote, sentences, n=1, cutoff=0.35)
    return matches[0] if matches else None


def find_text_location(pdf_bytes: bytes, quote: str, raw_pages: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
    """
    Locate a quote inside a PDF (given as bytes).

    Tries, in order:
      1. Exact PyMuPDF text search (page.search_for) — gives real bounding boxes.
      2. Exact search using just the first ~6 words of the quote (handles cases
         where the AI's quote trails off with paraphrased words at the end).
      3. Word-overlap fallback against raw_pages text — picks the best-matching
         page and, if possible, the closest matching sentence on it, and searches
         for THAT sentence to get real bounding boxes.

    Returns None if nothing usable was found.
    """
    if not quote or not quote.strip() or not pdf_bytes:
        return None

    quote_clean = quote.strip()
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")

    try:
        # Strategy 1: exact match, full quote
        for page_num in range(len(doc)):
            rects = doc[page_num].search_for(quote_clean)
            if rects:
                return {"page_num": page_num, "bboxes": [list(r) for r in rects], "match_type": "exact"}

        # Strategy 2: exact match, first 6 words only (handles slight trailing drift)
        words = quote_clean.split()
        if len(words) > 6:
            short_quote = " ".join(words[:6])
            for page_num in range(len(doc)):
                rects = doc[page_num].search_for(short_quote)
                if rects:
                    return {"page_num": page_num, "bboxes": [list(r) for r in rects], "match_type": "partial"}

        # Strategy 3: fuzzy fallback using raw_pages (word overlap -> best sentence)
        if raw_pages:
            best_page_idx, best_ratio = None, 0.0
            for idx, page_text in enumerate(raw_pages):
                if not page_text:
                    continue
                ratio = _word_overlap_ratio(quote_clean, page_text)
                if ratio > best_ratio:
                    best_ratio, best_page_idx = ratio, idx

            if best_page_idx is not None and best_ratio >= 0.5:
                page_text = raw_pages[best_page_idx]
                best_sentence = _best_fuzzy_sentence(quote_clean, page_text)
                if best_sentence:
                    rects = doc[best_page_idx].search_for(best_sentence)
                    if rects:
                        return {"page_num": best_page_idx, "bboxes": [list(r) for r in rects], "match_type": "fuzzy"}
                # Couldn't get exact bboxes for the sentence either -> open the
                # page with no highlight rather than showing nothing at all.
                return {"page_num": best_page_idx, "bboxes": [], "match_type": "page_only"}

        return None
    finally:
        doc.close()


def render_highlighted_page(pdf_bytes: bytes, page_num: int, bboxes: Optional[List] = None, zoom: float = 2.0) -> bytes:
    """Render a single PDF page to a PNG image, drawing yellow highlight boxes if given."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        page = doc[page_num]
        if bboxes:
            for bbox in bboxes:
                rect = fitz.Rect(bbox)
                annot = page.add_highlight_annot(rect)
                annot.set_colors(stroke=(1, 0.9, 0.2))
                annot.update()
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat)
        return pix.tobytes("png")
    finally:
        doc.close()


def get_highlighted_page_image(pdf_bytes: bytes, quote: str, raw_pages: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
    """
    Convenience wrapper: find the quote in the PDF and render the highlighted
    page as a PNG. Returns None if the quote couldn't be located at all.
    """
    location = find_text_location(pdf_bytes, quote, raw_pages)
    if not location:
        return None
    img_bytes = render_highlighted_page(pdf_bytes, location["page_num"], location.get("bboxes"))
    return {
        "image_bytes": img_bytes,
        "page_num": location["page_num"],
        "match_type": location["match_type"],
    }