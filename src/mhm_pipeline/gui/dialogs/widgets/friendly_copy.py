"""Plain-English label mappings + small text helpers for the AI dialog.

The eval-agent's machine-readable identifiers (``evaluator_id``,
``judge_id``, raw verdict ``overall`` strings, ``[STEP]`` log lines,
section headings inside ``report.md``) are unfit for non-engineer
audiences. This module is the single place the dialog reaches into
when it needs to render any of them.

Adding a new evaluator? Add a row to ``_FRIENDLY_EVALUATORS``. The
dialog's tables / cards / report markdown will all pick it up.
"""

from __future__ import annotations

import re

# ── Friendly label maps ─────────────────────────────────────────────

_FRIENDLY_EVALUATORS: dict[str, str] = {
    "person_ner":       "Person AI",
    "provenance_ner":   "Owner AI",
    "contents_ner":     "Contents AI",
    "genre_classifier": "Genre AI",
    "place_ner":        "Place AI",
}

_FRIENDLY_MODELS: dict[str, str] = {
    "gemini-2.0-flash-exp":  "Gemini 2 Flash",
    "gemini-2.0-flash":      "Gemini 2 Flash",
    "gemini-2.5-flash":      "Gemini 2.5 Flash",
    "gemini-2.5-pro":        "Gemini 2.5 Pro",
    "gemini-3-flash":        "Gemini 3 (free tier)",
    "gemini-3-pro":          "Gemini 3 Pro",
}

_FRIENDLY_VERDICT_STATUSES: dict[str, str] = {
    "full":    "Looks right",
    "yes":     "Looks right",
    "ok":      "Looks right",
    "partial": "Partly right",
    "fail":    "Got it wrong",
    "no":      "Got it wrong",
    "abstain": "Couldn't tell",
    "unsure":  "Couldn't tell",
    "unknown": "Couldn't tell",
    "n/a":     "Not checked",
    "error":   "Error",
}

_FRIENDLY_INPUT_KEYS: dict[str, str] = {
    "_control_number":  "Manuscript ID",
    "control_number":   "Manuscript ID",
    "entities":         "Predictions",
    "marc_fields":      "MARC source",
    "marc_record":      "MARC source",
    "ml_genres":        "Genre predictions",
    "text":             "Full text the AI looked at",
    "title":            "Title",
    "shelfmark":        "Shelfmark",
    "evaluator_id":     "AI checker",
    "record_id":        "Manuscript",
    "judge_id":         "AI we asked",
    "candidate":        "What it looked at",
    "verdict":          "What the AI thought",
    "sub_type":         "Entity kind",
    "cache_key":        "Reuse key",
    "judged_at":        "When checked",
    "schema_version":   "Result format version",
    "input_tokens":     "Words sent to the AI",
    "output_tokens":    "Words from the AI",
}

# ``[STEP]`` log lines arrive in shapes like:
#   "[STEP] Judging person_ner 47/143…"
#   "[STEP] Loading models"
# These regex-rewrites turn them into something a curator can read.
_LOG_REWRITES: list[tuple[re.Pattern[str], str]] = [
    # "Judging <evaluator> N/M" → "Checking <friendly> N of M"
    (
        re.compile(r"^\s*Judging\s+(?P<ev>[\w\-]+)\s+(?P<n>\d+)\s*/\s*(?P<m>\d+)"),
        "Checking {ev_friendly} {n} of {m}",
    ),
    # "Judging N/M" with no evaluator prefix
    (
        re.compile(r"^\s*Judging\s+(?P<n>\d+)\s*/\s*(?P<m>\d+)"),
        "Checking prediction {n} of {m}",
    ),
    (re.compile(r"^\s*Loading\s+models?\b.*", re.I),       "Getting the AI ready"),
    (re.compile(r"^\s*Extracting\s+candidates\b.*", re.I), "Listing predictions to check"),
    (re.compile(r"^\s*Writing\s+report\b.*", re.I),        "Writing the report"),
    (re.compile(r"^\s*Self[-_ ]verify\b.*", re.I),         "Double-checking a sample"),
]


# ── Public helpers ──────────────────────────────────────────────────


def humanise_evaluator(raw: str) -> str:
    """Return the friendly label for an evaluator id.

    Unknown ids fall through to a Title-Cased version of the raw id
    with underscores turned into spaces — never the raw snake_case.
    """
    if not raw:
        return "AI checker"
    friendly = _FRIENDLY_EVALUATORS.get(raw)
    if friendly is not None:
        return friendly
    return raw.replace("_", " ").title()


def humanise_model(raw: str) -> str:
    """Return the friendly label for an AI model identifier."""
    if not raw:
        return "Google's AI"
    friendly = _FRIENDLY_MODELS.get(raw)
    if friendly is not None:
        return friendly
    # Unknown but recognisable family → render with the version visible
    return raw


def humanise_verdict(raw: str) -> str:
    """Plain-English label for a raw verdict status."""
    if not raw:
        return "Couldn't tell"
    return _FRIENDLY_VERDICT_STATUSES.get(raw.strip().lower(), raw)


def humanise_log_line(raw: str) -> str:
    """Rewrite an eval-agent ``[STEP]`` line into friendly prose.

    The function is forgiving: a raw line we don't recognise is
    returned unchanged so engineers debugging through the **Advanced
    details** disclosure see the full output.
    """
    if not raw:
        return ""

    line = raw.strip()
    # Strip a leading "[STEP]" marker if present so the rewrites match.
    if line.startswith("[STEP]"):
        line = line[len("[STEP]"):].strip()

    for pattern, template in _LOG_REWRITES:
        match = pattern.match(line)
        if match is None:
            continue
        groups = match.groupdict()
        if "ev" in groups and groups["ev"]:
            groups["ev_friendly"] = humanise_evaluator(groups["ev"])
        try:
            return template.format(**groups)
        except KeyError:
            # Template referenced a group not present on this match —
            # fall through to the next pattern.
            continue
    return line


def humanise_report_md(raw: str) -> str:
    """Rewrite an eval-agent report's section headers into friendly prose."""
    if not raw:
        return ""

    # Rewrite engineer-y section headers. Operate on a line-by-line
    # basis so we can pattern-match on the leading "## " marker.
    out_lines: list[str] = []
    for line in raw.splitlines():
        stripped = line.lstrip()
        if not stripped.startswith("#"):
            out_lines.append(line)
            continue

        # "## Sample failures (provenance_ner, sub_type=AUTHOR)"
        m = re.match(
            r"^(#+\s*)Sample failures\s*\(\s*(?P<ev>[\w\-]+)"
            r"(?:\s*,\s*sub_type\s*=\s*(?P<sub>[^)]+))?\s*\)",
            stripped,
        )
        if m is not None:
            prefix = m.group(1)
            ev_friendly = humanise_evaluator(m.group("ev"))
            sub = m.group("sub")
            heading = f"A few examples the AI disagreed with — {ev_friendly}"
            if sub:
                heading = f"{heading}, {sub.lower()}"
            out_lines.append(f"{prefix}{heading}")
            continue

        # Generic "## Summary" / "## Per evaluator" → friendlier prose
        generic = re.match(r"^(#+\s*)(Summary|Per[- _]evaluator).*", stripped, re.I)
        if generic is not None:
            out_lines.append(f"{generic.group(1)}Overall results")
            continue

        out_lines.append(line)

    return "\n".join(out_lines)


def compose_headline(summary_rows: list[dict]) -> str:
    """Render a 1-sentence plain-English headline from ``summary.csv`` rows.

    Each row is expected to carry the columns the eval-agent emits:
    ``evaluator_id``, ``candidates_total``, ``full``, ``partial``,
    ``fail``, ``errors`` (some of which may be strings — they are
    coerced before formatting).
    """
    if not summary_rows:
        return "The AI didn't review any predictions in this run."

    # Cap at the three biggest evaluators to keep the sentence readable.
    rows: list[tuple[str, int, int, int]] = []
    for row in summary_rows:
        ev_id = (row.get("evaluator_id") or row.get("evaluator") or "").strip()
        if not ev_id:
            continue
        try:
            total = int(row.get("candidates_total") or row.get("total") or 0)
        except (TypeError, ValueError):
            total = 0
        if total == 0:
            continue
        try:
            full = int(row.get("full") or 0)
        except (TypeError, ValueError):
            full = 0
        try:
            fail = int(row.get("fail") or 0)
        except (TypeError, ValueError):
            fail = 0
        rows.append((ev_id, total, full, fail))

    if not rows:
        return "The AI didn't have any predictions to review."

    rows.sort(key=lambda r: r[1], reverse=True)
    rows = rows[:3]

    fragments: list[str] = []
    flagged_total = 0
    for ev_id, total, full, fail in rows:
        pct = round((full / total) * 100) if total else 0
        fragments.append(f"{pct}% of the {humanise_evaluator(ev_id)}'s calls")
        flagged_total += fail

    if len(fragments) == 1:
        agree_clause = f"The AI agreed with {fragments[0]}."
    elif len(fragments) == 2:
        agree_clause = f"The AI agreed with {fragments[0]} and {fragments[1]}."
    else:
        agree_clause = (
            f"The AI agreed with {fragments[0]}, {fragments[1]}, "
            f"and {fragments[2]}."
        )

    if flagged_total <= 0:
        flagged_clause = " It didn't flag anything as wrong."
    elif flagged_total == 1:
        flagged_clause = " It flagged 1 prediction as wrong."
    else:
        flagged_clause = f" It flagged {flagged_total} predictions as wrong."

    return agree_clause + flagged_clause


__all__ = [
    "compose_headline",
    "humanise_evaluator",
    "humanise_log_line",
    "humanise_model",
    "humanise_report_md",
    "humanise_verdict",
    "_FRIENDLY_EVALUATORS",
    "_FRIENDLY_INPUT_KEYS",
    "_FRIENDLY_MODELS",
    "_FRIENDLY_VERDICT_STATUSES",
]
