"""Unit tests for :mod:`converter.wikidata.hebrew_translit` (Rule 46).

The module implements a three-tier waterfall:

1. Curated override dict for canonical Latin names of well-known figures.
2. NLI ALA-LC romanization read from the source record (MARC 246 / 880).
3. Deterministic ALA-LC-inspired character map.

These tests pin all three tiers, the empty-input contract, the mixed-script
contract, and the integration with the item_builder work-label path that was
the original motivation (replacing the synthetic
``"work from Hebrew manuscript <shelfmark>"`` placeholder).
"""

from __future__ import annotations

from converter.wikidata.hebrew_translit import (
    _algorithmic_transliterate,
    _norm_hebrew_key,
    english_label_for_hebrew,
)

# ── Tier 1: curated override dict ────────────────────────────────────


class TestCuratedOverrides:
    """Tier 1 — well-known figures resolve to canonical English labels."""

    def test_maimonides_full_patronymic(self):
        assert english_label_for_hebrew("משה בן מימון") == "Maimonides"

    def test_maimonides_acronym_rambam(self):
        # ``רמב״ם`` with gershayim ((U+05F4) and ASCII variants
        assert english_label_for_hebrew('רמב"ם') == "Maimonides"
        assert english_label_for_hebrew("רמב״ם") == "Maimonides"

    def test_rashi_acronym(self):
        assert english_label_for_hebrew("רש״י") == "Rashi"

    def test_rashi_full_patronymic(self):
        assert english_label_for_hebrew("שלמה בן יצחק") == "Rashi"

    def test_nahmanides(self):
        assert english_label_for_hebrew("רמב״ן") == "Nahmanides"

    def test_baal_shem_tov_full_and_acronym(self):
        assert english_label_for_hebrew("בעש״ט") == "Baal Shem Tov"
        assert english_label_for_hebrew("ישראל בעל שם טוב") == "Baal Shem Tov"

    def test_torah_and_talmud(self):
        assert english_label_for_hebrew("תורה") == "Torah"
        assert english_label_for_hebrew("תלמוד") == "Talmud"

    def test_overrides_strip_isbd_terminators(self):
        # MARC ISBD punctuation must not block the override match.
        assert english_label_for_hebrew("משה בן מימון.") == "Maimonides"
        assert english_label_for_hebrew("משה בן מימון, ") == "Maimonides"
        assert english_label_for_hebrew("רש״י :") == "Rashi"

    def test_overrides_collapse_internal_whitespace(self):
        # Two spaces between the same words must still hit the override.
        assert english_label_for_hebrew("משה  בן  מימון") == "Maimonides"


# ── Tier 2: NLI romanization read ────────────────────────────────────


class TestNliRomanizationRead:
    """Tier 2 — explicit ALA-LC romanization on the source record is preferred
    over Tier-3 algorithmic transliteration, but Tier 1 still wins."""

    def test_title_romanized_key_is_returned(self):
        rec = {"title_romanized": "Sefer ha-Zohar"}
        # Tier 1 cannot match because זוהר is not in the override dict in
        # this exact form (the override is the bare ``זהר``).
        assert english_label_for_hebrew("ספר הזוהר", rec) == "Sefer ha-Zohar"

    def test_marc_880_alt_script_field(self):
        rec = {"marc_880": "Perush ha-Torah"}
        assert english_label_for_hebrew("פירוש התורה", rec) == "Perush ha-Torah"

    def test_marc_246_variant_title(self):
        rec = {"marc_246": "Mishneh Torah"}
        assert english_label_for_hebrew("משנה תורה", rec) == "Mishneh Torah"

    def test_name_romanized_for_persons(self):
        rec = {"name_romanized": "Yosef ben Avraham"}
        assert english_label_for_hebrew("יוסף בן אברהם", rec) == "Yosef ben Avraham"

    def test_list_shape_uses_first_non_empty_entry(self):
        rec = {"title_romanized": ["", "Sefer Yetzirah"]}
        assert english_label_for_hebrew("ספר יצירה", rec) == "Sefer Yetzirah"

    def test_dict_shape_picks_en_or_latn_key(self):
        rec = {"title_romanized": {"en": "Sefer ha-Aggadah", "he": "ignored"}}
        assert english_label_for_hebrew("ספר האגדה", rec) == "Sefer ha-Aggadah"

    def test_pure_hebrew_in_romanization_field_is_rejected(self):
        # If the importer accidentally copied the Hebrew title into the
        # romanization field, Tier 2 must reject it (otherwise the function
        # would re-emit the same Hebrew string as if it were Latin).
        rec = {"title_romanized": "מחזור ויטרי"}
        result = english_label_for_hebrew("מחזור ויטרי", rec)
        assert result is not None
        # The result must be Latin script, not Hebrew.
        assert not any("֐" <= c <= "׿" for c in result)

    def test_overrides_beat_romanization(self):
        # Tier 1 must beat Tier 2 — even if the importer suggests a
        # different romanization, the curated canonical label wins.
        rec = {"title_romanized": "Moshe ben Maimon"}
        assert english_label_for_hebrew("משה בן מימון", rec) == "Maimonides"


# ── Tier 3: deterministic ALA-LC character map ────────────────────────


class TestAlgorithmicTransliteration:
    """Tier 3 — every Hebrew input produces a Latin-script output."""

    def test_ricardo_example_from_task_brief(self):
        # The task brief calls out this exact smoke test: it must produce
        # something Latin and capitalised, never None.
        result = english_label_for_hebrew("ריקרדו")
        assert result is not None
        assert result[0].isupper()
        assert not any("֐" <= c <= "׿" for c in result)

    def test_final_letters_map_correctly(self):
        # ך → kh, ם → m, ן → n, ף → f, ץ → ts
        assert _algorithmic_transliterate("ך") == "Kh"
        assert _algorithmic_transliterate("ם") == "M"
        assert _algorithmic_transliterate("ן") == "N"
        assert _algorithmic_transliterate("ף") == "F"
        assert _algorithmic_transliterate("ץ") == "Ts"

    def test_silent_letters_dropped(self):
        # Aleph and ayin map to empty strings in the consonantal table.
        # The full word should still produce a non-empty Latin output (case-
        # insensitive — the first alphabetic char gets capitalised).
        result = _algorithmic_transliterate("אבן").lower()
        assert "v" in result
        assert "n" in result

    def test_shin_to_sh(self):
        # ש must map to "sh", not "s".
        assert "sh" in _algorithmic_transliterate("שלום").lower()

    def test_tsadi_to_ts(self):
        assert "ts" in _algorithmic_transliterate("צבי").lower()

    def test_nikud_is_stripped_safely(self):
        # Vocalised input must not crash and must still produce Latin output.
        # ``בְּרֵאשִׁית`` (Genesis) carries five nikud marks.
        result = _algorithmic_transliterate("בְּרֵאשִׁית")
        assert result is not None
        assert len(result) > 0
        # Must produce only Latin/whitespace (no Hebrew letters or nikud).
        for c in result:
            assert not ("֐" <= c <= "׿"), c

    def test_returns_string_starting_capitalised(self):
        result = _algorithmic_transliterate("דניאל")
        assert result[0].isupper()


# ── Empty input and mixed-script handling ─────────────────────────────


class TestEdgeCases:
    """Empty and mixed-script inputs must not produce synthetic placeholders."""

    def test_empty_string_returns_none(self):
        assert english_label_for_hebrew("") is None

    def test_whitespace_only_returns_none(self):
        assert english_label_for_hebrew("    ") is None
        assert english_label_for_hebrew("\n\t") is None

    def test_non_string_returns_none(self):
        # type: ignore[arg-type]
        assert english_label_for_hebrew(None) is None  # type: ignore[arg-type]
        assert english_label_for_hebrew(123) is None  # type: ignore[arg-type]

    def test_latin_only_input_returns_none(self):
        # The function is the Hebrew-branch helper; if the caller already
        # has a Latin string they should not be calling us. Returning None
        # tells the caller "this is not my problem, keep your Latin string".
        assert english_label_for_hebrew("Bible") is None
        assert english_label_for_hebrew("Diodati Segre") is None

    def test_mixed_hebrew_and_latin_passes_through_latin_unchanged(self):
        # Mixed strings preserve the Latin embed and transliterate the
        # Hebrew portion. The Latin part is not destroyed by the table pass.
        result = english_label_for_hebrew("דניאל Daniel")
        assert result is not None
        assert "Daniel" in result


# ── Override-key normalisation contract ───────────────────────────────


class TestKeyNormalisation:
    """Verify the normalisation helper that powers Tier 1 dict lookup."""

    def test_gershayim_variants_collapse_to_same_key(self):
        # ASCII " and Unicode ״ must normalise to the same key.
        assert _norm_hebrew_key('רמב"ם') == _norm_hebrew_key("רמב״ם")

    def test_trailing_isbd_terminators_stripped(self):
        assert _norm_hebrew_key("רש״י.") == _norm_hebrew_key("רש״י")
        assert _norm_hebrew_key("רש״י :") == _norm_hebrew_key("רש״י")

    def test_internal_whitespace_collapsed(self):
        assert _norm_hebrew_key("משה  בן  מימון") == _norm_hebrew_key("משה בן מימון")


# ── Full-waterfall integration ────────────────────────────────────────


class TestWaterfallIntegration:
    """Tier order is Tier 1 > Tier 2 > Tier 3 and never returns ``None`` for
    real Hebrew input (only for empty / non-Hebrew input)."""

    def test_tier_1_beats_tier_3(self):
        # Without Tier 1, the algorithmic table would transliterate
        # ``משה בן מימון`` as something like ``Mshh vn miimon`` — definitely
        # not ``Maimonides``.
        assert english_label_for_hebrew("משה בן מימון") == "Maimonides"

    def test_tier_2_beats_tier_3(self):
        rec = {"title_romanized": "Sefer Sodot"}
        assert english_label_for_hebrew("ספר סודות", rec) == "Sefer Sodot"

    def test_tier_3_is_last_resort_and_never_returns_none_for_hebrew(self):
        # A made-up Hebrew word that is not in the override dict and has
        # no romanization in the record must still produce *something*.
        result = english_label_for_hebrew("גלרברגון")
        assert result is not None
        assert len(result) > 0

    def test_synthetic_placeholder_never_emitted(self):
        # The whole reason this module exists. The string "work from Hebrew
        # manuscript" must never appear in any output of this function.
        for hebrew_input in ("ספר התניא", "מורה נבוכים", "כוזרי"):
            result = english_label_for_hebrew(hebrew_input)
            assert result is None or "work from Hebrew manuscript" not in result


class TestTier4LatinOnlyOutput:
    """Rule 47: TaatikNet must never leak Hebrew script into the en label.

    Three failure modes have been observed on the production corpus:
    pure Hebrew echo, partial echo mixed with Latin words, and gibberish
    NER fragments. All three must collapse to ``None`` so the caller
    falls back to ``NLI <control_number>`` alone.
    """

    def test_taatiknet_pure_hebrew_echo_returns_none(self, monkeypatch):
        import converter.wikidata.taatiknet_translit as tt

        monkeypatch.setattr(tt, "_TAATIKNET", (None, None, None))
        # Model echoes the input unchanged — out-of-distribution case
        monkeypatch.setattr(tt, "_translit_single_word", lambda w: w)
        assert tt.transliterate_hebrew_to_latin("מא") is None

    def test_taatiknet_mixed_output_returns_none(self, monkeypatch):
        import converter.wikidata.taatiknet_translit as tt

        monkeypatch.setattr(tt, "_TAATIKNET", (None, None, None))
        # First word echoes (Hebrew); second word transliterates cleanly
        def per_word(w: str) -> str:
            return "ktovim" if w == "ktovim_hebrew" else w
        # "מא ktovim" — first echoes back as Hebrew, second succeeds.
        # The all-or-nothing rule should still collapse this to None.
        def real_per_word(w: str) -> str | None:
            if w == "מא":
                return "מא"  # echo — has Hebrew
            return "ktovim"  # clean Latin
        monkeypatch.setattr(tt, "_translit_single_word", real_per_word)
        assert tt.transliterate_hebrew_to_latin("מא כתבים") is None

    def test_taatiknet_clean_output_is_returned(self, monkeypatch):
        import converter.wikidata.taatiknet_translit as tt

        monkeypatch.setattr(tt, "_TAATIKNET", (None, None, None))
        # Every word transliterates cleanly to Latin
        monkeypatch.setattr(
            tt, "_translit_single_word",
            lambda w: {"תקנות": "takanut", "רבנו": "rivno"}.get(w, "x"),
        )
        result = tt.transliterate_hebrew_to_latin("תקנות רבנו")
        assert result is not None
        assert "ת" not in result and "ק" not in result  # no Hebrew script
        assert result == "Takanut rivno"

    def test_waterfall_defensive_latin_only_guard(self, monkeypatch):
        """Even if a future engine swap regresses on the Latin-only
        invariant, the orchestrator's defensive guard must catch it."""
        import converter.wikidata.taatiknet_translit as tt

        # Pretend Tier 4 returned Hebrew (regressed engine)
        monkeypatch.setattr(
            tt, "best_effort_vocalized_transliterate",
            lambda text: "מא partial",
        )
        # Tier 5 disabled (work-label callsite contract)
        result = english_label_for_hebrew(
            "מא", allow_algorithmic=False,
        )
        assert result is None
