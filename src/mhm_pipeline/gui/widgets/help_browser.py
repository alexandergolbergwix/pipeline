"""Built-in help browser — full pipeline documentation in-app.

Opens as a GlassDialog with a left-side topic list and a right-side
markdown-rendered content panel. All topics live as inline strings so
the bundled .app has no external file dependency.

Topics are grouped:

* Getting started      — first-run, what each stage does
* Stage walkthroughs   — Stage 1 → Stage 6 with example outputs
* Concepts             — auto-approve, MARC grounding, "Exists in"
                          column, role-grounded vs wrong-field vs
                          discovery, confidence calibration
* Reference            — keyboard shortcuts, file locations, common
                          errors, when to re-train

The Help menu also offers direct shortcuts:

  Help → Help & Documentation…   (opens this browser)
  Help → MARC Grounding…         (jumps straight to the grounding topic)
  Help → Keyboard Shortcuts…     (jumps to the shortcuts topic)
  Help → Report an Issue…        (opens the project's bug tracker)
  Help → About                   (the existing about box)
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSplitter,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from mhm_pipeline.gui import theme
from mhm_pipeline.gui.widgets.glass_dialog import GlassDialog


# ── Topic content (markdown) ────────────────────────────────────────────

_TOPICS: list[tuple[str, str, str]] = [
    # (key, sidebar title, markdown body)
    (
        "getting-started",
        "Getting Started",
        """# Getting Started

Welcome to the **MHM Pipeline** — an end-to-end MARC-to-RDF
conversion tool for Hebrew manuscript records.

## What the app does

1. **Stage 1 — MARC Parse**: reads an NLI TSV / MARC-XML export
   and converts it to structured `marc_extracted.json`.
2. **Stage 2 — NER**: runs five trained models (Person, Provenance,
   Contents NER + Genre + Colophon classifiers) to extract entities
   from the source text.
3. **Stage 3 — Authority Resolution**: matches extracted persons /
   places / works against Mazal (NLI J9U), VIAF, KIMA, and Wikidata.
4. **Stage 4 — RDF Mapping**: serialises the enriched data as
   Turtle, ready for SPARQL.
5. **Stage 5 — SHACL Validation**: checks the RDF against the HMO
   ontology shapes.
6. **Stage 6 — Wikidata Upload**: review-gated upload of new items
   to Wikidata or the project's wikibase.cloud.

## First-run checklist

1. Open **Pipeline → Run Stage 1** with a test TSV
2. Open **Stage 2 (NER)**, click **Run**
3. When NER finishes, click **Edit & Review** — the review dialog
   opens automatically
4. Review entities; approve the good ones; un-approve the bad ones
5. Continue to Stage 3 → 6 from the main panel

The first run may download model weights (~3 GB total) — bundled
in the macOS app, downloaded on demand on Windows / Linux.
""",
    ),
    (
        "review-and-edit",
        "The Review & Edit dialog",
        """# Review & Edit dialog

After Stage 2 (NER) finishes, the **Edit & Review** dialog opens
with every extracted entity in a sortable / filterable table.

## Columns

| Column | Meaning |
|--------|---------|
| Record       | NLI control number |
| Entity       | The extracted text (person name, place, work…) |
| Type         | Entity type — varies by source (PERSON, OWNER, FOLIO, …) |
| Role         | For Person NER only: AUTHOR / TRANSCRIBER / OWNER / … |
| Conf.        | Keyword-classifier confidence (bimodal 0.60 / 0.85) |
| Model Conf.  | Real softmax probability from the BIO classifier |
| Source       | Which NER model produced this entity |
| **Exists in** | **NEW** — see "MARC Grounding" topic for the full story |
| Approved     | Checkbox — set to True before continuing to Stage 3 |

## Approving entities

Three approval paths:

1. **Tick a row's "Approved" checkbox** — single-entity decision.
2. **⚡ Auto-approve…** — opens a rule builder. By default the
   dialog opens with the safe pair `confidence > 0.85 AND
   grounded = True` pre-filled.
3. **Approve all visible** — bulk approve everything currently
   passing the filter. Use with care.

Only **approved** entities flow into Stage 3 onwards.

## Filters

- Search box (top): matches against any column.
- Source / Type / Role checkbox filters: combine with AND.
- Click any column header to sort.
""",
    ),
    (
        "marc-grounding",
        "MARC Grounding (the \"Exists in\" column)",
        """# MARC Grounding — the "Exists in" column

Every NER extraction is checked against the **structured MARC
fields** of its source record. The result lands in the **Exists in**
column on the Review & Edit table.

## The three states

| Cell colour | State | Meaning |
|-------------|-------|---------|
| 🟢 **Green** — ✓ grounded | **Role-grounded** | The predicted name appears in the MARC field its role implies (e.g., AUTHOR found in `authors`, TRANSCRIBER found in `colophon_text`). Safe to auto-approve. |
| 🟡 **Yellow** — ⚠ wrong field | **Wrong field** | The name *is* in MARC but in a different field than the role implies. Almost always means the *role* is wrong (AUTHOR predicted but the name lives in `contributors` / 700 — the model probably should have said TRANSLATOR or OWNER). |
| 🔵 **Blue** — 🆕 new | **Discovery** | The name is NOT found in any structured MARC field. Either a real discovery (the NER enriched the catalog) OR a hallucination. The reviewer must decide. |

## What "role-implied field" means

The pipeline maps every predicted role/type to the MARC field(s)
where MARC catalogers conventionally put that kind of information:

| Predicted role | Expected MARC field(s) |
|----------------|------------------------|
| AUTHOR         | `authors[]` (MARC 100 / 110) |
| TRANSCRIBER    | `colophon_text`, `data_from_colophon.scribe`, `contributors[]` |
| TRANSLATOR     | `contributors[]`, `notes` |
| COMMENTATOR    | `contributors[]`, `notes` |
| EDITOR         | `contributors[]` |
| CENSOR         | `notes`, `contributors[]` |
| OWNER          | `provenance`, `notes` |

Click any **Exists in** cell to see the full MARC record with
green / yellow highlighted matches.

## Matching rules

- **Exact match** — ignoring whitespace + ASCII↔Unicode quotes
- **Word-order swap** — "Yossi Stiwi" ≡ "Stiwi Yossi" (MARC
  inverts names; the NER doesn't)
- **Single-token-of-name match** — "Stiwi" matches "Yossi Stiwi"
  (every token of the prediction appears in the MARC value)

## Why this matters for auto-approval

Confidence alone was a bad gate — 25% of high-confidence (>0.85)
TRANSCRIBER predictions turned out to be wrong-role (the name was
in `provenance`, the person is an OWNER). The default Auto-approve
dialog now opens with `grounded = True` pre-filled so the safe
default catches these.
""",
    ),
    (
        "auto-approve",
        "Auto-approve rules",
        """# Auto-approve rules

Click **⚡ Auto-approve…** in the Review & Edit dialog to open the
rule builder. The dialog opens with sensible defaults and you can
add / remove conditions before applying.

## Default rules (when grounding data is present)

```
confidence  >  0.85
grounded    =  True
Combine: AND
```

This catches the proven-precise subset: high-confidence predictions
where the name lives in the role-mapped MARC field.

## Available fields

| Field | Type | Description |
|-------|------|-------------|
| confidence       | numeric | Keyword classifier score (0.60 / 0.85) |
| model_confidence | numeric | Softmax probability from the BIO head |
| type             | enum    | PERSON / OWNER / WORK / FOLIO / … |
| role             | enum    | AUTHOR / TRANSCRIBER / … (persons only) |
| source           | enum    | person_ner / provenance_ner / contents_ner / genre_ml |
| **grounded**     | enum    | True / False (F8 MARC grounding) |

## Operators

- `>` `>=` `=` `<=` `<` `≠` for numeric fields
- `=` `≠` `in` `not in` for enum fields

## Combinator

- `AND`: every condition must match (recommended default)
- `OR`: any one match approves the row

## Examples

```text
# Conservative — only role-grounded high-confidence
confidence > 0.85  AND  grounded = True

# Catch obvious false positives — drop ungrounded high-conf TRANSCRIBERs
role = TRANSCRIBER  AND  grounded = False     ← then UN-approve

# Approve every FOLIO regardless of confidence
type = FOLIO
```
""",
    ),
    (
        "stages",
        "Stage-by-stage walkthrough",
        """# Stages 1 – 6

## Stage 1 — MARC Parse

**Input**: NLI TSV export (one record per row, with subject /
contributor columns).
**Output**: `eval/work/marc_extracted.json` — structured per-record
dict with all bibliographic fields.

## Stage 2 — NER

**Input**: `marc_extracted.json`.
**Output**: `eval/work/ner_results.json` — every extracted entity
with `confidence`, `model_confidence`, `grounded`, `grounded_field`,
`exists_in`.

Four models run:

| Model | Output channel | Purpose |
|-------|---------------|---------|
| Person NER (joint name + role)         | `entities[source=person_ner]` | Names + AUTHOR/TRANSCRIBER/OWNER… role |
| Provenance NER (OWNER/DATE/COLLECTION) | `entities[source=provenance_ner]` | Ownership inscriptions |
| Contents NER (WORK/FOLIO/WORK_AUTHOR)  | `entities[source=contents_ner]` | Cited works + folios |
| Genre classifier                       | `ml_genres[]` | MARC 655 fallback |

## Stage 3 — Authority Resolution

For each approved person / place / work, queries:

1. **Mazal (NLI J9U)** — local SQLite, sub-second
2. **VIAF** — SRU API, ~2 req/s
3. **KIMA** — local SQLite (places only)
4. **Wikidata** — SPARQL endpoint

Identifies the entity across all four (when possible) and writes
the cross-references into `authority_enriched.json`.

## Stage 4 — RDF Mapping

Takes `authority_enriched.json` and emits Turtle under
`hmo/output.ttl` using the HMO ontology (FRBRoo + CIDOC-CRM).

## Stage 5 — SHACL Validation

Runs `pyshacl` against `ontology/shacl-shapes.ttl`. Violations are
shown in the GUI with severity (error / warning / info).

## Stage 6 — Wikidata Upload

**Dry-run by default** — emits a QuickStatements file. Switch to
live upload only after manual review. The `MORATORIUM_LIFTED`
env var must be `true` to enable live writes (Rule 25 safety gate).
""",
    ),
    (
        "shortcuts",
        "Keyboard Shortcuts",
        """# Keyboard Shortcuts

## Global

| Key | Action |
|-----|--------|
| `Cmd+,`           | Open Settings (macOS) |
| `Cmd+Q` / `Ctrl+Q`| Quit |
| `F1`              | Open this help browser |

## Review & Edit dialog

| Key | Action |
|-----|--------|
| `Cmd+F` / `Ctrl+F`   | Focus the search box |
| `Space`              | Toggle Approved on the focused row |
| `Cmd+A` / `Ctrl+A`   | Select all visible rows |
| `Delete`             | Mark selected rows for deletion |
| `Enter`              | Edit the entity text in the focused row |

## NER panel

| Key | Action |
|-----|--------|
| `Cmd+R` / `Ctrl+R`   | Re-run the NER stage |
| `Cmd+E` / `Ctrl+E`   | Open the Review & Edit dialog |
""",
    ),
    (
        "files",
        "File locations",
        """# File locations

## Pipeline output

By default each Pipeline run writes to `eval/work/`:

```
eval/work/
├── marc_extracted.json        ← Stage 1 output
├── ner_results.json           ← Stage 2 output (includes grounding)
├── authority_enriched.json    ← Stage 3 output
├── output.ttl                 ← Stage 4 output
├── validation_report.json     ← Stage 5 output
└── quickstatements.txt        ← Stage 6 dry-run output
```

## App data

- **macOS**: `~/Library/Application Support/MHM Pipeline/`
- **Windows**: `%APPDATA%\\MHMPipeline\\`
- **Linux**: `~/.local/share/MHMPipeline/`

Contains:

- `settings.json` — your preferences (theme, GPU, log level)
- `cache/` — VIAF / Wikidata response cache (one row per query)
- `logs/` — rolling app log files (7-day retention)

## Bundled data (read-only)

Inside the .app bundle:

```
Contents/Resources/pipeline/
├── data/kima/kima_index.db        ← KIMA places index
├── converter/authority/mazal_index.db  ← Mazal NLI authority
└── models/                        ← bundled NER + classifier weights
```
""",
    ),
    (
        "errors",
        "Common errors",
        """# Common errors

## "No KIMA index — places won't resolve"

The `data/kima/kima_index.db` file is missing. Rebuild from TSVs:

```bash
PYTHONPATH=src:. .venv/bin/python -c "
from converter.authority.kima_index import build_kima_index
build_kima_index('data/kima', 'data/kima/kima_index.db', verbose=True)
"
```

## "Stage 1 crashes when importing pymarc"

Run `uv sync --python 3.12` from a terminal — the bundled venv
must have pymarc installed.

## "Wikidata upload refused: MORATORIUM_LIFTED not set"

Live uploads are gated by an environment variable for safety
(Rule 25). Confirm with the project maintainer before setting
`MORATORIUM_LIFTED=true` and re-running Stage 6.

## "Stage 6.5 IIIF manifests skipped: no wikibase cloud credentials"

Open Settings → Wikibase Cloud and enter your bot password (created
at https://mhm-hmo.wikibase.cloud/wiki/Special:BotPasswords). The
manifests will still be written to `iiif_manifests/` locally — only
the upload step is skipped.

## "All NER entities show 🆕 new (not in structured fields)"

This usually means `marc_extracted.json` is empty or the control
numbers don't match between Stage 1 and Stage 2 outputs. Check
that you ran Stage 1 on the same TSV.

## Where to ask for help

- File an issue: https://github.com/alexgoldberg/mhm-pipeline/issues
- Email the lab: mhm-pipeline@biu.ac.il
""",
    ),
    (
        "about",
        "About this build",
        """# About MHM Pipeline

A desktop application for converting MARC bibliographic records of
Hebrew manuscripts into RDF, with authority resolution, validation,
and curated Wikidata upload.

## Maintainer

Alexander Goldberg
Bar-Ilan University

## License

GPL-3.0-or-later

## Citing this work

If you use the MHM Pipeline in your research, please cite:

> Goldberg, A. (2026). *Mapping Hebrew Manuscripts: An End-to-End
> Pipeline for MARC-to-RDF Conversion with LLM-Assisted Authority
> Resolution*. Doctoral dissertation, Bar-Ilan University.

## Acknowledgements

- DICTA Institute (DictaBERT)
- The National Library of Israel (Mazal / J9U authority data, KIMA
  places, NLI manuscript catalogue)
- The HMO Ontology working group
""",
    ),
]


# ── The dialog ──────────────────────────────────────────────────────────


class HelpBrowser(GlassDialog):
    """Two-pane help browser with topic list + markdown content view."""

    def __init__(
        self, *, initial_topic: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Help & Documentation")
        self.resize(960, 700)

        outer = QVBoxLayout(self.glass_content)
        outer.setContentsMargins(
            theme.SPACE_LG, theme.SPACE_LG, theme.SPACE_LG, theme.SPACE_LG,
        )
        outer.setSpacing(theme.SPACE_MD)

        # Header
        title = QLabel("Help & Documentation")
        title.setStyleSheet(
            f"color: {theme.ui('text')}; "
            f"font-size: {theme.FONT_XL}px; font-weight: 700;"
        )
        outer.addWidget(title)

        # Two-pane splitter: topic list + content
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(1)

        self._list = QListWidget()
        self._list.setStyleSheet(
            f"QListWidget {{ background: transparent; color: {theme.ui('text')}; "
            f"border: 1px solid {theme.ui('border')}; "
            f"border-radius: {theme.RADIUS_MD}px; "
            f"padding: {theme.SPACE_XS}px; }} "
            f"QListWidget::item {{ padding: 6px 10px; border-radius: {theme.RADIUS_SM}px; }} "
            f"QListWidget::item:selected {{ background: {theme.ui('highlight')}; }}"
        )
        for key, label, _body in _TOPICS:
            it = QListWidgetItem(label)
            it.setData(Qt.ItemDataRole.UserRole, key)
            self._list.addItem(it)
        self._list.setMinimumWidth(220)
        self._list.setMaximumWidth(280)
        splitter.addWidget(self._list)

        self._content = QTextBrowser()
        self._content.setOpenExternalLinks(True)
        self._content.setStyleSheet(
            f"QTextBrowser {{ background: transparent; color: {theme.ui('text')}; "
            f"border: 1px solid {theme.ui('border')}; "
            f"border-radius: {theme.RADIUS_MD}px; "
            f"padding: {theme.SPACE_MD}px; "
            f"font-size: {theme.FONT_BASE}px; }} "
        )
        splitter.addWidget(self._content)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        outer.addWidget(splitter, stretch=1)

        # Footer button
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        close_btn = QPushButton("Close")
        close_btn.setStyleSheet(theme.button_style())
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        outer.addLayout(btn_row)

        self._list.currentRowChanged.connect(self._show_topic)
        target = 0
        if initial_topic:
            for i, (key, _label, _body) in enumerate(_TOPICS):
                if key == initial_topic:
                    target = i
                    break
        self._list.setCurrentRow(target)

    # ── Routing ─────────────────────────────────────────────────────────

    def _show_topic(self, row: int) -> None:
        if not 0 <= row < len(_TOPICS):
            return
        _key, _label, body = _TOPICS[row]
        self._content.setMarkdown(body)

    @staticmethod
    def topic_keys() -> list[str]:
        return [k for k, _l, _b in _TOPICS]


__all__ = ["HelpBrowser"]
