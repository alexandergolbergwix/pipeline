"""Professional model evaluation harness using Gemini 2.5 Pro as judge.

Reads the MHM Pipeline's Stage 2 output (`ner_results.json`) and the
original MARC TSV extract (`marc_extracted.json`) for the canonical
test_subset.tsv corpus, filters every model prediction at the
``model_confidence >= 0.85`` auto-approve threshold (with fallback to
``confidence`` for sources that don't emit ``model_confidence``), then
asks Gemini 2.5 Pro to compare each prediction against the full MARC
record and score correctness per field.

Models evaluated (all 5):

  Person NER (Joint)         — entities[source=person_ner]
  Provenance NER             — entities[source=provenance_ner]
  Contents NER               — entities[source=contents_ner]
  Genre Classifier           — ml_genres
  MARC500 Colophon Classifier — ml_colophon_sentences

Each judgment emits a structured JSON verdict:

    {
        "name_ok": "yes|partial|no",   // does the text actually denote that entity in MARC?
        "type_ok": "yes|partial|no",   // is OWNER/WORK/PERSON/genre/colophon correct?
        "role_ok": "yes|partial|no|n/a",  // n/a if the model doesn't assign a role
        "overall": "full|partial|fail",
        "reasoning": "1-2 sentence explanation"
    }

The script is idempotent — judgments are cached by SHA-256 of the prompt
so re-runs only call Gemini for new candidates.

Usage:

    PYTHONPATH=src:. .venv/bin/python scripts/evaluate_models_with_gemini.py

    # Override inputs:
    PYTHONPATH=src:. .venv/bin/python scripts/evaluate_models_with_gemini.py \\
        --ner-results eval/work/ner_results.json \\
        --marc-extract eval/work/marc_extracted.json \\
        --confidence-threshold 0.85 \\
        --parallel 8

Outputs (under ``eval/``):

    results_<ts>.jsonl   — one judgment per line
    summary_<ts>.csv     — per-model + per-type aggregate metrics
    report_<ts>.md       — human-readable markdown report
"""

from __future__ import annotations

import argparse
import csv
import getpass
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import threading
from collections import Counter, defaultdict, deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

# ──────────────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).resolve().parent.parent
EVAL_DIR = REPO_ROOT / "eval"
CACHE_PATH = EVAL_DIR / ".gemini_cache.jsonl"

DEFAULT_NER_RESULTS = EVAL_DIR / "work" / "ner_results.json"
DEFAULT_MARC_EXTRACT = EVAL_DIR / "work" / "marc_extracted.json"
DEFAULT_THRESHOLD = 0.85
# Gemini's free-tier limit on 3.1 Pro Preview is roughly 60 RPM, but
# the practical sustained rate before 429s start is lower (~30 RPM).
# We combine a small parallel pool (latency overlap) with a strict
# global token-bucket rate limiter (DEFAULT_RPM) — see _RateLimiter.
# Net effect: zero 429s, predictable throughput.
DEFAULT_PARALLEL = 2
DEFAULT_RPM = 25
DEFAULT_MODEL = "gemini-3.1-pro-preview"

# Strict JSON Schema enforced by Gemini's structured-output mechanism. The
# v1beta REST API on `generativelanguage.googleapis.com` accepts schemas
# in the 2.5-style uppercase form (`"type": "OBJECT"`) — the newer
# `responseFormat.text.schema` shape advertised in Gemini 3 marketing
# docs is not yet wired into v1beta as of 2026-05. Enums lock the verdict
# labels so the local parser never has to guess.
_VERDICT_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "name_ok":   {"type": "STRING", "enum": ["yes", "partial", "no"]},
        "type_ok":   {"type": "STRING", "enum": ["yes", "partial", "no"]},
        "role_ok":   {"type": "STRING", "enum": ["yes", "partial", "no", "n/a"]},
        "overall":   {"type": "STRING", "enum": ["full", "partial", "fail"]},
        "reasoning": {"type": "STRING"},
    },
    "required": ["name_ok", "type_ok", "role_ok", "overall", "reasoning"],
    "propertyOrdering": ["name_ok", "type_ok", "role_ok", "overall", "reasoning"],
}

GEMINI_URL_TEMPLATE = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "{model}:generateContent"
)

# marc_extracted.json holds Stage 1's *post-processed* semantic keys, not
# raw MARC subfields. The schema is roughly:
#   title             — str (from MARC 245$a)
#   variant_titles    — list[str] (MARC 246/740)
#   authors           — list[{name, role, certainty, attribution_source}] (100/110/111)
#   contributors      — list[{name, role}] (700/710/711)
#   provenance        — str (MARC 561 free text)
#   notes             — list[str] (MARC 500 sentences; first element is the
#                       source filename — skip it when formatting)
#   contents          — str / list (MARC 505 contents note)
#   colophon_text     — str (extracted scribe-completion note)
#   data_from_colophon — dict (parsed names/dates from colophon)
#   genres            — list[str] (MARC 655 gold genre headings)
#   subjects          — list[{term, type, ...}] (MARC 600/610/630/650/651)
#   dates             — dict (parsed canonical date)
#   canonical_references — list[{hierarchy, book/tractate}] (parsed 630/etc.)
#   related_works     — list[{title, date, relationship}]
#   place             — str (cataloged place of creation)
#   related_places    — list[str]
#   shelfmark         — str
#
# Each model gets a curated slice of these so Gemini has just enough
# context to judge without drowning in irrelevant fields.

_PERSON_FIELDS = [
    "title", "authors", "contributors", "provenance", "notes",
    "colophon_text", "data_from_colophon",
]
_PROVENANCE_FIELDS = [
    "provenance", "notes", "colophon_text", "dates", "place",
    "acquisition_source", "related_places",
]
_CONTENTS_FIELDS = [
    "title", "variant_titles", "contents", "notes", "colophon_text",
    "canonical_references", "related_works",
]
_GENRE_FIELDS = [
    "title", "variant_titles", "notes", "genres", "subjects",
    "is_anthology", "has_decoration",
]
_MARC500_FIELDS = [
    "title", "notes", "provenance", "colophon_text", "data_from_colophon",
]

# ──────────────────────────────────────────────────────────────────────────────
# Data classes
# ──────────────────────────────────────────────────────────────────────────────


@dataclass
class Candidate:
    """One prediction to be judged by Gemini."""
    record_id: str
    model: str  # 'person_ner' | 'provenance_ner' | 'contents_ner' | 'genre' | 'marc500'
    payload: dict[str, Any]      # the model's emitted dict (entity / ml_genre / colophon sentence)
    confidence: float            # the filter we tripped on
    marc_context: dict[str, str]  # selected MARC subfields for the judge


@dataclass
class Judgment:
    """Structured verdict from Gemini per candidate."""
    record_id: str
    model: str
    payload: dict[str, Any]
    confidence: float
    name_ok: str = "no"      # yes | partial | no
    type_ok: str = "no"      # yes | partial | no
    role_ok: str = "n/a"     # yes | partial | no | n/a
    overall: str = "fail"    # full | partial | fail
    reasoning: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ──────────────────────────────────────────────────────────────────────────────
# I/O helpers
# ──────────────────────────────────────────────────────────────────────────────


def _clean_cell(cell: str | None) -> str:
    """Strip the TSV's triple-quote / doubled-quote MARC encoding."""
    if cell is None:
        return ""
    s = str(cell).strip()
    if not s:
        return ""
    if s.startswith('"""') and s.endswith('"""') and len(s) >= 6:
        s = s[3:-3]
    elif s.startswith('"') and s.endswith('"') and len(s) >= 2:
        s = s[1:-1]
    return s.replace('""', '"').strip()


def _select_marc(record: dict[str, Any], fields: list[str]) -> dict[str, str]:
    """Return a tidy {key: human-readable value} dict.

    Coerces semantic fields from marc_extracted.json (lists of dicts,
    nested dicts, etc.) into one-line strings so Gemini can read them.
    Skips empty fields. Treats ``notes[0]`` as the source filename and
    strips it (Stage 1 stores it as a marker, not actual MARC content).
    """
    out: dict[str, str] = {}
    for f in fields:
        v = record.get(f)
        if v is None or v == "" or v == []:
            continue
        if f == "notes" and isinstance(v, list):
            # First element is the source filename — skip it. Join real
            # notes with ' | ' to keep them on one logical line each.
            real = [str(x) for x in v[1:] if x]
            if not real:
                continue
            out[f] = " | ".join(real)
            continue
        if isinstance(v, list):
            out[f] = " | ".join(
                json.dumps(x, ensure_ascii=False) if isinstance(x, dict) else str(x)
                for x in v if x
            )
        elif isinstance(v, dict):
            out[f] = json.dumps(v, ensure_ascii=False)
        else:
            out[f] = str(v)
    return out


def _ner_confidence(entity: dict[str, Any]) -> float:
    """Return the entity's ``confidence`` value.

    We deliberately use ``confidence`` (NOT ``model_confidence``) for all
    sources, matching the GUI's primary auto-approve gate:

    - Person NER: ``confidence`` is the keyword role-classifier score
      (bimodal 0.60 / 0.85 — 0.85 means a Hebrew role keyword like
      ``מעתיק`` / ``מתרגם`` matched cleanly). Stricter and more honest
      than the raw softmax (``model_confidence``), which can be
      over-confident on ambiguous Hebrew names.
    - Provenance / Contents / Genre / MARC500: ``confidence`` is the
      model's own softmax / sigmoid probability — same semantics they
      use to drive their own auto-approval logic.

    Using a single field across all 5 models also keeps the per-model
    precision numbers in this evaluation directly comparable.
    """
    c = entity.get("confidence")
    if c is None:
        return 0.0
    try:
        return float(c)
    except (TypeError, ValueError):
        return 0.0


# ──────────────────────────────────────────────────────────────────────────────
# Candidate extraction
# ──────────────────────────────────────────────────────────────────────────────


def build_candidates(
    ner_records: list[dict[str, Any]],
    marc_records: list[dict[str, Any]],
    threshold: float,
) -> list[Candidate]:
    """Walk all records and emit one Candidate per prediction crossing threshold."""
    marc_by_id = {
        r.get("_control_number") or r.get("001", ""): r for r in marc_records
    }
    candidates: list[Candidate] = []

    for r in ner_records:
        rid = r.get("_control_number") or r.get("001", "") or ""
        marc = marc_by_id.get(rid, {})

        # — entities (person / provenance / contents NER) —
        for ent in r.get("entities", []) or []:
            src = ent.get("source", "")
            conf = _ner_confidence(ent)
            if conf < threshold:
                continue
            if src == "person_ner":
                fields, model = _PERSON_FIELDS, "person_ner"
            elif src == "provenance_ner":
                fields, model = _PROVENANCE_FIELDS, "provenance_ner"
            elif src == "contents_ner":
                fields, model = _CONTENTS_FIELDS, "contents_ner"
            else:
                continue
            candidates.append(Candidate(
                record_id=rid,
                model=model,
                payload=ent,
                confidence=conf,
                marc_context=_select_marc(marc, fields),
            ))

        # — genre classifier predictions —
        for g in r.get("ml_genres", []) or []:
            conf = float(g.get("confidence", 0.0))
            if conf < threshold:
                continue
            candidates.append(Candidate(
                record_id=rid,
                model="genre",
                payload=g,
                confidence=conf,
                marc_context=_select_marc(marc, _GENRE_FIELDS),
            ))

        # — MARC 500 colophon classifier — sentences already passed per-fold
        #   threshold during classification; we still apply the auto-approve
        #   floor for consistency. ml_colophon_sentences may be list[str] or
        #   list[dict] depending on pipeline version.
        for cs in r.get("ml_colophon_sentences", []) or []:
            if isinstance(cs, str):
                sentence, conf = cs, 1.0  # passed threshold → treat as 1.0
            else:
                sentence = cs.get("text") or cs.get("sentence") or ""
                conf = float(cs.get("confidence", 1.0))
            if conf < threshold or not sentence:
                continue
            candidates.append(Candidate(
                record_id=rid,
                model="marc500",
                payload={"sentence": sentence, "confidence": conf},
                confidence=conf,
                marc_context=_select_marc(marc, _MARC500_FIELDS),
            ))

    return candidates


# ──────────────────────────────────────────────────────────────────────────────
# Prompt builders
# ──────────────────────────────────────────────────────────────────────────────


_JUDGE_INSTRUCTIONS = """\
You are an expert Hebrew-manuscript cataloger evaluating an automated NER
pipeline's predictions against the original MARC bibliographic record.

For each prediction, decide:

  name_ok   — does the extracted text actually denote a real entity present
              in (or strongly implied by) the MARC context?
              yes     : exact or trivially-equivalent surface form match
              partial : same entity, but trimmed / extended / mis-vowelled /
                        wrong subset of the span
              no      : the text does not appear in MARC at all, OR refers
                        to a different entity than the model claims

  type_ok   — is the predicted entity type / class correct?
              yes     : type label matches what MARC indicates
              partial : type label is in the right family but not exact
                        (e.g. predicted COLLECTION but MARC says PUBLISHER)
              no      : type label is clearly wrong

  role_ok   — only for person NER. Does the assigned role (AUTHOR, OWNER,
              SCRIBE, TRANSLATOR, COMMENTATOR, EDITOR, CENSOR) match the
              MARC role indicator ($e subfield) or the role implied by the
              MARC field (100 = author, 700 = added entry, 561 = owner)?
              yes / partial / no / n/a (n/a if model is not person NER)

  overall   — full   : every applicable check is "yes"
              partial: at least one check is "partial" (or one is "no" and
                       the others are "yes")
              fail   : two or more checks are "no", or name_ok is "no"

  reasoning — one to two short sentences explaining the verdict. English
              or Hebrew, whichever is clearer. Cite the MARC subfield that
              decided it (e.g. "MARC 100$a says 'X', model said 'Y'").

CRITICAL — return ONLY a single JSON object, no markdown fences, no prose
before or after. Schema:

{
  "name_ok":   "yes" | "partial" | "no",
  "type_ok":   "yes" | "partial" | "no",
  "role_ok":   "yes" | "partial" | "no" | "n/a",
  "overall":   "full" | "partial" | "fail",
  "reasoning": "..."
}
"""


def _format_marc(marc: dict[str, str]) -> str:
    if not marc:
        return "  (no relevant MARC subfields present)"
    return "\n".join(f"  {k}: {v}" for k, v in sorted(marc.items()))


def build_prompt(cand: Candidate) -> str:
    """Return the full prompt text to send to Gemini."""
    p = cand.payload
    if cand.model == "person_ner":
        block = (
            f"Model: Person NER (Joint name + role)\n"
            f"Prediction:\n"
            f"  text:        {p.get('person', p.get('text', ''))}\n"
            f"  role:        {p.get('role', 'n/a')}\n"
            f"  confidence:  {cand.confidence:.3f}\n"
            f"  source-field hint: {p.get('source_field', 'n/a')}\n"
        )
    elif cand.model == "provenance_ner":
        block = (
            f"Model: Provenance NER (OWNER / DATE / COLLECTION)\n"
            f"Prediction:\n"
            f"  text:        {p.get('text', p.get('person', ''))}\n"
            f"  type:        {p.get('type', '')}\n"
            f"  confidence:  {cand.confidence:.3f}\n"
        )
    elif cand.model == "contents_ner":
        block = (
            f"Model: Contents NER (WORK / FOLIO / WORK_AUTHOR)\n"
            f"Prediction:\n"
            f"  text:        {p.get('text', '')}\n"
            f"  type:        {p.get('type', '')}\n"
            f"  confidence:  {cand.confidence:.3f}\n"
        )
    elif cand.model == "genre":
        block = (
            f"Model: Genre Classifier (multi-label)\n"
            f"Prediction:\n"
            f"  genre:       {p.get('label', p.get('genre', ''))}\n"
            f"  confidence:  {cand.confidence:.3f}\n"
            f"Note: type_ok=yes if the genre is supported by the title/notes\n"
            f"(MARC 245+500) or matches the gold MARC 655 label when present.\n"
            f"role_ok = n/a.\n"
        )
    elif cand.model == "marc500":
        block = (
            f"Model: MARC 500 Colophon Classifier (binary)\n"
            f"Prediction:\n"
            f"  sentence:    {p.get('sentence', '')}\n"
            f"  classified-as: COLOPHON (above 0.45 threshold)\n"
            f"Note: name_ok=yes iff the sentence is actually a colophon\n"
            f"(scribe signature: completion date, place, name). type_ok=yes\n"
            f"if it's the right TYPE of note (colophon vs codicological note\n"
            f"vs bibliographic citation). role_ok = n/a.\n"
        )
    else:
        block = f"Model: {cand.model}\nPrediction: {json.dumps(p, ensure_ascii=False)}\n"

    return (
        _JUDGE_INSTRUCTIONS
        + "\n────────────────────────────────────────\n"
        + f"Record ID: {cand.record_id}\n\n"
        + block
        + f"\nRelevant MARC subfields for this record:\n{_format_marc(cand.marc_context)}\n"
        + "\nReturn only the JSON verdict."
    )


# ──────────────────────────────────────────────────────────────────────────────
# Rate limiter
# ──────────────────────────────────────────────────────────────────────────────


class _RateLimiter:
    """Thread-safe sliding-window rate limiter.

    Guarantees no more than ``max_rpm`` requests are dispatched in any
    60-second window. Threads call ``acquire()`` before each request; if
    the budget for the current window is exhausted, the call blocks until
    the oldest in-window request ages out.

    This is a hard cap — combined with retry-on-429 it makes 429s nearly
    impossible. The free tier on Gemini 3.1 Pro Preview reliably accepts
    25 RPM sustained; 30 starts triggering bursts. We default to 25.
    """

    def __init__(self, max_rpm: int) -> None:
        self._max = max(1, int(max_rpm))
        self._window = deque()  # timestamps of in-flight starts
        self._lock = threading.Lock()

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                # Drop timestamps older than the 60-second window
                while self._window and now - self._window[0] >= 60.0:
                    self._window.popleft()
                if len(self._window) < self._max:
                    self._window.append(now)
                    return
                # Budget exhausted — compute wait until oldest ages out
                wait = 60.0 - (now - self._window[0]) + 0.05
            time.sleep(wait)


_LIMITER: _RateLimiter | None = None  # initialised in run_eval()


# ──────────────────────────────────────────────────────────────────────────────
# Gemini client
# ──────────────────────────────────────────────────────────────────────────────


def _gemini_request(api_key: str, prompt: str, model: str = DEFAULT_MODEL,
                    timeout: int = 120, max_retries: int = 6) -> str:
    """POST a prompt to Gemini and return the candidate text."""
    # Block until the rate-limiter says we can fire. Set inside run_eval.
    if _LIMITER is not None:
        _LIMITER.acquire()

    url = GEMINI_URL_TEMPLATE.format(model=model)
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.0,
            "topP": 0.95,
            # Gemini 3.1: thinkingLevel="low" minimises CoT (the 2.5-era
            # thinkingBudget=0 is rejected by 3.x with "cannot use both
            # thinking_level and legacy thinking_budget"). "low" allows
            # a few thinking tokens for ambiguous judgments without the
            # full dynamic-thinking pre-roll that crashes maxOutputTokens.
            "thinkingConfig": {"thinkingLevel": "low"},
            "maxOutputTokens": 4096,
            # Structured-output enforcement via the v1beta REST shape
            # (flat responseMimeType + responseSchema, uppercase JSON
            # types). The Gemini 3 marketing docs advertise a newer
            # responseFormat.text.schema wrapper, but as of 2026-05 the
            # v1beta endpoint still uses the 2.x flat shape — the new
            # one returns HTTP 400 "Invalid value at … text.mime_type".
            "responseMimeType": "application/json",
            "responseSchema": _VERDICT_SCHEMA,
        },
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(
                url, data=body,
                headers={
                    "Content-Type": "application/json",
                    "x-goog-api-key": api_key,
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            cands = data.get("candidates", [])
            if not cands:
                raise RuntimeError(f"no candidates in response: {data}")
            parts = cands[0].get("content", {}).get("parts", [])
            if not parts:
                raise RuntimeError(f"no parts in candidate: {cands[0]}")
            return parts[0].get("text", "")
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="ignore")
            if e.code == 429 and attempt < max_retries - 1:
                wait = 2 ** attempt * 5
                print(f"  [rate-limit] sleeping {wait}s before retry", file=sys.stderr)
                time.sleep(wait)
                continue
            raise RuntimeError(f"HTTP {e.code}: {err_body[:200]}") from e
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"network error: {e}") from e
    raise RuntimeError("max retries exhausted")


_VERDICT_KEYS = ("name_ok", "type_ok", "role_ok", "overall", "reasoning")


def _parse_verdict(text: str) -> dict[str, Any]:
    """Best-effort parse of Gemini's JSON verdict."""
    text = text.strip()
    # Strip markdown fences if present
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            raise ValueError("not a JSON object")
        return {k: parsed.get(k, "") for k in _VERDICT_KEYS}
    except (json.JSONDecodeError, ValueError) as e:
        # Try to salvage by finding first JSON-looking block
        match = re.search(r"\{[^{}]*\}", text, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group(0))
                return {k: parsed.get(k, "") for k in _VERDICT_KEYS}
            except json.JSONDecodeError:
                pass
        return {"name_ok": "no", "type_ok": "no", "role_ok": "n/a",
                "overall": "fail",
                "reasoning": f"PARSE_ERROR: {e}: {text[:200]}"}


# ──────────────────────────────────────────────────────────────────────────────
# Cache
# ──────────────────────────────────────────────────────────────────────────────


def _cache_key(prompt: str, model: str) -> str:
    return hashlib.sha256(f"{model}\n{prompt}".encode("utf-8")).hexdigest()


def _load_cache() -> dict[str, dict[str, Any]]:
    cache: dict[str, dict[str, Any]] = {}
    if not CACHE_PATH.exists():
        return cache
    with CACHE_PATH.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                cache[rec["key"]] = rec["verdict"]
            except (json.JSONDecodeError, KeyError):
                continue
    return cache


def _cache_append(key: str, verdict: dict[str, Any]) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CACHE_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"key": key, "verdict": verdict},
                            ensure_ascii=False) + "\n")


# ──────────────────────────────────────────────────────────────────────────────
# Driver
# ──────────────────────────────────────────────────────────────────────────────


def judge_candidate(cand: Candidate, api_key: str, cache: dict[str, dict],
                    model: str) -> Judgment:
    prompt = build_prompt(cand)
    key = _cache_key(prompt, model)
    if key in cache:
        v = cache[key]
    else:
        try:
            text = _gemini_request(api_key, prompt, model=model)
            v = _parse_verdict(text)
            _cache_append(key, v)
            cache[key] = v
        except Exception as e:  # noqa: BLE001
            return Judgment(
                record_id=cand.record_id, model=cand.model,
                payload=cand.payload, confidence=cand.confidence,
                error=str(e),
            )
    return Judgment(
        record_id=cand.record_id, model=cand.model,
        payload=cand.payload, confidence=cand.confidence,
        name_ok=v.get("name_ok", "no"),
        type_ok=v.get("type_ok", "no"),
        role_ok=v.get("role_ok", "n/a"),
        overall=v.get("overall", "fail"),
        reasoning=v.get("reasoning", ""),
    )


def run_eval(args: argparse.Namespace) -> tuple[Path, Path, Path]:
    ner_path = Path(args.ner_results)
    marc_path = Path(args.marc_extract)
    ner_records = json.loads(ner_path.read_text(encoding="utf-8"))
    marc_records = json.loads(marc_path.read_text(encoding="utf-8"))

    candidates = build_candidates(ner_records, marc_records, args.confidence_threshold)
    print(f"Found {len(candidates)} candidates above confidence ≥ {args.confidence_threshold}")
    by_model = Counter(c.model for c in candidates)
    for m, n in sorted(by_model.items()):
        print(f"  {m:20s}  {n:>4d}")

    if args.dry_run:
        print("--dry-run: stopping before Gemini calls.")
        sys.exit(0)

    api_key = os.environ.get("GEMINI_API_KEY") or args.api_key
    if not api_key:
        api_key = getpass.getpass("Gemini API key (hidden, will not be stored): ")
    if not api_key:
        print("ERROR: no Gemini API key provided.", file=sys.stderr)
        sys.exit(1)

    cache = _load_cache()
    misses = sum(1 for c in candidates
                 if _cache_key(build_prompt(c), args.model) not in cache)
    print(f"Cache: {len(cache)} prior verdicts loaded; {misses} candidates will call Gemini.")

    # Initialise the global rate limiter — hard cap on RPM, threading-safe.
    global _LIMITER  # noqa: PLW0603
    _LIMITER = _RateLimiter(args.rpm)
    if misses > 0:
        eta = (misses / max(1, args.rpm)) * 60
        print(f"Rate limit: {args.rpm} RPM with {args.parallel} parallel workers — "
              f"min runtime ≈ {eta:.0f}s for {misses} cache misses.")

    judgments: list[Judgment] = []
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.parallel) as pool:
        futures = {
            pool.submit(judge_candidate, c, api_key, cache, args.model): c
            for c in candidates
        }
        for i, fut in enumerate(as_completed(futures), 1):
            j = fut.result()
            judgments.append(j)
            if i % 25 == 0 or i == len(futures):
                elapsed = time.time() - t0
                print(f"  judged {i}/{len(futures)} ({elapsed:.0f}s elapsed)")
    print(f"All {len(judgments)} judgments done in {time.time()-t0:.0f}s.")

    return _write_outputs(judgments, args)


def _write_outputs(judgments: list[Judgment],
                   args: argparse.Namespace) -> tuple[Path, Path, Path]:
    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    jsonl_path = EVAL_DIR / f"results_{ts}.jsonl"
    with jsonl_path.open("w", encoding="utf-8") as f:
        for j in judgments:
            f.write(json.dumps(j.to_dict(), ensure_ascii=False) + "\n")

    # ── per-model + per-type aggregate ────────────────────────────────────
    metrics: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: {
        "total": 0, "full": 0, "partial": 0, "fail": 0,
        "name_yes": 0, "type_yes": 0, "role_yes": 0,
    })
    for j in judgments:
        sub_type = j.payload.get("type") or j.payload.get("role") or j.payload.get("label") or ""
        key = (j.model, sub_type)
        m = metrics[key]
        m["total"] += 1
        m[j.overall] = m.get(j.overall, 0) + 1
        if j.name_ok == "yes":
            m["name_yes"] += 1
        if j.type_ok == "yes":
            m["type_yes"] += 1
        if j.role_ok == "yes":
            m["role_yes"] += 1

    csv_path = EVAL_DIR / f"summary_{ts}.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["model", "sub_type", "total", "full", "partial", "fail",
                    "name_yes", "type_yes", "role_yes",
                    "precision_full", "precision_full_or_partial"])
        for (model, sub_type), m in sorted(metrics.items()):
            full = m["full"]
            partial = m["partial"]
            total = m["total"] or 1
            w.writerow([
                model, sub_type, m["total"], full, partial, m["fail"],
                m["name_yes"], m["type_yes"], m["role_yes"],
                round(full / total, 4),
                round((full + partial) / total, 4),
            ])

    md_path = EVAL_DIR / f"report_{ts}.md"
    md = ["# MHM Pipeline — Model Evaluation Report",
          f"\nGenerated: {ts}",
          f"\nGemini model: `{args.model}`",
          f"\nConfidence threshold: `{args.confidence_threshold}`",
          f"\nTotal judgments: {len(judgments)}",
          "\n## Per-model summary\n",
          "| Model | Sub-type | Total | Full | Partial | Fail | Precision (full) | Precision (full+partial) |",
          "|---|---|---:|---:|---:|---:|---:|---:|"]
    for (model, sub_type), m in sorted(metrics.items()):
        total = m["total"] or 1
        md.append(
            f"| {model} | {sub_type or '—'} | {m['total']} | {m['full']} | "
            f"{m['partial']} | {m['fail']} | "
            f"{m['full']/total:.1%} | {(m['full']+m['partial'])/total:.1%} |"
        )
    # Sample fails per model
    fails_by_model = defaultdict(list)
    for j in judgments:
        if j.overall == "fail":
            fails_by_model[j.model].append(j)
    md.append("\n## Sample failures (up to 5 per model)\n")
    for model, fails in sorted(fails_by_model.items()):
        md.append(f"\n### {model}\n")
        for j in fails[:5]:
            md.append(f"- **{j.record_id}** — `{json.dumps(j.payload, ensure_ascii=False)[:120]}` — {j.reasoning}")
    md_path.write_text("\n".join(md), encoding="utf-8")

    return jsonl_path, csv_path, md_path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--ner-results", default=str(DEFAULT_NER_RESULTS))
    p.add_argument("--marc-extract", default=str(DEFAULT_MARC_EXTRACT))
    p.add_argument("--confidence-threshold", type=float, default=DEFAULT_THRESHOLD)
    p.add_argument("--parallel", type=int, default=DEFAULT_PARALLEL)
    p.add_argument("--rpm", type=int, default=DEFAULT_RPM,
                   help=f"Global cap on Gemini requests-per-minute (default: {DEFAULT_RPM}; "
                        "25 is safe on free-tier 3.1 Pro Preview, 60 maxes it).")
    p.add_argument("--model", default=DEFAULT_MODEL,
                   help=f"Gemini model id (default: {DEFAULT_MODEL})")
    p.add_argument("--api-key", default=os.environ.get("GEMINI_API_KEY", ""),
                   help="Gemini API key; falls back to GEMINI_API_KEY env or getpass")
    p.add_argument("--dry-run", action="store_true",
                   help="List candidates without sending to Gemini")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    jsonl, csv_p, md = run_eval(args)
    print(f"\nWrote:")
    print(f"  {jsonl}")
    print(f"  {csv_p}")
    print(f"  {md}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
