# Test Subset Creation — `data/tsvs/test_subset.tsv`

This document explains how the **68-record** test corpus
[`data/tsvs/test_subset.tsv`](../data/tsvs/test_subset.tsv) is built, what
criteria each record is selected against, and why the resulting subset is the
*single source of truth* pinned by the paper-claim verification harness
([`paper/verification/verify_paper.py`](../paper/verification/verify_paper.py)).

---

## 1. Purpose

The MHM Pipeline processes 123,000+ MARC manuscript records. Most experiments
and paper claims cannot be run against the full corpus on every change — too
slow, too costly. Instead we maintain a tiny, hand-built **coverage-maximizing
subset**:

- **As small as possible** so unit + integration tests stay fast (`pytest tests/`
  finishes in ~63 s with this subset pinned).
- **As broad as possible** so every meaningful MARC pattern the pipeline must
  handle is present in at least one record.

Every numeric claim in the SWJ paper (`paper/swj-paper.tex`) — "14 distinct
validator rules fire", "37 LCSH headings", "23 Hebrew ordinal forms",
"545 unit tests pass" — is verified against this subset. Changing the subset
changes the claims, so the subset is content-addressed by SHA-256 and pinned
in [`paper/verification/CLAIMS.yaml`](../paper/verification/CLAIMS.yaml).

> The subset is **not** representative of the full catalog — it is
> deliberately enriched for hard-to-trigger features.

---

## 2. Source corpora

| Layer | Path | Size | sha256 | Role |
|---|---|---:|---|---|
| Primary | [`data/tsvs/17th_century_samples.tsv`](../data/tsvs/17th_century_samples.tsv) | 897 records | `0b0d455b…58133` | Stages 1–3 |
| Secondary | [`data/tsvs/filtered_manuscripts_after_906a.tsv`](../data/tsvs/filtered_manuscripts_after_906a.tsv) | — | `ae69b2a0…68b794` | Stage 4 supplement |
| **Output** | [`data/tsvs/test_subset.tsv`](../data/tsvs/test_subset.tsv) | 68 records | `3f2cd2bb…7c7ed2` | Pinned in CLAIMS.yaml |

Both source SHA-256s are recorded in the manifest
[`data/tsvs/test_subset_manifest.json`](../data/tsvs/test_subset_manifest.json)
so the build is bit-reproducible: same sources + same seed (`42`) + same
predicate code (`signal_predicate_versions: v1`) → same 68 records on every
machine.

---

## 3. Selection algorithm (4 stages)

Implemented in [`scripts/build_test_subset.py`](../scripts/build_test_subset.py).
Run with:

```bash
PYTHONPATH=src:. .venv/bin/python scripts/build_test_subset.py [--verbose]
```

Defaults: `--target-baseline=60`, `--cap=80`, `--seed=42`.

### Stage 1 — Hard-signal force-include (**8 records**)

For each of the 14 "hard" signals (see §5), find every record that satisfies
the signal in the primary corpus, then pick the record with the **highest
total signal count** among hits. Ties broken by stable hash of `001`.

Many hard signals collapse onto the same rich record (a manuscript with both
Hebrew-century date *and* MARC 880 *and* translator role covers three at once).
On the 2026-05-02 run, 14 hard signals → **8 unique records**.

### Stage 2 — Greedy set-cover (**9 records**)

Iteratively pick the record that covers the most *still-uncovered* signals.
Tie-breakers in order: (a) total signal count on the record, (b) stable hash
of `001`. Stop when no remaining record adds any new signal.

This pulls in 9 more records covering signals like `PROVENANCE_561_MULTI`,
`AUTH_ID_LOCAL_9`, `SUBJECT_BIBLE_630`, `URL_856`, etc.

### Stage 3 — Stratified-typical fill (**43 records**)

Top up to `target_baseline = 60` records via stratified random sampling:

- **50/50** with vs without MARC 561 (provenance)
- Proportional to corpus on MARC 700 (added person entries)
- Proportional by **decade** of MARC 008 date (so 1610s and 1670s both
  represented)

`fill_n = max(0, target_baseline − stage1 − stage2)` = `max(0, 60 − 8 − 9) = 43`.

This is the "you also need to see what normal records look like" stage. It
keeps the subset from being entirely edge-cases.

### Stage 4 — Supplement from secondary corpus (**8 records**)

After stages 1–3, walk every signal that's still **zero-hit** in the selected
records and try to pull a single replacement from
`filtered_manuscripts_after_906a.tsv`. Each supplemental record resolves one
or more zero-hit signals.

In the 2026-05-02 run, 8 signals had zero hits in the primary corpus and 8
supplemental records satisfied them:

| Record ID | Resolved signal(s) |
|---|---|
| `990001026150205171` | `REPRO_533` |
| `997008371275105171` | `SUBJECT_MEETING_611`, `DATE_PUB_264C` |
| `990001402000205171` | `DATE_INCEPTION_046` |
| `990019020880205171` | `SERIES_490_830`, `PUB_PUBLISHER_260B`, `PUB_PLACE_260A` |
| `997009236549805171` | `PUB_HISTORY_534`, `TITLE_PART_DESIGNATIONS`, `SUMMARY_520` |
| `990001340200205171` | `LANG_TRANSLATION_041H` |
| `990001801390205171` | `CONTENTS_505_LONG`, `TITLE_PLACEHOLDER_KOVETZ`, `DATE_PRE_1582_JULIAN` |
| `990000927260205171` | `PROVENANCE_OWNER_562` |

### Cap enforcement (no records dropped on this run)

If stages 1–4 total exceeds `--cap=80`, stage 3 records are dropped first
(they're the most replaceable), then redundant stage 2 records whose signals
are already covered by another retained record. The 2026-05-02 run came in at
**68 ≤ 80**, so no drops.

### Why 68?

```
Stage 1 (hard force):      8
Stage 2 (greedy):          9
Stage 3 (stratified):     43       = max(0, 60 − 8 − 9)
Stage 4 (supplement):    + 8       = one per zero-hit signal in primary
                       ----
                         68
```

The numbers 8 / 9 are corpus-dependent (how many records you need to cover the
14 hard signals + remaining greedy targets). The number 43 is purely
arithmetic — pegged to `target_baseline=60`. The 8 supplements depend on which
signals the primary corpus *can't* fire. Different source TSV → different
total, but always deterministic for the same inputs.

---

## 4. The 78 signal predicates

Signals are deterministic boolean functions `Record -> bool` defined in
[`scripts/build_test_subset.py:120-530`](../scripts/build_test_subset.py).

> The codebase lists **78 signals** in the `_SIGNALS` tuple; the manifest's
> `coverage` block summarises **74** (the four that fire on zero records in
> the subset are listed below in §6).

### 4.1 Dates / chronology (8)

| Signal | Predicate | Description |
|---|---|---|
| `DATE_008` | `_has_marc008_date` | MARC 008 positions 7–10 contain a parseable year |
| `DATE_HEBREW_CENTURY` | `_has_hebrew_century` | `260$c`/`260$d` contains Hebrew-letter century (`מאה ט"ז`) |
| `DATE_HEBREW_YEAR` | `_has_hebrew_year` | `260$c` contains Hebrew gematria year (e.g. `ת"ר`) |
| `DATE_PRE_1582_JULIAN` | `_has_pre_1582_date` | MARC 008 year < 1582 (Julian calendar) |
| `DATE_POST_1582_GREGORIAN` | `_has_post_1582_date` | MARC 008 year ≥ 1582 (Gregorian) |
| `DATE_INCEPTION_046` | `_has_inception_046` | MARC 046 inception field present |
| `DATE_PUB_260C` | _lambda_ | `260$c` present (publication date narrative) |
| `DATE_PUB_264C` | _lambda_ | `264$c` present (new MARC 21 publication date) |

### 4.2 Roles (5)

Detected via Hebrew + English role keywords in `100$e`, `700$e`, `710$e`:

| Signal | Keywords |
|---|---|
| `ROLE_SCRIBE` | מעתיק, סופר, כתבן, נקדן, scribe, copyist |
| `ROLE_TRANSLATOR` | מתרגם, translator |
| `ROLE_COMMENTATOR` | מפרש, מבאר, פרשן, commentator |
| `ROLE_OWNER` | בעלים, former owner, owner |
| `ROLE_EDITOR` | עורך, editor |

### 4.3 Languages (10)

| Signal | Detection |
|---|---|
| `LANG_HEBREW_TITLE` | Hebrew Unicode chars in `245$a`/`b`/`p` |
| `LANG_LATIN_TITLE` | Latin script in `245$a`/`b` |
| `LANG_ARABIC` | Arabic script anywhere in content or MARC 041 = `ara` |
| `LANG_ARAMAIC` | MARC 041 = `arc` |
| `LANG_YIDDISH` | MARC 041 = `yid` |
| `LANG_LADINO` | MARC 041 = `lad` |
| `LANG_LATIN` | MARC 041 = `lat` |
| `LANG_GREEK` | Greek Unicode chars or MARC 041 = `grc`/`gre` |
| `LANG_MULTI_041` | MARC 041 lists more than one language code |
| `LANG_TRANSLATION_041H` | MARC 041$h present (translated-from source language) |

### 4.4 Titles (8)

| Signal | Description |
|---|---|
| `TITLE_HEBREW` | Hebrew chars in `245$a`/`b`/`p` (alias of `LANG_HEBREW_TITLE` for the cover-set) |
| `TITLE_VARIANT_246_740` | MARC 246 or 740 variant/added title present |
| `TITLE_UNIFORM_240_130` | MARC 130 or 240 uniform title present |
| `TITLE_ISBD_PERIOD` | `245$a` ends with trailing ISBD `.` |
| `TITLE_PLACEHOLDER_KOVETZ` | `245$a` is a generic catalog placeholder (`קובץ.`, `קובץ בקבלה.`, …) |
| `TITLE_PART_DESIGNATIONS` | `245$n`/`245$p` part designations present |
| `TITLE_LINKED_880` | `245$6` cross-links to a MARC 880 alt-script field |
| `FIELD_880_PRESENT` | Any MARC 880 alt-script subfield present |

### 4.5 Genres / subjects (10)

| Signal | Description |
|---|---|
| `GENRE_655_PRESENT` | At least one MARC 655 genre/form heading |
| `GENRE_655_MULTIPLE` | Two or more distinct MARC 655 headings |
| `GENRE_FORM_TERM_2` | MARC 655$2 source vocabulary tag present |
| `SUBJECT_LCSH_650` | MARC 650 LCSH heading |
| `SUBJECT_BIBLE_630` | MARC 630 Bible book heading (Hebrew or Latin) |
| `SUBJECT_TALMUD_630` | MARC 630 Talmud tractate heading |
| `SUBJECT_PERSON_600` | MARC 600 person-as-subject |
| `SUBJECT_CORP_610` | MARC 610 corporate-as-subject |
| `SUBJECT_MEETING_611` | MARC 611 meeting-as-subject |
| `SUBJECT_GEO_651_751` | MARC 651 or 751 geographic heading |

### 4.6 Provenance (5)

| Signal | Description |
|---|---|
| `PROVENANCE_561` | MARC 561 ownership note present |
| `PROVENANCE_561_LONG` | MARC 561 ≥ 200 chars — multi-owner chain, exercises NER |
| `PROVENANCE_561_MULTI` | MARC 561 with multiple `|`-separated entries |
| `PROVENANCE_ACQUISITION_541` | MARC 541 acquisition note |
| `PROVENANCE_OWNER_562` | MARC 562 owner identification note |

### 4.7 Contributors (8)

| Signal | Field |
|---|---|
| `CONTRIB_MAIN_PERSON_100` | MARC 100 (main personal author) |
| `CONTRIB_MAIN_CORP_110` | MARC 110 (main corporate author) |
| `CONTRIB_ADDED_PERSON_700` | MARC 700 (added personal entry) |
| `CONTRIB_ADDED_CORP_710` | MARC 710 (added corporate entry) |
| `CONTRIB_ADDED_MEETING_711` | MARC 711 (added meeting/conference entry) |
| `CONTRIB_DATES_100D` | MARC 100$d life-dates |
| `CONTRIB_DATES_700D` | MARC 700$d life-dates |
| `AUTH_ID_LOCAL_9` | MARC 100/700 $9 (local NLI authority key) |

### 4.8 Notes / contents (8)

| Signal | Description |
|---|---|
| `NOTE_500` | MARC 500 general note present |
| `NOTE_500_LONG` | Combined MARC 500 ≥ 500 chars (exercises sentence classifier) |
| `NOTE_500_COLOPHON_KW` | MARC 500 contains colophon keywords (`נשלם`, `סיום`, `קולופון`) |
| `NOTE_500_3_MATERIALS` | MARC 500$3 'materials specified' codicological-unit marker |
| `CU_MULTIPLE_500` | Multiple MARC 500 entries (multi-CU manuscript) |
| `CONTENTS_505` | MARC 505 contents note present |
| `CONTENTS_505_LONG` | MARC 505 ≥ 500 chars |
| `SUMMARY_520` | MARC 520 summary/abstract present |

### 4.9 Codicology / physical (3)

| Signal | Description |
|---|---|
| `EXTENT_300A` | MARC 300$a extent (folio count) |
| `PHYSICAL_300_FULL` | MARC 300 with $b (other physical details) + $c (dimensions) |
| `MATERIAL_340` | MARC 340 physical medium description |

### 4.10 Misc / external (8)

| Signal | Description |
|---|---|
| `URL_856` | MARC 856 electronic location |
| `REPRO_533` | MARC 533 reproduction note |
| `PUB_HISTORY_534` | MARC 534 publication history |
| `FUNDING_536` | MARC 536 funding/sponsorship note |
| `RIGHTS_540` | MARC 540 rights statement |
| `ACTION_583` | MARC 583 action note (conservation, digitisation, …) |
| `LOCAL_NOTE_59X` | Any MARC 590/591/… local note |
| `SERIES_490_830` | MARC 490 series statement and/or 830 series added entry |

### 4.11 Geography / publication (4)

| Signal | Description |
|---|---|
| `ORIGIN_PLACE_751` | MARC 751 place of creation |
| `PUB_PLACE_260A` | MARC 260$a publication place |
| `PUB_PUBLISHER_260B` | MARC 260$b publisher |
| `GEO_COORDS_034` | MARC 034 geographic coordinates |

### 4.12 Identifiers (1)

| Signal | Description |
|---|---|
| `CALL_NUMBER_050_090` | MARC 050 (LC) or 090 (local) call number |

---

## 5. Hard signals (14)

These are the signals stage 1 force-includes. They are listed explicitly in
[`build_test_subset.py:646-661`](../scripts/build_test_subset.py) as
`_HARD_SIGNALS`:

```python
_HARD_SIGNALS = (
    "DATE_PRE_1582_JULIAN",      # < 1582 → Julian calendar
    "TITLE_PLACEHOLDER_KOVETZ",  # generic catalog placeholder title
    "ROLE_TRANSLATOR",           # rare role in 17C manuscripts
    "ROLE_COMMENTATOR",          # rare role in 17C manuscripts
    "PROVENANCE_561_LONG",       # long ownership chain
    "PROVENANCE_561_MULTI",      # multi-owner chain
    "GENRE_655_MULTIPLE",        # multiple genres on one record
    "SUBJECT_BIBLE_630",         # Bible book heading
    "SUBJECT_TALMUD_630",        # Talmud tractate heading
    "CU_MULTIPLE_500",           # multi-codicological-unit marker
    "LANG_GREEK",                # Greek text
    "LANG_LADINO",               # Ladino text
    "FIELD_880_PRESENT",         # alt-script (CJK / RTL transliteration)
    "DATE_HEBREW_CENTURY",       # 'מאה ט"ז' century in Hebrew letters
)
```

Each is rare in the 17th-century catalog (< 5 % of records typically). Without
force-include, an unlucky random draw might miss them entirely.

---

## 6. Coverage results (the actual 68 records)

**74 / 78 signals covered (94.9 %).** Per-signal record counts pulled from
[`data/tsvs/test_subset_manifest.json`](../data/tsvs/test_subset_manifest.json)
`coverage` block:

| Group | Covered | Highlights |
|---|---|---|
| Dates (8/8) | ✓ | 65 records have MARC 008 dates; 1 pre-1582 Julian; 4 Hebrew century |
| Roles (4/5) | ✗ `ROLE_COMMENTATOR` | 28 scribes, 4 translators, 67 owners, 1 editor |
| Languages (10/10) | ✓ | 66 Hebrew, 6 Arabic, 3 Aramaic, 1 each Ladino/Greek/Latin/Yiddish translation |
| Titles (8/8) | ✓ | 4 records with 880 linked alt-script; 1 Kovetz placeholder |
| Genres / subjects (10/10) | ✓ | 8 Bible, 2 Talmud, 37 geographic, 11 person-as-subject |
| Provenance (5/5) | ✓ | 36 with MARC 561; 8 long; 20 multi-owner |
| Contributors (7/8) | ✗ `CONTRIB_ADDED_MEETING_711` | 67 corporate added entries; 24 with life-dates |
| Notes (7/8) | ✗ `NOTE_500_3_MATERIALS` | 68 have notes; 12 long; 5 with colophon keywords |
| Codicology (3/3) | ✓ | 62 with extent + dimensions; 23 with material |
| Misc (8/8) | ✓ | 11 URLs; 1 each of reproduction/history/action/series |
| Geography (3/4) | ✗ `GEO_COORDS_034` | 37 origin places; 1 publication place |
| Identifiers (1/1) | ✓ | 65 call numbers |

### Signals NOT covered (4)

These four are simply absent from both source corpora — not a sampling defect,
a data-availability fact:

| Signal | Why it isn't there |
|---|---|
| `ROLE_COMMENTATOR` | The 17th-century corpus has no `100$e`/`700$e` carrying "מפרש"/"פרשן"/"מבאר"/`commentator`. The role exists conceptually but isn't tagged in NLI's MARC for this sub-corpus. |
| `CONTRIB_ADDED_MEETING_711` | MARC 711 (meeting/conference added entry) is rare in Hebrew manuscript catalog records — almost never used outside printed conference proceedings. |
| `NOTE_500_3_MATERIALS` | The MARC 500 `$3` "materials specified" subfield is an OCLC-style codicological convention NLI doesn't apply on this sub-corpus. |
| `GEO_COORDS_034` | NLI doesn't record latitude/longitude coordinates in MARC 034 for manuscript records — only printed maps. |

If any of these become important later, the supplement corpus
`filtered_manuscripts_after_906a.tsv` should be extended with records that
satisfy them, then rerun the build script.

---

## 7. Determinism guarantees

The build is bit-reproducible:

1. **Source SHA-256 pinning** — manifest stores `source_corpus_sha256` and
   `supplement.source_sha256`. If either source file changes, rerunning the
   script writes a different output and the manifest reflects new SHAs.

2. **Fixed RNG seed** — stage 3's stratified sampler uses `--seed=42`. Same
   seed + same predicate code + same source TSVs → same 43 stage-3 records.

3. **Stable tie-breakers** — stages 1 and 2 break score ties by `hashlib.md5`
   of the record's `001` control number (not Python's runtime `hash()` which
   is randomised).

4. **Predicate version tag** — manifest stores
   `signal_predicate_versions: "v1"`. If any predicate changes semantics, bump
   this string and regenerate.

5. **Output SHA-256** — manifest stores `subset_sha256`. CI / verification
   harness compares this against the actual file on disk to detect tampering.

---

## 8. How to regenerate

```bash
PYTHONPATH=src:. .venv/bin/python scripts/build_test_subset.py --verbose
```

Verbose flag prints stage-by-stage selection counts and any zero-hit signals
that fell through to the supplement stage.

Custom sizing:

```bash
# Smaller subset (50 records, no supplement padding)
PYTHONPATH=src:. .venv/bin/python scripts/build_test_subset.py \
  --cap 50 --target-baseline 42

# Larger subset (100 records)
PYTHONPATH=src:. .venv/bin/python scripts/build_test_subset.py \
  --cap 100 --target-baseline 92
```

**Exit codes:**

| Code | Meaning |
|---:|---|
| 0 | Success |
| 2 | Source missing or empty |
| 3 | At least one signal had zero hits in both corpora (output still written unless `--dry-run`) |

---

## 9. Where the subset is consumed

| Consumer | How |
|---|---|
| [`paper/verification/verify_paper.py`](../paper/verification/verify_paper.py) | Reads `subset_sha256` from manifest, asserts on-disk file matches. Runs all paper-claim checks against the 68 records. |
| [`tests/integration/test_pipeline_e2e.py`](../tests/integration/test_pipeline_e2e.py) | Stage 1–6 happy-path tests use this file as the canonical small input. |
| [`paper/verification/CLAIMS.yaml`](../paper/verification/CLAIMS.yaml) | Every claim that contains a measured number ("14 validator rules", "37 LCSH headings", …) pins to this subset's SHA-256 so reviewers can replay locally. |
| [`paper/verification/DRIFT_LOG.md`](../paper/verification/DRIFT_LOG.md) | When a claim's measured value drifts, the entry records "subset version: 3f2cd2bb…" so historical numbers can be re-derived. |

---

## 10. Update protocol

When you need to update the subset (added a new signal, found a new
hard-to-cover MARC pattern, extended the source corpus):

1. Edit `_SIGNALS` and/or `_HARD_SIGNALS` in
   [`scripts/build_test_subset.py`](../scripts/build_test_subset.py).
2. Bump `signal_predicate_versions` if the change alters which records
   satisfy any existing signal.
3. Regenerate (`build_test_subset.py --verbose`).
4. Update the pinned SHA-256 in `paper/verification/CLAIMS.yaml`.
5. Run `paper/verification/verify_paper.py` and update any claim numbers
   that drifted in [`paper/verification/DRIFT_LOG.md`](../paper/verification/DRIFT_LOG.md).
6. Run `pytest tests/` — integration tests should still pass.

The subset is a working artefact. Don't be afraid to regenerate; just remember
to update the consumers' pinned SHA-256 in lockstep.

---

## Related documents

- [`paper/verification/README.md`](../paper/verification/README.md) — overall verification framework
- [`paper/verification/PROTOCOL.md`](../paper/verification/PROTOCOL.md) — claim → check protocol
- [`paper/verification/HOW_TO_RUN.md`](../paper/verification/HOW_TO_RUN.md) — running the verifier
- [`paper/verification/CLAIMS.yaml`](../paper/verification/CLAIMS.yaml) — every claim + pinned subset SHA
- [`paper/verification/DRIFT_LOG.md`](../paper/verification/DRIFT_LOG.md) — when numbers drifted and what was fixed
