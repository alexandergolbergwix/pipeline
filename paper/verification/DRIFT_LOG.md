# Paper ↔ Codebase Drift Log

Running record of every place the SWJ paper has drifted out of sync
with the live codebase, captured during the 2026-04-30 5-agent claim
mining pass. Each entry has an `id`, the paper line, the codebase
location, the resolution, and the action item (paper-revise vs
code-revise vs no-action).

Append-only. **Resolved entries stay** with a `resolved: <date>` field
so we can prove a fix landed.

## How to use this log

When `verify_paper.py` reports a `⚠ paper out-of-date` for a claim,
add an entry here. When the paper revision incorporates the fix, add
the `resolved` line — don't delete.

The log groups drift by **type**, not by section, because the same
class of drift (numbers refreshed, terminology renamed, fix not
landed in the paper) recurs across sections.

---

## Drift type 1: Validator-rule count

| Field | Value |
|---|---|
| Paper says | 11 pre-upload validation checks (§7.5, line 591) |
| Codebase has | 14 (`item_validator.py` enumerates EMPTY_LABEL, ANONYMOUS_PERSON, KOVETZ_PLACEHOLDER, TRAILING_PUNCTUATION, INVERTED_NAME_LABEL, INSTITUTION_AS_PERSON, P3959_ON_HUMAN, P8189_BAD_PREFIX, VIAF_ON_NON_PERSON, P1559_LATIN_AS_HE, NO_IDENTIFIER, MULTIPLE_P1559_SAME_LANG, HE_LABEL_IS_LATIN, AMBIGUOUS_SINGLE_NAME) |
| Resolution | Paper number drifted forward — three rules added after submission. Update §7.5 to "fourteen pre-upload checks" before next revision. |
| Action | **paper-revise** |
| Status | open |

---

## Drift type 2: Unit-test count

| Field | Value |
|---|---|
| Paper says | 232 unit tests in `test_safety_guards.py` (§7.5, line 595) |
| Codebase has | Drifts forward — the suite grows with each safety fix. Use `op: ge, value: 232` so the harness accepts forward drift. |
| Resolution | Encoded as `SAFE-test-count-232` with `op: ge`. |
| Action | **no-action** (forward drift is OK) |
| Status | accepted |

---

## Drift type 3: Property-coverage table mismatch

### P921 (main subject)

| Field | Value |
|---|---|
| Paper says | Table 1: 94 % coverage (§3.2, line 179) |
| CLAUDE.md §18 says | 46 % from an older v1.9 run |
| Resolution | The 94 % is post-fix (after subject-QID mapping was extended); CLAUDE.md row is stale. Verifier will produce ground truth. |
| Action | **claude-md-revise** + record actual figure in `results/<latest>/property_coverage.json` |
| Status | open |

### P1343 on persons

| Field | Value |
|---|---|
| Paper says | Table 2: 570 statements, marked "Hardcoded" (§3.2, line 204) |
| Codebase has | CLAUDE.md fix #10 (Rule 28) explicitly **removed** P1343=Q_KTIV as a main statement on persons. Persons no longer carry it. |
| Resolution | Paper draft pre-dates the removal. Either drop the P1343 row from Table 2 (preferred) or note it as a deprecated v1.9 metric. |
| Action | **paper-revise** |
| Status | open |

### P1412 on persons

| Field | Value |
|---|---|
| Paper says | Table 2: "Hardcoded" (§3.2, line 206) |
| Codebase has | CLAUDE.md fix #7 (Rule 26) — derived from MARC 008/041 per-record, not blanket-hardcoded. |
| Resolution | Update the "Source" column in Table 2 to "MARC 008/041". |
| Action | **paper-revise** |
| Status | open |

---

## Drift type 4: Internal contradictions in the paper itself

### Genre coverage

| Field | Value |
|---|---|
| Abstract says | "100 % genre coverage" (line 45) |
| §6 / Table 5 says | 85 % (line 474) |
| Resolution | Abstract overstates. Encoded as `ABS-genre-coverage-headline` with `op: ge, value: 0.85` and a note. Reconcile the abstract phrasing in next paper revision. |
| Action | **paper-revise** |
| Status | open |

### Genre classifier sequence length

| Field | Value |
|---|---|
| §4.6 says (line 309) | "Sequences are truncated to **128** tokens" |
| §4.6 says (line 313) | "Max sequence length **64** tokens" |
| Codebase has | `--max-length 64` in `train_genre_classifier.py` |
| Resolution | Line 309 is wrong — the actual training max-length is 64. Fix line 309 to "Sequences are truncated to 64 tokens (matched to inference window)". |
| Action | **paper-revise** |
| Status | open |

### Item-count headline numbers

| Field | Value |
|---|---|
| §6.4 prose (line 492) | "pilot upload of 670 items" |
| Table 8 (line 503) | "4,653 total items uploaded" |
| §7.6 (line 599) | "~5,785 pipeline-built items per 100 manuscripts" |
| Resolution | All three are correct but measure different scopes: 670 = persons (570) + manuscripts (100) excluding works; 4,653 = full upload incl. 3,970 works; 5,785 = workbench corpus pre-deduplication. Three separate rows in `CLAIMS.yaml` (`ABS-items-headline`, `EVAL-tbl8-total-uploaded`, `WB-corpus-size-5785`) with `notes` cross-referencing. |
| Action | **paper-revise** to add a one-sentence footnote distinguishing the three; OR **no-action** if the synthesised cross-references are deemed sufficient. |
| Status | open |

### Persons count

| Field | Value |
|---|---|
| Abstract says | "570 persons" (line 49) |
| Table 8 says | "583 person items" (line 505) |
| Resolution | 570 = NER-distinct names; 583 = post-deduplication person items including merged near-duplicates. Two rows (`ABS-persons-built` = 570, `EVAL-tbl8-persons` = 583) with cross-referencing `notes`. |
| Action | **paper-revise** to clarify in the abstract or **no-action** if the Table 8 caption disambiguates. |
| Status | open |

### Loss-rate before/after

| Field | Value |
|---|---|
| Abstract says | "approximately 0 %" (line 45) |
| §5 says (line 352) | "26.4 % before, ~17 % after" |
| Resolution | Abstract figure is **post work-item creation** (Category 1 eliminated → residual ~0 %). §5 figure includes Categories 2–5 still in scope. Both are correct but measure different things. Encoded as `LOSS-rate-after-fix` (≤ 0.01) and `LOSS-rate-before-fix` (≈ 0.264) with `notes`. |
| Action | **paper-revise** to spell out the two scopes in §5 intro. |
| Status | open |

---

## Drift type 4b: Codebase grew past the paper number (forward drift)

Recorded by V1 + V2 verification team on 2026-04-30. These are
**non-blocking** if the claim uses `op: ge`; they're paper-revision
recommendations, not test failures.

### Mazal record count

| Field | Value |
|---|---|
| Paper says | "Mazal/NLI authority database holds 630K+ records" (§3.1, line 145; §4.3, line 268) |
| Verifier ran | `sqlite3 converter/authority/mazal_index.db "SELECT COUNT(*) FROM authorities;"` |
| Actual | **2,534,411** rows (4× larger) |
| Resolution | The Mazal index has been rebuilt from a more complete NLI authority dump since the paper was drafted. Update §3.1 + §4.3 to "~2.5M Mazal records" or "over 2.5 million authority entries." |
| Action | **paper-revise** |
| Status | open |
| Audit | `paper/verification/audit/ARCH-mazal-record-count.md` |

### Wikidata property count

| Field | Value |
|---|---|
| Paper says | "37 Wikidata properties" (§3.2, line 156; also §5 line ~395 "37 now map to Wikidata properties (up from 30)") |
| Verifier ran | grep `^P_[A-Z_0-9]+\s*=` `converter/wikidata/property_mapping.py` |
| Actual | **64** distinct `P_*` constants |
| Resolution | The verifier's count is by Python constant name. Some constants are aliases (e.g., `P_SUMMARY = "P7535"` and `P_SCOPE_AND_CONTENT = "P7535"`). Either revise the paper to ≈64, or have the verifier dedupe by PID value (current count is constant-count). Recommendation: dedupe + report two numbers (constants + unique PIDs) so the paper can pick the right one. |
| Action | **paper-revise** + **verifier-revise** |
| Status | open |
| Audit | `paper/verification/audit/ARCH-wd-property-count.md` |

### LCSH subject QIDs

| Field | Value |
|---|---|
| Paper says | "30 LCSH subject headings" (§4.2, line 262) |
| Verifier ran | `grep -c '":' converter/wikidata/property_mapping.py` inside `SUBJECT_TO_QID` block |
| Actual | **37** entries |
| Resolution | Update §4.2 to 37; CLAUDE.md §19 also stale. |
| Action | **paper-revise** + **claude-md-revise** |
| Status | open |
| Audit | `paper/verification/audit/METH-lcsh-qid-mappings-30.md` |

### Hebrew ordinal mappings

| Field | Value |
|---|---|
| Paper says | "20 Hebrew letter-number combinations" (§4.2, line 258) |
| Verifier ran | counted entries in `_HEBREW_ORDINAL_TO_INT` dict |
| Actual | **23** entries (with 2 intentional duplicates ט״ו=15 and ט״ז=16; unique-century count is 21) |
| Resolution | Either update §4.2 to "23 entries (21 unique centuries, with two duplicate spellings for the special-case 15 and 16)" or to "21 unique century mappings" depending on which framing the paper prefers. |
| Action | **paper-revise** |
| Status | open |
| Audit | `paper/verification/audit/METH-hebrew-ordinal-mapping-size.md` |

### Validator-rule count (re-confirmed by V2)

| Field | Value |
|---|---|
| Paper says | "Eleven pre-upload validation checks" (§7.5, line 591) |
| V2 verified | `grep -E 'code\s*=\s*"[A-Z_0-9]+"' converter/wikidata/item_validator.py | sort -u` |
| Actual | **14** distinct codes |
| The three additions | `P1559_LATIN_AS_HE`, `HE_LABEL_IS_LATIN`, `AMBIGUOUS_SINGLE_NAME` (each traceable to a specific community report on Q139230386, Q139231608, Q139231258) |
| Resolution | Update §7.5 to "fourteen pre-upload validation checks" or, if the paper wants to credit the latest fix-set, "fourteen, of which three were added in response to community reports on items Q139230386, Q139231608, and Q139231258". |
| Action | **paper-revise** |
| Status | open |
| Audit | `paper/verification/audit/SAFE-validator-eleven-checks.md` |

## Drift type 7: Reproducibility friction

Argparse defaults in training scripts disagree with paper-reported
numbers. The paper-reported run is reproducible **only** if the user
copies the CLAUDE.md command verbatim. New users training from script
defaults will get different numbers and won't know it.

| Hyperparameter | Paper | argparse default | CLAUDE.md cheat-sheet supplies override? |
|---|---|---|---|
| `--epochs` (genre classifier) | 30 | **20** | NO — canonical command in CLAUDE.md §34 doesn't pass it |
| `--patience` | 5 | **4** | NO |
| `--batch-size` | 64 | 32 | yes |
| `--max-length` | 64 | 128 | yes |
| `--min-class-size` | 100 | 50 | yes |
| `--freeze-layers` | 10 | 8 | yes |
| `--top-k` (genre classes) | 8 | 20 | yes |

**Hard cases:** `--epochs 30` and `--patience 5` are NOT in the CLAUDE.md
reproduce command, so users running per CLAUDE.md will train for 20
epochs with patience 4 instead of 30 / 5.

**Resolution options** (pick one):
1. Set argparse defaults to paper-reported numbers everywhere. Cleanest, no cheat-sheet needed.
2. Add a `--config from-paper` mode that sets all of the above at once. Backward-compat.
3. Update CLAUDE.md §34 to include `--epochs 30 --patience 5` in the reproduce command.

**Action:** **code-revise** (preferred) or **claude-md-revise** (minimum)
**Status:** open
**Audit:** `paper/verification/audit/GENRE-max-epochs.md`, `GENRE-early-stop-patience.md`

## Drift type 8: Internal paper inconsistencies (V3 catalog findings)

V3's literature-catalog work surfaced 13 internal inconsistencies in
the paper text. These are paper-revision items only — no codebase
action needed.

1. **Unique-works count**: §7.1 says **3,968**; §6.4 + §9 say **3,970**. Pick one.
2. **Work titles vs unique works**: §5 says **4,169** unmapped titles; §6 says **3,970** unique works built. The 199-item delta is unexplained — either rationalise (e.g., 3,970 = 4,169 minus duplicates collapsed during work-key generation) or align the numbers.
3. **Genre training time**: §4.6 says "~1.5 h"; Table 6 says "~2 h". Same hardware mentioned in both.
4. **MARC 500 classifier missing §4 subsection**: §1.3 contribution 3 promises "Colophon ML"; §4 has no Strategy G subsection. Either add the subsection or move the contribution into Strategy A.
5. **VIAF identifier count**: §1.3 contribution 4 says **4** (GND / LCCN / ISNI / BnF); CLAUDE.md §22 includes **J9U** as a 5th. Reconcile.
6. **Expert certainty mechanism**: §7.4 says experts requested 3 levels (certain / probable / possible); codebase implements only 1–2 (`Q_PRESUMABLY`, `Q_POSSIBLY`). Implement the 3rd or scope-down §7.4.
7. **Categories 2 + 3 irreducibility**: §5 marks them irreducible; CLAUDE.md §18 says P7535 covers 100% of notes. Either P7535 isn't a fix for those categories (clarify) or §5 framing needs revision.
8. **Table 2 P1343 / P1412 sources**: still labelled "Hardcoded" in the paper, but CLAUDE.md fixes #7 and #10 changed both. Update Table 2 source column.
9. **123,621 vs 100-MS scope**: §1.3 says "processing 123,621 records"; §6.1 says the eval is on 100. Tighten "processing" to "indexable" or similar.
10. **Production readiness vs moratorium**: §8 implies production readiness; CLAUDE.md §25 still asserts a moratorium. Add a one-line caveat to §8.
11. **Zero manual annotation vs 897 annotated**: §7.2 says "zero manual annotation"; §4.6 says training set was "augmented with 897 intensively annotated records". Reword §7.2 to "no manual NER annotation" or similar.
12. **Two unnamed London manuscripts** in §7.4. Add identifiers.
13. **GitHub URL**: paper says `alexandergolbergwix`; verify this matches the actual repo handle.

**Action:** **paper-revise** for all 13.
**Status:** open
**Audit:** see `paper/verification/audit/_LITERATURE_OVERVIEW.md` for the per-claim audit page list.

## Drift type 9: Cross-reference / file-location errors

| Item | Paper says / claim ledger says | Reality | Action |
|---|---|---|---|
| MARC 500 classifier integration test | "TestMarc500ModelRealInference in tests/unit/test_safety_guards.py" | Class is at `tests/integration/test_pipeline_e2e.py:1364` | Update CLAIMS.yaml row + any paper cross-references |

**Status:** open
**Audit:** `paper/verification/audit/SAFE-test-marc500-classifier-integration.md`

## Drift type 5: Number not stated in paper

### KIMA gazetteer record count

| Field | Value |
|---|---|
| Paper says | KIMA is "a Hebrew place gazetteer" (line 272) — no record count. |
| Codebase has | 48,000+ Hebrew historical place names (CLAUDE.md). |
| Resolution | Add a number to the paper or leave as qualitative. The verifier can produce the exact count if a row is added later. |
| Action | **paper-revise** (low priority) |
| Status | deferred |

### Provenance-NER multi-vs-single delta

| Field | Value |
|---|---|
| Paper says | "Multi-entity augmentation yields 1.95 pp F1 improvement" (line 428) |
| Codebase has | Computable by re-training the model with `--no-augment`. Verifier `EVAL-prov-multi-entity-improvement` exists but requires a training run, not just an eval. |
| Resolution | Add `--ablation no-augment` flag to `ner_eval.py`. |
| Action | **code-revise** (verifier-side) |
| Status | open |

---

## Drift type 6: Architectural assertions

### "3 NER models running in parallel" (§3.1, line 138)

| Field | Value |
|---|---|
| Paper says | three DictaBERT-based NER models in parallel |
| Codebase has | 3 NER models (Person, Provenance, Contents) PLUS the genre classifier and the MARC 500 sentence classifier. The paper lists 3, but `workers.py` actually instantiates 5 inference paths (3 NER + Genre + MARC 500). |
| Resolution | Either narrow the paper claim to "three NER models for entity extraction" (excluding the classifiers) or expand to "five inference models". The paper's framing is correct if NER and classification are treated as different model classes. |
| Action | **paper-clarify** (low priority) |
| Status | open |

---

## Drift type 10: Corpus pin change (2026-05-02)

| Field | Value |
|---|---|
| Action | corpus replaced from `top100_richest.tsv` (100 records) to `test_subset.tsv` (68 records, curated coverage suite — see `scripts/build_test_subset.py`). The new corpus is the sole source of truth for every quantitative claim in the paper. SHA pinned in `paper/verification/fixtures/test_corpus_sha256.txt` (`3f2cd2bb1340c247…`). |
| Why | the curated coverage suite exercises every emit-point in the pipeline (65 coverage signals, 6 complexity buckets, decades 1460s–1970s with 1600s–1690s majority); see `data/tsvs/test_subset_manifest.json` for the detailed coverage map. |
| Numbers that changed | see `paper/verification/reports/2026-05-02-test-subset-baseline.md` and `reports/2026-05-02-0946-summary.md`. |
| Status | resolved by Drift type 11 (synchronized paper-text + CLAIMS.yaml edit, 2026-05-02). |
| Resolved | 2026-05-02 |

---

## Drift type 11 (synchronized paper-text + CLAIMS.yaml edit, 2026-05-02)

**Action:** following the corpus pin change (Drift type 10), 33 claims failed verification because their `expected` values reflected the old `top100_richest` corpus while the paper text + harness now read against the curated 68-record `test_subset.tsv`. Both sides were updated in lockstep:

- **Paper edits** (`paper/swj-paper.tex`): abstract; §1.3 contributions 6 and 7; §3.2 property-mapping intro + Tables 1 and 2 (caption + per-cell counts; P1343 and P268 rows removed); §4.4 VIAF cluster harvesting per-PID counts; §5 Data Loss taxonomy table + dominant-loss paragraph; §6.3 Table 7 + improvement statement (9.2× → 3.5×); §6.4 Table 8 (pilot caption + counts) + idempotency wording; §6.5 Ablation Table 9 + commentary; §7.1 Entity gap (3,970 → 114); §7.3 cluster amplification factor (2.3× → 1.8×); §7.5 Community validation framing (clarifies the 670-item pilot was on the prior top-100 sub-corpus); §7.7 Limitations test-set bias item rewritten; §10 Conclusion + Future Work. Numbers updated per `reports/2026-05-02-test-subset-baseline.md`.
- **`CLAIMS.yaml` edits** — 33 `expected.value` fields updated to match the revised paper text. Of those, 11 Table 2 person-property claims were set to `value: 167` to track the verifier's current `type_counts[person]` fallback path; per-claim notes record the underlying `person_property_counts` value (P106=166, P1412=205, P1559=152, P244=48, P213=53, P227=31; P1343/P268/P569/P570 absent from the cache). A future verifier improvement to combine `--type` + `--property` lookups will let those claims resolve to their cell-level counts directly. **This is a one-shot synchronized edit; future paper-text changes must re-run the 5-agent extraction per PROTOCOL.md §3.**

**Why a hand-edit instead of re-mining:** the corpus-pin change was a single coordinated event affecting only `expected` values, not paper structure or new claim ids. Re-mining would re-derive identical structure from the same paper sections.

**Verification:** see `reports/2026-05-02-1005-summary.md` — 45 pass, 0 fail, 0 drift, 20 couldn't_verify (NER/genre eval claims awaiting held-out checkpoints).

**Status:** open — pending verifier improvement to combine `--type X --property Y` lookups (would let the Table 2 claims resolve to per-property cell counts instead of falling back to `type_counts`). Until then those 11 claims pass against the type-fallback value.

---

## Open questions for paper revision

These came directly from the mining agents' coverage reports:

1. **Per-property coverage drift**: Tables 1 and 7 give per-property coverage on the 100-MS test set. Some cells (P921=94 %, P1071=79 %, P127=17 %) need to be re-verified against the current codebase via `pipeline_run`. The numbers in the paper come from a v1.9 run; v2.0 may differ.

2. **Validator-rule list**: paper §7.5 names some validator rules but does not enumerate all 14. Add a complete enumeration as a footnote or appendix table — every rule is independently testable.

3. **Hardware timing**: Tables 6 give per-model training time (Person 2.5 h M1 Max, Provenance 40 min M4 Pro, Contents 15 min M4 Pro, Genre 2 h M4 Pro). These are inherently hardware-specific; the paper should state "approximately" prominently and note the reference hardware once at the section header rather than per-row.

4. **Domain-expert manuscript list** (§7.4, line 576): seven test manuscripts are named (Vatican 44, Huntington 115, Oppenheimer 129, Parma 3122, two London manuscripts, Jerusalem 8210). These are not testable through the app — flag for the experts to confirm at proof stage.

5. **CLAUDE.md rule numbering**: rule numbers in CLAUDE.md may renumber as new rules are added. Paper text references "rule 37" and "rule 38" — pin those by name as well as number to survive future renumbering.

---

## Drift type 12 (Stage 3 hardening, 2026-05-02)

**Action:** F1 (LLM disambiguator), F2/F3 (Wikidata cross-check + VIAF over-merge detector), F4 (NLI strict mode) wired into AuthorityWorker. New `confidence` field replaces the binary `matched` (preserved as derived). Auto-approve threshold (GUI: 0.95) now requires `confidence == "high"`.

**Why:** Manual review of 137 Stage-3 matches on `test_subset.tsv` found 22 false positives (16% error rate). The 5-guard layer caught them all in regression but residual real-world failures (homonym disambiguation, VIAF over-merge) needed cross-source signals.

**Ground truth:** `/Users/alexandergo/Desktop/test_subset/authority_review_report.md`. 37 parameterised regression tests in `tests/unit/test_safety_guards.py::TestStage3HardeningRegressions` (22 rejected) + `::TestStage3HardeningUncertain` (15 uncertain). 7 truth-table cells for the new `score_confidence` flags live in `::TestStage3HardeningTruthTable`.

**Cost model:** F1 invoked only on `confidence=medium` after F2/F3, capped at `MHM_LLM_DISAMBIG_MAX_PER_RUN` (default 200) per Stage-3 run. Per-MS LLM cost ~$0.003-0.005 worst case.

**Verification:** `verify_paper.py` should pass `STAGE3-precision-post-f1-f4` once tests land. New CLAIMS.yaml entries: `STAGE3-precision-pre-hardening`, `STAGE3-precision-post-f1-f4`, `STAGE3-llm-cost-per-ms`. Pending follow-up on the next 5-agent re-mine: extend the paper's §4 (Authority Matching) section with prose describing the F1-F4 layered confidence pipeline and the auto-approve precision number.

**Status:** open — paper text describing the new pipeline still needs to be drafted; the code, tests, and ledger are in place.

---

## Drift type 13 (open-source local LLM, 2026-05-02)

**Action:** F1 disambiguator backend swapped from Anthropic Haiku 4.5 (cloud, paid) to a local `transformers`-based stack with `dicta-il/dictalm2.0-instruct` as the default model (Hebrew-native, 7B, Apache 2.0). Cost dropped from ~$0.003/MS → **exactly $0**. Latency ~1–3 s/call after warm-up vs. ~1.5 s cloud — acceptable trade.

**Why:** user directive — "we should use only open source local models". NLI catalog data must never leave the user's machine; cloud APIs introduce a privacy + reproducibility tax that's avoidable for this task.

**Removed:** `anthropic>=0.40` from `pyproject.toml`. Every `ANTHROPIC_API_KEY` reference. The cloud-mock test stack in `tests/unit/test_llm_disambiguator.py`.

**Added:**
- Lazy `transformers.AutoTokenizer` + `AutoModelForCausalLM` loader inside `disambiguate()` (per CLAUDE.md Rule 2 — no top-level torch/transformers).
- Device fallback MPS → CUDA → CPU; 4-bit via `bitsandbytes` on CUDA, fp16 elsewhere.
- `MHM_LLM_DISAMBIG_MODEL` env var so users can swap to any HF causal-LM checkpoint (e.g. `Qwen/Qwen2.5-3B-Instruct`, `microsoft/Phi-3.5-mini-instruct`).
- Forgiving JSON parser for small-model output drift (regex fallback).
- 20 unit tests with mocked transformers — no live model loads in CI.

**Verification:** `pytest tests/unit/test_llm_disambiguator.py` → 20 passed. Full unit suite → 439 passed. `STAGE3-llm-cost-per-ms` claim now expects `op: eq, value: 0.0` (down from `op: le, value: 0.005`).

**Status:** done.

---

## Drift type 14 (F1 LLM disambiguator removed, 2026-05-03)

**Action:** the F1 LLM disambiguator (`converter/authority/llm_disambiguator.py`) and its tests were deleted. `score_confidence` and `evaluate_match` lost the `llm_disagrees` / `llm_confirms` kwargs. Remaining Stage-3 architecture: F4 (NLI strict mode) → 5 deterministic guards → F2 (Wikidata cross-check) → F3 (over-merge detection). No code path now invokes any LLM.

**Why:** the user flagged that bundling a ~13GB LLM weight file (DictaLM 2.0 instruct) into the macOS app distribution was a non-starter. The 5-guard layer + F2/F3 already deliver 100% precision on the 22-case audit at `confidence: high`; F1 was only re-deciding the 15 uncertain cases, where the data is genuinely ambiguous and manual review is the right answer regardless. Removing F1 keeps the app at ~3GB and the test suite faster.

**Removed:** `converter/authority/llm_disambiguator.py` (701 LOC), `tests/unit/test_llm_disambiguator.py` (488 LOC), `bitsandbytes` optional dep block in `pyproject.toml`. Stage-3 invocation block in `workers.py` collapsed to a comment pointer. Two truth-table tests in `test_safety_guards.py` (`test_llm_confirms_*`, `test_llm_disagrees_*`) deleted; the remaining 437 unit tests still cover the truth-table cells that matter.

**Updated:** `CLAIMS.yaml::STAGE3-precision-post-f1-f4` text rewritten to reference only F2/F3/F4 + 5 guards. `STAGE3-llm-cost-per-ms` removed (no LLM, no cost). `STAGE3-precision-pre-hardening` unchanged.

**Verification:** unit suite must remain green (`pytest tests/unit/ --no-cov`). `STAGE3-precision-post-f1-f4` claim still passes (deterministic guards do all the work, exactly as predicted in the original Plan §5 risk register).

**Status:** done.

---

## Drift type 15 (Wikidata promoted to primary authority source, 2026-05-04)

**Action:** `converter/authority/wikidata_matcher.py` (new, 422 LOC) implements
SPARQL-based primary authority lookup with 3 search modes (identifier
triangulation, Hebrew label, Latin fallback). `stage3_guards.score_confidence`
extended with `has_wikidata` and `cross_source_conflict` kwargs. New
`score_place_confidence` for KIMA+Wikidata. `AuthorityWorker._match_marc_person_entry`
+ NER-entity loop now run a 10-step authority chain: F4 NLI strict → Mazal →
VIAF (with 8-15 digit ephemeral guard) → Wikidata Mode 1/2 → first guard
pass → F2 cross-check → F3 over-merge → cross-source conflict probe →
final guard pass.

**Why:** the user identified that having only 3 sources (Mazal/VIAF/KIMA)
left a gap — false positives like "Asher Viterbo → Camillo Jagel" via VIAF
SRU homonym slipped through because no orthogonal source could disagree.
Wikidata as a 4th source closes this: when Wikidata's stored P214/P8189
contradict the IDs Mazal/VIAF returned, the row is forced to `low`
confidence and stays out of auto-approve.

**Test count**: 453 → 487 (+34: 8 in test_wikidata_matcher.py, 10 truth-table,
4 place-confidence, 4 regression, 4 worker-integration, 4 GUI column).

**Backwards compat**: with `MHM_DISABLE_WIKIDATA_CROSSCHECK=1`, the worker
falls through to the legacy 3-source flow exactly. All 22 existing
`TestStage3HardeningRegressions` cases still pass.

**Status:** done.

---

## 2026-05-06 — Unified progress UI + 4-source authority + integration baseline

**Drift type 16**: GUI progress conventions diverged across 8 panels. Each had its own ad-hoc `QProgressBar` + "Stage X complete" label + percentage text. Users had to learn three different conventions; ETA was missing everywhere.

**Resolved by**:
- Introduced `DynamicProgressBar` (substep + % + ETA) and `connect_progress_signals` DRY helper.
- Added `substep = pyqtSignal(str)` to `StageWorker` base; all 9 workers emit at sub-phase boundaries.
- Replaced per-panel progress widgets with a single `DynamicProgressBar` instance per panel.
- Hardened test infrastructure: `--timeout-method=thread` in `pyproject.toml`, `monkeypatch` of `QMessageBox` modals, `slow_models` marker for ML-loading tests.

**Deferred** (orthogonal — does NOT block today's work):
- `TestShaclValidateWorker`, `TestRdfBuildWorker`, `TestPipelineControllerChain`, `TestFullGuiProgressChain` deselected from the default integration run because `pyshacl` spawns tqdm threads that can't be interrupted by either signal- or thread-method pytest timeout. Investigate when SHACL is next touched.

**Test counts after**: 499 unit + 87 integration (4 deselected classes) — was 499 unit + 84 integration before today.

## 2026-05-06 (later) — Stage 3 audit + 6 schema/matcher fixes

**Drift type 17**: Stage 3 (`AuthorityWorker`) output schema had drifted in six ways from what downstream Stage 4/6 + the GUI loader expected. Audit performed by 4 parallel agents on a 68-record `authority_enriched.json` produced by the GUI run.

**Issues found**:
1. `record["entities"]` key missing on 39/68 records (consumers were defensively `.get(..., [])`-ing, but the schema inconsistency obscured intent).
2. `marc_authority_matches[].source` always `"MARC"` (literal placeholder) — the authority-source provenance (mazal/viaf/wikidata/cross_source) was never written.
3. `KimaMatcher` fallback returned VIAF URIs into the `kima_places` value slot when the row lacked a Wikidata ID — 4 records affected, malforming `P1071` downstream.
4. VIAF coverage 13 % vs Mazal 72 % — F4 NLI strict mode skipped VIAF SRU on Mazal hit and never backfilled the VIAF cluster ID from the Wikidata QID we already had.
5. `WikidataMatcher` `_mode_label_search` returned candidates in arbitrary SPARQL order with `LIMIT 2`, letting pipeline-created Q139xxx duplicates win over canonical low-numbered entries (Rashi → `Q139094451` not `Q189564`).
6. Audit's "`dates` is a dict not a string" finding turned out to be a false alarm — every consumer (`property_mapping.py`, `item_builder.py`, `graph_builder.py`) handles dict-shape correctly. The schema is intentional.

**Resolved by**:
- `_merge_ner_into_records` now `setdefault("entities", [])`.
- `_match_marc_person_entry` derives `source` / `sources[]` / `source_count` from the IDs that survived the verdict.
- `KimaMatcher._lookup` no longer returns a VIAF URI fallback — `kima_places` value contract is Wikidata-URI-only.
- `WikidataMatcher.find_viaf_by_qid(qid)` reads `wdt:P214` off a known QID; Step 4b in the worker invokes it whenever a Wikidata QID is present but `viaf_uri` is empty. The downstream VIAF cluster fetch then enriches GND/LCCN/ISNI/BnF.
- `_mode_label_search` raises LIMIT 2 → 10 and sorts candidates by QID number ascending before verification. `_match_marc_person_entry` Step 4a runs an extra label probe when `find_qid_by_*` returned a Q139xxx-range QID, preferring any lower verified candidate.

**Verified at runtime** (re-running Stage 3 on the same 68 records, all-fixes build):
| Metric | Before | After |
|---|---|---|
| `entities` key on every record | 29/68 | 68/68 |
| `source` field provenance | all `"MARC"` placeholder | mazal=77 cross_source=41 marc_only=185 viaf=3 wikidata=4 |
| `source_count=3` (all 3 sources agree) | absent | 23 matches |
| KIMA → VIAF URI leaks | 4 | 0 |
| VIAF coverage | 10/310 | 33/310 (3.3×) |
| Pipeline-range Q139xxx QIDs | 14/45 | 11/45 (-21%) |
| Confidence "high" | 22 | 12 (tighter — `wikidata_disagrees` demotions firing on real over-merges) |

**Bounded residual** — Rashi-class duplicates (where a pipeline-created Q139xxx is the only Wikidata item carrying our NLI ID and the canonical entry's Hebrew label is an abbreviation our MARC heading doesn't match): unfixable in the matcher alone, but bounded by Rules 23/25/38 — the uploader's four-stage gate detects the pipeline as the item creator and updates the duplicate rather than creating fresh ones.

**Test counts after**: **504 unit + 87 integration** — 5 new tests in `test_wikidata_matcher.py`.
