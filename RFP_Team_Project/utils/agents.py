"""
Multi-agent RFP analysis pipeline WITH RAG (Retrieval-Augmented Generation)
AND a double-verification pass on the highest-stakes outputs.

Instead of one large Gemini prompt trying to extract everything at once, this
module splits the work into FOUR independent, single-responsibility agents:

    SummaryAgent       -> project_summary + executive_summaries + 20 FAQs
    DeliverablesAgent  -> deliverables (with section_ref/source_file/quote)
    ExtractionAgent    -> evaluation_criteria + compliance_checklist + certifications
    RiskAgent          -> go/no-go scoring + internal conflict/contradiction check

────────────────────────────────────────────────────────────────────────
RAG: WHY AND HOW (the token-cost fix)
────────────────────────────────────────────────────────────────────────
Previously every agent received the FULL RFP text. With 4 agents, a
document worth "1x tokens" cost "4x tokens" of input every analysis.

Now, for documents above a size threshold, the text is:
    1. Chunked ONCE          (split by FILE: markers, then ~1400-char pieces)
    2. Embedded ONCE         (Gemini text-embedding-004, shared across agents)
    3. Retrieved per-agent   (each agent pulls ONLY the chunks relevant to
                              its own task, via its retrieval_queries)

Small documents (< RAG_MIN_CHARS) skip retrieval entirely -- every agent
gets the full text. Below that size, embedding overhead isn't worth it and
quality matters more than the (tiny) savings.

Trade-off worth knowing: RiskAgent's conflict-detection half needs broad
cross-document visibility (a date conflict could be between section 2 and
section 40 of the same file), so it is deliberately given the widest
retrieval budget of the four agents. RAG will still never be quite as
thorough as full-text for finding contradictions -- this is the accepted
cost of the token savings.

If the embedding API is ever unavailable, retrieval automatically FALLS
BACK to keyword-overlap scoring (pure Python, no API), so the pipeline
never breaks because of RAG.

Why FOUR agents and not more:
  Gemini's free tier caps out at 5 requests/minute for gemini-2.5-flash. Each
  agent below fires exactly one request, so four agents running concurrently
  leaves headroom under that ceiling instead of tripping it.

Why this shape beats one giant prompt:
  - Parallel:        all agents dispatch together via a thread pool, so
                      wall-clock time is roughly max(agent times), not sum().
  - Maintainable:     each agent owns its own prompt, schema, and fallback.
  - Isolated failure: one agent's parse failure only degrades that agent's
                      section(s) -- the rest of the analysis still succeeds.
  - Resilient:        every model call retries on 429 with Google's own
                      suggested backoff, and fails over across the model
                      list on a permanent zero-quota error.

────────────────────────────────────────────────────────────────────────
DOUBLE VERIFICATION (new): catching AI mistakes before they reach the user
────────────────────────────────────────────────────────────────────────
There is no guarantee a single extraction pass got every detail right, and
the highest-stakes numbers in this app are the ones that directly drive the
bid/no-bid decision. Rather than re-checking EVERYTHING (which would double
API usage -- 4 extraction calls -> 8 total), verification is scoped to what
actually matters for the decision, and split into two tiers:

  1. FREE, zero-cost quote verification (no AI call at all): every
     deliverable's "quote" field is checked with plain Python string
     matching to confirm it's an actual substring of the source document --
     catching the single most common hallucination (a fabricated quote)
     for $0 and 0ms of extra API latency.

  2. ONE additional batched AI fact-check call (after the 4 extraction
     agents finish, not one-per-agent): it re-examines the highest-stakes
     claims --  deliverables, the go/no-go checklist (literally the
     pass/fail gate on the bid decision), and REQUIRED certifications
     (mandatory-for-eligibility items) -- against the source RFP text, and
     returns corrections for anything it finds unsupported. This keeps the
     pipeline at 5 total calls per analysis instead of 8.

     Deliberately OUT of scope (lower-stakes, would just add cost without
     much value): executive summaries, FAQs, evaluation criteria, softer
     compliance task lists, Recommended-tier certifications, and conflicts.

  Corrections found by the verifier are applied AUTOMATICALLY -- each
  verified item is tagged "verified": true/false so the UI can surface this
  later if desired, and any go/no-go score/decision/counts affected by a
  correction are recalculated from the corrected checklist.
"""

import json
import re
import time
import math
import concurrent.futures
from typing import Dict, Any, List, Optional, Tuple, Union

import google.generativeai as genai

print("=" * 60)
print("agents.py LOADED — VERSION: rag-v1 + verification-v1")
print("   (if you don't see this line on every app restart,")
print("    Python is running a stale/cached copy of this file)")
print("=" * 60)


# ============================================================
# JSON PARSING HELPERS (unchanged from your current version)
# ============================================================
def _extract_json(raw_text: str) -> str:
    """Strip markdown code fences (```json ... ```) that Gemini sometimes wraps JSON in."""
    raw_text = raw_text.strip()
    if "```json" in raw_text:
        raw_text = raw_text.split("```json")[1].split("```")[0].strip()
    elif "```" in raw_text:
        raw_text = raw_text.split("```")[1].split("```")[0].strip()
    return raw_text


def _close_truncated_json(json_str: str) -> str:
    """
    Best-effort repair for a response that got cut off mid-JSON (e.g. hit
    max_output_tokens partway through a string or object). Walks the text
    tracking bracket/brace/string state, trims any dangling partial token
    at the very end, and appends whatever closing characters are needed to
    make it syntactically valid. This can't recover data that was never
    generated, but it turns "the whole agent falls back to defaults" into
    "we keep everything that WAS generated before the cutoff".
    """
    stack = []
    in_string = False
    escape = False
    last_safe_end = 0  # index right after the last structurally-complete token

    for i, ch in enumerate(json_str):
        if in_string:
            if escape:
                escape = False
            elif ch == '\\':
                escape = True
            elif ch == '"':
                in_string = False
                last_safe_end = i + 1
            continue

        if ch == '"':
            in_string = True
        elif ch in '{[':
            stack.append(ch)
        elif ch in '}]':
            if stack:
                stack.pop()
            last_safe_end = i + 1
        elif ch in ',:':
            pass  # don't advance last_safe_end -- a trailing comma/colon means more was expected
        elif not ch.isspace():
            last_safe_end = i + 1

    if in_string:
        # Response was cut off inside a string literal -- close the string
        # at the last safe point instead of keeping a dangling half-string.
        json_str = json_str[:last_safe_end] + '"'
    else:
        json_str = json_str[:last_safe_end] if last_safe_end else json_str

    # Drop a trailing comma/colon left dangling right before we close things out
    json_str = re.sub(r'[,:]\s*$', '', json_str)

    # Close whatever brackets/braces were still open, innermost first
    closers = {'{': '}', '[': ']'}
    json_str += ''.join(closers[c] for c in reversed(stack))
    return json_str


def _loose_json_parse(raw_text: str) -> Dict[str, Any]:
    """Parse JSON, trying progressively more aggressive repairs before giving up:
    1) straight parse, 2) strip trailing commas, 3) repair a truncated/cut-off
    response (e.g. one that hit max_output_tokens partway through)."""
    json_str = _extract_json(raw_text)
    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        pass

    # try to salvage: grab the outermost {...} block and strip trailing commas
    match = re.search(r'(\{.*\})', json_str, re.DOTALL)
    candidate = match.group(1) if match else json_str
    trimmed = re.sub(r',\s*}', '}', candidate)
    trimmed = re.sub(r',\s*]', ']', trimmed)
    try:
        return json.loads(trimmed)
    except json.JSONDecodeError:
        pass

    # last resort: the response was likely truncated mid-generation -- close
    # off open strings/brackets and salvage everything generated up to that point
    repaired = _close_truncated_json(candidate if match else json_str)
    return json.loads(repaired)


def _is_rate_limit_error(error: Exception) -> bool:
    """Detect a Gemini free-tier '429 quota exceeded' error specifically."""
    msg = str(error)
    return "429" in msg or "quota" in msg.lower() or "rate limit" in msg.lower()


def _is_zero_quota_error(error: Exception) -> bool:
    """
    Detect the specific case where a model has NO free-tier allocation on this
    account at all (limit: 0) -- as opposed to a normal "you used up your
    quota for now" 429. A zero-quota error will NEVER succeed no matter how
    long we wait, so it means "permanently skip this model", not "retry it".
    """
    return "limit: 0" in str(error)


def _parse_retry_delay_seconds(error: Exception, default: float = 15.0) -> float:
    """
    Google's 429 error body includes a suggested wait time, e.g.
    'retry_delay { seconds: 19 }'. Pull that out if present so we wait the
    right amount instead of guessing.
    """
    match = re.search(r'retry_delay\s*\{\s*seconds:\s*(\d+)', str(error))
    if match:
        return float(match.group(1)) + 2.0  # small buffer on top of Google's own suggestion
    return default


def _call_model_with_retry(models, prompt: str, max_retries_per_model: int = 3):
    """
    Call generate_content() against a LIST of models (in preference order),
    automatically retrying on a normal 429 rate limit with the backoff Google
    itself suggests, and automatically failing over to the next model in the
    list on a permanent zero-quota error or once retries on the current model
    are exhausted. Non-rate-limit, non-quota errors raise immediately --
    no point retrying a bad prompt or a genuine auth failure.
    """
    if not isinstance(models, (list, tuple)):
        models = [models]
    if not models:
        raise RuntimeError("No Gemini model available to call.")

    last_error = None
    for model in models:
        for attempt in range(max_retries_per_model):
            try:
                return model.generate_content(prompt)
            except Exception as e:
                last_error = e
                if _is_zero_quota_error(e):
                    break  # this model will never work on this account -- move on
                if not _is_rate_limit_error(e):
                    raise  # a real bug/auth error -- don't waste time retrying it
                if attempt == max_retries_per_model - 1:
                    break  # exhausted retries on this model's own rate limit -- try the next one
                time.sleep(_parse_retry_delay_seconds(e))
    raise last_error


# ============================================================
# RAG CORE: chunking, embedding, retrieval
# ============================================================
EMBED_MODEL = "models/text-embedding-004"

# Below this many characters of combined RFP text, RAG is skipped entirely
# and every agent gets the full text. Embedding overhead isn't worth it on
# short documents, and quality matters more than the (tiny) token savings.
RAG_MIN_CHARS = 12000

# Matches the FILE: header block that app.py inserts between documents.
_FILE_MARKER_RE = re.compile(r'={10,}\s*\n\s*FILE:\s*(.+?)\s*\n\s*={10,}')


def _split_by_file(text: str) -> List[Tuple[str, str]]:
    """
    Split the combined text back into (file_name, file_text) segments using the
    'FILE: <name>' markers app.py writes. Falls back to a single 'Document'
    segment for pasted text that has no markers.
    """
    if not text:
        return []

    matches = list(_FILE_MARKER_RE.finditer(text))
    if not matches:
        return [("Document", text)]

    segments: List[Tuple[str, str]] = []

    if matches[0].start() > 0:
        pre = text[:matches[0].start()].strip()
        if pre:
            segments.append(("Document", pre))

    for i, m in enumerate(matches):
        file_name = m.group(1).strip()
        seg_start = m.end()
        seg_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        seg_text = text[seg_start:seg_end].strip()
        if seg_text:
            segments.append((file_name, seg_text))

    return segments


def _chunk_text(text: str, size: int = 1400, overlap: int = 200) -> List[str]:
    """
    Split one file's text into overlapping chunks, preferring to break on
    paragraph/sentence boundaries so nothing important is cut mid-sentence.
    Overlap keeps context continuous across chunk edges.
    """
    text = text.strip()
    if len(text) <= size:
        return [text] if text else []

    chunks: List[str] = []
    start = 0
    n = len(text)

    while start < n:
        end = min(start + size, n)
        chunk = text[start:end]

        if end < n:
            para_break = chunk.rfind('\n\n')
            sent_break = chunk.rfind('. ')
            brk = max(para_break, sent_break)
            if brk > size * 0.5:
                chunk = chunk[:brk + 1]
                end = start + len(chunk)

        cleaned = chunk.strip()
        if cleaned:
            chunks.append(cleaned)

        if end >= n:
            break
        start = max(0, end - overlap)

    return chunks


def _embed_texts(texts: List[str], task_type: str, batch_size: int = 100) -> List[List[float]]:
    """
    Embed a list of texts with Gemini, batched. task_type should be
    'retrieval_document' for chunks and 'retrieval_query' for queries.
    Returns one vector per input text. Uses the module-level genai config
    already set up by RFPProcessor.__init__ (genai.configure(api_key=...)),
    so no separate auth is needed here.
    """
    all_emb: List[List[float]] = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        result = genai.embed_content(model=EMBED_MODEL, content=batch, task_type=task_type)
        emb = result["embedding"]
        if emb and isinstance(emb[0], (int, float)):
            all_emb.append(emb)  # a batch of exactly 1 can come back as a flat vector
        else:
            all_emb.extend(emb)
    return all_emb


def _cosine(a: List[float], b: List[float]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


class DocumentChunk:
    """A single retrievable piece of the document, tagged with its source file."""

    __slots__ = ("text", "file_name", "position", "embedding")

    def __init__(self, text: str, file_name: str, position: int):
        self.text = text
        self.file_name = file_name
        self.position = position
        self.embedding: Optional[List[float]] = None


class DocumentRetriever:
    """
    Chunks + embeds the document ONCE, then serves per-agent retrieval.
    Build it a single time and share it across all agents.

    Documents under RAG_MIN_CHARS skip chunking/embedding entirely --
    retrieve() just returns the full text for every query in that case.
    """

    def __init__(self, text: str):
        self.full_text: str = text or ""
        self.use_rag: bool = len(self.full_text) >= RAG_MIN_CHARS
        self.chunks: List[DocumentChunk] = self._build_chunks(self.full_text) if self.use_rag else []
        self.embeddings_available: bool = self._embed_chunks() if self.use_rag else False

    def _build_chunks(self, text: str) -> List[DocumentChunk]:
        chunks: List[DocumentChunk] = []
        position = 0
        for file_name, file_text in _split_by_file(text):
            for piece in _chunk_text(file_text):
                chunks.append(DocumentChunk(piece, file_name, position))
                position += 1
        return chunks

    def _embed_chunks(self) -> bool:
        """Embed every chunk once. Returns False (→ keyword fallback) on failure."""
        if not self.chunks:
            return False
        try:
            vectors = _embed_texts([c.text for c in self.chunks], task_type="retrieval_document")
            for chunk, vec in zip(self.chunks, vectors):
                chunk.embedding = vec
            return True
        except Exception:
            return False

    def retrieve(self, queries: List[str], top_k_per_query: int) -> str:
        """
        For each query, pull the top_k most relevant chunks; union them, put
        them back in original document order, and reassemble with FILE:
        headers so agents still know each chunk's source.
        """
        if not self.use_rag or not self.chunks or not queries:
            return self.full_text

        selected: set = set()

        if self.embeddings_available:
            try:
                query_vecs = _embed_texts(list(queries), task_type="retrieval_query")
                for q_vec in query_vecs:
                    ranked = sorted(
                        self.chunks,
                        key=lambda c: _cosine(q_vec, c.embedding or []),
                        reverse=True,
                    )
                    for chunk in ranked[:top_k_per_query]:
                        selected.add(chunk.position)
            except Exception:
                selected = self._keyword_retrieve(queries, top_k_per_query)
        else:
            selected = self._keyword_retrieve(queries, top_k_per_query)

        if not selected:
            return self.full_text  # safety net: never send an empty context

        chosen = sorted((c for c in self.chunks if c.position in selected),
                        key=lambda c: c.position)
        return self._assemble(chosen)

    def _keyword_retrieve(self, queries: List[str], top_k: int) -> set:
        """Pure-Python fallback: rank chunks by word overlap with the query."""
        selected: set = set()
        for q in queries:
            q_words = set(re.findall(r'\w+', q.lower()))
            if not q_words:
                continue
            scored = []
            for chunk in self.chunks:
                c_words = set(re.findall(r'\w+', chunk.text.lower()))
                scored.append((len(q_words & c_words), chunk.position))
            scored.sort(reverse=True)
            for overlap, pos in scored[:top_k]:
                if overlap > 0:
                    selected.add(pos)
        return selected

    def _assemble(self, chunks: List[DocumentChunk]) -> str:
        parts: List[str] = []
        current_file: Optional[str] = None
        for chunk in chunks:
            if chunk.file_name != current_file:
                parts.append(
                    "\n========================================\n"
                    f"FILE: {chunk.file_name}\n"
                    "========================================\n"
                )
                current_file = chunk.file_name
            parts.append(chunk.text)
        return "\n\n".join(parts)


# ============================================================
# BASE AGENT (now RAG-aware, model-fallback-aware)
# ============================================================
class BaseAgent:
    """
    A single-responsibility RFP analysis agent.

    Each subclass declares:
      - build_prompt()      what it asks the model
      - fallback()          safe default if the model/JSON fails
      - retrieval_queries   what slices of the doc it actually needs (RAG)
      - retrieval_top_k     chunks to pull PER query

    If retrieval_queries is empty (or the document is under RAG_MIN_CHARS),
    the agent gets the full text.
    """

    name: str = "base"
    retrieval_queries: List[str] = []
    retrieval_top_k: int = 3

    def build_prompt(self, text: str) -> str:
        raise NotImplementedError

    def fallback(self, error: str) -> Dict[str, Any]:
        raise NotImplementedError

    def postprocess(self, result: Dict[str, Any]) -> Dict[str, Any]:
        return result

    def get_context(self, source: Union[str, DocumentRetriever]) -> str:
        """
        Resolve the text this agent should actually see.
        - DocumentRetriever -> retrieve only this agent's relevant chunks
        - str               -> wrap in a one-off retriever (so standalone
                               calls also benefit from RAG), or return as-is
                               if this agent opts out of retrieval.
        """
        if isinstance(source, DocumentRetriever):
            retriever = source
        elif isinstance(source, str):
            if not self.retrieval_queries:
                return source
            retriever = DocumentRetriever(source)
        else:
            return str(source)

        if self.retrieval_queries:
            return retriever.retrieve(self.retrieval_queries, self.retrieval_top_k)
        return retriever.full_text

    def run(self, models, source: Union[str, DocumentRetriever], _meta: Optional[dict] = None) -> Dict[str, Any]:
        context = self.get_context(source)
        if _meta is not None:
            _meta["context_chars"] = len(context)

        prompt = self.build_prompt(context)
        try:
            response = _call_model_with_retry(models, prompt)
            result = _loose_json_parse(response.text)
            return self.postprocess(result)
        except Exception as e:
            return self.fallback(str(e))


# ============================================================
# AGENT 1: Summary  (project summary + 3 persona executive summaries + 20 FAQs)
# ============================================================
class SummaryAgent(BaseAgent):
    name = "summary"

    # This agent needs the broadest, shallowest coverage of the four --
    # 20 FAQs plus 3 executive summaries touch nearly every topic an RFP
    # covers, so it gets many queries at a low top_k each rather than a
    # few queries at a high top_k.
    retrieval_queries = [
        "project overview purpose and scope of work",
        "background and introduction of this RFP",
        "issuing organization and contact information",
        "submission deadline and due date",
        "proposal submission instructions and format",
        "estimated budget or contract value",
        "contract duration and period of performance",
        "subcontractor and teaming partner rules",
        "mandatory eligibility requirements",
        "certifications licenses and registrations required",
        "pre-proposal conference and question period",
        "evaluation and scoring methodology",
        "page limit and proposal format requirements",
        "insurance and bonding requirements",
        "payment terms and invoicing",
        "set-aside diversity minority veteran business requirements",
        "award date and decision timeline",
    ]
    retrieval_top_k = 2

    def build_prompt(self, text: str) -> str:
        return f"""
        You are an expert RFP analyst and proposal strategist. Your job has THREE
        parts. Do not extract deliverables, criteria, or a checklist.

        RFP TEXT (relevant excerpts):
        {text}

        PART 1 -- a brief project summary: 2-3 sentences describing what the
        project/RFP is asking for.

        PART 2 -- THREE executive summaries of this same RFP, each tailored to
        a different internal stakeholder who will read it before a bid/no-bid
        meeting. Each is roughly 120-180 words, in confident, plain prose
        grounded in specifics from the RFP (cite section numbers, dates, or
        dollar figures where relevant instead of vague language). Each MUST
        open with one plain-text headline sentence (the single most important
        takeaway for that persona), followed by 3-5 more sentences of
        supporting detail. Do not repeat the same sentences across the three
        versions -- each must genuinely reflect what that persona cares about.

          - "technical" -- for a Technical Lead / Solutions Architect. Cover:
            scope of work, technical requirements, deliverable complexity,
            integration/security/compliance standards, feasibility with a
            typical mid-size firm's current capabilities, and technical risks
            or open questions worth flagging before bid.

          - "cfo" -- for a CFO / Finance Director. Cover: contract value
            signals and budget (state clearly if not disclosed), payment
            terms, insurance/bonding requirements, penalty or liability
            clauses, cost-of-compliance risk, and anything with cash-flow or
            margin impact.

          - "ceo" -- for a CEO / final decision-maker. Cover: strategic fit,
            client/market significance, competitive positioning, the top 2-3
            risks, the top 2-3 opportunities, and a one-line bid/no-bid
            leaning with a brief reason.

        PART 3 -- FREQUENTLY ASKED QUESTIONS: write EXACTLY 20 basic
        questions a client, proposal manager, or new team member would
        commonly ask about an RFP like this one, each with a clear, concise
        answer (1-3 sentences). This is a standard reference section every
        RFP review should include, so cover the standard ground even if a
        given answer has to say the RFP doesn't specify it:
          - What is this RFP asking for / what is the scope of work
          - Who is the issuing organization / client
          - What is the submission deadline
          - How should proposals be submitted (portal, email, physical copy)
          - What is the estimated contract value or budget (if stated)
          - What is the contract length / period of performance
          - Are subcontractors or teaming partners allowed
          - What are the mandatory eligibility requirements
          - What certifications or licenses are required
          - Is there a pre-proposal conference or Q&A period, and when
          - How will proposals be evaluated / scored
          - What is the page limit or format requirement
          - What insurance or bonding is required
          - What are the payment terms
          - Is a bid bond or performance bond required
          - Can the same vendor submit multiple approaches/options
          - What happens if the deadline is missed
          - Who is the point of contact for questions
          - Are there set-aside or diversity (small business, minority-owned, etc.) requirements
          - What is the expected award date / decision timeline
        Use the exact wording above only as a guide -- phrase each question
        naturally, and where the RFP text answers it, ground the answer in
        that specific detail (cite the section/dollar figure/date). Where the
        RFP text is silent on something, say so plainly (e.g. "The RFP does
        not specify a budget.") rather than guessing.

        Return ONLY valid JSON, no text outside the JSON:
        {{
            "project_summary": "A brief 2-3 sentence summary of what the project/RFP is asking for",
            "executive_summaries": {{
                "technical": "...",
                "cfo": "...",
                "ceo": "..."
            }},
            "faqs": [
                {{"question": "What is the submission deadline?", "answer": "Proposals are due by 5:00 PM on March 15, 2026, per Section 2.1."}}
            ]
        }}
        The "faqs" array must contain exactly 20 items.
        """

    DEFAULT_FAQS = [
        {"question": "What is this RFP asking for?", "answer": "See the Project Summary above for the scope of work; the RFP text did not yield enough detail to answer this automatically."},
        {"question": "Who is the issuing organization?", "answer": "Not specified in the extracted text — check the RFP's cover page or introduction."},
        {"question": "What is the submission deadline?", "answer": "The RFP does not clearly specify a submission deadline in the extracted text."},
        {"question": "How should proposals be submitted?", "answer": "Not specified — check the RFP's submission instructions section."},
        {"question": "What is the estimated contract value or budget?", "answer": "The RFP does not disclose a budget or estimated contract value."},
        {"question": "What is the contract length or period of performance?", "answer": "Not specified in the extracted text."},
        {"question": "Are subcontractors or teaming partners allowed?", "answer": "Not specified — check the RFP's eligibility or teaming section."},
        {"question": "What are the mandatory eligibility requirements?", "answer": "See the Certification Navigator and Checklist sections for what was found."},
        {"question": "What certifications or licenses are required?", "answer": "See the Certification Navigator section for certifications identified in this RFP."},
        {"question": "Is there a pre-proposal conference or Q&A period?", "answer": "Not specified in the extracted text."},
        {"question": "How will proposals be evaluated?", "answer": "See the Evaluation Criteria section for the criteria identified in this RFP."},
        {"question": "What is the page limit or format requirement?", "answer": "Not specified in the extracted text."},
        {"question": "What insurance or bonding is required?", "answer": "See the Checklist Evaluation section for insurance/bonding items identified."},
        {"question": "What are the payment terms?", "answer": "See the Checklist Evaluation section for payment terms identified in this RFP."},
        {"question": "Is a bid bond or performance bond required?", "answer": "See the Checklist Evaluation section for bonding requirements identified."},
        {"question": "Can the same vendor submit multiple approaches?", "answer": "Not specified in the extracted text."},
        {"question": "What happens if the deadline is missed?", "answer": "Not specified in the extracted text."},
        {"question": "Who is the point of contact for questions?", "answer": "Not specified — check the RFP's contact/administration section."},
        {"question": "Are there set-aside or diversity requirements?", "answer": "See the Certification Navigator section for any set-aside or diversity requirements identified."},
        {"question": "What is the expected award date?", "answer": "Not specified in the extracted text."},
    ]

    def _normalize_faqs(self, faqs: Any) -> List[Dict[str, str]]:
        cleaned: List[Dict[str, str]] = []
        if isinstance(faqs, list):
            for item in faqs:
                if isinstance(item, dict) and item.get("question") and item.get("answer"):
                    cleaned.append({
                        "question": str(item["question"]).strip(),
                        "answer": str(item["answer"]).strip(),
                    })
        existing_questions = {f["question"].lower() for f in cleaned}
        for default_faq in self.DEFAULT_FAQS:
            if len(cleaned) >= 20:
                break
            if default_faq["question"].lower() not in existing_questions:
                cleaned.append(default_faq)
                existing_questions.add(default_faq["question"].lower())
        return cleaned[:20]

    def postprocess(self, result: Dict[str, Any]) -> Dict[str, Any]:
        result.setdefault("project_summary", "No summary available")

        summaries = result.get("executive_summaries", {})
        if not isinstance(summaries, dict):
            summaries = {}
        for key in ("technical", "cfo", "ceo"):
            value = summaries.get(key)
            if not value or not isinstance(value, str):
                summaries[key] = "Not available."
        result["executive_summaries"] = summaries

        result["faqs"] = self._normalize_faqs(result.get("faqs"))
        return result

    def fallback(self, error: str) -> Dict[str, Any]:
        msg = "Could not generate this summary due to an analysis error."
        return {
            "project_summary": "Error processing document",
            "executive_summaries": {"technical": msg, "cfo": msg, "ceo": msg},
            "faqs": self.DEFAULT_FAQS,
            "error": error,
        }


# ============================================================
# AGENT 2: Deliverables (with exact source quote for PDF highlighting)
# ============================================================
class DeliverablesAgent(BaseAgent):
    name = "deliverables"

    retrieval_queries = [
        "required deliverables and submission items",
        "forms documents attachments to submit",
        "proposal submission requirements and format",
        "scope of work tasks and technical requirements",
        "contractor responsibilities and obligations",
        "certifications insurance and compliance documents to submit",
    ]
    retrieval_top_k = 4

    def build_prompt(self, text: str) -> str:
        return f"""
        You are an expert RFP analyst. Your ONLY job is to extract DELIVERABLES.
        Do not write a project summary, evaluation criteria, or compliance checklist.

        The text below may contain content from MULTIPLE FILES, each marked with:
        "========================================"
        "FILE: [filename]"
        "========================================"

        **CRITICAL: For EVERY deliverable, include the EXACT filename it came from.**

        **CRITICAL: For EVERY deliverable, include a "quote" field — an EXACT, VERBATIM
        phrase copied directly from the RFP text (5 to 15 words). Do NOT paraphrase.
        Copy the exact characters as they appear in the RFP TEXT, including original
        spelling/punctuation. This is used to programmatically locate and highlight the
        deliverable inside the source PDF.**

        RFP TEXT (relevant excerpts):
        {text}

        Group deliverables into BUSINESS CATEGORIES (max 5-6 categories, max 5-6 items each).
        For EACH deliverable include:
          - "name": The deliverable name
          - "section_ref": The section number where it appears (e.g., "Section XI.B.1")
          - "reason": Why this deliverable is required (include the section reference)
          - "source_file": The EXACT filename where this deliverable was found
          - "quote": An EXACT verbatim 5-15 word snippet copied directly from the RFP text

        Return ONLY valid JSON, no text outside the JSON:
        {{
            "deliverables": [
                {{
                    "category": "Documentation & Forms",
                    "items": [
                        {{"name": "RFP Cover Sheet", "section_ref": "Section XI.B.1", "reason": "Requires the return of the RFP cover sheet", "source_file": "doc1.txt", "quote": "Offerors must return the completed RFP Cover Sheet"}}
                    ]
                }}
            ]
        }}
        """

    def postprocess(self, result: Dict[str, Any]) -> Dict[str, Any]:
        deliverables = result.get('deliverables', [])

        if isinstance(deliverables, list) and len(deliverables) > 0:
            if isinstance(deliverables[0], str):
                flat_list = [
                    {"name": item, "section_ref": "N/A", "reason": "Required by RFP", "source_file": "Unknown", "quote": ""}
                    for item in deliverables
                ]
                deliverables = [{"category": "General", "items": flat_list}]
            else:
                for cat in deliverables:
                    items = cat.get('items', [])
                    if items and isinstance(items[0], str):
                        cat['items'] = [
                            {"name": item, "section_ref": "N/A", "reason": "Required by RFP", "source_file": "Unknown", "quote": ""}
                            for item in items
                        ]
                    else:
                        for item in items:
                            item.setdefault('section_ref', 'N/A')
                            item.setdefault('reason', 'Required by RFP')
                            item.setdefault('source_file', 'Unknown')
                            item.setdefault('quote', '')

        result['deliverables'] = deliverables
        return result

    def fallback(self, error: str) -> Dict[str, Any]:
        return {"deliverables": [], "error": error}


# ============================================================
# AGENT 3: Extraction  (evaluation criteria + compliance checklist + certifications)
# ============================================================
class ExtractionAgent(BaseAgent):
    name = "extraction"

    retrieval_queries = [
        "evaluation criteria and scoring methodology",
        "how proposals will be evaluated and awarded",
        "legal requirements NDA contract terms",
        "accounting requirements W-9 tax forms invoicing",
        "technical requirements and standards",
        "operational requirements and submission logistics",
        "human resources staffing and personnel requirements",
        "certifications licenses registrations and accreditations required",
        "set-aside diversity minority women veteran owned business requirements",
        "security compliance ISO SOC HIPAA PCI standards",
    ]
    retrieval_top_k = 3

    def build_prompt(self, text: str) -> str:
        return f"""
        You are an expert RFP analyst. Your job has THREE parts. Do not
        extract deliverables or write a project summary.

        RFP TEXT (relevant excerpts):
        {text}

        PART 1 -- EVALUATION CRITERIA: the criteria the client will use to
        judge/score proposals (flat list).

        PART 2 -- COMPLIANCE CHECKLIST: broken out by internal department.
        Departments to use as keys: Legal, Accounting, Technical, Operations, HR

        PART 3 -- CERTIFICATION NAVIGATOR: identify every certification,
        license, registration, or accreditation mentioned or implied in the
        RFP that a bidder needs in order to be ELIGIBLE or to be
        COMPETITIVE. Look across the whole excerpt set -- eligibility
        sections, evaluation criteria, technical/security requirements,
        set-aside or diversity requirements, and any "must have"/"preferred"
        language. Examples of what to look for (only include what's actually
        relevant to this RFP, don't invent generic ones): state business
        license, professional/trade licenses, security clearances, ISO
        9001/27001, SOC 2, CMMI level, HIPAA compliance, PCI-DSS,
        industry-specific accreditations, minority/women/veteran/
        disadvantaged-owned business certifications, HUBZone, small business
        set-aside status, bonding capacity certification, safety
        certifications (e.g. OSHA), and professional certifications for
        named personnel (e.g. PMP, PE, CPA).

        For each certification found, classify it as:
          - "Required" -- the RFP explicitly states this is mandatory for
            eligibility (a bidder without it cannot submit or will be
            disqualified)
          - "Recommended" -- not mandatory, but the RFP's evaluation
            criteria, scoring weight, or preferred-qualifications language
            indicates it will make a proposal more competitive

        Return ONLY valid JSON, no text outside the JSON:
        {{
            "evaluation_criteria": ["Experience", "Technical Capability", "Cost", "..."],
            "compliance_checklist": {{
                "Legal": ["NDA required", "..."],
                "Accounting": ["W-9 form", "..."],
                "Technical": ["..."],
                "Operations": ["..."],
                "HR": ["..."]
            }},
            "certifications": [
                {{
                    "name": "State Contractor's License",
                    "type": "Required",
                    "reason": "Section 3.2 states bidders must hold a valid state contractor's license to be eligible to submit.",
                    "section_ref": "Section 3.2"
                }},
                {{
                    "name": "ISO 9001 Quality Management Certification",
                    "type": "Recommended",
                    "reason": "Section 6.1 evaluation criteria awards additional points for bidders holding recognized quality certifications.",
                    "section_ref": "Section 6.1"
                }}
            ]
        }}

        If the RFP mentions no certifications at all, return an empty
        "certifications" list -- do not invent generic ones that aren't
        actually supported by the text.
        """

    def postprocess(self, result: Dict[str, Any]) -> Dict[str, Any]:
        result.setdefault("evaluation_criteria", [])
        checklist = result.get("compliance_checklist", {})
        if not isinstance(checklist, dict):
            checklist = {}
        for dept in ("Legal", "Accounting", "Technical", "Operations", "HR"):
            checklist.setdefault(dept, [])
        result["compliance_checklist"] = checklist

        certifications = result.get("certifications", [])
        if not isinstance(certifications, list):
            certifications = []
        cleaned_certs: List[Dict[str, Any]] = []
        for item in certifications:
            if not isinstance(item, dict):
                continue
            item.setdefault("name", "")
            if item.get("type") not in ("Required", "Recommended"):
                item["type"] = "Recommended"
            item.setdefault("reason", "")
            item.setdefault("section_ref", "N/A")
            if item["name"]:
                cleaned_certs.append(item)
        result["certifications"] = cleaned_certs

        return result

    def fallback(self, error: str) -> Dict[str, Any]:
        return {
            "evaluation_criteria": ["Unable to extract evaluation criteria"],
            "compliance_checklist": {
                "Legal": ["Unable to extract compliance tasks"],
                "Accounting": ["Unable to extract compliance tasks"],
                "Technical": ["Unable to extract compliance tasks"],
                "Operations": ["Unable to extract compliance tasks"],
                "HR": ["Unable to extract compliance tasks"],
            },
            "certifications": [],
            "error": error,
        }


# ============================================================
# GO/NO-GO SCORING (shared by RiskAgent.postprocess AND the verification
# pass below -- factored out so a correction applied after verification
# recalculates score/decision/counts the exact same way the original
# extraction did, rather than duplicating this logic in two places).
# ============================================================
def _recompute_go_no_go_scores(result: Dict[str, Any]) -> Dict[str, Any]:
    checklist = result.get('checklist', [])
    total_score = sum(item.get('score', 0) for item in checklist)
    max_score = len(checklist) * 10

    result['overall_score'] = round(min(100, (total_score / max_score) * 100)) if max_score > 0 else 50

    score = result['overall_score']
    if score >= 71:
        result['overall_decision'] = 'GO'
    elif 51 <= score <= 70:
        result['overall_decision'] = 'ESCALATE'
    else:
        result['overall_decision'] = 'NO-GO'

    result['go_count'] = sum(1 for i in checklist if i.get('status') == 'GO')
    result['no_go_count'] = sum(1 for i in checklist if i.get('status') == 'NO-GO')
    result['escalate_count'] = sum(1 for i in checklist if i.get('status') == 'ESCALATE')
    result['conditional_count'] = result['escalate_count']
    return result


# ============================================================
# AGENT 4: Risk  (go/no-go scoring + internal conflict detection)
# ============================================================
class RiskAgent(BaseAgent):
    name = "risk"

    # Deliberately the widest retrieval budget of the four agents.
    # Conflict/contradiction detection needs to "see" widely separated
    # sections at once (e.g. a date in section 2 vs. a date in section 40),
    # so this agent gets more queries AND a higher top_k than the others.
    retrieval_queries = [
        "payment terms net 30 invoicing schedule",
        "insurance requirements liability coverage amount",
        "bid bond performance bond security deposit",
        "budget contract value financial requirements",
        "eligibility criteria registration requirements",
        "e-verify state registration legal compliance",
        "contract terms and conditions",
        "required forms and submission deadline",
        "vendor registration signatory authority",
        "scope alignment technical requirements",
        "security requirements and industry standards",
        "system integration needs",
        "submission deadline and due dates",
        "deliverable and scope of work descriptions",
        "page limit format and proposal requirements",
        "penalty clauses and liability terms",
    ]
    retrieval_top_k = 4

    def build_prompt(self, text: str) -> str:
        return f"""
        You are a Bid/No-Bid decision expert AND a meticulous RFP compliance
        reviewer. Your job has TWO parts on this same RFP. Do not extract
        deliverables, evaluation criteria, or a project summary.

        **CRITICAL: You MUST read and extract ALL numeric values (payment terms, dollar amounts, dates, deadlines) from the RFP text.**

        The text below may contain content from MULTIPLE FILES, each marked with:
        "========================================"
        "FILE: [filename]"
        "========================================"

        RFP TEXT (relevant excerpts -- may not be the full document):
        {text}

        ========================================
        PART 1 -- GO/NO-GO: Evaluate against our company checklist
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

        STATUS DEFINITIONS:
        - "GO" = Score 7-10 (We fully meet this)
        - "ESCALATE" = Score 3-6 (Missing info or needs management review)
        - "NO-GO" = Score 0-2 (Cannot meet this)

        ========================================
        PART 2 -- INTERNAL CONFLICT / CONTRADICTION CHECK
        ========================================
        Find places where the document (or, if multiple files are present,
        different files) says two different, inconsistent things about the
        same subject, based on the excerpts given to you above. Look
        specifically for:
          - DATE/DEADLINE conflicts: two different due dates, submission
            deadlines, project start/end dates, or milestone dates for what
            should be the same event.
          - DELIVERABLE/SCOPE conflicts: one section describes a deliverable
            or scope item one way, another section describes it differently.
          - FINANCIAL conflicts: different budget figures, payment terms,
            insurance amounts, or penalty amounts for the same item.
          - ELIGIBILITY/REQUIREMENT conflicts: mandatory-vs-optional language
            about the same requirement in different places.
          - FORMAT conflicts: different page limits, font requirements, or
            submission formats given in different sections.
          - Any other place where two statements about the SAME specific
            topic cannot both be true.

        Do NOT flag things that are simply incomplete, vague, or about
        different topics. Only flag genuine contradictions you can see
        directly in the excerpts above. If you find no genuine conflicts,
        return an empty list -- do not invent conflicts just to have
        something to report.

        Return JSON ONLY in this format:
        {{
            "checklist": [
                {{"category": "Financial", "item": "Payment Terms", "score": 10, "status": "GO", "reason": "NET30 terms found", "evidence": "Section 1: NET30"}}
            ],
            "go_count": 10,
            "no_go_count": 0,
            "escalate_count": 2,
            "summary": "We should bid with escalation items",
            "conflicts": [
                {{
                    "type": "Date Conflict",
                    "severity": "High",
                    "description": "Section 2 lists the proposal due date as March 15, but Section 8 lists it as March 22.",
                    "statement_a": {{"text": "Proposal due date stated as March 15", "section_ref": "Section 2.1", "source_file": "rfp.pdf", "quote": "Proposals are due no later than March 15, 2026"}},
                    "statement_b": {{"text": "Proposal due date stated as March 22", "section_ref": "Section 8.4", "source_file": "rfp.pdf", "quote": "All submissions must be received by March 22, 2026"}},
                    "recommendation": "Submit a clarification question to the contracting officer before relying on either date."
                }}
            ],
            "conflict_summary": "One sentence overview, e.g. 'Found 2 conflicts: 1 high severity date conflict and 1 medium severity scope conflict.' or 'No internal conflicts found.'"
        }}

        IMPORTANT: DO NOT include "overall_score" in your JSON. It is calculated automatically.
        """

    def postprocess(self, result: Dict[str, Any]) -> Dict[str, Any]:
        if 'checklist' not in result:
            result['checklist'] = []

        result = _recompute_go_no_go_scores(result)

        conflicts = result.get("conflicts", [])
        if not isinstance(conflicts, list):
            conflicts = []

        cleaned: List[Dict[str, Any]] = []
        for c in conflicts:
            if not isinstance(c, dict):
                continue
            c.setdefault("type", "Other")
            if c.get("severity") not in ("High", "Medium", "Low"):
                c["severity"] = "Medium"
            c.setdefault("description", "")
            c.setdefault("recommendation", "Clarify with the contracting officer before submission.")
            for key in ("statement_a", "statement_b"):
                stmt = c.get(key)
                if not isinstance(stmt, dict):
                    stmt = {}
                stmt.setdefault("text", "")
                stmt.setdefault("section_ref", "N/A")
                stmt.setdefault("source_file", "Unknown")
                stmt.setdefault("quote", "")
                c[key] = stmt
            cleaned.append(c)

        result["conflicts"] = cleaned
        if not result.get("conflict_summary"):
            result["conflict_summary"] = (
                "No internal conflicts found." if not cleaned
                else f"Found {len(cleaned)} potential conflict(s) requiring review."
            )

        return result

    def fallback(self, error: str) -> Dict[str, Any]:
        return {
            "overall_decision": "NEEDS REVIEW",
            "overall_score": 50,
            "checklist": [
                {"category": "Financial", "item": "Payment Terms", "score": 5, "status": "ESCALATE", "reason": "Could not analyze", "evidence": "Check RFP manually"},
                {"category": "Legal", "item": "Eligibility", "score": 5, "status": "ESCALATE", "reason": "Could not analyze", "evidence": "Check RFP manually"},
                {"category": "Operations", "item": "Deadlines", "score": 5, "status": "ESCALATE", "reason": "Could not analyze", "evidence": "Check RFP manually"},
                {"category": "Technical", "item": "Scope", "score": 5, "status": "ESCALATE", "reason": "Could not analyze", "evidence": "Check RFP manually"},
            ],
            "go_count": 0,
            "no_go_count": 0,
            "escalate_count": 4,
            "conditional_count": 4,
            "summary": f"AI analysis encountered an error: {error}. Please review the RFP manually.",
            "conflicts": [],
            "conflict_summary": (
                "Conflict analysis could not be completed due to an error. "
                "Please review the RFP manually for inconsistencies."
            ),
            "error": error,
        }


# ============================================================
# DOUBLE VERIFICATION PASS
# ============================================================

# ---- Tier 1: FREE, zero-cost quote verification (no AI call) --------------

def _normalize_for_match(text: str) -> str:
    return re.sub(r'\s+', ' ', text or '').strip().lower()


def _quote_supported_by_text(quote: str, full_text: str, normalized_full: Optional[str] = None) -> bool:
    """
    True if `quote` is genuinely present in the source document -- tried as
    an exact substring first (cheapest, most reliable), then falling back to
    a whitespace/case-normalized substring check (handles the AI copying a
    quote that spans a PDF-extraction line break or has doubled spaces).
    """
    if not quote or not quote.strip():
        return False
    if quote in full_text:
        return True
    normalized_full = normalized_full if normalized_full is not None else _normalize_for_match(full_text)
    normalized_quote = _normalize_for_match(quote)
    return bool(normalized_quote) and normalized_quote in normalized_full


def _verify_quotes_programmatically(deliverables: List[Dict[str, Any]], full_text: str) -> Tuple[int, int]:
    """
    Tags every deliverable item with "quote_verified": bool, checked against
    the actual source text with plain Python string matching -- zero API
    calls, zero added latency worth mentioning. Returns (checked, verified)
    counts for the pipeline's reporting/meta.
    """
    normalized_full = _normalize_for_match(full_text)
    checked = 0
    verified = 0
    for cat in deliverables or []:
        for item in cat.get('items', []):
            checked += 1
            ok = _quote_supported_by_text(item.get('quote', ''), full_text, normalized_full)
            item['quote_verified'] = ok
            if ok:
                verified += 1
    return checked, verified


# ---- Tier 2: ONE batched AI fact-check over the highest-stakes claims -----

class VerificationAgent:
    """
    Fact-checks the highest-stakes outputs from the four extraction agents --
    deliverables, the go/no-go checklist, and REQUIRED (mandatory-for-
    eligibility) certifications -- against the source RFP text, in ONE
    additional batched call rather than one verification call per agent.
    Explicitly out of scope: executive summaries, FAQs, evaluation criteria,
    soft compliance tasks, Recommended-tier certifications, and conflicts --
    lower stakes, not worth the extra cost.
    """

    name = "verification"

    def _build_context(self, retriever: DocumentRetriever) -> str:
        # Reuse DeliverablesAgent's + RiskAgent's own retrieval queries (they
        # already cover deliverables and the financial/legal/ops/technical
        # checklist topics) plus one query specifically for certifications,
        # at a slightly wider top_k since the verifier needs to actually SEE
        # the supporting text, not just a hint of it.
        queries = (
            list(DeliverablesAgent.retrieval_queries)
            + list(RiskAgent.retrieval_queries)
            + ["certifications licenses registrations required for eligibility"]
        )
        return retriever.retrieve(queries, top_k_per_query=5)

    def _collect_claims(
        self,
        deliverables: List[Dict[str, Any]],
        checklist: List[Dict[str, Any]],
        certifications: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        claims: List[Dict[str, Any]] = []

        for cat_idx, cat in enumerate(deliverables or []):
            for item_idx, item in enumerate(cat.get('items', [])):
                claims.append({
                    "id": f"D{cat_idx}-{item_idx}",
                    "kind": "deliverable",
                    "name": item.get('name', ''),
                    "reason": item.get('reason', ''),
                    "section_ref": item.get('section_ref', ''),
                })

        for idx, item in enumerate(checklist or []):
            claims.append({
                "id": f"C{idx}",
                "kind": "checklist_item",
                "category": item.get('category', ''),
                "item": item.get('item', ''),
                "status": item.get('status', ''),
                "score": item.get('score', ''),
                "reason": item.get('reason', ''),
                "evidence": item.get('evidence', ''),
            })

        for idx, cert in enumerate(certifications or []):
            if cert.get('type') != 'Required':
                continue  # mandatory items only -- Recommended certs are out of scope
            claims.append({
                "id": f"R{idx}",
                "kind": "required_certification",
                "name": cert.get('name', ''),
                "reason": cert.get('reason', ''),
                "section_ref": cert.get('section_ref', ''),
            })

        return claims

    def build_prompt(self, context: str, claims: List[Dict[str, Any]]) -> str:
        claims_json = json.dumps(claims, ensure_ascii=False, indent=2)
        return f"""
        You are a meticulous fact-checker reviewing another AI's extraction of
        an RFP document. Below are RFP text excerpts and a list of CLAIMS that
        AI already made. Your ONLY job is to verify whether each claim is
        actually supported by the RFP text excerpts given -- do not invent
        new claims, do not extract anything not already listed.

        RFP TEXT (relevant excerpts):
        {context}

        CLAIMS TO VERIFY ({len(claims)} total):
        {claims_json}

        For EACH claim id above, decide if it is genuinely supported by the
        RFP text excerpts:
          - "deliverable" claims: is this actually described as required
            somewhere in the excerpts, and is the section_ref/reason accurate?
          - "checklist_item" claims: does the excerpt evidence actually
            support the given score/status, or should it be different?
          - "required_certification" claims: does the RFP excerpt actually
            state this certification/license is MANDATORY for eligibility
            (not just preferred)?

        If a claim is NOT well supported, or is only PARTIALLY correct, mark
        "verified": false and provide corrected values for whichever fields
        are wrong (omit a "corrected_..." key entirely if that field was
        actually fine as originally written).

        If you cannot find evidence either way in the excerpts given (the
        excerpts may not cover 100% of the document), mark "verified": true
        UNLESS something in the excerpts actively CONTRADICTS the claim --
        do not fail a claim just because its supporting section wasn't in
        the excerpt you were given.

        Return ONLY valid JSON, no text outside the JSON, in this exact shape:
        {{
            "verifications": [
                {{
                    "id": "D0-0",
                    "verified": true
                }},
                {{
                    "id": "D0-1",
                    "verified": false,
                    "corrected_reason": "...",
                    "corrected_section_ref": "..."
                }},
                {{
                    "id": "C3",
                    "verified": false,
                    "corrected_status": "ESCALATE",
                    "corrected_score": 5,
                    "corrected_reason": "...",
                    "corrected_evidence": "..."
                }},
                {{
                    "id": "R0",
                    "verified": false,
                    "corrected_type": "Recommended",
                    "corrected_reason": "..."
                }}
            ]
        }}
        You MUST include exactly one entry per claim id given above -- {len(claims)} total.
        """

    def run(
        self,
        models,
        retriever: DocumentRetriever,
        deliverables: List[Dict[str, Any]],
        checklist: List[Dict[str, Any]],
        certifications: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        claims = self._collect_claims(deliverables, checklist, certifications)
        if not claims:
            return {"verifications": [], "claim_count": 0}

        context = self._build_context(retriever)
        prompt = self.build_prompt(context, claims)
        try:
            response = _call_model_with_retry(models, prompt)
            result = _loose_json_parse(response.text)
            verifications = result.get("verifications", [])
            if not isinstance(verifications, list):
                verifications = []
            return {"verifications": verifications, "claim_count": len(claims)}
        except Exception as e:
            # A failed verification pass should never take down the analysis --
            # every claim just stays unverified (no corrections applied).
            return {"verifications": [], "claim_count": len(claims), "error": str(e)}


# ---- Applying corrections --------------------------------------------------

def _apply_deliverable_correction(item: Dict[str, Any], v: Dict[str, Any]) -> None:
    item['verified'] = bool(v.get('verified', True))
    if not item['verified']:
        if 'corrected_reason' in v and v['corrected_reason']:
            item['reason'] = v['corrected_reason']
        if 'corrected_section_ref' in v and v['corrected_section_ref']:
            item['section_ref'] = v['corrected_section_ref']


def _apply_checklist_correction(item: Dict[str, Any], v: Dict[str, Any]) -> None:
    item['verified'] = bool(v.get('verified', True))
    if not item['verified']:
        if v.get('corrected_status') in ('GO', 'NO-GO', 'ESCALATE'):
            item['status'] = v['corrected_status']
        if 'corrected_score' in v:
            try:
                item['score'] = max(0, min(10, int(v['corrected_score'])))
            except (TypeError, ValueError):
                pass
        if 'corrected_reason' in v and v['corrected_reason']:
            item['reason'] = v['corrected_reason']
        if 'corrected_evidence' in v and v['corrected_evidence']:
            item['evidence'] = v['corrected_evidence']


def _apply_certification_correction(cert: Dict[str, Any], v: Dict[str, Any]) -> None:
    cert['verified'] = bool(v.get('verified', True))
    if not cert['verified']:
        if v.get('corrected_type') in ('Required', 'Recommended'):
            cert['type'] = v['corrected_type']
        if 'corrected_reason' in v and v['corrected_reason']:
            cert['reason'] = v['corrected_reason']


def _run_verification_pass(
    models,
    retriever: DocumentRetriever,
    deliverables: List[Dict[str, Any]],
    checklist: List[Dict[str, Any]],
    certifications: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Runs both verification tiers and applies corrections IN PLACE (mutating
    the dicts inside deliverables/checklist/certifications, which are the
    same objects held by raw_results, so callers don't need to re-assign
    anything). Returns a meta dict for _agent_meta["verification"].

    If the checklist was touched, the caller is responsible for calling
    _recompute_go_no_go_scores() again afterward -- this function only
    corrects individual checklist ITEMS, not the aggregate score/decision.
    """
    start = time.time()

    quote_checked, quote_verified = _verify_quotes_programmatically(deliverables, retriever.full_text)

    ai_output = VerificationAgent().run(models, retriever, deliverables, checklist, certifications)
    verifications_by_id = {
        v.get('id'): v for v in ai_output.get('verifications', [])
        if isinstance(v, dict) and v.get('id')
    }

    corrected_count = 0
    verified_count = 0

    for cat_idx, cat in enumerate(deliverables or []):
        for item_idx, item in enumerate(cat.get('items', [])):
            v = verifications_by_id.get(f"D{cat_idx}-{item_idx}")
            if v is None:
                continue
            _apply_deliverable_correction(item, v)
            verified_count += 1
            if not item['verified']:
                corrected_count += 1

    for idx, item in enumerate(checklist or []):
        v = verifications_by_id.get(f"C{idx}")
        if v is None:
            continue
        _apply_checklist_correction(item, v)
        verified_count += 1
        if not item['verified']:
            corrected_count += 1

    for idx, cert in enumerate(certifications or []):
        v = verifications_by_id.get(f"R{idx}")
        if v is None:
            continue
        _apply_certification_correction(cert, v)
        verified_count += 1
        if not cert['verified']:
            corrected_count += 1

    meta: Dict[str, Any] = {
        "elapsed_seconds": round(time.time() - start, 2),
        "claims_checked": ai_output.get("claim_count", 0),
        "claims_verified_ok": verified_count - corrected_count,
        "corrections_applied": corrected_count,
        "quote_check": {"checked": quote_checked, "verified": quote_verified},
    }
    if "error" in ai_output:
        meta["error"] = ai_output["error"]
    return meta


# ============================================================
# ORCHESTRATOR: dispatch all agents concurrently, merge results
# ============================================================
AGENT_REGISTRY = {
    "summary": SummaryAgent,
    "deliverables": DeliverablesAgent,
    "extraction": ExtractionAgent,
    "risk": RiskAgent,
}


def _timed_run(agent: BaseAgent, models, source: Union[str, DocumentRetriever]):
    meta: dict = {}
    start = time.time()
    result = agent.run(models, source, _meta=meta)
    return result, time.time() - start, meta


def run_agents_parallel(models, text: str) -> Dict[str, Any]:
    """
    Build the retriever ONCE (chunk + embed, if the document is large enough
    to bother), then run every agent concurrently. Each agent pulls only its
    relevant chunks, so total generation input drops well below the old ~4x
    full-text-per-agent cost on large documents. Small documents skip RAG
    entirely and every agent gets the full text, unchanged from before.

    `models` is a list of GenerativeModel instances in preference order --
    each agent call independently fails over down that list on a zero-quota
    or exhausted-rate-limit error (see _call_model_with_retry).

    After the four extraction agents finish, ONE additional verification
    pass runs automatically: it fact-checks deliverables + the go/no-go
    checklist + required certifications against the source text and
    auto-corrects anything unsupported (see "DOUBLE VERIFICATION PASS"
    above). This makes it 5 total model calls per analysis, not 8.
    """
    start_time = time.time()

    retriever = DocumentRetriever(text)

    agent_instances = {name: cls() for name, cls in AGENT_REGISTRY.items()}
    raw_results: Dict[str, Any] = {}
    agent_timings: Dict[str, float] = {}
    agent_errors: Dict[str, str] = {}
    per_agent_context_chars: Dict[str, int] = {}

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(agent_instances)) as executor:
        future_to_name = {
            executor.submit(_timed_run, agent, models, retriever): name
            for name, agent in agent_instances.items()
        }

        for future in concurrent.futures.as_completed(future_to_name):
            name = future_to_name[future]
            try:
                result, elapsed, meta = future.result()
                raw_results[name] = result
                agent_timings[name] = round(elapsed, 2)
                per_agent_context_chars[name] = meta.get("context_chars", 0)
                if isinstance(result, dict) and 'error' in result:
                    agent_errors[name] = result['error']
            except Exception as e:
                raw_results[name] = agent_instances[name].fallback(str(e))
                agent_timings[name] = 0.0
                agent_errors[name] = str(e)
                per_agent_context_chars[name] = 0

    summary_result = raw_results.get("summary", {})
    extraction_result = raw_results.get("extraction", {})
    risk_result = raw_results.get("risk", {})

    deliverables = raw_results.get("deliverables", {}).get("deliverables", [])
    checklist = risk_result.get("checklist", [])
    certifications = extraction_result.get("certifications", [])

    # ---- Double verification pass (runs automatically every analysis) ----
    verification_meta = _run_verification_pass(models, retriever, deliverables, checklist, certifications)
    if checklist:
        # Corrections may have changed individual item scores/statuses --
        # recompute the aggregate overall_score/decision/counts to match.
        risk_result = _recompute_go_no_go_scores(risk_result)

    total_elapsed = time.time() - start_time

    full_chars = len(retriever.full_text)
    num_agents = len(agent_instances)
    baseline_chars = full_chars * num_agents  # old: full doc sent to every agent
    actual_chars = sum(per_agent_context_chars.values())
    savings_pct = round((1 - actual_chars / baseline_chars) * 100, 1) if baseline_chars else 0.0

    combined: Dict[str, Any] = {
        "project_summary": summary_result.get("project_summary", "No summary available"),
        "executive_summaries": summary_result.get(
            "executive_summaries",
            {"technical": "Not available.", "cfo": "Not available.", "ceo": "Not available."},
        ),
        "faqs": summary_result.get("faqs", SummaryAgent.DEFAULT_FAQS),
        "deliverables": deliverables,
        "evaluation_criteria": extraction_result.get("evaluation_criteria", []),
        "certifications": certifications,
        "compliance_checklist": extraction_result.get("compliance_checklist", {}),
        "go_no_go": {k: v for k, v in risk_result.items() if k not in ("conflicts", "conflict_summary")},
        "conflicts": risk_result.get("conflicts", []),
        "conflict_summary": risk_result.get(
            "conflict_summary",
            "Conflict analysis could not be completed due to an error. Please review the RFP manually.",
        ),
        "_agent_meta": {
            "total_elapsed_seconds": round(total_elapsed, 2),
            "per_agent_seconds": agent_timings,
            "errors": agent_errors,
            "retrieval": {
                "rag_used": retriever.use_rag,
                "embeddings_used": retriever.embeddings_available,
                "retrieval_mode": (
                    "gemini-embeddings" if retriever.embeddings_available
                    else ("keyword-fallback" if retriever.use_rag else "full-text (below RAG_MIN_CHARS)")
                ),
                "num_chunks": len(retriever.chunks),
                "full_text_chars": full_chars,
                "per_agent_context_chars": per_agent_context_chars,
                "baseline_chars_full_copy": baseline_chars,
                "actual_context_chars": actual_chars,
                "estimated_token_savings_pct": savings_pct,
            },
            "verification": verification_meta,
        },
    }
    return combined