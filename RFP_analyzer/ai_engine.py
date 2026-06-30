import os
import json
import re
import time
from pathlib import Path
from dotenv import load_dotenv
import google.generativeai as genai
from google.api_core.exceptions import ResourceExhausted, ServiceUnavailable, InternalServerError

env_path = Path(__file__).parent / ".env"
load_dotenv(dotenv_path=env_path)

API_KEY = os.getenv("GEMINI_API_KEY")
if not API_KEY:
    raise ValueError("GEMINI_API_KEY not found in .env")

genai.configure(api_key=API_KEY)
MODEL = genai.GenerativeModel("gemini-2.5-flash")

MAX_RETRIES = 3
DEFAULT_BACKOFF_SECONDS = 15  # used when the API doesn't tell us a retry_delay


class RfpAnalysisError(Exception):
    """User-facing error with a clean message (no raw stack traces)."""
    pass


def _extract_retry_delay(error: ResourceExhausted) -> float:
    """Pull the server-suggested retry_delay (seconds) out of a 429 error, if present."""
    match = re.search(r"retry_delay\s*{\s*seconds:\s*(\d+)", str(error))
    if match:
        return float(match.group(1))
    return DEFAULT_BACKOFF_SECONDS

# JSON schema description handed to the model so output is predictable
SCHEMA_HINT = """
Return ONLY a single valid JSON object (no markdown fences, no commentary) with
EXACTLY this shape:

{
  "verdict": "GO" | "CONDITIONAL" | "NO-GO",
  "fit_score": <integer 0-100, how well the COMPANY PROFILE matches this RFP>,
  "headline_summary": "<3-5 sentence executive summary of the fit, written the way a
        pre-bid case file would: mention real strengths and real gaps>",

  "deliverables": [
    {
      "title": "<short deliverable name>",
      "description": "<1-3 sentence description>",
      "mandatory": true | false,
      "weeks_estimate": <integer, realistic effort estimate in weeks>
    }
  ],

  "evaluation_criteria": [
    {
      "name": "<criterion name>",
      "weight_pct": <integer, should sum to ~100 across all criteria if RFP states
            weights; if the RFP gives no formal scoring, infer a reasonable
            qualitative weighting and say so in description>",
      "description": "<1-2 sentence description of how this is scored>"
    }
  ],

  "compliance": {
    "Legal": [ <ComplianceItem>, ... ],
    "Accounting": [ <ComplianceItem>, ... ],
    "Technical": [ <ComplianceItem>, ... ],
    "Operations": [ <ComplianceItem>, ... ]
  },

  "key_dates_budget": {
    "submission_deadline": "<value or 'Not specified.'>",
    "pre_proposal_conference": "<value or 'Not specified.'>",
    "qa_deadline": "<value or 'Not specified.'>",
    "project_timeline": "<value or 'Not specified.'>",
    "total_budget": "<value or 'Not specified.'>",
    "bond_requirements": "<value or 'Not specified.'>"
  },

  "opportunity_assessment": {
    "key_reasons": ["<short bullet>", "..."],
    "potential_disqualifiers": ["<short bullet>", "..."]
  }
}

Where each ComplianceItem is:
{
  "requirement": "<the RFP requirement, restated clearly, one sentence>",
  "status": "MET" | "GAP" | "REVIEW",
  "note": "<1-2 sentence explanation, MUST reference the company profile when
        relevant — e.g. why it's met, why it's a gap, or what needs confirming>"
}

Rules for status:
- "MET": the company profile clearly satisfies this requirement.
- "GAP": the company profile clearly FAILS or falls short of this requirement
  (e.g. insurance coverage too low, missing required certification). Gaps are
  serious and should be rare but precise.
- "REVIEW": the RFP doesn't give enough info, or the company profile doesn't say
  one way or the other, so it needs human confirmation before submission.

Rules for fit_score and verdict:
- fit_score should reflect mandatory-deliverable alignment, compliance gaps,
  experience fit, and financial/insurance adequacy. Heavy GAPs on mandatory
  insurance/legal/financial items should pull the score down significantly.
- verdict "GO" only if there are no GAP items and mandatory deliverables are a
  strong match. "NO-GO" if there are disqualifying GAPs. Otherwise "CONDITIONAL".

Be specific and concrete. Use information ONLY from the RFP text and the company
profile provided below — never invent facts that aren't supported by either.
"""


def analyze_rfp(rfp_text: str, company_profile: str, on_retry=None) -> dict:
    """
    Calls Gemini with retry/backoff for transient errors (rate limits, 503s).
    `on_retry(attempt, wait_seconds, reason)` is an optional callback so the UI
    can show "retrying in Ns..." instead of just hanging.
    """
    prompt = f"""
You are a senior RFP/proposal analyst producing a pre-bid "Go/No-Go" case file.
Compare the RFP requirements below against the company's profile to assess fit.

{SCHEMA_HINT}

---
COMPANY PROFILE (the vendor considering bidding):
{company_profile}

---
RFP DOCUMENT:
{rfp_text}
"""
    last_error = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = MODEL.generate_content(
                prompt,
                generation_config={"response_mime_type": "application/json"},
            )
            return _parse_json(response.text)

        except ResourceExhausted as e:
            last_error = e
            if attempt == MAX_RETRIES:
                raise RfpAnalysisError(
                    "Gemini's free-tier rate limit was hit and retries were exhausted. "
                    "This API key/project has used up its quota for now — wait a bit and "
                    "try again, switch to a different API key/project, or enable billing "
                    "on the Google Cloud project for higher limits."
                ) from e
            wait_seconds = _extract_retry_delay(e)
            if on_retry:
                on_retry(attempt, wait_seconds, "rate limit")
            time.sleep(wait_seconds)

        except (ServiceUnavailable, InternalServerError) as e:
            last_error = e
            if attempt == MAX_RETRIES:
                raise RfpAnalysisError(
                    "Gemini's servers were unavailable after several retries. Please try again "
                    "in a moment."
                ) from e
            wait_seconds = DEFAULT_BACKOFF_SECONDS * attempt  # simple exponential-ish backoff
            if on_retry:
                on_retry(attempt, wait_seconds, "server error")
            time.sleep(wait_seconds)

        except ValueError:
            # malformed JSON from the model - not worth retrying silently, surface it
            raise

        except Exception as e:
            raise RfpAnalysisError(f"AI analysis failed unexpectedly: {e}") from e

    # Should never reach here, but just in case
    raise RfpAnalysisError(f"AI analysis failed after {MAX_RETRIES} attempts: {last_error}")


def _parse_json(raw_text: str) -> dict:
    """Robustly parse the model's JSON output, stripping markdown fences if present."""
    cleaned = raw_text.strip()
    cleaned = re.sub(r"^```(?:json)?", "", cleaned.strip())
    cleaned = re.sub(r"```$", "", cleaned.strip())
    cleaned = cleaned.strip()

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise ValueError(f"Model did not return valid JSON: {e}\n\nRaw output:\n{raw_text}")

    # Defensive defaults so the UI never KeyErrors on a slightly malformed response
    data.setdefault("verdict", "CONDITIONAL")
    data.setdefault("fit_score", 0)
    data.setdefault("headline_summary", "")
    data.setdefault("deliverables", [])
    data.setdefault("evaluation_criteria", [])
    data.setdefault("compliance", {"Legal": [], "Accounting": [], "Technical": [], "Operations": []})
    for dept in ["Legal", "Accounting", "Technical", "Operations"]:
        data["compliance"].setdefault(dept, [])
    data.setdefault("key_dates_budget", {})
    data.setdefault("opportunity_assessment", {"key_reasons": [], "potential_disqualifiers": []})

    return data