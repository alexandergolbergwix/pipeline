"""Authority-match editor — Authority Resolution review surface.

Mirrors :mod:`extraction_editor` for authority-match results. A row is
one ``(entity, match)`` tuple drawn from three shapes in
``authority_enriched.json``:

* ``marc_authority_matches[*]`` — persons from MARC 100/700/710 etc.
* ``entities[*]`` where the NER entity was enriched with an authority ID
* ``kima_places`` — place-name → Wikidata URI matches

The user approves each match; on save, unapproved rows are dropped before
the file is read by Stage 3 (``RdfBuildWorker``). Stage 3 already tolerates
missing authority links — each entity falls back to a local item — so
dropping rows is safe.

Columns: Record · Entity · Match · Source · Type · Conf. · Approved · ✎↗
"""

from __future__ import annotations

import copy
import json
import logging
import re
from pathlib import Path
from typing import Any

# Pattern for extracting a Wikidata QID from a Wikidata entity URI of the
# shape ``https://www.wikidata.org/entity/Q123`` (KIMA values often arrive
# as such URIs). Used by :func:`flatten_authority_records` to surface the
# QID in its dedicated column.
_WIKIDATA_QID_RE = re.compile(r"/entity/(Q\d+)/?$")

# Stage-3 hardening (2026-05-02) emits ``confidence`` as a tri-level
# string ("high"/"medium"/"low") instead of a 0.0–1.0 float. The widget
# stores everything as a float for sorting/colour coding — this coercer
# bridges both schemas without breaking older artefacts.
_CONF_BUCKET_TO_FLOAT = {"high": 0.95, "medium": 0.6, "low": 0.3}


def _coerce_confidence(value: object) -> float:
    """Return a 0.0–1.0 float for any of: float, int, bool, level-string."""
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        s = value.strip().lower()
        if s in _CONF_BUCKET_TO_FLOAT:
            return _CONF_BUCKET_TO_FLOAT[s]
        try:
            return float(s)
        except ValueError:
            return 0.0
    return 0.0

from PyQt6.QtCore import (
    QAbstractItemModel,
    QAbstractTableModel,
    QModelIndex,
    QSortFilterProxyModel,
    Qt,
    pyqtSignal,
)
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QStyledItemDelegate,
    QTableView,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

# We reuse the rule primitives + the multi-select combo from the NER editor
# so the auto-approve flow stays consistent across stages.
from mhm_pipeline.gui.widgets.extraction_editor import (
    _CheckableMultiCombo,
    evaluate_rule,
    evaluate_rules,
)

logger = logging.getLogger(__name__)

VALID_SOURCES: list[str] = [
    "MARC 100", "MARC 110", "MARC 111",
    "MARC 700", "MARC 710", "MARC 711",
    "NER Author",
    "NER Owner", "NER Date", "NER Collection",
    "NER Work", "NER Folio", "NER Work Author",
    "KIMA Place",
]

VALID_MATCH_TYPES: list[str] = [
    "person",
    "place",
    "work",
    "work_author",
    "folio",
    "owner",
    "collection",
    "date",
]

VALID_CONF_BANDS: list[str] = ["high", "medium", "low", "no_match"]


def _conf_band(conf: float | None) -> str:
    if conf is None or conf <= 0:
        return "no_match"
    if conf >= 0.90:
        return "high"
    if conf >= 0.70:
        return "medium"
    return "low"


# ────────────────────────────────────────────────────────────────────────────
# Normalisation — flatten authority_enriched.json into flat match rows
# ────────────────────────────────────────────────────────────────────────────


def _origin_label(
    *,
    kind: str,
    marc_field: str | None,
    entity_type: str | None,
    ner_source: str | None,
) -> str:
    """Human-readable origin label for the auth-match table's Source column.

    Maps each row's origin (MARC catalog field or NER pipeline + type) to
    one of the labels in :data:`VALID_SOURCES`.
    """
    if kind == "kima":
        return "KIMA Place"
    if kind == "marc":
        # The worker emits ``field`` collapsed as "100/110/111" (main
        # entries) or "700/710/711" (added entries). Combine that with
        # the entry's type to disambiguate to a specific MARC field.
        prefix = "MARC "
        is_main = "100" in (marc_field or "")
        et = (entity_type or "").lower()
        if et == "organization":
            return prefix + ("110" if is_main else "710")
        if et == "meeting":
            return prefix + ("111" if is_main else "711")
        return prefix + ("100" if is_main else "700")
    if kind == "ner":
        if ner_source == "person_ner":
            return "NER Author"
        if ner_source == "provenance_ner":
            t = (entity_type or "").upper()
            if t == "OWNER":
                return "NER Owner"
            if t == "DATE":
                return "NER Date"
            if t == "COLLECTION":
                return "NER Collection"
        if ner_source == "contents_ner":
            t = (entity_type or "").upper()
            if t == "WORK":
                return "NER Work"
            if t == "FOLIO":
                return "NER Folio"
            if t == "WORK_AUTHOR":
                return "NER Work Author"
        return "NER Author" if ner_source == "person_ner" else "unknown"
    return "unknown"


# Map an NER entity's ``type`` field onto a row ``match_type`` value.
_NER_TYPE_TO_MATCH_TYPE: dict[str, str] = {
    "OWNER": "owner",
    "DATE": "date",
    "COLLECTION": "collection",
    "WORK": "work",
    "WORK_AUTHOR": "work_author",
    "FOLIO": "folio",
}


def flatten_authority_records(records: list[dict]) -> list[dict]:
    """Flatten the three-shape authority JSON into flat match-rows.

    Each returned dict has the shape consumed by :class:`AuthorityMatchModel`:

    .. code-block::

        {
          "_control_number": "...",
          "_origin_kind": "marc" | "entity" | "kima",
          "_origin_index": int,   # position in the origin list
          "_auth_kind": "mazal" | "viaf" | "kima" | "marc_field"
                          | "ner_mazal" | "ner_viaf" | "ner_unmatched",
          "entity_text": str,
          "match_type": str,      # person / place / work / owner / date / …
          "role": str,
          "matched_name": str,
          "source": str,          # human-readable origin label (see VALID_SOURCES)
          "matched_id": str,
          "wikidata_qid": str,    # Wikidata QID when known (KIMA URI / harvested)
          "confidence": float,
          "dates": str,
          "gnd_id": str, "lc_id": str, "isni": str, "bnf_id": str,
          "field_origin": str,
          "approved": bool,
        }
    """
    out: list[dict] = []
    for record in records:
        cn = str(record.get("_control_number", ""))
        # Manuscript catalogued year (Stage 0 ``record["dates"]["year"]``).
        # Surfaced onto every row so the Dates column can render the
        # MS-vs-candidate comparison without re-reading the parent record.
        _dates_field = record.get("dates")
        _ms_year_raw = _dates_field.get("year") if isinstance(_dates_field, dict) else None
        ms_year = _ms_year_raw if isinstance(_ms_year_raw, int) else None

        # 1. MARC authority matches (persons from MARC fields)
        for i, m in enumerate(record.get("marc_authority_matches") or []):
            viaf = m.get("viaf_uri") or ""
            mazal = m.get("mazal_id") or ""
            auth_kind = "mazal" if mazal else ("viaf" if viaf else "marc_field")
            # When neither Mazal nor VIAF resolved, the match was not
            # found — show "(no match found)" instead of echoing the
            # entity name (which previously made the row look like a
            # successful self-match).
            if mazal or viaf:
                matched_name = str(m.get("preferred_name_lat") or m.get("name") or "")
            else:
                matched_name = "(no match found)"
            source = _origin_label(
                kind="marc",
                marc_field=str(m.get("field") or ""),
                entity_type=str(m.get("type") or m.get("entity_kind") or ""),
                ner_source=None,
            )
            out.append({
                "_control_number": cn,
                "_origin_kind": "marc",
                "_origin_index": i,
                "_auth_kind": auth_kind,
                "entity_text": str(m.get("name") or ""),
                "match_type": "person",
                "role": str(m.get("role") or ""),
                "matched_name": matched_name,
                "source": source,
                "matched_id": str(mazal or viaf or ""),
                "wikidata_qid": str(m.get("wikidata_qid") or ""),
                "confidence": _coerce_confidence(m.get("confidence")),
                "_confidence_bucket": str(m.get("confidence") or ""),
                "dates": str(m.get("dates") or ""),
                "gnd_id": str(m.get("gnd_id") or ""),
                "lc_id": str(m.get("lc_id") or ""),
                "isni": str(m.get("isni") or ""),
                "bnf_id": str(m.get("bnf_id") or ""),
                "field_origin": str(m.get("field") or ""),
                "approved": bool(m.get("approved", False)),
                # Tooltip breakdown signals (Stage 3 ``evaluate_match`` verdict).
                "_guard_flags": list(m.get("guard_flags") or []),
                "_rejection_reason": str(m.get("rejection_reason") or ""),
                "_sources": list(m.get("sources") or []),
                "_source_count": int(m.get("source_count") or 0),
                "_preferred_name_lat": str(m.get("preferred_name_lat") or ""),
                "_birth_year": m.get("birth_year"),
                "_death_year": m.get("death_year"),
                "_ms_year": ms_year,
                "_entity_kind": str(m.get("entity_kind") or ""),
            })

        # 2. NER entities — emit one row per entity, matched or not.
        for i, e in enumerate(record.get("entities") or []):
            viaf = e.get("viaf_uri") or ""
            mazal = e.get("mazal_id") or ""
            ner_source = e.get("source")
            entity_type = e.get("type")
            source_label = _origin_label(
                kind="ner",
                marc_field=None,
                entity_type=entity_type,
                ner_source=ner_source,
            )
            if mazal:
                auth_kind = "ner_mazal"
            elif viaf:
                auth_kind = "ner_viaf"
            else:
                auth_kind = "ner_unmatched"

            # match_type derives from the entity's NER type (with a
            # person_ner default of "person" when no type field is set).
            t_norm = (entity_type or "").upper()
            if t_norm in _NER_TYPE_TO_MATCH_TYPE:
                match_type = _NER_TYPE_TO_MATCH_TYPE[t_norm]
            elif entity_type:
                match_type = str(entity_type).lower()
            else:
                match_type = "person"

            if mazal or viaf:
                matched_name = str(e.get("preferred_name_lat") or "")
            else:
                matched_name = "(no match found)"

            confidence_value = e.get("model_confidence")
            if confidence_value is None:
                confidence_value = e.get("confidence")

            out.append({
                "_control_number": cn,
                "_origin_kind": "entity",
                "_origin_index": i,
                "_auth_kind": auth_kind,
                "entity_text": str(e.get("person") or e.get("text") or ""),
                "match_type": match_type,
                "role": str(e.get("role") or ""),
                "matched_name": matched_name,
                "source": source_label,
                "matched_id": str(mazal or viaf or ""),
                "wikidata_qid": str(e.get("wikidata_qid") or ""),
                "confidence": _coerce_confidence(confidence_value),
                "_confidence_bucket": "",
                "dates": "",
                "gnd_id": "", "lc_id": "", "isni": "", "bnf_id": "",
                "field_origin": "ner",
                "approved": bool(e.get("authority_approved", False)),
                # Tooltip breakdown signals — NER entities carry the raw
                # model and keyword-classifier scores rather than the
                # tri-level guard verdict (no Stage 3 verdict yet).
                "_guard_flags": list(e.get("guard_flags") or []),
                "_rejection_reason": str(e.get("rejection_reason") or ""),
                "_sources": [],
                "_source_count": (1 if (mazal or viaf) else 0),
                "_preferred_name_lat": str(e.get("preferred_name_lat") or ""),
                "_birth_year": None,
                "_death_year": None,
                "_ms_year": ms_year,
                "_entity_kind": str(entity_type or ""),
                "_ner_keyword_conf": _coerce_confidence(e.get("confidence")),
                "_ner_model_conf": _coerce_confidence(e.get("model_confidence")),
                "_ner_source": str(ner_source or ""),
            })

        # 3. KIMA places (name → Wikidata URI)
        kima = record.get("kima_places") or {}
        if isinstance(kima, dict):
            for i, (name, uri) in enumerate(kima.items()):
                # KIMA values are often Wikidata entity URIs of the form
                # ``https://www.wikidata.org/entity/Q1218``. Extract the
                # QID so it can be surfaced in its own column without
                # forcing the reviewer to parse the URI by eye.
                qid_match = _WIKIDATA_QID_RE.search(str(uri))
                qid = qid_match.group(1) if qid_match else ""
                out.append({
                    "_control_number": cn,
                    "_origin_kind": "kima",
                    "_origin_index": i,
                    "_auth_kind": "kima",
                    "entity_text": str(name),
                    "match_type": "place",
                    "role": "",
                    "matched_name": "",
                    "source": "KIMA Place",
                    "matched_id": str(uri),
                    "wikidata_qid": qid,
                    "confidence": 1.0,          # KIMA is a direct-index lookup
                    "_confidence_bucket": "",
                    "dates": "",
                    "gnd_id": "", "lc_id": "", "isni": "", "bnf_id": "",
                    "field_origin": "marc_place",
                    "approved": False,
                    "_guard_flags": [],
                    "_rejection_reason": "",
                    "_sources": ["kima"],
                    "_source_count": 1,
                    "_preferred_name_lat": "",
                    "_birth_year": None,
                    "_death_year": None,
                    "_ms_year": ms_year,
                    "_entity_kind": "place",
                })
    return out


def unflatten_rows_into_records(
    rows: list[dict], original_records: list[dict],
) -> list[dict]:
    """Inverse of :func:`flatten_authority_records`.

    Takes the possibly-edited flat rows and merges them back into the
    original record skeletons. Rows with ``approved=False`` are DROPPED
    from the corresponding ``marc_authority_matches`` / ``entities`` /
    ``kima_places`` collections. Stage 3 tolerates empties, so this is
    safe downstream.
    """
    out = [copy.deepcopy(r) for r in original_records]
    by_cn: dict[str, dict] = {str(r.get("_control_number") or ""): r for r in out}

    # Reset the authority-bearing collections on each record; we'll
    # repopulate only the approved rows below.
    for r in out:
        r["marc_authority_matches"] = []
        r["kima_places"] = {}
        for e in r.get("entities") or []:
            # Clear authority IDs — will be re-populated if approved.
            e.pop("viaf_uri", None)
            e.pop("mazal_id", None)

    for row in rows:
        if not row.get("approved", False):
            continue
        cn = row.get("_control_number", "")
        rec = by_cn.get(cn)
        if rec is None:
            continue
        kind = row.get("_origin_kind")
        auth_kind = str(row.get("_auth_kind") or "")
        if kind == "marc":
            marc_match: dict[str, Any] = {
                "name": row.get("entity_text", ""),
                "role": row.get("role", ""),
                "field": row.get("field_origin", ""),
                "confidence": row.get("confidence", 0.0),
                "mazal_id": row.get("matched_id", "") if auth_kind == "mazal" else "",
                "viaf_uri": row.get("matched_id", "") if auth_kind == "viaf" else "",
                "preferred_name_lat": row.get("matched_name", ""),
                "dates": row.get("dates", ""),
                "gnd_id": row.get("gnd_id", ""),
                "lc_id": row.get("lc_id", ""),
                "isni": row.get("isni", ""),
                "bnf_id": row.get("bnf_id", ""),
                "approved": True,
            }
            if row.get("wikidata_qid"):
                marc_match["wikidata_qid"] = row["wikidata_qid"]
            rec["marc_authority_matches"].append(marc_match)
        elif kind == "entity":
            idx = int(row.get("_origin_index") or 0)
            entities = rec.get("entities") or []
            if 0 <= idx < len(entities):
                e = entities[idx]
                if auth_kind == "ner_mazal":
                    e["mazal_id"] = row.get("matched_id", "")
                elif auth_kind == "ner_viaf":
                    e["viaf_uri"] = row.get("matched_id", "")
                # Unmatched NER entities (auth_kind == "ner_unmatched")
                # round-trip back to ``record["entities"]`` unchanged.
                if row.get("wikidata_qid"):
                    e["wikidata_qid"] = row["wikidata_qid"]
                e["authority_approved"] = True
        elif kind == "kima":
            name = row.get("entity_text", "")
            uri = row.get("matched_id", "")
            if name and uri:
                rec["kima_places"][name] = uri
    return out


# ────────────────────────────────────────────────────────────────────────────
# Confidence-tooltip helpers
# ────────────────────────────────────────────────────────────────────────────
#
# Hover tooltip on the ``Conf.`` column explains *how* the tri-level
# verdict was computed: which authority sources matched, which
# stage3_guards fired, whether a Latin preferred name was present, and
# what the rejection_reason was when a guard hard-rejected.
#
# HTML wrapper follows the same pattern as ``extraction_editor`` — Qt
# QToolTip detects HTML content and renders it via QTextDocument,
# overriding the macOS NSTooltip native frame so the theme colours stick.


def _auth_tooltip_colours() -> tuple[str, str, str]:
    """Return ``(bg, text, subtle)`` for the active theme."""
    from mhm_pipeline.gui import theme  # noqa: PLC0415
    return theme.ui("tooltip_bg"), theme.ui("tooltip_text"), theme.ui("subtext")


def _auth_esc(value: object) -> str:
    """HTML-escape arbitrary content for tooltip bodies."""
    import html  # noqa: PLC0415
    return html.escape(str(value or ""))


# Human-readable labels for the guard flags surfaced by
# :func:`converter.authority.stage3_guards.evaluate_match`. Keys match
# the strings appended to ``guard_flags`` in ``stage3_guards.py``.
_GUARD_FLAG_LABELS: dict[str, str] = {
    "placeholder_name":      "Placeholder name (cataloguer abbreviation)",
    "date_conflict":         "Date conflict — biographical years incompatible with manuscript date",
    "short_name_homonym":    "Short-name homonym — single-token MARC name on a richly-disambiguated cluster",
    "cluster_collapse":      "Cluster collapse — two distinct MARC names share one VIAF cluster",
    "over_merge_detected":   "Over-merge — Mazal pair-collision on the same VIAF cluster",
    "wikidata_disagrees":    "Wikidata disagrees with VIAF/Mazal candidate",
    "wikidata_confirms":     "Wikidata confirms VIAF/Mazal candidate",
    "has_wikidata":          "Wikidata QID resolved",
    "cross_source_conflict": "Cross-source identifier conflict — sticky-low",
}


def _confidence_band_label(confidence: float, bucket_hint: str = "") -> tuple[str, str]:
    """Map a 0–1 confidence into ``(label, colour)``.

    Stage 3 produces the tri-level via :func:`stage3_guards.score_confidence`;
    when the original string is present in ``bucket_hint`` we honour that
    directly. Otherwise fall through to the band table. Colours come from
    the central theme token registry (Rule 36) so they follow the theme.
    """
    from mhm_pipeline.gui import theme  # noqa: PLC0415
    if bucket_hint:
        s = bucket_hint.strip().lower()
        if s in {"high", "medium", "low"}:
            return {
                "high":   ("HIGH",   theme.ui("success")),
                "medium": ("MEDIUM", theme.ui("warning")),
                "low":    ("LOW",    theme.ui("error")),
            }[s]
    if confidence >= 0.8:
        return ("HIGH", theme.ui("success"))
    if confidence >= 0.45:
        return ("MEDIUM", theme.ui("warning"))
    return ("LOW", theme.ui("error"))


def _build_authority_confidence_tooltip(row: dict) -> str:
    """Structured HTML breakdown explaining the authority confidence.

    Surfaces every signal that fed the tri-level score: which authority
    sources matched (Mazal / VIAF / Wikidata / KIMA), whether a Latin
    preferred name was found, every guard flag with a human-readable
    description, and any hard ``rejection_reason``.
    """
    from mhm_pipeline.gui import theme  # noqa: PLC0415
    bg, text, subtle = _auth_tooltip_colours()
    confidence = float(row.get("confidence") or 0.0)
    bucket = str(row.get("_confidence_bucket") or "")
    label, colour = _confidence_band_label(confidence, bucket)

    parts: list[str] = []
    parts.append(
        f'<div style="background:{bg}; color:{text};'
        f' padding:8px 12px; border-radius:6px;'
        f' max-width:440px;">'
    )
    parts.append(
        f'<div style="font-weight:600; color:{colour}; margin-bottom:6px;">'
        f'Authority confidence: {_auth_esc(label)} '
        f'({confidence:.2f})'
        f'</div>'
    )

    auth_kind = str(row.get("_auth_kind") or "")
    if auth_kind == "kima":
        parts.append(
            f'<div style="color:{subtle}; line-height:1.4;">'
            f'KIMA direct-index lookup — place name resolved against the '
            f'KIMA SQLite index. Confidence is constant 1.0 because the '
            f'index is authoritative (no fuzzy match, no SPARQL).'
            f'</div>'
        )
        parts.append('</div>')
        return "".join(parts)

    # ── Source breakdown ────────────────────────────────────────────────
    sources_present: list[str] = []
    if row.get("matched_id") and row.get("_auth_kind") in {"mazal", "ner_mazal"}:
        sources_present.append("Mazal (NLI)")
    if row.get("matched_id") and row.get("_auth_kind") in {"viaf", "ner_viaf"}:
        sources_present.append("VIAF SRU")
    sources_list = row.get("_sources") or []
    for s in sources_list:
        s_l = str(s).strip().lower()
        if s_l == "mazal" and "Mazal (NLI)" not in sources_present:
            sources_present.append("Mazal (NLI)")
        elif s_l == "viaf" and "VIAF SRU" not in sources_present:
            sources_present.append("VIAF SRU")
        elif s_l == "wikidata":
            sources_present.append("Wikidata SPARQL")
    if row.get("wikidata_qid") and "Wikidata SPARQL" not in sources_present:
        sources_present.append("Wikidata SPARQL")

    source_count = int(row.get("_source_count") or 0) or len(sources_present)

    parts.append(
        f'<div style="margin-bottom:4px;"><b>Sources agreed:</b> '
        f'{source_count}'
        f'</div>'
    )

    def _row(matched: bool, name: str, value: str = "") -> str:
        glyph = "✓" if matched else "—"
        colour_inner = theme.ui("success") if matched else subtle
        suffix = f' <span style="color:{subtle};">({_auth_esc(value)})</span>' if value else ""
        return (
            f'<div style="margin-left:6px; color:{colour_inner};">'
            f'{glyph} {_auth_esc(name)}{suffix}'
            f'</div>'
        )

    parts.append(_row(
        bool(row.get("matched_id")) and "Mazal" in " ".join(sources_present),
        "Mazal (NLI)",
        str(row.get("matched_id") if row.get("_auth_kind") in {"mazal", "ner_mazal"} else ""),
    ))
    parts.append(_row(
        "VIAF" in " ".join(sources_present),
        "VIAF SRU",
        str(row.get("matched_id") if row.get("_auth_kind") in {"viaf", "ner_viaf"} else ""),
    ))
    parts.append(_row(
        bool(row.get("wikidata_qid")),
        "Wikidata SPARQL",
        str(row.get("wikidata_qid") or ""),
    ))
    parts.append(_row(
        bool(row.get("_preferred_name_lat")),
        "Latin preferred name (cross-script verification)",
        str(row.get("_preferred_name_lat") or ""),
    ))

    # ── Cluster identifiers (VIAF cross-references) ─────────────────────
    cluster_ids = [
        ("GND",  row.get("gnd_id")),
        ("LCCN", row.get("lc_id")),
        ("ISNI", row.get("isni")),
        ("BnF",  row.get("bnf_id")),
    ]
    cluster_present = [(k, v) for k, v in cluster_ids if v]
    if cluster_present:
        bits = ", ".join(f"{k}: {_auth_esc(v)}" for k, v in cluster_present)
        parts.append(
            f'<div style="color:{subtle}; margin-top:4px;">'
            f'<b>VIAF cluster IDs:</b> {bits}'
            f'</div>'
        )

    # ── Biographical years (Stage 3 guard input) ────────────────────────
    by = row.get("_birth_year")
    dy = row.get("_death_year")
    if by is not None or dy is not None:
        parts.append(
            f'<div style="color:{subtle}; margin-top:4px;">'
            f'<b>Biographical years:</b> '
            f'birth {_auth_esc(by if by is not None else "—")}, '
            f'death {_auth_esc(dy if dy is not None else "—")}'
            f'</div>'
        )

    # ── Guard flags ─────────────────────────────────────────────────────
    flags = row.get("_guard_flags") or []
    if flags:
        parts.append(
            '<div style="margin-top:6px;"><b>Guards fired:</b></div>'
        )
        for flag in flags:
            label_text = _GUARD_FLAG_LABELS.get(
                str(flag), str(flag).replace("_", " ").capitalize()
            )
            sign_colour = (
                theme.ui("success") if flag in {"wikidata_confirms", "has_wikidata"}
                else theme.ui("error")
            )
            parts.append(
                f'<div style="margin-left:6px; color:{sign_colour};">'
                f'• {_auth_esc(label_text)}'
                f'</div>'
            )

    rejection = str(row.get("_rejection_reason") or "")
    if rejection:
        parts.append(
            f'<div style="color:{theme.ui("error")}; margin-top:6px;">'
            f'<b>Rejection reason:</b> {_auth_esc(rejection)}'
            f'</div>'
        )

    parts.append(
        f'<div style="color:{subtle}; margin-top:8px; font-size:11px;">'
        f'Scoring: 2+ sources + Latin name → high. 1 source → medium. '
        f'Any guard fired → low (sticky).'
        f'</div>'
    )
    parts.append('</div>')
    return "".join(parts)


# ────────────────────────────────────────────────────────────────────────────
# Dates column — at-a-glance MS-vs-candidate comparison
# ────────────────────────────────────────────────────────────────────────────
#
# The Stage 3 date-conflict guard already vets every match (rejecting
# candidates born after the manuscript was made, etc.). The verdict
# surfaces in the row's tri-level confidence and in the Conf. tooltip,
# but a curator scanning 200 rows cannot tell at a glance *which*
# matches were date-checked or *what years* drove the decision. The
# Dates column shows both years and a status glyph.


def _format_dates_cell(row: dict) -> str:
    """Render the Dates column DisplayRole text.

    - Both MS and candidate dates present: ``"MS 1650 | 1138–1204 ✓"``
      where the glyph is ✓ (no conflict), ✗ (date-guard fired), or
      ⚠ (partial: only one candidate-side year known).
    - Only candidate dates: ``"1138–1204"``
    - Only MS year: ``"MS 1650"``
    - Neither: ``"—"``
    - KIMA / place rows (no birth/death semantics): always ``"—"``
    """
    if str(row.get("_auth_kind") or "") == "kima":
        return "—"

    ms_year = row.get("_ms_year")
    birth = row.get("_birth_year")
    death = row.get("_death_year")

    has_ms = isinstance(ms_year, int)
    has_birth = isinstance(birth, int)
    has_death = isinstance(death, int)

    if not (has_ms or has_birth or has_death):
        return "—"

    # Candidate range string.
    if has_birth and has_death:
        candidate = f"{birth}–{death}"
    elif has_birth:
        candidate = f"{birth}–?"
    elif has_death:
        candidate = f"?–{death}"
    else:
        candidate = ""

    if has_ms and candidate:
        guard_fired = "date_conflict" in (row.get("_guard_flags") or [])
        if guard_fired:
            glyph = "✗"   # ✗ conflict
        elif has_birth and has_death:
            glyph = "✓"   # ✓ both sides present + no conflict
        else:
            glyph = "⚠"   # ⚠ partial
        return f"MS {ms_year} | {candidate} {glyph}"
    if candidate:
        return candidate
    if has_ms:
        return f"MS {ms_year}"
    return "—"


def _build_authority_dates_tooltip(row: dict) -> str:
    """HTML tooltip for the Dates column — enumerates every input the
    Stage 3 date-conflict guard considered and the rule that applied."""
    from mhm_pipeline.gui import theme  # noqa: PLC0415
    bg, text, subtle = _auth_tooltip_colours()
    role = str(row.get("role") or "")
    ms_year = row.get("_ms_year")
    birth = row.get("_birth_year")
    death = row.get("_death_year")
    guard_flags = row.get("_guard_flags") or []
    auth_kind = str(row.get("_auth_kind") or "")

    parts: list[str] = []
    parts.append(
        f'<div style="background:{bg}; color:{text};'
        f' padding:8px 12px; border-radius:6px; max-width:420px;">'
    )

    if auth_kind == "kima":
        parts.append(
            f'<div style="color:{subtle}; line-height:1.4;">'
            f'KIMA place row — no birth/death dates apply.'
            f'</div>'
            f'</div>'
        )
        return "".join(parts)

    # Header: verdict
    if "date_conflict" in guard_flags:
        parts.append(
            f'<div style="color:{theme.ui("error")}; font-weight:600; margin-bottom:6px;">'
            'Date conflict fired'
            '</div>'
        )
    elif isinstance(ms_year, int) and isinstance(birth, int) and isinstance(death, int):
        parts.append(
            f'<div style="color:{theme.ui("success")}; font-weight:600; margin-bottom:6px;">'
            'No date conflict'
            '</div>'
        )
    else:
        parts.append(
            f'<div style="color:{subtle}; font-weight:600; margin-bottom:6px;">'
            'Date check incomplete (missing years)'
            '</div>'
        )

    # Inputs
    parts.append(
        f'<div><b>Manuscript year:</b> '
        f'{_auth_esc(ms_year) if isinstance(ms_year, int) else "—"}</div>'
    )
    parts.append(
        f'<div><b>Candidate birth:</b> '
        f'{_auth_esc(birth) if isinstance(birth, int) else "—"}</div>'
    )
    parts.append(
        f'<div><b>Candidate death:</b> '
        f'{_auth_esc(death) if isinstance(death, int) else "—"}</div>'
    )
    parts.append(
        f'<div style="margin-top:4px;"><b>Role:</b> {_auth_esc(role or "—")}</div>'
    )

    # Rule explanation by role.
    role_l = role.lower()
    if role_l in {"scribe", "transcriber", "copyist"}:
        rule = (
            "Physical-production role: candidate must have been alive "
            "near the manuscript date (death-side check active, "
            "80-year tolerance)."
        )
    elif role_l in {"author", "translator", "commentator", "editor"}:
        rule = (
            "Textual-authorship role: candidate must have existed before "
            "the manuscript was made (only birth-side check active; "
            "Hebrew manuscripts routinely copy authors centuries later)."
        )
    elif role_l == "subject":
        rule = (
            "Subject role: the manuscript is ABOUT this person. Only "
            "the birth-side check fires (a manuscript can be about "
            "someone who died centuries before it was made, but not "
            "about someone born after it)."
        )
    else:
        rule = (
            "Universal birth check: candidate cannot have been born "
            "more than 5 years after the manuscript date "
            "(DATE_BIRTH_BUFFER_YEARS)."
        )
    parts.append(
        f'<div style="color:{subtle}; margin-top:6px; line-height:1.4;">'
        f'{_auth_esc(rule)}</div>'
    )

    parts.append('</div>')
    return "".join(parts)


# ────────────────────────────────────────────────────────────────────────────
# Proxy / filtering
# ────────────────────────────────────────────────────────────────────────────


class AuthorityFilterProxy(QSortFilterProxyModel):
    """Proxy filtering by source / match_type / confidence-band + free search,
    plus per-column value filters (Rule 49 §E)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.source_filter: set[str] = set()
        self.type_filter: set[str] = set()
        self.band_filter: set[str] = set()
        # Per-column include-sets. Empty dict = no per-column filters
        # active. Each entry MUST be a non-empty set (the setter
        # treats an empty set as "clear this column").
        self._column_filters: dict[int, set[str]] = {}

    def set_dimension_filters(
        self,
        sources: set[str],
        types: set[str],
        bands: set[str],
    ) -> None:
        self.source_filter = set(sources)
        self.type_filter = set(types)
        self.band_filter = set(bands)
        self.invalidateFilter()

    # ── per-column filter API (Rule 49 §E) ──────────────────────────

    def set_column_filter(self, column: int, values: set[str]) -> None:
        """Replace the include-set for ``column``. An empty set CLEARS
        the filter on that column."""
        if values:
            self._column_filters[column] = set(values)
        else:
            self._column_filters.pop(column, None)
        self.invalidateFilter()

    def clear_all_column_filters(self) -> None:
        self._column_filters.clear()
        self.invalidateFilter()

    def column_filter(self, column: int) -> set[str]:
        return set(self._column_filters.get(column, set()))

    def has_any_column_filter(self) -> bool:
        return bool(self._column_filters)

    # ── filter pipeline ─────────────────────────────────────────────

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:  # noqa: N802
        m = self.sourceModel()
        if not isinstance(m, AuthorityMatchModel):
            return True
        if source_row >= len(m._rows):
            return True
        row = m._rows[source_row]
        if self.source_filter and str(row.get("source") or "") not in self.source_filter:
            return False
        if self.type_filter and str(row.get("match_type") or "") not in self.type_filter:
            return False
        if self.band_filter and _conf_band(row.get("confidence")) not in self.band_filter:
            return False
        # Per-column value filters AND with the dimension chips above.
        for col, allowed in self._column_filters.items():
            if cell_value_for_filter(m, source_row, col) not in allowed:
                return False
        return super().filterAcceptsRow(source_row, source_parent)

    # ── header decoration: append ▾ to filtered columns ─────────────

    def headerData(  # noqa: N802
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> object:
        base = super().headerData(section, orientation, role)
        if (
            role == Qt.ItemDataRole.DisplayRole
            and orientation == Qt.Orientation.Horizontal
            and section in self._column_filters
        ):
            label = "" if base is None else str(base)
            return f"{label} ▾".lstrip()
        return base


def cell_value_for_filter(
    model: AuthorityMatchModel,
    source_row: int,
    column: int,
) -> str:
    """Return the canonical string used by the popup for ``(row, col)``.

    Mirrors :meth:`AuthorityMatchModel.data` for ``DisplayRole`` so the
    popup's checkbox list and the proxy filter agree on what each
    cell's value is.
    """
    if source_row < 0 or source_row >= len(model._rows):
        return ""
    row = model._rows[source_row]
    if column == COL_RECORD:
        return str(row.get("_control_number", ""))
    if column == COL_ENTITY:
        return str(row.get("entity_text", ""))
    if column == COL_MATCH:
        mid = str(row.get("matched_id", "") or "")
        mname = str(row.get("matched_name", "") or "")
        if mname and mid:
            return f"{mname} ({mid})"
        return mname or mid or ""
    if column == COL_SOURCE:
        return str(row.get("source", "") or "")
    if column == COL_TYPE:
        return str(row.get("match_type", "") or "")
    if column == COL_CONF:
        # Filter on the confidence BAND ("high"/"medium"/"low") rather
        # than the numeric value, so a user choosing "high" matches
        # every row above the threshold without needing to click
        # individual 0.95 / 0.96 values.
        return _conf_band(row.get("confidence"))
    if column == COL_DATES:
        return _format_dates_cell(row)
    if column == COL_APPROVED:
        return "approved" if row.get("approved", False) else "pending"
    if column == COL_WIKIDATA_QID:
        return str(row.get("wikidata_qid", "") or "")
    if column == COL_ACTIONS:
        return ""
    return ""


# ────────────────────────────────────────────────────────────────────────────
# Model
# ────────────────────────────────────────────────────────────────────────────

COL_RECORD = 0
COL_ENTITY = 1
COL_MATCH = 2
COL_SOURCE = 3
COL_TYPE = 4
COL_CONF = 5
COL_DATES = 6
COL_APPROVED = 7
COL_WIKIDATA_QID = 8
COL_ACTIONS = 9


class AuthorityMatchModel(QAbstractTableModel):
    """Flat model over authority matches, supporting approval + editing."""

    HEADERS = [
        "Record", "Entity", "Match", "Source", "Type", "Conf.", "Dates",
        "Approved", "Wikidata QID", " ",
    ]

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rows: list[dict] = []
        self._original: list[dict] = []
        self._records: list[dict] = []

    def load(self, records: list[dict]) -> None:
        self.beginResetModel()
        self._records = records
        self._rows = flatten_authority_records(records)
        self._original = copy.deepcopy(self._rows)
        self.endResetModel()

    def to_records(self) -> list[dict]:
        """Return records with ALL rows unfolded (approved or not)."""
        return unflatten_rows_into_records(
            [dict(r, approved=True) for r in self._rows], self._records,
        )

    def to_approved_records(self) -> list[dict]:
        """Return records with unapproved rows dropped — fed to authority resolution."""
        return unflatten_rows_into_records(self._rows, self._records)

    def is_dirty(self) -> bool:
        return self._rows != self._original

    def revert(self) -> None:
        self.beginResetModel()
        self._rows = copy.deepcopy(self._original)
        self.endResetModel()

    def set_approved_bulk(self, source_rows: list[int], approved: bool) -> int:
        if not source_rows:
            return 0
        changed = 0
        for r in source_rows:
            if 0 <= r < len(self._rows):
                if self._rows[r].get("approved", False) != approved:
                    self._rows[r]["approved"] = approved
                    changed += 1
        if changed:
            tl = self.index(0, 0)
            br = self.index(self.rowCount() - 1, self.columnCount() - 1)
            self.dataChanged.emit(tl, br)
        return changed

    # ── QAbstractTableModel API ──────────────────────────────────────────

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return len(self.HEADERS)

    def headerData(  # noqa: N802
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> str | None:
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.HEADERS[section]
        return None

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> object:
        if not index.isValid() or index.row() >= len(self._rows):
            return None
        r = self._rows[index.row()]
        col = index.column()

        # Confidence-column tooltip — structured breakdown of every signal
        # that fed the Stage 3 verdict (sources matched, guards fired,
        # rejection reason, biographical years, etc).
        if role == Qt.ItemDataRole.ToolTipRole and col == COL_CONF:
            return _build_authority_confidence_tooltip(r)

        # Dates-column tooltip — MS-vs-candidate breakdown + role rule.
        if role == Qt.ItemDataRole.ToolTipRole and col == COL_DATES:
            return _build_authority_dates_tooltip(r)

        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            if col == COL_RECORD:
                return r["_control_number"]
            if col == COL_ENTITY:
                return r["entity_text"]
            if col == COL_MATCH:
                mid = r.get("matched_id", "")
                mname = r.get("matched_name", "")
                if mname and mid:
                    return f"{mname} ({mid})"
                return mname or mid or "—"
            if col == COL_SOURCE:
                return r["source"]
            if col == COL_TYPE:
                return r["match_type"]
            if col == COL_CONF:
                c = r.get("confidence", 0.0)
                return f"{c:.2f}" if c else ""
            if col == COL_DATES:
                return _format_dates_cell(r)
            if col == COL_WIKIDATA_QID:
                return r.get("wikidata_qid", "")

        if role == Qt.ItemDataRole.UserRole:
            if col == COL_CONF:
                return r.get("confidence", 0.0)
            if col == COL_APPROVED:
                return int(bool(r.get("approved", False)))
            return self.data(index, Qt.ItemDataRole.DisplayRole)

        if role == Qt.ItemDataRole.CheckStateRole and col == COL_APPROVED:
            return (
                Qt.CheckState.Checked if r.get("approved", False)
                else Qt.CheckState.Unchecked
            )

        if role == Qt.ItemDataRole.BackgroundRole and r.get("approved", False):
            from mhm_pipeline.gui import theme  # noqa: PLC0415
            return QColor(22, 163, 74, 28 if theme.is_dark() else 18)

        return None

    def setData(  # noqa: N802
        self,
        index: QModelIndex,
        value: object,
        role: int = Qt.ItemDataRole.EditRole,
    ) -> bool:
        if not index.isValid():
            return False
        col = index.column()
        row = index.row()
        if row >= len(self._rows):
            return False
        r = self._rows[row]
        if role == Qt.ItemDataRole.CheckStateRole and col == COL_APPROVED:
            r["approved"] = (Qt.CheckState(value) == Qt.CheckState.Checked)
            self.dataChanged.emit(index, index.siblingAtColumn(COL_ACTIONS))
            return True
        return False

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        base = super().flags(index)
        if index.column() == COL_APPROVED:
            return base | Qt.ItemFlag.ItemIsUserCheckable
        return base


# ────────────────────────────────────────────────────────────────────────────
# Popup dialogs
# ────────────────────────────────────────────────────────────────────────────


class MatchEditDialog(QDialog):
    """Edit a single authority match — entity text + matched name + external IDs."""

    def __init__(self, row: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        from mhm_pipeline.gui import theme  # noqa: PLC0415

        self.setWindowTitle(f"Edit match — {row.get('_control_number','')}")
        self.resize(560, 420)
        self._row = copy.deepcopy(row)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            theme.SPACE_LG, theme.SPACE_LG, theme.SPACE_LG, theme.SPACE_LG,
        )
        layout.setSpacing(theme.SPACE_MD)

        form = QFormLayout()
        form.setSpacing(theme.SPACE_SM)

        self._entity_edit = QLineEdit(str(row.get("entity_text") or ""))
        form.addRow("Entity text:", self._entity_edit)

        self._matched_edit = QLineEdit(str(row.get("matched_name") or ""))
        form.addRow("Preferred name:", self._matched_edit)

        self._id_edit = QLineEdit(str(row.get("matched_id") or ""))
        self._id_edit.setPlaceholderText("Primary authority ID (Mazal ID or VIAF URI)")
        form.addRow("Matched ID:", self._id_edit)

        self._source_combo = QComboBox()
        self._source_combo.addItems(VALID_SOURCES)
        cur = str(row.get("source") or "")
        if cur in VALID_SOURCES:
            self._source_combo.setCurrentText(cur)
        form.addRow("Source:", self._source_combo)

        self._gnd_edit = QLineEdit(str(row.get("gnd_id") or ""))
        form.addRow("GND ID:", self._gnd_edit)

        layout.addLayout(form)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel = QPushButton("Cancel")
        cancel.setStyleSheet(theme.ghost_button_style())
        cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel.clicked.connect(self.reject)
        save = QPushButton("Save")
        save.setStyleSheet(theme.success_btn_style())
        save.setCursor(Qt.CursorShape.PointingHandCursor)
        save.clicked.connect(self.accept)
        btn_row.addWidget(cancel)
        btn_row.addWidget(save)
        layout.addLayout(btn_row)

    def edited_row(self) -> dict:
        self._row["entity_text"] = self._entity_edit.text().strip()
        self._row["matched_name"] = self._matched_edit.text().strip()
        self._row["matched_id"] = self._id_edit.text().strip()
        self._row["source"] = self._source_combo.currentText()
        self._row["gnd_id"] = self._gnd_edit.text().strip()
        return self._row


class MatchSourceViewDialog(QDialog):
    """Show authority-record context for a single match row."""

    def __init__(self, row: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        from mhm_pipeline.gui import theme  # noqa: PLC0415

        self.setWindowTitle(f"Match context — {row.get('_control_number','')}")
        self.resize(640, 420)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            theme.SPACE_LG, theme.SPACE_LG, theme.SPACE_LG, theme.SPACE_LG,
        )
        layout.setSpacing(theme.SPACE_MD)

        summary = QLabel(
            f"<b>Entity:</b> {row.get('entity_text','')}"
            f"<br><b>Match:</b> {row.get('matched_name','')} "
            f"(<code>{row.get('matched_id','')}</code>)"
            f"<br><b>Source:</b> {row.get('source','')} · "
            f"<b>Type:</b> {row.get('match_type','')} · "
            f"<b>Conf.:</b> {row.get('confidence',0.0):.2f} "
            f"(band: {_conf_band(row.get('confidence'))})"
        )
        summary.setTextFormat(Qt.TextFormat.RichText)
        summary.setWordWrap(True)
        layout.addWidget(summary)

        details = QTextEdit()
        details.setReadOnly(True)
        lines = [
            f"Record:        {row.get('_control_number','')}",
            f"Field origin:  {row.get('field_origin','')}",
            f"Role:          {row.get('role','')}",
            f"Dates:         {row.get('dates','')}",
            f"GND:           {row.get('gnd_id','')}",
            f"LCCN:          {row.get('lc_id','')}",
            f"ISNI:          {row.get('isni','')}",
            f"BnF:           {row.get('bnf_id','')}",
        ]
        details.setPlainText("\n".join(lines))
        layout.addWidget(details, stretch=1)

        close = QPushButton("Close")
        close.setStyleSheet(theme.button_style())
        close.clicked.connect(self.accept)
        bar = QHBoxLayout()
        bar.addStretch()
        bar.addWidget(close)
        layout.addLayout(bar)


# ────────────────────────────────────────────────────────────────────────────
# Auto-approve rule builder — specialised field set for authority stage
# ────────────────────────────────────────────────────────────────────────────

_AUTH_FIELDS: list[str] = ["confidence", "source", "match_type", "confidence_band", "has_external_id"]
_AUTH_FIELD_OPTIONS: dict[str, list[str]] = {
    "source": VALID_SOURCES,
    "match_type": VALID_MATCH_TYPES,
    "confidence_band": VALID_CONF_BANDS,
    "has_external_id": ["true", "false"],
}


class _AuthRuleRow(QWidget):
    """A single authority rule row (field · op · value · remove)."""

    removed = pyqtSignal(object)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        options_for: dict[str, list[str]] | None = None,
    ) -> None:
        super().__init__(parent)
        from mhm_pipeline.gui import theme  # noqa: PLC0415

        self._options_for = options_for or {}
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(theme.SPACE_SM)

        self.field_combo = QComboBox()
        self.field_combo.addItems(_AUTH_FIELDS)
        self.field_combo.setMinimumWidth(140)
        self.field_combo.currentTextChanged.connect(self._refresh)
        h.addWidget(self.field_combo)

        self.op_combo = QComboBox()
        self.op_combo.setMinimumWidth(100)
        # Pre-populate the numeric op set so the combo is never empty on
        # first render — the default field is "confidence".
        self.op_combo.addItems([">", ">=", "=", "<=", "<", "≠"])
        self.op_combo.currentTextChanged.connect(self._refresh)
        h.addWidget(self.op_combo)

        from PyQt6.QtWidgets import QDoubleSpinBox  # noqa: PLC0415
        self.value_num = QDoubleSpinBox()
        self.value_num.setRange(0.0, 1.0)
        self.value_num.setSingleStep(0.05)
        self.value_num.setDecimals(2)
        self.value_num.setValue(0.80)
        h.addWidget(self.value_num, stretch=1)

        self.value_single = QComboBox()
        h.addWidget(self.value_single, stretch=1)

        self.value_multi = _CheckableMultiCombo([])
        h.addWidget(self.value_multi, stretch=1)

        self.value_text = QLineEdit()
        self.value_text.setPlaceholderText("value")
        h.addWidget(self.value_text, stretch=1)

        remove = QPushButton("×")
        remove.setFixedWidth(28)
        remove.setCursor(Qt.CursorShape.PointingHandCursor)
        remove.setStyleSheet(theme.ghost_button_style())
        remove.clicked.connect(lambda: self.removed.emit(self))
        h.addWidget(remove)

        self._refresh()

    def _hide_all(self) -> None:
        self.value_num.setVisible(False)
        self.value_single.setVisible(False)
        self.value_multi.setVisible(False)
        self.value_text.setVisible(False)

    def _refresh(self, *_a: object) -> None:
        field = self.field_combo.currentText()
        # Always reconcile op_combo items with the current field. This used
        # to be guarded by a sender() check — unreliable in PyQt6 when the
        # slot is invoked directly, which left op_combo blank.
        expected_ops = (
            [">", ">=", "=", "<=", "<", "≠"] if field == "confidence"
            else ["=", "≠", "in", "not in"]
        )
        if [self.op_combo.itemText(i) for i in range(self.op_combo.count())] != expected_ops:
            current_op = self.op_combo.currentText()
            self.op_combo.blockSignals(True)
            self.op_combo.clear()
            self.op_combo.addItems(expected_ops)
            if current_op:
                idx = self.op_combo.findText(current_op)
                if idx >= 0:
                    self.op_combo.setCurrentIndex(idx)
            self.op_combo.blockSignals(False)

        op = self.op_combo.currentText()
        self._hide_all()
        if field == "confidence":
            self.value_num.setVisible(True)
            return

        options = self._options_for.get(field) or _AUTH_FIELD_OPTIONS.get(field)
        if options is None:
            self.value_text.setVisible(True)
            return
        if op in ("=", "≠"):
            cur = self.value_single.currentText()
            self.value_single.blockSignals(True)
            self.value_single.clear()
            self.value_single.addItems(options)
            if cur in options:
                self.value_single.setCurrentText(cur)
            self.value_single.blockSignals(False)
            self.value_single.setVisible(True)
        elif op in ("in", "not in"):
            snap = getattr(self.value_multi, "_items_snapshot", None)
            if snap != options:
                self.value_multi.setParent(None)
                self.value_multi.deleteLater()
                self.value_multi = _CheckableMultiCombo(options)
                self.value_multi._items_snapshot = list(options)  # type: ignore[attr-defined]
                self.layout().insertWidget(4, self.value_multi, 1)
            self.value_multi.setVisible(True)
        else:
            self.value_text.setVisible(True)

    def to_rule(self) -> dict[str, Any]:
        field = self.field_combo.currentText()
        op = self.op_combo.currentText()
        if field == "confidence":
            return {"field": field, "op": op, "value": self.value_num.value()}
        if op in ("=", "≠") and self.value_single.isVisible():
            return {"field": field, "op": op, "value": self.value_single.currentText()}
        if op in ("in", "not in") and self.value_multi.isVisible():
            return {"field": field, "op": op, "value": list(self.value_multi.checked_items())}
        raw = self.value_text.text().strip()
        if op in ("in", "not in"):
            return {"field": field, "op": op,
                    "value": [s.strip() for s in raw.split(",") if s.strip()]}
        return {"field": field, "op": op, "value": raw}


def evaluate_auth_rule(row: dict, rule: dict) -> bool:
    """Evaluate one authority-rule against a flat match row."""
    field = rule["field"]
    if field == "confidence_band":
        band = _conf_band(row.get("confidence"))
        return evaluate_rule({"confidence_band": band}, rule)
    if field == "has_external_id":
        has = bool(row.get("matched_id"))
        return evaluate_rule({"has_external_id": "true" if has else "false"}, rule)
    return evaluate_rule(row, rule)


def evaluate_auth_rules(row: dict, rules: list[dict], combinator: str) -> bool:
    if not rules:
        return False
    results = [evaluate_auth_rule(row, r) for r in rules]
    return all(results) if combinator == "AND" else any(results)


class AuthorityAutoApproveDialog(QDialog):
    """Multi-condition builder tailored to authority match fields."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        options_for: dict[str, list[str]] | None = None,
    ) -> None:
        super().__init__(parent)
        from mhm_pipeline.gui import theme  # noqa: PLC0415

        self.setWindowTitle("Auto-approve authority matches")
        self.resize(720, 420)
        self._options_for = options_for or {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            theme.SPACE_LG, theme.SPACE_LG, theme.SPACE_LG, theme.SPACE_LG,
        )
        layout.setSpacing(theme.SPACE_MD)

        info = QLabel(
            "Approve every match that satisfies all (or any) of the "
            "conditions. Use ``confidence_band`` for high/medium/low/no_match, "
            "``has_external_id`` to require a Mazal/VIAF/KIMA ID."
        )
        info.setWordWrap(True)
        info.setStyleSheet(f"color: {theme.ui('subtext')};")
        layout.addWidget(info)

        comb = QHBoxLayout()
        comb.setSpacing(theme.SPACE_SM)
        comb.addWidget(QLabel("Combine with:"))
        self.combinator = QComboBox()
        self.combinator.addItems(["AND", "OR"])
        comb.addWidget(self.combinator)
        comb.addStretch()
        layout.addLayout(comb)

        self._rules_container = QWidget()
        self._rules_layout = QVBoxLayout(self._rules_container)
        self._rules_layout.setContentsMargins(0, 0, 0, 0)
        self._rules_layout.setSpacing(theme.SPACE_SM)
        self._rule_widgets: list[_AuthRuleRow] = []
        sa = QScrollArea()
        sa.setWidget(self._rules_container)
        sa.setWidgetResizable(True)
        sa.setFrameShape(QFrame.Shape.NoFrame)
        sa.setStyleSheet("QScrollArea { background: transparent; }")
        layout.addWidget(sa, stretch=1)

        bottom = QHBoxLayout()
        bottom.setSpacing(theme.SPACE_SM)
        add_rule = QPushButton("+ Add condition")
        add_rule.setStyleSheet(theme.ghost_button_style())
        add_rule.setCursor(Qt.CursorShape.PointingHandCursor)
        add_rule.clicked.connect(self._add_rule)
        bottom.addWidget(add_rule)
        bottom.addStretch()
        cancel = QPushButton("Cancel")
        cancel.setStyleSheet(theme.ghost_button_style())
        cancel.clicked.connect(self.reject)
        apply_btn = QPushButton("Apply")
        apply_btn.setStyleSheet(theme.success_btn_style())
        apply_btn.clicked.connect(self.accept)
        bottom.addWidget(cancel)
        bottom.addWidget(apply_btn)
        layout.addLayout(bottom)

        self._add_rule()

    def _add_rule(self) -> None:
        r = _AuthRuleRow(options_for=self._options_for)
        r.removed.connect(self._remove_rule)
        self._rule_widgets.append(r)
        self._rules_layout.addWidget(r)

    def _remove_rule(self, w: _AuthRuleRow) -> None:
        if w in self._rule_widgets:
            self._rule_widgets.remove(w)
            self._rules_layout.removeWidget(w)
            w.deleteLater()

    def rules(self) -> list[dict[str, Any]]:
        return [w.to_rule() for w in self._rule_widgets]

    def combinator_value(self) -> str:
        return self.combinator.currentText()


# ────────────────────────────────────────────────────────────────────────────
# Main editor widget
# ────────────────────────────────────────────────────────────────────────────


class AuthorityEditor(QWidget):
    """Review surface for authority matches — mirrors ``ExtractionEditor``."""

    entities_changed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        from mhm_pipeline.gui import theme  # noqa: PLC0415

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(theme.SPACE_SM)

        # Header
        header = QHBoxLayout()
        header.setSpacing(theme.SPACE_SM)
        self._stats = QLabel("No authority matches loaded")
        header.addWidget(self._stats)
        header.addStretch()

        def _ghost(text: str, on_click: Any) -> QPushButton:
            btn = QPushButton(text)
            btn.setStyleSheet(theme.ghost_button_style())
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(on_click)
            return btn

        header.addWidget(_ghost("⚡ Auto-approve…", self._on_auto_approve))
        header.addWidget(_ghost("Approve visible", lambda: self._set_visible(True)))
        header.addWidget(_ghost("Clear approval", lambda: self._set_visible(False)))
        header.addWidget(_ghost("Revert", self._on_revert))
        # Rule 49 §E — top-level escape hatch for per-column filters
        # (the chip-row dimension filter is independent and has its
        # own control). Greyed out when no column filter is active.
        self._clear_col_filters_btn = _ghost(
            "🗑 Clear column filters", self._on_clear_column_filters,
        )
        self._clear_col_filters_btn.setEnabled(False)
        header.addWidget(self._clear_col_filters_btn)
        self._save_btn = QPushButton("Save")
        self._save_btn.setStyleSheet(theme.success_btn_style())
        self._save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._save_btn.clicked.connect(self._on_save)
        header.addWidget(self._save_btn)
        layout.addLayout(header)

        # Search
        search = QHBoxLayout()
        search.setSpacing(theme.SPACE_SM)
        search.addWidget(QLabel("Search:"))
        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("Filter by record, entity, match, ID…")
        self._search_edit.textChanged.connect(self._on_search)
        search.addWidget(self._search_edit)
        layout.addLayout(search)

        # Table
        self._model = AuthorityMatchModel()
        self._proxy = AuthorityFilterProxy()
        self._proxy.setSourceModel(self._model)
        self._proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._proxy.setFilterKeyColumn(-1)
        self._proxy.setSortRole(Qt.ItemDataRole.UserRole)

        self._table = QTableView()
        self._table.setModel(self._proxy)
        self._table.setSortingEnabled(True)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)

        h = self._table.horizontalHeader()
        assert h is not None
        h.setSectionResizeMode(COL_RECORD, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(COL_ENTITY, QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(COL_MATCH, QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(COL_SOURCE, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(COL_TYPE, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(COL_CONF, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(COL_DATES, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(COL_APPROVED, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(COL_WIKIDATA_QID, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(COL_ACTIONS, QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(COL_ACTIONS, 78)
        # Rule 48: enable horizontal scroll-when-too-wide.
        from mhm_pipeline.gui import theme as _theme  # noqa: PLC0415
        _theme.install_table_overflow_scroll(self._table)

        # Rule 49 §E — per-column value filters via right-click header menu.
        from mhm_pipeline.gui.widgets.column_filter_popup import (  # noqa: PLC0415
            install_column_filters,
        )

        def _distinct_values_for_authority_col(col: int) -> list[str]:
            seen: set[str] = set()
            for i in range(len(self._model._rows)):
                seen.add(cell_value_for_filter(self._model, i, col))
            return sorted(seen, key=lambda s: (s != "", s.lower(), s))

        def _counts_for_authority_col(col: int) -> dict[str, int]:
            counts: dict[str, int] = {}
            for i in range(len(self._model._rows)):
                v = cell_value_for_filter(self._model, i, col)
                counts[v] = counts.get(v, 0) + 1
            return counts

        install_column_filters(
            self._table,
            self._proxy,
            distinct_values_for=_distinct_values_for_authority_col,
            counts_for=_counts_for_authority_col,
            on_filter_changed=self._update_stats,
        )

        layout.addWidget(self._table, stretch=1)

        self._output_path: Path | None = None
        # MARC records keyed by control number — injected via
        # ``set_marc_records`` so the Record-column click can open the
        # friendly MARC popup with the ORIGINAL bibliographic record
        # (the authority-enriched records are not the original MARC).
        self._marc_by_cn: dict[str, dict] = {}
        self._model.dataChanged.connect(self._update_stats)
        self._model.modelReset.connect(self._refresh_actions)
        self._model.rowsInserted.connect(self._refresh_actions)
        self._model.rowsRemoved.connect(self._refresh_actions)

        # Record-column click → friendly MARC record popup.
        self._table.clicked.connect(self._on_table_clicked)

    # ── Public API ───────────────────────────────────────────────────────

    def load_records(self, records: list[dict], output_path: Path | None = None) -> None:
        self._model.load(records)
        self._output_path = output_path
        self._refresh_actions()
        self._update_stats()

    def set_marc_records(self, records: list[dict]) -> None:
        """Index the ORIGINAL MARC records by control number.

        Feed before opening the editor so the Record-column click can
        render the full bibliographic record. Stores only references.
        """
        self._marc_by_cn = {
            str(r.get("_control_number")): r
            for r in records
            if r.get("_control_number")
        }

    def _on_table_clicked(self, proxy_idx: QModelIndex) -> None:
        """Route a click on the Record (control-number) column to the
        friendly MARC record popup."""
        if not proxy_idx.isValid() or proxy_idx.column() != COL_RECORD:
            return
        source_row = self._proxy.mapToSource(proxy_idx).row()
        if not 0 <= source_row < len(self._model._rows):
            return
        cn = str(self._model._rows[source_row].get("_control_number") or "")
        marc_record = self._marc_by_cn.get(cn)
        if marc_record is None and self._output_path is not None:
            from mhm_pipeline.gui.dialogs.widgets.marc_record_popup import (  # noqa: PLC0415
                load_marc_index,
            )
            marc_record = load_marc_index(self._output_path.parent).get(cn)
        from mhm_pipeline.gui.dialogs.widgets.marc_record_popup import (  # noqa: PLC0415
            open_marc_popup,
        )
        open_marc_popup(cn, marc_record, parent=self)

    def get_all_sources(self) -> list[str]:
        return sorted({r.get("source") or "" for r in self._model._rows if r.get("source")})

    def get_all_types(self) -> list[str]:
        return sorted({r.get("match_type") or "" for r in self._model._rows if r.get("match_type")})

    def get_all_bands(self) -> list[str]:
        return sorted({_conf_band(r.get("confidence")) for r in self._model._rows})

    def apply_filters(
        self,
        sources: set[str] | None,
        types: set[str] | None,
        bands: set[str] | None,
    ) -> None:
        self._proxy.set_dimension_filters(
            set(sources or ()), set(types or ()), set(bands or ()),
        )
        self._refresh_actions()
        self._update_stats()

    # ── Actions column (✎ Edit · ↗ View) ─────────────────────────────────

    def _refresh_actions(self) -> None:
        from mhm_pipeline.gui import theme  # noqa: PLC0415

        for row in range(self._proxy.rowCount()):
            idx = self._proxy.index(row, COL_ACTIONS)
            self._table.setIndexWidget(idx, None)

        btn_qss = (
            f"QPushButton {{ background: transparent;"
            f" color: {theme.ui('text')};"
            f" border: 1px solid {theme.ui('border')};"
            f" border-radius: {theme.RADIUS_SM}px;"
            f" font-size: {theme.FONT_BASE}px;"
            f" font-weight: 600; padding: 0 4px;"
            f" min-height: 22px; min-width: 24px; }}"
            f"QPushButton:hover {{ background: rgba(255,255,255,18);"
            f" border-color: {theme.ui('highlight')}; }}"
        )

        for row in range(self._proxy.rowCount()):
            idx = self._proxy.index(row, COL_ACTIONS)
            container = QWidget()
            h = QHBoxLayout(container)
            h.setContentsMargins(2, 1, 2, 1)
            h.setSpacing(4)

            edit_btn = QPushButton("✎")
            edit_btn.setToolTip("Edit match")
            edit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            edit_btn.setStyleSheet(btn_qss)
            edit_btn.clicked.connect(lambda _=False, r=row: self._on_edit(r))
            h.addWidget(edit_btn)

            view_btn = QPushButton("↗")
            view_btn.setToolTip("View match context")
            view_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            view_btn.setStyleSheet(btn_qss)
            view_btn.clicked.connect(lambda _=False, r=row: self._on_view(r))
            h.addWidget(view_btn)

            compare_btn = QPushButton("🧬")
            compare_btn.setToolTip(
                "Compare biographical data (dates, places, names, "
                "occupations) between the MARC record and the matched "
                "authority — makes approval decisions faster."
            )
            compare_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            compare_btn.setStyleSheet(btn_qss)
            compare_btn.clicked.connect(lambda _=False, r=row: self._on_compare(r))
            h.addWidget(compare_btn)

            self._table.setIndexWidget(idx, container)

    def _proxy_to_source(self, proxy_row: int) -> int:
        idx = self._proxy.index(proxy_row, COL_ACTIONS)
        return self._proxy.mapToSource(idx).row()

    def _on_edit(self, proxy_row: int) -> None:
        src = self._proxy_to_source(proxy_row)
        if not 0 <= src < len(self._model._rows):
            return
        dlg = MatchEditDialog(self._model._rows[src], parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        new_row = dlg.edited_row()
        self._model._rows[src] = new_row
        self._model.dataChanged.emit(
            self._model.index(src, 0),
            self._model.index(src, self._model.columnCount() - 1),
        )
        self.entities_changed.emit()
        self._update_stats()

    def _on_view(self, proxy_row: int) -> None:
        src = self._proxy_to_source(proxy_row)
        if not 0 <= src < len(self._model._rows):
            return
        MatchSourceViewDialog(self._model._rows[src], parent=self).exec()

    def _on_compare(self, proxy_row: int) -> None:
        """Open the biodata comparison dialog for this match.

        MARC-side data is extracted synchronously from the already-
        loaded record and rendered immediately; the dialog is usable
        even if the async authority fetch is slow or fails. The
        authority side fills in when the VIAF/Mazal fetch resolves
        (or stays blank for ``marc_field`` matches, which have no
        external counterpart). The dialog exposes Approve + Next so
        the reviewer can bulk-process without closing between rows.
        """
        src = self._proxy_to_source(proxy_row)
        if not 0 <= src < len(self._model._rows):
            return

        from mhm_pipeline.gui.widgets.match_comparison_dialog import (  # noqa: PLC0415
            MatchComparisonDialog,
        )

        dlg = MatchComparisonDialog(
            self._model._rows[src],
            parent=self,
            on_approve=self._compare_approve_handler,
            on_next=self._compare_next_handler,
        )
        self._hydrate_compare_dialog(dlg, src)
        dlg.exec()

    def _hydrate_compare_dialog(self, dlg, src: int) -> None:
        """Populate *dlg* with MARC + authority data for source row *src*.

        Called both on initial open and when the user clicks → Next.
        """
        from converter.authority.biodata import (  # noqa: PLC0415
            BioComparison,
            BioData,
            extract_marc_biodata,
        )

        from mhm_pipeline.gui.widgets.match_comparison_dialog import (  # noqa: PLC0415
            fetch_biodata_async,
        )

        row = self._model._rows[src]

        # Find the MARC record that hosts this match
        cn = str(row.get("_control_number", ""))
        marc_record: dict | None = None
        for r in self._model._records:
            if str(r.get("_control_number", "")) == cn:
                marc_record = r
                break

        auth_kind = str(row.get("_auth_kind") or "")
        # The biodata fetcher routes by a short authority key — collapse
        # the two NER variants onto their underlying authority.
        if auth_kind == "ner_mazal":
            fetch_source = "mazal"
        elif auth_kind == "ner_viaf":
            fetch_source = "viaf"
        elif auth_kind in {"mazal", "viaf", "kima"}:
            fetch_source = auth_kind
        else:
            fetch_source = ""
        auth_id = str(row.get("matched_id", ""))
        if fetch_source == "viaf" and "/" in auth_id:
            auth_id = auth_id.rstrip("/").split("/")[-1]

        marc_bio = extract_marc_biodata(marc_record, row=row)
        initial = BioComparison(
            marc=marc_bio, authority=BioData(), source=fetch_source,
        )
        dlg.load_row(row, comparison=initial)

        if not fetch_source or not auth_id:
            # No async work — keep the dialog synchronous
            dlg._progress.setVisible(False)  # type: ignore[attr-defined]
            return

        viaf_fetcher = self._make_viaf_fetcher()
        mazal_fetcher = self._make_mazal_fetcher()
        kima_fetcher = self._make_kima_fetcher()

        signals = fetch_biodata_async(
            source=fetch_source, auth_id=auth_id, marc_record=marc_record,
            viaf_fetcher=viaf_fetcher,
            mazal_fetcher=mazal_fetcher,
            kima_fetcher=kima_fetcher,
        )
        # Reference pinning — without this, Python may GC the signals
        # holder before the QRunnable queues.
        dlg._bio_signals = signals  # type: ignore[attr-defined]

        def _on_ready(_s: str, _i: str, cmp_: object) -> None:
            merged = BioComparison(
                marc=marc_bio, authority=cmp_.authority, source=cmp_.source,
            )
            dlg.show_comparison(merged)

        signals.ready.connect(_on_ready)
        signals.failed.connect(
            lambda _s, _i, msg: dlg.show_error(msg),
        )

    def _compare_approve_handler(self, row: dict) -> None:
        """Flip the approved flag on the row in the model + refresh view."""
        for i, r in enumerate(self._model._rows):
            if r is row:
                r["approved"] = True
                tl = self._model.index(i, 0)
                br = self._model.index(i, self._model.columnCount() - 1)
                self._model.dataChanged.emit(tl, br)
                self.entities_changed.emit()
                self._update_stats()
                break

    def _compare_next_handler(self, row: dict) -> dict | None:
        """Return the next row + hydrate the caller's dialog.

        The returned dict ``{"row": ..., "comparison": BioComparison,
        "show_progress": bool}`` is what :meth:`MatchComparisonDialog
        .load_row` expects. We hydrate the dialog asynchronously; for
        VIAF/Mazal rows the dialog shows the MARC side immediately +
        a spinner.
        """
        current_src = -1
        for i, r in enumerate(self._model._rows):
            if r is row:
                current_src = i
                break
        if current_src < 0:
            return None
        next_src = self._find_next_compare_row(current_src)
        if next_src is None:
            return None

        # Hydrate via the same path used for initial open — pull the
        # parent dialog from the caller's stack via self.focusWidget()
        # fallback. Simpler: we just rebuild the initial comparison
        # here and let load_row apply it.
        from converter.authority.biodata import (  # noqa: PLC0415
            BioComparison,
            BioData,
            extract_marc_biodata,
        )

        next_row = self._model._rows[next_src]
        cn = str(next_row.get("_control_number", ""))
        marc_record: dict | None = None
        for r in self._model._records:
            if str(r.get("_control_number", "")) == cn:
                marc_record = r
                break
        next_auth_kind = str(next_row.get("_auth_kind") or "")
        if next_auth_kind == "ner_mazal":
            next_fetch_source = "mazal"
        elif next_auth_kind == "ner_viaf":
            next_fetch_source = "viaf"
        elif next_auth_kind in {"mazal", "viaf", "kima"}:
            next_fetch_source = next_auth_kind
        else:
            next_fetch_source = ""

        marc_bio = extract_marc_biodata(marc_record, row=next_row)
        initial = BioComparison(
            marc=marc_bio, authority=BioData(), source=next_fetch_source,
        )

        # Kick off the async fetch so that by the time the dialog's
        # load_row returns, the authority side will start filling in
        # through the dialog's existing signals wiring. To keep the
        # signals attached to THIS dialog we reach up the widget tree
        # — the parent of the caller row is self, and the active modal
        # child of self is the dialog.
        dlg = None
        from PyQt6.QtWidgets import QApplication  # noqa: PLC0415

        for w in QApplication.topLevelWidgets():
            if w.__class__.__name__ == "MatchComparisonDialog" and w.isVisible():
                dlg = w
                break
        if dlg is not None:
            self._hydrate_compare_dialog(dlg, next_src)

        return {
            "row": next_row,
            "comparison": initial,
            "show_progress": bool(next_fetch_source)
                and bool(next_row.get("matched_id")),
        }

    def _find_next_compare_row(self, current_src: int) -> int | None:
        """Return the index of the next row after *current_src* that has
        a matched entity worth comparing. Wraps at the end."""
        n = len(self._model._rows)
        if n == 0:
            return None
        for offset in range(1, n + 1):
            idx = (current_src + offset) % n
            if idx == current_src:
                return None
            r = self._model._rows[idx]
            if r.get("entity_text") or r.get("matched_name"):
                return idx
        return None

    def _make_viaf_fetcher(self):  # noqa: ANN001
        """Return a callable ``id -> raw_cluster_dict`` or ``None`` if
        the VIAF matcher can't be constructed (offline mode)."""
        try:
            from converter.authority.viaf_matcher import VIAFMatcher  # noqa: PLC0415
            if not hasattr(self, "_viaf_matcher") or self._viaf_matcher is None:
                self._viaf_matcher = VIAFMatcher()
            return self._viaf_matcher.get_cluster_biodata
        except Exception:
            return None

    def _make_mazal_fetcher(self):  # noqa: ANN001
        """Return a thread-safe Mazal fetcher.

        SQLite connections are bound to the thread that created them —
        reusing a single connection across the main thread and a
        QThreadPool worker raises ``SQLite objects created in a thread
        can only be used in that same thread``. Opening a fresh
        connection per call is cheap (~2 ms) and the dialog already
        caches results in :data:`match_comparison_dialog._CACHE`, so
        on the steady state this only fires on the first miss per
        authority ID.
        """
        from mhm_pipeline.platform_.paths import bundled_resource_root as _root  # noqa: PLC0415

        db_path = str(_root() / "converter" / "authority" / "mazal_index.db")

        def _fetch(auth_id: str) -> dict | None:
            try:
                from converter.authority.mazal_index import MazalIndex  # noqa: PLC0415

                with MazalIndex(db_path) as idx:
                    return idx.get_record(auth_id)
            except Exception:
                return None

        return _fetch

    def _make_kima_fetcher(self):  # noqa: ANN001
        # KIMA currently resolves by name, not by ID; return None until
        # lookup_by_id is added.
        return None

    # ── Auto-approve ─────────────────────────────────────────────────────

    def _on_auto_approve(self) -> None:
        options_for = {
            "source": self.get_all_sources(),
            "match_type": self.get_all_types(),
            "confidence_band": self.get_all_bands(),
        }
        dlg = AuthorityAutoApproveDialog(self, options_for=options_for)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        rules = dlg.rules()
        combinator = dlg.combinator_value()
        matched = [
            i for i, r in enumerate(self._model._rows)
            if evaluate_auth_rules(r, rules, combinator)
        ]
        changed = self._model.set_approved_bulk(matched, True)
        self._update_stats()
        self.entities_changed.emit()
        QMessageBox.information(
            self, "Auto-approve",
            f"Approved {changed} match{'es' if changed != 1 else ''} "
            f"matching the rules.",
        )

    def _set_visible(self, approved: bool) -> None:
        rows: list[int] = []
        for r in range(self._proxy.rowCount()):
            rows.append(self._proxy_to_source(r))
        changed = self._model.set_approved_bulk(rows, approved)
        self._update_stats()
        self.entities_changed.emit()
        verb = "Approved" if approved else "Cleared approval on"
        QMessageBox.information(
            self, "Bulk approval",
            f"{verb} {changed} visible match{'es' if changed != 1 else ''}.",
        )

    # ── Stats + CRUD ─────────────────────────────────────────────────────

    def _update_stats(self) -> None:
        total = self._model.rowCount()
        visible = self._proxy.rowCount() if self._proxy else total
        approved = sum(1 for r in self._model._rows if r.get("approved", False))
        dirty = " (modified)" if self._model.is_dirty() else ""
        pct = (approved / total * 100) if total else 0.0
        if visible == total:
            self._stats.setText(f"{total} matches · {approved} approved ({pct:.0f}%){dirty}")
        else:
            self._stats.setText(f"{visible} of {total} visible · {approved} approved ({pct:.0f}%){dirty}")
        # Rule 49 §E — clear-column-filters button reflects active state.
        clear_btn = getattr(self, "_clear_col_filters_btn", None)
        if clear_btn is not None and isinstance(self._proxy, AuthorityFilterProxy):
            clear_btn.setEnabled(self._proxy.has_any_column_filter())

    def _on_clear_column_filters(self) -> None:
        """Slot wired to the 🗑 Clear column filters button. Drops every
        per-column include-set without touching the chip-row dimension
        filter (Rule 49 §E)."""
        if isinstance(self._proxy, AuthorityFilterProxy):
            self._proxy.clear_all_column_filters()
            self._refresh_actions()
            self._update_stats()

    def _on_search(self, text: str) -> None:
        self._proxy.setFilterFixedString(text)
        self._refresh_actions()
        self._update_stats()

    def _on_revert(self) -> None:
        if not self._model.is_dirty():
            return
        if QMessageBox.question(
            self, "Revert Changes",
            "Discard all edits and revert to the original matches?",
        ) == QMessageBox.StandardButton.Yes:
            self._model.revert()
            self._refresh_actions()
            self._update_stats()

    def _on_save(self) -> None:
        if not self._output_path:
            return
        total = len(self._model._rows)
        approved = sum(1 for r in self._model._rows if r.get("approved", False))
        rejected = total - approved
        if rejected > 0:
            if QMessageBox.question(
                self, "Save approved matches only",
                f"Save will keep {approved} approved match"
                f"{'es' if approved != 1 else ''} and drop "
                f"{rejected} unapproved from the output file.\n\nProceed?",
            ) != QMessageBox.StandardButton.Yes:
                return
        records = self._model.to_approved_records()
        self._output_path.write_text(
            json.dumps(records, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self._model._original = copy.deepcopy(self._model._rows)
        self._update_stats()
        logger.info(
            "Saved %d approved authority matches (%d dropped) to %s",
            approved, rejected, self._output_path,
        )
        QMessageBox.information(
            self, "Saved",
            f"Saved {approved} approved match{'es' if approved != 1 else ''}"
            f" ({rejected} dropped) to\n{self._output_path}",
        )
