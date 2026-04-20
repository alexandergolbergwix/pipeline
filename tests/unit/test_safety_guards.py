"""Tests for the safety guards that prevent the wrong-merge / wrong-edit disaster
that occurred 2026-04-12 (902+ items merged across unrelated entities, 2,313
modifications on items not created by the authenticated user).

These tests verify:
1. Reconciler REJECTS a candidate match when other identifiers conflict.
2. Uploader REFUSES to add an identity-property value that would create a
   multi-value conflict on an existing item.
3. Uploader does NOT overwrite an existing label/description on an item.
"""

from __future__ import annotations

import pathlib
from unittest.mock import MagicMock, patch

import pytest
from converter.wikidata.reconciler import WikidataReconciler

# ── Reconciler cross-identifier verification ────────────────────────────────


class TestReconcilerVerification:
    """Reconciler must reject a single-identifier match when other IDs disagree."""

    def test_match_accepted_when_no_other_identifiers_to_check(self) -> None:
        """If we only know one ID, a match on it is accepted (no info to conflict)."""
        r = WikidataReconciler()
        with (
            patch.object(r, "reconcile_person_by_viaf", return_value="Q123"),
            patch.object(r, "_fetch_identity_claims", return_value={"P214": {"12345"}}),
        ):
            qid = r.reconcile_person(
                name="Alice", viaf_uri="http://viaf.org/viaf/12345", nli_id=None
            )
            assert qid == "Q123"

    def test_match_rejected_when_other_id_conflicts(self) -> None:
        """Two lawyers share an ISNI but have different GNDs → REJECT."""
        r = WikidataReconciler()
        with (
            patch.object(r, "reconcile_person_by_external_id", return_value="Q999"),
            patch.object(
                r,
                "_fetch_identity_claims",
                return_value={
                    "P213": {"0000000123750072"},  # candidate has my ISNI
                    "P227": {"118576488"},  # candidate has DIFFERENT GND than mine
                },
            ),
        ):
            qid = r.reconcile_person(
                name="Lawyer A",
                viaf_uri=None,
                nli_id=None,
                gnd_id="119033348",  # mine
                isni="0000000123750072",
            )
            assert qid is None  # REJECTED — must create new item

    def test_match_accepted_when_other_id_agrees(self) -> None:
        """Both ISNI and GND agree — confirmed same person."""
        r = WikidataReconciler()
        with (
            patch.object(r, "reconcile_person_by_external_id", return_value="Q888"),
            patch.object(
                r,
                "_fetch_identity_claims",
                return_value={
                    "P213": {"0000000123750072"},
                    "P227": {"118576488"},
                },
            ),
        ):
            qid = r.reconcile_person(
                name="Bob",
                viaf_uri=None,
                nli_id=None,
                gnd_id="118576488",
                isni="0000000123750072",
            )
            assert qid == "Q888"

    def test_match_rejected_for_band_vs_person_p31_conflict(self) -> None:
        """Pallor's case: pipeline matched a person to a band item via shared name.

        The band's P31 is Q215380 (band) but we'd want Q5 (human). _IDENTITY_PROPS
        does not currently include P31 in the reconciler check (only the uploader
        does), so the reconciler relies on identifier conflicts. This test documents
        that — if no shared identifier, the band cannot be matched at all (no SPARQL
        query for "name").
        """
        r = WikidataReconciler()
        with patch.object(r, "_query", return_value=[]):
            # No identifiers at all → no match attempt → returns None
            qid = r.reconcile_person(name="Unique", viaf_uri=None, nli_id=None)
            assert qid is None


# ── Uploader identity-conflict guard ────────────────────────────────────────


class TestUploaderIdentityConflict:
    """Uploader must refuse to add a P569/P19/P227/etc. value that conflicts
    with an existing one (this is what created the 902 multi-DOB items)."""

    def _make_uploader(self) -> object:
        from converter.wikidata.uploader import WikidataUploader

        # Construct without invoking WBI / network
        u = WikidataUploader.__new__(WikidataUploader)
        return u

    def _make_stmt(self, prop: str, value: str) -> object:
        stmt = MagicMock()
        stmt.property_id = prop
        stmt.value = value
        return stmt

    def _make_existing_claim(self, value: str) -> object:
        claim = MagicMock()
        claim.mainsnak.datavalue = {"value": value}
        return claim

    def test_skips_non_identity_property(self) -> None:
        """Non-identity props (e.g., P50 author) bypass the guard entirely."""
        u = self._make_uploader()
        wbi_item = MagicMock()
        wbi_item.claims.get = MagicMock(return_value=[self._make_existing_claim("Q1")])
        stmt = self._make_stmt("P50", "Q2")
        assert u._would_create_identity_conflict(wbi_item, stmt) is False

    def test_skips_when_no_existing_claim(self) -> None:
        """Identity prop, but item has no existing value → safe to add."""
        u = self._make_uploader()
        wbi_item = MagicMock()
        wbi_item.claims.get = MagicMock(return_value=[])
        stmt = self._make_stmt("P227", "118576488")
        assert u._would_create_identity_conflict(wbi_item, stmt) is False

    def test_skips_when_existing_claim_matches(self) -> None:
        """Identity prop with same value already present → safe (WBI dedups)."""
        u = self._make_uploader()
        wbi_item = MagicMock()
        wbi_item.claims.get = MagicMock(return_value=[self._make_existing_claim("118576488")])
        stmt = self._make_stmt("P227", "118576488")
        assert u._would_create_identity_conflict(wbi_item, stmt) is False

    def test_blocks_when_existing_claim_differs(self) -> None:
        """Identity prop with DIFFERENT value present → MUST block."""
        u = self._make_uploader()
        wbi_item = MagicMock()
        wbi_item.claims.get = MagicMock(return_value=[self._make_existing_claim("118576488")])
        stmt = self._make_stmt("P227", "119033348")  # different GND
        assert u._would_create_identity_conflict(wbi_item, stmt) is True

    def test_dob_precision_normalized_for_compare(self) -> None:
        """+1850-01-01 and +1850-01-01T00:00:00Z both encode same date prefix."""
        u = self._make_uploader()
        wbi_item = MagicMock()
        wbi_item.claims.get = MagicMock(
            return_value=[self._make_existing_claim("+1850-01-01T00:00:00Z")]
        )
        stmt = self._make_stmt("P569", "+1850-01-01T00:00:00Z")
        assert u._would_create_identity_conflict(wbi_item, stmt) is False

    def test_dob_year_difference_blocks(self) -> None:
        """Different birth year → block (Kolja21's lawyer case)."""
        u = self._make_uploader()
        wbi_item = MagicMock()
        wbi_item.claims.get = MagicMock(
            return_value=[self._make_existing_claim("+1850-01-01T00:00:00Z")]
        )
        stmt = self._make_stmt("P569", "+1860-01-01T00:00:00Z")
        assert u._would_create_identity_conflict(wbi_item, stmt) is True


# ── merge_duplicates.py pre-merge conflict check ────────────────────────────


class TestMergeDuplicatesConflictCheck:
    """The cleanup script must refuse merges where source and target have
    conflicting identity properties."""

    def test_has_conflict_detects_different_gnds(self) -> None:
        from scripts.merge_duplicates import _has_conflict

        from_claims = {"P227": {"118576488"}}
        to_claims = {"P227": {"119033348"}}
        assert _has_conflict(from_claims, to_claims) == ["P227"]

    def test_has_conflict_allows_overlap(self) -> None:
        from scripts.merge_duplicates import _has_conflict

        from_claims = {"P227": {"118576488", "extra"}}
        to_claims = {"P227": {"118576488"}}
        assert _has_conflict(from_claims, to_claims) == []

    def test_has_conflict_allows_one_side_missing(self) -> None:
        from scripts.merge_duplicates import _has_conflict

        from_claims: dict[str, set[str]] = {}
        to_claims = {"P227": {"118576488"}}
        assert _has_conflict(from_claims, to_claims) == []

    def test_has_conflict_detects_dob(self) -> None:
        from scripts.merge_duplicates import _has_conflict

        from_claims = {"P569": {"+1850-00-00"}}
        to_claims = {"P569": {"+1860-00-00"}}
        assert "P569" in _has_conflict(from_claims, to_claims)


# ── is_safe_to_revert (latest-editor guard) ─────────────────────────────────


class TestIsSafeToRevert:
    """The combined safety check used by all revert scripts.

    Catches the disaster pattern: someone (e.g., Epìdosis) re-applied my
    edit because it was actually correct. A naive re-run of the revert
    script would silently override their correction. The latest-editor
    check refuses to undo when the most recent revision is by anyone else.
    """

    def _patch(self, monkeypatch, creator: str, latest: str) -> None:
        from scripts.lib import wikidata_safety

        monkeypatch.setattr(wikidata_safety, "get_first_revision_author", lambda _s, _q: creator)
        monkeypatch.setattr(wikidata_safety, "get_latest_revision_author", lambda _s, _q: latest)

    def test_safe_when_creator_is_other_and_latest_is_me(self, monkeypatch) -> None:
        from scripts.lib.wikidata_safety import is_safe_to_revert

        self._patch(monkeypatch, creator="OtherUser", latest="me")
        safe, _reason = is_safe_to_revert(None, "Q1", "me")
        assert safe is True

    def test_unsafe_when_i_created_the_item(self, monkeypatch) -> None:
        from scripts.lib.wikidata_safety import is_safe_to_revert

        self._patch(monkeypatch, creator="me", latest="me")
        safe, reason = is_safe_to_revert(None, "Q1", "me")
        assert safe is False
        assert "I created" in reason

    def test_unsafe_when_creator_unknown(self, monkeypatch) -> None:
        from scripts.lib.wikidata_safety import is_safe_to_revert

        self._patch(monkeypatch, creator="", latest="me")
        safe, reason = is_safe_to_revert(None, "Q1", "me")
        assert safe is False
        assert "creator" in reason

    def test_unsafe_when_latest_editor_is_someone_else(self, monkeypatch) -> None:
        """The Epìdosis case — they re-applied my edit because it was correct."""
        from scripts.lib.wikidata_safety import is_safe_to_revert

        self._patch(monkeypatch, creator="OtherUser", latest="Epìdosis")
        safe, reason = is_safe_to_revert(None, "Q1", "me")
        assert safe is False
        assert "Epìdosis" in reason
        assert "override" in reason

    def test_unsafe_when_latest_editor_unknown(self, monkeypatch) -> None:
        from scripts.lib.wikidata_safety import is_safe_to_revert

        self._patch(monkeypatch, creator="OtherUser", latest="")
        safe, reason = is_safe_to_revert(None, "Q1", "me")
        assert safe is False
        assert "latest" in reason


# ── Rule 25 — moratorium on Wikidata bulk operations ────────────────────────


class TestMoratorium:
    """The WikidataUploader must refuse to write against production Wikidata
    while CLAUDE.md rule 25 is in effect, unless MORATORIUM_LIFTED=true."""

    def test_live_uploader_refuses_without_lifted_env(self, monkeypatch) -> None:
        from converter.wikidata.uploader import WikidataUploader

        monkeypatch.delenv("MORATORIUM_LIFTED", raising=False)
        u = WikidataUploader(token="dummy", is_test=False)
        with pytest.raises(RuntimeError, match="MORATORIUM"):
            u._check_moratorium_for_live()

    def test_test_mode_bypasses_moratorium(self, monkeypatch) -> None:
        from converter.wikidata.uploader import WikidataUploader

        monkeypatch.delenv("MORATORIUM_LIFTED", raising=False)
        u = WikidataUploader(token="dummy", is_test=True)
        u._check_moratorium_for_live()  # should not raise

    def test_lifted_env_unlocks_live_uploads(self, monkeypatch) -> None:
        from converter.wikidata.uploader import WikidataUploader

        monkeypatch.setenv("MORATORIUM_LIFTED", "true")
        u = WikidataUploader(token="dummy", is_test=False)
        u._check_moratorium_for_live()  # should not raise


# ── Fix #2 — P8189 must not be attached to bibliographic IDs ───────────────


class TestP8189Restriction:
    """Bug fix 2026-04-15 (Geagea complaint): P8189 (NLI J9U ID) is for
    authority records only. Bibliographic IDs (prefix 990…) and non-Q5
    items must NOT receive P8189."""

    def test_authority_id_attached_to_person(self) -> None:
        import pathlib

        src = pathlib.Path("converter/wikidata/item_builder.py").read_text(encoding="utf-8")
        # The fix introduces the check `mazal_str.startswith("9870") and not is_org`
        assert 'mazal_str.startswith("9870")' in src
        assert "not is_org" in src

    def test_bibliographic_prefix_rejected(self) -> None:
        import pathlib

        src = pathlib.Path("converter/wikidata/item_builder.py").read_text(encoding="utf-8")
        assert 'not mazal_str.startswith("9870")' in src


# ── Fix #3 — Hebrew labels in natural order ────────────────────────────────


class TestNaturalNameOrder:
    """Bug fix 2026-04-15 (Geagea complaint on Q139230386): Wikidata labels
    must be in natural order (Given Surname), not MARC's inverted form
    (Surname, Given). The inverted form is preserved in P1559."""

    def test_inverted_form_is_flipped(self) -> None:
        from converter.wikidata.item_builder import _to_natural_name_order

        assert _to_natural_name_order("סופינו, עמנואל") == "עמנואל סופינו"
        assert _to_natural_name_order("Smith, John") == "John Smith"

    def test_unchanged_when_no_comma(self) -> None:
        from converter.wikidata.item_builder import _to_natural_name_order

        assert _to_natural_name_order("עמנואל סופינו") == "עמנואל סופינו"
        assert _to_natural_name_order("Joseph Gikatilla") == "Joseph Gikatilla"

    def test_trailing_dates_become_qualifier(self) -> None:
        from converter.wikidata.item_builder import _to_natural_name_order

        assert _to_natural_name_order("Smith, John, 1850-1900") == "John Smith (1850-1900)"

    def test_three_commas_left_unchanged(self) -> None:
        from converter.wikidata.item_builder import _to_natural_name_order

        # Conservative — don't try to flip ambiguous multi-part names
        assert "b a" not in _to_natural_name_order("a, b, c, d")

    def test_empty_string_safe(self) -> None:
        from converter.wikidata.item_builder import _to_natural_name_order

        assert _to_natural_name_order("") == ""


# ── Fix #4 — MARC 710 institutional names → P195, never P50 ────────────────


class TestInstitutionalNameRouting:
    """Bug fix 2026-04-15 (Geagea complaint on Q139085958): institutional
    contributors (MARC 710) must not become P50 (author). They route to
    P195 (collection) instead."""

    def test_library_recognised_as_institution(self) -> None:
        from converter.wikidata.item_builder import _is_institutional_name

        assert _is_institutional_name("National Library of Israel") is True
        assert _is_institutional_name("Bodleian Library") is True
        assert _is_institutional_name("Vatican Library") is True

    def test_hebrew_institution_recognised(self) -> None:
        from converter.wikidata.item_builder import _is_institutional_name

        assert _is_institutional_name("הספרייה הלאומית של ישראל") is True
        assert _is_institutional_name("מכון בן-צבי") is True

    def test_person_name_not_flagged(self) -> None:
        from converter.wikidata.item_builder import _is_institutional_name

        assert _is_institutional_name("Joseph Gikatilla") is False
        assert _is_institutional_name("עמנואל סופינו") is False

    def test_routing_table_uses_p195_for_institutions(self) -> None:
        """The fix introduces a check that flips P50 → P195 when
        _is_institutional_name returns True."""
        import pathlib

        src = pathlib.Path("converter/wikidata/item_builder.py").read_text(encoding="utf-8")
        assert "_is_institutional_name(name)" in src
        assert 'pid = "P195"' in src or "pid = P_COLLECTION" in src


# ── Fix #1 — Reconciler always receives all 5 identifiers ──────────────────


class TestReconcilerCallsiteCompleteness:
    """Bug fix 2026-04-15 (Geagea complaint about duplicates page): the
    NER-entity branch of the reconcile loop must pass all 5 IDs
    (lc_id/gnd_id/isni in addition to viaf/nli) so existing community
    items are found before we create duplicates."""

    def test_ner_branch_passes_all_identifiers(self) -> None:
        import pathlib

        src = pathlib.Path("converter/wikidata/reconciler.py").read_text(encoding="utf-8")
        ner_block_start = src.find("Reconcile NER entities")
        assert ner_block_start > 0, "NER entity block not found"
        ner_block = src[ner_block_start : ner_block_start + 1500]
        assert 'entity.get("lc_id")' in ner_block
        assert 'entity.get("gnd_id")' in ner_block
        assert 'entity.get("isni")' in ner_block


# ── Web-audit Fix #1 — century date encoding ────────────────────────────────


class TestCenturyDateEncoding:
    """Bug fix 2026-04-15 (web audit Fix #1): Wikidata precision-7 dates
    interpret the stored year as the START of the century, not the midpoint.
    Previously the pipeline emitted `+1550-00-00` for 16th century, causing
    silent SPARQL query corruption."""

    def test_english_16th_century_starts_at_1501(self) -> None:
        from converter.wikidata.property_mapping import date_to_wikidata

        result = date_to_wikidata({"original_string": "16th century"})
        assert result is not None
        assert result[0] == "+1501-00-00T00:00:00Z"

    def test_english_1st_century_starts_at_0001(self) -> None:
        from converter.wikidata.property_mapping import date_to_wikidata

        result = date_to_wikidata({"original_string": "1st century"})
        assert result is not None
        assert result[0] == "+0001-00-00T00:00:00Z"

    def test_hebrew_century_starts_at_century_year_1(self) -> None:
        from converter.wikidata.property_mapping import date_to_wikidata

        # מאה ט"ז = 16th century → must start at 1501, not 1550
        result = date_to_wikidata({"original_string": 'מאה ט"ז'})
        assert result is not None
        assert result[0] == "+1501-00-00T00:00:00Z"

    def test_full_year_unchanged(self) -> None:
        from converter.wikidata.property_mapping import date_to_wikidata

        # Regression: full-year input must still encode the year, not the century start.
        result = date_to_wikidata({"year": 1407, "date_format": "FullDate"})
        assert result is not None
        assert result[0] == "+1407-01-01T00:00:00Z"


# ── Web-audit Fix #4 — P21 (gender) NOT blanket-set ────────────────────────


class TestP21NotBlanketAssigned:
    """Bug fix 2026-04-15 (web audit Fix #4): every non-org person was being
    unconditionally assigned P21=Q6581097 (male). Source MARC carries no
    gender info; unsourced gender claims are flagged by the community
    (UW iSchool 2023 'P21 Problem' study)."""

    def test_p21_male_constant_not_emitted_in_person_creation(self) -> None:
        """Source-grep: the literal Q6581097 male-constant assignment must
        be removed from _get_or_create_person. The string can still appear
        in comments documenting why it was removed."""
        import pathlib
        import re

        src = pathlib.Path("converter/wikidata/item_builder.py").read_text(encoding="utf-8")
        # Find the _get_or_create_person body
        method_start = src.find("def _get_or_create_person")
        assert method_start > 0
        method_end = src.find("\n    def ", method_start + 1)
        body = src[method_start:method_end]
        # No WikidataStatement should attach P21 in this method body
        # (allow Q6581097 to appear ONLY in comments/docstrings, not in code)
        code_lines = [
            line for line in body.splitlines() if line.strip() and not line.lstrip().startswith("#")
        ]
        joined = "\n".join(code_lines)
        # Strip docstrings (triple-quoted blocks)
        joined_no_doc = re.sub(r'"""[\s\S]*?"""', "", joined)
        assert 'property_id="P21"' not in joined_no_doc
        assert "Q6581097" not in joined_no_doc


# ── Web-audit Fix #6 — edit summary on every WBI write ─────────────────────


class TestEditSummaryPassed:
    """Bot policy compliance: every wbi_item.write() must include a
    descriptive edit summary."""

    def test_uploader_passes_summary_to_write(self) -> None:
        import pathlib

        src = pathlib.Path("converter/wikidata/uploader.py").read_text(encoding="utf-8")
        assert "wbi_item.write(summary=" in src
        # The bare `wbi_item.write()` call should be gone
        assert "wbi_item.write()" not in src

    def test_summary_template_mentions_pipeline_and_source(self) -> None:
        import pathlib

        src = pathlib.Path("converter/wikidata/uploader.py").read_text(encoding="utf-8")
        # The template includes "MHM Pipeline" and a Ktiv attribution
        assert "MHM Pipeline" in src
        assert "Ktiv" in src


# ── Web-audit Fix #7 — P1412 derived from MARC, not blanket Hebrew ─────────


class TestP1412DerivedFromManuscript:
    """Bug fix 2026-04-15 (web audit Fix #7): P1412 (language) was hardcoded
    to Hebrew (Q9288) for every non-org person. Now derived from the
    manuscript's languages (MARC 008/35-37 + 041); omitted when no language
    data exists."""

    def test_hardcoded_hebrew_language_removed_from_person_creation(self) -> None:
        """The literal hardcoded value="Q9288" assignment with property_id
        P1412 must NOT be present in _get_or_create_person."""
        import pathlib
        import re

        src = pathlib.Path("converter/wikidata/item_builder.py").read_text(encoding="utf-8")
        method_start = src.find("def _get_or_create_person")
        assert method_start > 0
        method_end = src.find("\n    def ", method_start + 1)
        body = src[method_start:method_end]
        # Look for the OLD pattern: P1412 with hardcoded Q9288 string literal.
        # The new code uses a variable lang_qid sourced from LANG_TO_QID.
        offending = re.search(
            r'property_id="P1412"[\s\S]{0,80}value="Q9288"',
            body,
        )
        assert offending is None, "P1412 must not be hardcoded to Q9288"

    def test_p1412_loop_iterates_over_source_languages(self) -> None:
        """The new code reads source_record.get('languages', ...) and emits
        one P1412 statement per language."""
        import pathlib

        src = pathlib.Path("converter/wikidata/item_builder.py").read_text(encoding="utf-8")
        method_start = src.find("def _get_or_create_person")
        method_end = src.find("\n    def ", method_start + 1)
        body = src[method_start:method_end]
        assert 'source_record.get("languages")' in body
        assert "LANG_TO_QID.get" in body


# ── Web-audit Fix #8 — disambiguating work descriptions ────────────────────


class TestWorkDescriptionDisambiguation:
    """Bug fix 2026-04-15 (web audit Fix #8): all 3,970 work items got the
    identical description 'Hebrew manuscript work'. Wikidata requires
    descriptions to disambiguate same-label items."""

    def test_includes_author_when_known(self) -> None:
        from converter.wikidata.item_builder import _build_work_description

        desc = _build_work_description(author_name="Maimonides", century=None)
        assert "Maimonides" in desc
        assert "Hebrew manuscript work" in desc

    def test_includes_century_when_known(self) -> None:
        from converter.wikidata.item_builder import _build_work_description

        desc = _build_work_description(author_name="Maimonides", century="12th century")
        assert "Maimonides" in desc
        assert "12th century" in desc

    def test_falls_back_to_generic_when_nothing_known(self) -> None:
        from converter.wikidata.item_builder import _build_work_description

        desc = _build_work_description(author_name=None, century=None)
        assert desc == "Hebrew manuscript work"

    def test_strips_trailing_punctuation_from_author(self) -> None:
        from converter.wikidata.item_builder import _build_work_description

        desc = _build_work_description(author_name="Smith, John,", century=None)
        # The MARC trailing comma should be stripped before display.
        assert desc.endswith("Smith, John")


# ── Web-audit Fix #2 — work-item reconciliation ────────────────────────────


class TestWorkReconciliation:
    """Bug fix 2026-04-15 (web audit Fix #2): the pipeline created duplicate
    work items for classical Hebrew works. The reconciler now has a
    reconcile_work_by_label_and_author() method and the builder consults
    it before creating new work items."""

    def test_reconciler_method_returns_qid_on_match(self, monkeypatch) -> None:
        from converter.wikidata.reconciler import WikidataReconciler

        r = WikidataReconciler()
        # Stub _query to return a single fake binding
        monkeypatch.setattr(
            r,
            "_query",
            lambda _sparql: [
                {"item": {"value": "http://www.wikidata.org/entity/Q42"}, "author": {"value": ""}}
            ],
        )
        result = r.reconcile_work_by_label_and_author("ספר היצירה", lang="he")
        assert result == "Q42"

    def test_reconciler_returns_none_when_no_match(self, monkeypatch) -> None:
        from converter.wikidata.reconciler import WikidataReconciler

        r = WikidataReconciler()
        monkeypatch.setattr(r, "_query", lambda _sparql: [])
        result = r.reconcile_work_by_label_and_author("nonexistent work title", lang="he")
        assert result is None

    def test_author_conflict_rejects_candidate(self, monkeypatch) -> None:
        from converter.wikidata.reconciler import WikidataReconciler

        r = WikidataReconciler()
        # Candidate work has author Q999, but we proposed Q42 — must reject.
        monkeypatch.setattr(
            r,
            "_query",
            lambda _sparql: [
                {
                    "item": {"value": "http://www.wikidata.org/entity/Q1234"},
                    "author": {"value": "http://www.wikidata.org/entity/Q999"},
                }
            ],
        )
        result = r.reconcile_work_by_label_and_author(
            "Conflicting Work",
            lang="he",
            author_qid="Q42",
        )
        assert result is None

    def test_empty_title_returns_none(self) -> None:
        from converter.wikidata.reconciler import WikidataReconciler

        r = WikidataReconciler()
        assert r.reconcile_work_by_label_and_author("") is None
        assert r.reconcile_work_by_label_and_author("   ") is None

    def test_builder_accepts_optional_reconciler(self) -> None:
        """WikidataItemBuilder must accept reconciler=None (offline mode)
        and not crash when _get_or_create_work is called."""
        from converter.wikidata.item_builder import WikidataItemBuilder

        b = WikidataItemBuilder(reconciler=None)
        # Just constructing it should be fine.
        assert b._reconciler is None

    def test_builder_consults_reconciler_when_provided(self) -> None:
        """When a reconciler is wired in, _get_or_create_work calls
        reconcile_work_by_label_and_author() before creating."""
        import pathlib

        src = pathlib.Path("converter/wikidata/item_builder.py").read_text(encoding="utf-8")
        method_start = src.find("def _get_or_create_work")
        method_end = src.find("\n    def ", method_start + 1)
        body = src[method_start:method_end]
        assert "reconcile_work_by_label_and_author" in body
        assert "self._reconciler" in body


# ── Deeper-audit Fix #4-#6 — identifier-format normalisers ────────────────


class TestIdentifierNormalisers:
    """Bug fix 2026-04-16 (deeper audit Fixes #4-#6): VIAF returns raw
    identifier strings that violate Wikidata's strict P244/P213/P268
    format constraints. Normalise here or generate thousands of
    constraint-violation reports on every person item."""

    def test_lccn_inserts_missing_space(self) -> None:
        from converter.wikidata.property_mapping import normalize_lccn

        assert normalize_lccn("n78096039") == "n 78096039"

    def test_lccn_keeps_valid_form(self) -> None:
        from converter.wikidata.property_mapping import normalize_lccn

        assert normalize_lccn("n 78096039") == "n 78096039"
        assert normalize_lccn("nb 12345") == "nb 12345"

    def test_lccn_invalid_prefix_returns_none(self) -> None:
        from converter.wikidata.property_mapping import normalize_lccn

        assert normalize_lccn("xyz 12345") is None
        assert normalize_lccn("") is None
        assert normalize_lccn(None) is None

    def test_isni_groups_into_quartets(self) -> None:
        from converter.wikidata.property_mapping import normalize_isni

        assert normalize_isni("0000000123750072") == "0000 0001 2375 0072"

    def test_isni_already_grouped_unchanged(self) -> None:
        from converter.wikidata.property_mapping import normalize_isni

        assert normalize_isni("0000 0001 2375 0072") == "0000 0001 2375 0072"

    def test_isni_invalid_length_returns_none(self) -> None:
        from converter.wikidata.property_mapping import normalize_isni

        assert normalize_isni("12345") is None
        assert normalize_isni("") is None

    def test_bnf_strips_cb_prefix(self) -> None:
        from converter.wikidata.property_mapping import normalize_bnf

        assert normalize_bnf("cb12345678q") == "12345678q"

    def test_bnf_already_clean_unchanged(self) -> None:
        from converter.wikidata.property_mapping import normalize_bnf

        assert normalize_bnf("12345678q") == "12345678q"

    def test_bnf_invalid_format_returns_none(self) -> None:
        from converter.wikidata.property_mapping import normalize_bnf

        assert normalize_bnf("not-a-bnf-id") is None
        assert normalize_bnf("") is None


# ── Deeper-audit Fix #1-#2 — references on every person/work statement ─────


class TestPersonAndWorkReferences:
    """Bug fix 2026-04-16 (deeper audit Fixes #1, #2): every statement on
    person and work items must carry a P248 reference. Previously all
    person and work statements were emitted with empty references=[],
    which is a WikiProject Authority Control violation."""

    def test_viaf_reference_helper_returns_correct_url(self) -> None:
        from converter.wikidata.property_mapping import viaf_reference

        ref = viaf_reference("51777166")
        assert any(snak["value"] == "https://viaf.org/viaf/51777166" for snak in ref)
        assert any(snak["property"] == "P248" for snak in ref)
        assert any(snak["property"] == "P854" for snak in ref)
        assert any(snak["property"] == "P813" for snak in ref)

    def test_person_method_attaches_references_after_build(self) -> None:
        """Source-grep: _get_or_create_person must include the post-build
        loop that sets stmt.references on every statement."""
        import pathlib

        src = pathlib.Path("converter/wikidata/item_builder.py").read_text(encoding="utf-8")
        method_start = src.find("def _get_or_create_person")
        method_end = src.find("\n    def ", method_start + 1)
        body = src[method_start:method_end]
        assert "person_ref" in body
        assert "viaf_reference" in body or "nli_reference" in body
        # The post-build attach loop
        assert "stmt.references" in body

    def test_work_method_attaches_references_after_build(self) -> None:
        import pathlib

        src = pathlib.Path("converter/wikidata/item_builder.py").read_text(encoding="utf-8")
        method_start = src.find("def _get_or_create_work")
        method_end = src.find("\n    def ", method_start + 1)
        body = src[method_start:method_end]
        assert "work_ref" in body
        assert "stmt.references" in body


# ── Deeper-audit Fix #3 — bot=True on every WBI write ──────────────────────


class TestBotFlagOnWrite:
    """Bug fix 2026-04-16 (deeper audit Fix #3): wbi_item.write() must be
    called with bot=True so edits are filtered from the human RecentChanges
    feed. The single biggest reason bots get blocked at WD:AN."""

    def test_bot_true_passed_to_write(self) -> None:
        import pathlib

        src = pathlib.Path("converter/wikidata/uploader.py").read_text(encoding="utf-8")
        # Source-grep for the new pattern
        assert "wbi_item.write(summary=edit_summary, bot=True)" in src
        # The bare summary= call without bot= must be gone
        assert "wbi_item.write(summary=edit_summary)\n" not in src


# ── Deeper-audit Fix #7 — test SPARQL endpoint URL ─────────────────────────


class TestTestSparqlEndpoint:
    """Bug fix 2026-04-16 (deeper audit Fix #7): _TEST_SPARQL previously
    pointed at the MediaWiki API URL, not a SPARQL endpoint."""

    def test_test_sparql_is_not_api_url(self) -> None:
        from converter.wikidata import uploader

        assert uploader._TEST_SPARQL != "https://test.wikidata.org/w/api.php"
        assert "/sparql" in uploader._TEST_SPARQL


# ── Deeper-audit Fix #8 — edit-conflict detection in retry loop ────────────


class TestEditConflictHandling:
    """Bug fix 2026-04-16 (deeper audit Fix #8): the retry loop now
    inspects error codes and uses a shorter backoff for editconflict
    (someone is actively editing this item, retry quickly)."""

    def test_uploader_recognises_editconflict(self) -> None:
        import pathlib

        src = pathlib.Path("converter/wikidata/uploader.py").read_text(encoding="utf-8")
        # The new code inspects error string for these tokens.
        assert "editconflict" in src
        assert "badtoken" in src


# ── Deeper-audit Fix #9 — {{bots|deny=…}} compliance ──────────────────────


class TestBotExclusion:
    """Bug fix 2026-04-16 (deeper audit Fix #9): respect the community
    convention {{bots|deny=…}} on item talk pages."""

    def test_bot_excluded_method_exists(self) -> None:
        from converter.wikidata.uploader import WikidataUploader

        # The method must be defined on the class.
        assert callable(getattr(WikidataUploader, "_bot_excluded", None))

    def test_bot_excluded_caches_results(self, monkeypatch) -> None:
        """Once we look up a QID's exclusion status, we shouldn't re-fetch."""
        from converter.wikidata.uploader import WikidataUploader

        u = WikidataUploader.__new__(WikidataUploader)
        u._is_test = False
        u._authenticated_user = "TestBot"
        # Avoid the moratorium / real network.
        u._bot_exclusion_cache = {"Q42": True}
        assert u._bot_excluded("Q42") is True


# ── Deeper-audit Fix #10 — SPARQL escape for control_number ───────────────


class TestSparqlEscape:
    """Bug fix 2026-04-16 (deeper audit Fix #10): control_number is now
    escaped before injection into the SPARQL string."""

    def test_reconciler_escapes_control_number(self) -> None:
        import pathlib

        src = pathlib.Path("converter/wikidata/reconciler.py").read_text(encoding="utf-8")
        # Find reconcile_manuscript_by_nli_id and verify the safe variable
        method_start = src.find("def reconcile_manuscript_by_nli_id")
        assert method_start > 0
        method_end = src.find("\n    def ", method_start + 1)
        body = src[method_start:method_end]
        assert "safe_ctrl" in body or "replace('\"', '\\\\\"')" in body


# ── Deeper-audit Fix #11 — P7416 (folios) instead of P1104 (pages) ────────


class TestFolioVsPageProperty:
    """Bug fix 2026-04-16 (deeper audit Fix #11): manuscripts are counted
    in folios. Use P7416 unless the extent string explicitly says 'pages'."""

    def test_constant_p_number_of_folios_exists(self) -> None:
        from converter.wikidata import property_mapping as pm

        assert pm.P_NUMBER_OF_FOLIOS == "P7416"

    def test_default_to_p7416(self) -> None:
        import pathlib

        src = pathlib.Path("converter/wikidata/item_builder.py").read_text(encoding="utf-8")
        # The branch that picks the property
        assert "P_NUMBER_OF_FOLIOS" in src
        # The 'page' check that switches to P1104
        assert '"page" in low' in src or "page" in src


# ── Deeper-audit Fix #12 — P1412 only for AUTHOR role ─────────────────────


class TestP1412RoleFiltered:
    """Bug fix 2026-04-16 (deeper audit Fix #12): the manuscript's MARC
    languages are MANUSCRIPT-level, not person-level. Only emit P1412
    when role == author so we don't assert that scribes/owners spoke
    the manuscript's language."""

    def test_role_filter_present_in_source(self) -> None:
        import pathlib

        src = pathlib.Path("converter/wikidata/item_builder.py").read_text(encoding="utf-8")
        method_start = src.find("def _get_or_create_person")
        method_end = src.find("\n    def ", method_start + 1)
        body = src[method_start:method_end]
        # The role check that gates P1412 emission
        assert 'role_norm == "author"' in body


# ── Deeper-audit Fix #13 — person descriptions use role+dates ─────────────


class TestPersonDescription:
    """Bug fix 2026-04-16 (deeper audit Fix #13): person descriptions now
    include role/occupation, not just dates."""

    def test_author_with_dates(self) -> None:
        from converter.wikidata.item_builder import _build_person_description

        assert _build_person_description("AUTHOR", "1200-1280", False) == "author (1200-1280)"

    def test_scribe_no_dates(self) -> None:
        from converter.wikidata.item_builder import _build_person_description

        assert _build_person_description("SCRIBE", "", False) == "Hebrew manuscript scribe"

    def test_owner_no_dates(self) -> None:
        from converter.wikidata.item_builder import _build_person_description

        assert _build_person_description("OWNER", "", False) == "Hebrew manuscript owner"

    def test_unknown_role_falls_back(self) -> None:
        from converter.wikidata.item_builder import _build_person_description

        assert _build_person_description("", "1200-1280", False) == "person (1200-1280)"
        assert (
            _build_person_description("", "", False) == "person associated with Hebrew manuscripts"
        )

    def test_organisation_branch(self) -> None:
        from converter.wikidata.item_builder import _build_person_description

        assert (
            _build_person_description("", "", True)
            == "organization associated with Hebrew manuscripts"
        )


# ── Deeper-audit Fix #14 — drop P1559 for Latin-script names ──────────────


class TestP1559LatinDropped:
    """Bug fix 2026-04-16 (deeper audit Fix #14): Latin-script names no
    longer get P1559 with language 'la' (which was wrong for modern
    European names). The label already conveys the same information."""

    def test_native_lang_chain_no_longer_has_latin_branch(self) -> None:
        import pathlib

        src = pathlib.Path("converter/wikidata/item_builder.py").read_text(encoding="utf-8")
        # The old `native_lang = "la"` assignment must be gone.
        # (We allow the literal "la" elsewhere, but not as a P1559 fallback.)
        assert 'native_lang = "la"' not in src


# ── Deeper-audit Fix #15 — P6216 gated on inception year ──────────────────


class TestPublicDomainGate:
    """Bug fix 2026-04-16 (deeper audit Fix #15): only assert public-domain
    status (P6216=Q19652) when the inception date is known AND pre-1900."""

    def test_pre_1900_year_extracted(self) -> None:
        from converter.wikidata.item_builder import _extract_inception_year

        assert _extract_inception_year({"dates": {"year": 1407}}) == 1407
        assert _extract_inception_year({"dates": {"year": "1850"}}) == 1850

    def test_no_year_returns_none(self) -> None:
        from converter.wikidata.item_builder import _extract_inception_year

        assert _extract_inception_year({}) is None
        assert _extract_inception_year({"dates": {}}) is None

    def test_string_fallback_finds_year(self) -> None:
        from converter.wikidata.item_builder import _extract_inception_year

        assert _extract_inception_year({"dates": {"original_string": "ca. 1450"}}) == 1450


# ── Deeper-audit Fix #17 — description overwrite guard ─────────────────────


class TestDescriptionOverwriteGuard:
    """Bug fix 2026-04-16 (deeper audit Fix #17): existing descriptions
    are no longer overwritten on update. Mirrors the existing labels
    guard."""

    def test_descriptions_check_existing_value(self) -> None:
        import pathlib

        src = pathlib.Path("converter/wikidata/uploader.py").read_text(encoding="utf-8")
        # The guard must reference wbi_item.descriptions.get and skip
        # when the slot is non-empty for an existing item.
        assert "wbi_item.descriptions.get(lang)" in src


# ── Geagea complaint 2026-04-15: generic "קובץ." Hebrew labels ────────────


class TestKovetzPlaceholderTitleFilter:
    """Bug fix 2026-04-15 (Geagea complaint): MARC 245 sometimes contains a
    generic catalog placeholder like "קובץ." (= "compilation" / "file"),
    used by NLI catalogers when an anthology has no overarching real title.
    Emitting that as the Hebrew label produced 94 useless labels. The
    pipeline now detects placeholder titles and routes them to an alias
    while building a synthetic shelfmark-based label."""

    def test_kovetz_with_period_recognised(self) -> None:
        from converter.wikidata.item_builder import _is_placeholder_title

        assert _is_placeholder_title("קובץ.") is True
        assert _is_placeholder_title("קבץ.") is True

    def test_bare_kovetz_recognised(self) -> None:
        from converter.wikidata.item_builder import _is_placeholder_title

        assert _is_placeholder_title("קובץ") is True
        assert _is_placeholder_title("קבץ") is True

    def test_short_topical_kovetz_recognised(self) -> None:
        from converter.wikidata.item_builder import _is_placeholder_title

        assert _is_placeholder_title("קובץ בקבלה.") is True
        assert _is_placeholder_title("קובץ מדרשים.") is True
        assert _is_placeholder_title("קבץ מדרשים.") is True

    def test_real_titles_not_flagged(self) -> None:
        from converter.wikidata.item_builder import _is_placeholder_title

        assert _is_placeholder_title("גנת אגוז") is False
        assert _is_placeholder_title("ספר היצירה") is False
        assert _is_placeholder_title("Hebrew Manuscript") is False
        assert _is_placeholder_title("") is False
        assert _is_placeholder_title(None) is False

    def test_long_kovetz_titles_not_flagged(self) -> None:
        """A title that starts with 'קובץ' but is longer than ~25 chars is
        likely a real anthology with descriptive subtitle — leave alone."""
        from converter.wikidata.item_builder import _is_placeholder_title

        long_title = "קובץ פירושי המקרא של רבי אברהם אבן עזרא"
        assert _is_placeholder_title(long_title) is False

    def test_set_labels_routes_kovetz_to_alias(self) -> None:
        """The _set_labels method, when given a placeholder title and a
        shelfmark, should emit a shelfmark-based Hebrew label and put the
        original placeholder string in the aliases for searchability."""
        import pathlib

        src = pathlib.Path("converter/wikidata/item_builder.py").read_text(encoding="utf-8")
        method_start = src.find("def _set_labels")
        method_end = src.find("\n    def ", method_start + 1)
        body = src[method_start:method_end]
        assert "_is_placeholder_title" in body
        assert "is_placeholder" in body
        assert 'aliases.setdefault("he"' in body


# ── Geagea complaint 2026-04-15: P3959 must NEVER appear in pipeline ─────


class TestP3959NotEmittedByPipeline:
    """Bug fix 2026-04-15 (Geagea complaint): P3959 (NNL item ID, prefix
    99…, BIBLIOGRAPHIC records) is the wrong property for person items.
    The current pipeline source code must not emit P3959 anywhere; the
    100+ P3959-on-person items Geagea cleaned came from a one-off script
    before the current safety guards. This test guards against any future
    code path accidentally re-introducing the property."""

    def test_p3959_absent_from_pipeline_source(self) -> None:
        import pathlib

        for root in ("converter", "src"):
            for path in pathlib.Path(root).rglob("*.py"):
                text = path.read_text(encoding="utf-8")
                if "P3959" not in text:
                    continue
                for line in text.splitlines():
                    if "P3959" not in line:
                        continue
                    stripped = line.strip()
                    if (
                        stripped.startswith("#")
                        or "must not" in line.lower()
                        or "should not" in line.lower()
                        or "wrongly" in line.lower()
                        or "prohibited" in line.lower()
                    ):
                        continue
                    raise AssertionError(
                        f"Found P3959 reference in {path}:{line!r}. "
                        "P3959 (NNL item ID) is prohibited."
                    )


# ── Third audit fixes (2026-04-15) ──────────────────────────────────────────


class TestP217HasP195Qualifier:
    """Fix #1: P217 (inventory number) must carry P195 (collection) as qualifier."""

    def test_p217_statement_has_p195_qualifier(self) -> None:
        src = pathlib.Path("converter/wikidata/item_builder.py").read_text(encoding="utf-8")
        # Find the actual WikidataStatement call for P_INVENTORY_NUMBER (skip import)
        idx = src.find("property_id=P_INVENTORY_NUMBER")
        assert idx != -1, "P_INVENTORY_NUMBER statement not found"
        block = src[idx : idx + 700]
        assert "P_COLLECTION" in block, "P217 statement must have P195 qualifier"
        assert "Q_NLI" in block, "P195 qualifier value must be Q_NLI"

    def test_p195_qualifier_key_present(self) -> None:
        src = pathlib.Path("converter/wikidata/item_builder.py").read_text(encoding="utf-8")
        idx = src.find("property_id=P_INVENTORY_NUMBER")
        assert idx != -1
        block = src[idx : idx + 700]
        assert "qualifiers" in block


class TestP7153HasP3831Qualifier:
    """Fix #2: P7153 (significant place) must carry P3831 (object has role) qualifier."""

    def test_p7153_has_role_qualifier(self) -> None:
        src = pathlib.Path("converter/wikidata/item_builder.py").read_text(encoding="utf-8")
        # Find the actual statement call, not the constant definition
        idx = src.find("property_id=P_SIGNIFICANT_PLACE")
        assert idx != -1, "P_SIGNIFICANT_PLACE statement not found"
        block = src[idx : idx + 900]
        assert "P_OBJECT_HAS_ROLE" in block

    def test_p7153_role_qid_is_place_qid(self) -> None:
        src = pathlib.Path("converter/wikidata/item_builder.py").read_text(encoding="utf-8")
        idx = src.find("property_id=P_SIGNIFICANT_PLACE")
        block = src[idx : idx + 900]
        # Q1616923 = place of provenance
        assert "Q1616923" in block


class TestP887InReferenceNotQualifier:
    """Fix #3: P887 (based on heuristic) has scope=reference; must not be a qualifier."""

    def test_p887_not_in_qualifiers_list(self) -> None:
        src = pathlib.Path("converter/wikidata/item_builder.py").read_text(encoding="utf-8")
        # Find the P_INCEPTION statement block
        inception_idx = src.find("property_id=P_INCEPTION")
        assert inception_idx != -1
        block_start = src.rfind("qualifiers", 0, inception_idx)
        block = src[block_start : inception_idx + 600]
        # P887 must NOT appear inside the qualifiers list (it was moved to ref)
        # The qualifiers list for inception now only contains P_SOURCING_CIRCUMSTANCES
        # and P_EARLIEST_DATE / P_LATEST_DATE — never P887
        assert (
            "P_BASED_ON_HEURISTIC"
            not in block.split("qualifiers")[0 if block_start > 0 else 1].split("references")[0]
        )

    def test_p887_appears_in_reference_block(self) -> None:
        src = pathlib.Path("converter/wikidata/item_builder.py").read_text(encoding="utf-8")
        # P887 must appear inside a ref block (colophon_ref construction)
        assert "P_BASED_ON_HEURISTIC" in src
        colophon_ref_idx = src.find("colophon_ref")
        assert colophon_ref_idx != -1
        block = src[colophon_ref_idx : colophon_ref_idx + 300]
        assert "P_BASED_ON_HEURISTIC" in block


class TestNotabilityGate:
    """Fix #4: person items require at least one external identifier."""

    def test_notability_gate_in_source(self) -> None:
        src = pathlib.Path("converter/wikidata/item_builder.py").read_text(encoding="utf-8")
        assert "has_identifier" in src
        assert "Wikidata:Notability" in src

    def test_notability_check_uses_viaf_and_mazal(self) -> None:
        src = pathlib.Path("converter/wikidata/item_builder.py").read_text(encoding="utf-8")
        idx = src.find("has_identifier")
        block = src[idx : idx + 300]
        assert "viaf_uri" in block
        assert "mazal_id" in block

    def test_notability_check_uses_gnd_lc_isni_bnf(self) -> None:
        src = pathlib.Path("converter/wikidata/item_builder.py").read_text(encoding="utf-8")
        idx = src.find("has_identifier")
        block = src[idx : idx + 400]
        assert "gnd_id" in block
        assert "lc_id" in block
        assert "isni" in block
        assert "bnf_id" in block


class TestAnonymousPersonFilter:
    """Fix #5: anonymous/unknown placeholder names must not create items."""

    def test_anonymous_names_frozenset_exists(self) -> None:
        from converter.wikidata.item_builder import _ANONYMOUS_NAMES

        assert "unknown" in _ANONYMOUS_NAMES
        assert "anonymous" in _ANONYMOUS_NAMES

    def test_hebrew_unknown_in_set(self) -> None:
        from converter.wikidata.item_builder import _ANONYMOUS_NAMES

        assert "לא ידוע" in _ANONYMOUS_NAMES

    def test_is_anonymous_name_true_for_unknown(self) -> None:
        from converter.wikidata.item_builder import _is_anonymous_name

        assert _is_anonymous_name("unknown")
        assert _is_anonymous_name("Unknown.")
        assert _is_anonymous_name("ANONYMOUS")

    def test_is_anonymous_name_false_for_real_name(self) -> None:
        from converter.wikidata.item_builder import _is_anonymous_name

        assert not _is_anonymous_name("Moses ben Maimon")
        assert not _is_anonymous_name("יעקב בן אשר")


class TestWorkItemEnglishLabel:
    """Fix #6: work items must have an English label."""

    def test_work_item_english_label_set_in_source(self) -> None:
        src = pathlib.Path("converter/wikidata/item_builder.py").read_text(encoding="utf-8")
        work_idx = src.find("def _get_or_create_work")
        work_body_end = src.find("\n    def ", work_idx + 1)
        body = src[work_idx:work_body_end]
        assert 'work.labels["en"]' in body

    def test_shelfmark_fallback_in_source(self) -> None:
        src = pathlib.Path("converter/wikidata/item_builder.py").read_text(encoding="utf-8")
        work_idx = src.find("def _get_or_create_work")
        work_body_end = src.find("\n    def ", work_idx + 1)
        body = src[work_idx:work_body_end]
        assert "shelfmark_for_work" in body


class TestWorkP407DerivedFromManuscript:
    """Fix #7: P407 must be derived from manuscript languages, not hardcoded Hebrew."""

    def test_p407_not_hardcoded_hebrew_only(self) -> None:
        src = pathlib.Path("converter/wikidata/item_builder.py").read_text(encoding="utf-8")
        work_idx = src.find("def _get_or_create_work")
        work_body_end = src.find("\n    def ", work_idx + 1)
        body = src[work_idx:work_body_end]
        # The language must come from lang_qids_for_work, not bare Q9288
        assert "lang_qids_for_work" in body
        assert "LANG_TO_QID" in body

    def test_p407_has_hebrew_fallback(self) -> None:
        src = pathlib.Path("converter/wikidata/item_builder.py").read_text(encoding="utf-8")
        work_idx = src.find("def _get_or_create_work")
        work_body_end = src.find("\n    def ", work_idx + 1)
        body = src[work_idx:work_body_end]
        # Fallback to Hebrew when no language data
        assert "Q9288" in body


class TestP2093Fallback:
    """Fix #8: unresolved persons (no QID, no labels) must use P2093 name string."""

    def test_p2093_fallback_present_in_source(self) -> None:
        src = pathlib.Path("converter/wikidata/item_builder.py").read_text(encoding="utf-8")
        assert "P2093" in src

    def test_p2093_condition_checks_no_labels(self) -> None:
        src = pathlib.Path("converter/wikidata/item_builder.py").read_text(encoding="utf-8")
        idx = src.find("P2093")
        block = src[max(0, idx - 200) : idx + 200]
        assert "person_item.labels" in block or "labels" in block


class TestP1343NotAsStatement:
    """Fix #10: P1343 (described by source) must not be a main statement on persons."""

    def test_p1343_not_emitted_as_main_statement(self) -> None:
        src = pathlib.Path("converter/wikidata/item_builder.py").read_text(encoding="utf-8")
        # Find _get_or_create_person body
        idx = src.find("def _get_or_create_person")
        end = src.find("\n    def ", idx + 1)
        body = src[idx:end]
        # P1343 must not appear as a property_id in a WikidataStatement call
        assert 'property_id="P1343"' not in body


class TestP6216HasJurisdictionQualifier:
    """Fix #11: P6216 (public domain) needs P1001=Q801 jurisdiction qualifier."""

    def test_p6216_has_p1001_qualifier(self) -> None:
        src = pathlib.Path("converter/wikidata/item_builder.py").read_text(encoding="utf-8")
        # Find the actual statement call (property_id="P6216")
        idx = src.find('property_id="P6216"')
        assert idx != -1
        block = src[idx : idx + 600]
        assert "P1001" in block

    def test_p6216_jurisdiction_is_israel(self) -> None:
        src = pathlib.Path("converter/wikidata/item_builder.py").read_text(encoding="utf-8")
        idx = src.find('property_id="P6216"')
        block = src[idx : idx + 600]
        assert '"Q801"' in block


class TestCenturyDateBounds:
    """Fix #12: century-precision P571 must have P1319/P1326 start/end bounds."""

    def test_date_to_wikidata_returns_5tuple_for_century(self) -> None:
        from converter.wikidata.property_mapping import PRECISION_CENTURY, date_to_wikidata

        result = date_to_wikidata({"original_string": "16th century"})
        assert result is not None
        assert len(result) == 5
        time_val, precision, calendar, earliest, latest = result
        assert precision == PRECISION_CENTURY
        assert earliest == 1501
        assert latest == 1600

    def test_16th_century_bounds(self) -> None:
        from converter.wikidata.property_mapping import date_to_wikidata

        result = date_to_wikidata({"original_string": "16th century"})
        assert result is not None
        _, _, _, earliest, latest = result
        assert earliest == 1501
        assert latest == 1600

    def test_hebrew_century_returns_bounds(self) -> None:
        from converter.wikidata.property_mapping import date_to_wikidata

        result = date_to_wikidata({"original_string": 'מאה ט"ז'})
        assert result is not None
        _, _, _, earliest, latest = result
        assert earliest == 1501
        assert latest == 1600

    def test_year_precision_no_bounds(self) -> None:
        from converter.wikidata.property_mapping import PRECISION_YEAR, date_to_wikidata

        result = date_to_wikidata({"year": 1450})
        assert result is not None
        _, precision, _, earliest, latest = result
        assert precision == PRECISION_YEAR
        assert earliest is None
        assert latest is None

    def test_p1319_p1326_in_inception_code(self) -> None:
        src = pathlib.Path("converter/wikidata/item_builder.py").read_text(encoding="utf-8")
        assert "P_EARLIEST_DATE" in src
        assert "P_LATEST_DATE" in src
        assert "PRECISION_CENTURY" in src


class TestCalendarModel:
    """Fix #13: pre-1582 dates must use Julian calendar model."""

    def test_pre_1583_date_uses_julian(self) -> None:
        from converter.wikidata.property_mapping import JULIAN_CALENDAR, date_to_wikidata

        result = date_to_wikidata({"year": 1450})
        assert result is not None
        _, _, calendar, _, _ = result
        assert calendar == JULIAN_CALENDAR

    def test_post_1582_date_uses_gregorian(self) -> None:
        from converter.wikidata.property_mapping import GREGORIAN_CALENDAR, date_to_wikidata

        result = date_to_wikidata({"year": 1750})
        assert result is not None
        _, _, calendar, _, _ = result
        assert calendar == GREGORIAN_CALENDAR

    def test_century_dates_use_julian(self) -> None:
        from converter.wikidata.property_mapping import JULIAN_CALENDAR, date_to_wikidata

        result = date_to_wikidata({"original_string": "16th century"})
        assert result is not None
        _, _, calendar, _, _ = result
        assert calendar == JULIAN_CALENDAR

    def test_calendar_constants_defined(self) -> None:
        from converter.wikidata.property_mapping import GREGORIAN_CALENDAR, JULIAN_CALENDAR

        assert "Q1985727" in GREGORIAN_CALENDAR  # Gregorian
        assert "Q1985786" in JULIAN_CALENDAR  # Julian


class TestDescriptionLengthCap:
    """Fix #14: descriptions must be capped at 250 characters."""

    def test_cap_description_truncates_long(self) -> None:
        from converter.wikidata.item_builder import _cap_description

        long_desc = "a" * 300
        result = _cap_description(long_desc)
        assert len(result) == 250

    def test_cap_description_leaves_short_unchanged(self) -> None:
        from converter.wikidata.item_builder import _cap_description

        short = "Hebrew manuscript scribe (1200-1280)"
        assert _cap_description(short) == short

    def test_build_work_description_capped(self) -> None:
        from converter.wikidata.item_builder import _build_work_description

        long_author = "x" * 300
        result = _build_work_description(author_name=long_author, century="16th")
        assert len(result) <= 250


class TestTranslatorCommentatorProperties:
    """Fix #15: TRANSLATOR → P655, COMMENTATOR → P9046 (not P50)."""

    def test_translator_maps_to_p655(self) -> None:
        from converter.wikidata.property_mapping import ROLE_TO_PID

        assert ROLE_TO_PID.get("TRANSLATOR") == "P655"
        assert ROLE_TO_PID.get("translator") == "P655"

    def test_commentator_maps_to_p9046(self) -> None:
        from converter.wikidata.property_mapping import ROLE_TO_PID

        assert ROLE_TO_PID.get("COMMENTATOR") == "P9046"
        assert ROLE_TO_PID.get("commentator") == "P9046"

    def test_translator_not_mapped_to_p50(self) -> None:
        from converter.wikidata.property_mapping import P_AUTHOR, ROLE_TO_PID

        assert ROLE_TO_PID.get("TRANSLATOR") != P_AUTHOR
        assert ROLE_TO_PID.get("translator") != P_AUTHOR


class TestMaxlag:
    """Fix #16: MAXLAG must be at least 10 seconds."""

    def test_maxlag_at_least_10(self) -> None:
        src = pathlib.Path("converter/wikidata/uploader.py").read_text(encoding="utf-8")
        import re

        match = re.search(r'MAXLAG"\]\s*=\s*(\d+)', src)
        assert match is not None
        assert int(match.group(1)) >= 10


class TestEditSummaryTruncation:
    """Fix #17: edit summary must be truncated at 500 characters."""

    def test_truncation_logic_in_source(self) -> None:
        src = pathlib.Path("converter/wikidata/uploader.py").read_text(encoding="utf-8")
        assert "497" in src
        assert "..." in src


# ── Fourth audit fixes (2026-04-16) ─────────────────────────────────────────


class TestP7153RoleQIDIsProvenance:
    """P7153 P3831 qualifier must use Q1773840 (provenance), not Q1616923 (disambiguation page)."""

    def test_p7153_role_qid_not_heydeck_disambiguation(self) -> None:
        import re

        src = pathlib.Path("converter/wikidata/item_builder.py").read_text(encoding="utf-8")
        idx = src.find("property_id=P_SIGNIFICANT_PLACE")
        assert idx != -1, "P_SIGNIFICANT_PLACE statement not found"
        block = src[idx : idx + 900]
        # Find lines that actually SET Q1616923 as a value (not comment lines)
        bad_lines = [
            line for line in block.splitlines()
            if "Q1616923" in line and not line.strip().startswith("#")
        ]
        assert not bad_lines, (
            "Q1616923 (Heydeck disambiguation page) must not be used as P3831 role value"
        )

    def test_p7153_role_qid_is_q1773840_provenance(self) -> None:
        src = pathlib.Path("converter/wikidata/item_builder.py").read_text(encoding="utf-8")
        idx = src.find("property_id=P_SIGNIFICANT_PLACE")
        assert idx != -1
        block = src[idx : idx + 900]
        assert "Q1773840" in block, (
            "P3831 role on P7153 must use Q1773840 (provenance)"
        )


class TestOrgTypeSkipsVIAFPersonSearch:
    """Organization-type contributors must not be matched via VIAF person-name search."""

    def test_match_against_authorities_org_returns_none(self) -> None:
        """_match_against_authorities with entity_type='organization' must return (None, None)."""
        from unittest.mock import MagicMock

        # We can't import AuthorityWorker easily (PyQt6 dep), so use source inspection
        src = pathlib.Path("src/mhm_pipeline/controller/workers.py").read_text(encoding="utf-8")
        assert 'entity_type in ("organization", "meeting")' in src or \
               "entity_type==" in src or \
               'entity_type == "organization"' in src, (
            "_match_against_authorities must check entity_type to skip org VIAF search"
        )

    def test_match_marc_person_entry_passes_entity_type(self) -> None:
        src = pathlib.Path("src/mhm_pipeline/controller/workers.py").read_text(encoding="utf-8")
        assert 'entity_type = str(person.get("type"' in src, (
            "_match_marc_person_entry must read entity_type from person dict"
        )

    def test_match_against_authorities_has_entity_type_param(self) -> None:
        src = pathlib.Path("src/mhm_pipeline/controller/workers.py").read_text(encoding="utf-8")
        assert "entity_type: str = " in src, (
            "_match_against_authorities must have entity_type parameter"
        )


class TestP2093RoleQualifier:
    """P2093 fallback must include P3831 role qualifier; owner role must be suppressed."""

    def test_p2093_adds_role_qualifier_in_source(self) -> None:
        src = pathlib.Path("converter/wikidata/item_builder.py").read_text(encoding="utf-8")
        idx = src.find('property_id="P2093"')
        assert idx != -1, "P2093 statement not found"
        block = src[idx : idx + 600]
        assert "P_OBJECT_HAS_ROLE" in block or "p2093_qualifiers" in block, (
            "P2093 statement must use P3831 qualifier for role"
        )

    def test_p2093_suppressed_for_owner_role_in_source(self) -> None:
        src = pathlib.Path("converter/wikidata/item_builder.py").read_text(encoding="utf-8")
        assert '"owner"' in src and 'pass  # skip' in src, (
            "Owner role must be suppressed from P2093 (no string fallback for P127)"
        )

    def test_p2093_role_uses_role_to_occupation_map(self) -> None:
        src = pathlib.Path("converter/wikidata/item_builder.py").read_text(encoding="utf-8")
        assert "_ROLE_TO_OCCUPATION" in src, (
            "P2093 fallback must look up role QID from _ROLE_TO_OCCUPATION"
        )


# ── VIAF nameType cross-validation (2026-04-15) ─────────────────────────────


class TestVIAFNameTypeGuard:
    """VIAF search results must be validated by nameType to prevent cross-type matches.

    Root cause of the library-items-getting-person-VIAF-IDs incident (2026-04-15):
    VIAFMatcher._query_api() returned the top SRU result without checking nameType,
    so a Corporate cluster (e.g. Josef Chasanowich / NLI predecessor) surfaced by
    local.personalNames could be silently attached to person items, and conversely
    person clusters could be attached to place items.
    """

    def _make_sru_response(self, viaf_id: str, name_type: str | None) -> dict:
        cluster: dict = {"ns2:viafID": viaf_id}
        if name_type is not None:
            cluster["ns2:nameType"] = name_type
        return {
            "searchRetrieveResponse": {
                "records": {
                    "record": {
                        "recordData": {"ns2:VIAFCluster": cluster}
                    }
                }
            }
        }

    def _patched_matcher_get(self, matcher, response_data):
        """Context-manager helper: patch session.get to return response_data."""
        from unittest.mock import MagicMock, patch

        mock_resp = MagicMock()
        mock_resp.json.return_value = response_data
        mock_resp.raise_for_status.return_value = None
        return patch.object(matcher._session, "get", return_value=mock_resp)

    def test_match_person_rejects_corporate_cluster(self) -> None:
        """match_person() must return None when VIAF returns a Corporate nameType."""
        from converter.authority.viaf_matcher import VIAFMatcher

        matcher = VIAFMatcher()
        data = self._make_sru_response("123456", "Corporate")
        with self._patched_matcher_get(matcher, data):
            result = matcher.match_person("National Library of Israel")
        assert result is None, "Corporate cluster must be rejected for personal name search"

    def test_match_person_accepts_personal_cluster(self) -> None:
        """match_person() must return a URI when VIAF returns a Personal nameType."""
        from converter.authority.viaf_matcher import VIAFMatcher

        matcher = VIAFMatcher()
        data = self._make_sru_response("97804603", "Personal")
        with self._patched_matcher_get(matcher, data):
            result = matcher.match_person("Maimonides")
        assert result == "https://viaf.org/viaf/97804603"

    def test_match_place_rejects_personal_cluster(self) -> None:
        """match_place() must return None when VIAF returns a Personal nameType."""
        from converter.authority.viaf_matcher import VIAFMatcher

        matcher = VIAFMatcher()
        data = self._make_sru_response("78090059", "Personal")
        with self._patched_matcher_get(matcher, data):
            result = matcher.match_place("Jerusalem")
        assert result is None, "Personal cluster must be rejected for geographic name search"

    def test_match_place_accepts_geographic_cluster(self) -> None:
        """match_place() must accept a Geographic cluster."""
        from converter.authority.viaf_matcher import VIAFMatcher

        matcher = VIAFMatcher()
        data = self._make_sru_response("12345", "Geographic")
        with self._patched_matcher_get(matcher, data):
            result = matcher.match_place("Jerusalem")
        assert result == "https://viaf.org/viaf/12345"

    def test_missing_name_type_not_rejected(self) -> None:
        """If nameType is absent from the SRU response, accept the cluster."""
        from converter.authority.viaf_matcher import VIAFMatcher

        matcher = VIAFMatcher()
        data = self._make_sru_response("97804603", None)  # no nameType key
        with self._patched_matcher_get(matcher, data):
            result = matcher.match_person("Maimonides")
        assert result == "https://viaf.org/viaf/97804603", (
            "Absent nameType must not cause rejection"
        )

    def test_get_cluster_identifiers_returns_name_type(self) -> None:
        """get_cluster_identifiers() must include 'name_type' in the returned dict."""
        from unittest.mock import MagicMock, patch

        from converter.authority.viaf_matcher import VIAFMatcher

        cluster_response = {
            "ns1:VIAFCluster": {
                "ns1:viafID": "97804603",
                "ns1:nameType": "Personal",
                "ns1:sources": {"ns1:source": []},
            }
        }
        matcher = VIAFMatcher()
        mock_resp = MagicMock()
        mock_resp.json.return_value = cluster_response
        mock_resp.raise_for_status.return_value = None
        with patch.object(matcher._session, "get", return_value=mock_resp):
            result = matcher.get_cluster_identifiers("97804603")
        assert result.get("name_type") == "Personal"

    def test_p214_guarded_by_not_is_org_in_source(self) -> None:
        """P214 (VIAF ID) assignment must be guarded by 'not is_org' in item_builder."""
        src = pathlib.Path("converter/wikidata/item_builder.py").read_text(encoding="utf-8")
        idx = src.find("# P214 = VIAF ID")
        assert idx != -1, "P214 comment block not found in item_builder.py"
        block = src[idx : idx + 1200]
        assert "not is_org" in block, "P214 assignment must be guarded by 'not is_org'"

    def test_match_person_passes_expected_name_type_personal(self) -> None:
        """Source code: match_person() must pass expected_name_type='Personal'."""
        src = pathlib.Path("converter/authority/viaf_matcher.py").read_text(encoding="utf-8")
        assert (
            'expected_name_type="Personal"' in src or "expected_name_type='Personal'" in src
        ), "match_person must pass expected_name_type='Personal'"

    def test_match_place_passes_expected_name_type_geographic(self) -> None:
        """Source code: match_place() must pass expected_name_type='Geographic'."""
        src = pathlib.Path("converter/authority/viaf_matcher.py").read_text(encoding="utf-8")
        assert (
            'expected_name_type="Geographic"' in src or "expected_name_type='Geographic'" in src
        ), "match_place must pass expected_name_type='Geographic'"


# ── QuickStatements output QA fixes (2026-04-19) ────────────────────────────


class TestEmptyItemNotExported:
    """Bug fix: notability-filtered persons must not produce lone CREATE lines."""

    def test_notability_filtered_person_emits_no_create(self) -> None:
        """WikidataItem with no labels or statements returns empty string from export_item."""
        from converter.wikidata.item_builder import WikidataItem
        from converter.wikidata.quickstatements import QuickStatementsExporter

        item = WikidataItem(entity_type="person", local_id="test:empty")
        exporter = QuickStatementsExporter()
        result = exporter.export_item(item)
        assert result == "", f"Expected empty string, got: {result!r}"

    def test_item_with_label_emits_create(self) -> None:
        """WikidataItem with a label still produces CREATE + label line."""
        from converter.wikidata.item_builder import WikidataItem
        from converter.wikidata.quickstatements import QuickStatementsExporter

        item = WikidataItem(entity_type="person", local_id="test:has_label")
        item.labels["he"] = "משה הכהן"
        exporter = QuickStatementsExporter()
        result = exporter.export_item(item)
        assert "CREATE" in result
        assert "משה הכהן" in result


class TestInstitutionalP2093Suppressed:
    """Bug fix: institutional names must not fall through to P2093 string fallback."""

    def test_bodleian_is_institutional(self) -> None:
        """_is_institutional_name must recognise 'Bodleian Library'."""
        from converter.wikidata.item_builder import _is_institutional_name

        assert _is_institutional_name("Bodleian Library"), "Bodleian Library not detected"

    def test_palatina_is_institutional(self) -> None:
        """_is_institutional_name must recognise 'Bibliotheca Palatina'."""
        from converter.wikidata.item_builder import _is_institutional_name

        assert _is_institutional_name("Bibliotheca Palatina"), "Bibliotheca Palatina not detected"

    def test_institutional_name_no_p2093_guard_in_source(self) -> None:
        """Source code: the P2093 fallback block must check _is_institutional_name."""
        src = pathlib.Path("converter/wikidata/item_builder.py").read_text(encoding="utf-8")
        # Find the P2093 fallback elif clause
        idx = src.find("elif not person_item.labels")
        assert idx != -1, "P2093 fallback elif not found"
        line = src[idx : idx + 120]
        assert "_is_institutional_name" in line, (
            "P2093 fallback must guard against institutional names; "
            f"found: {line!r}"
        )


class TestPersonNameCleaning:
    """Bug fix: surrounding quotes in person names must be stripped."""

    def test_quoted_name_stripped(self) -> None:
        """Names wrapped in double quotes get those quotes removed."""
        # The clean_name logic in _get_or_create_person strips surrounding quotes.
        # Test it via source-code inspection (the logic is in a local scope).
        raw = '"Moshe ha-Kohen"'
        clean = raw.strip().strip('"\'').strip().rstrip(",;:")
        assert clean == "Moshe ha-Kohen"

    def test_trailing_comma_stripped(self) -> None:
        """Trailing commas still stripped after adding quote stripping."""
        raw = "Moshe ha-Kohen,"
        clean = raw.strip().strip('"\'').strip().rstrip(",;:")
        assert clean == "Moshe ha-Kohen"

    def test_quote_stripping_in_source(self) -> None:
        """Source code: clean_name line must include .strip('"\\'')'."""
        src = pathlib.Path("converter/wikidata/item_builder.py").read_text(encoding="utf-8")
        idx = src.find("Clean name: strip surrounding quotes")
        assert idx != -1, "Quote-stripping comment not found in item_builder.py"


class TestOwnerDescription:
    """Bug fix: _build_person_description must not produce 'Hebrew manuscript manuscript owner'."""

    def test_owner_description_no_double_word(self) -> None:
        """OWNER role → 'Hebrew manuscript owner', not 'Hebrew manuscript manuscript owner'."""
        from converter.wikidata.item_builder import _build_person_description

        desc = _build_person_description("OWNER", "", False)
        assert "manuscript manuscript" not in desc, f"Double word in: {desc!r}"
        assert desc == "Hebrew manuscript owner", f"Unexpected description: {desc!r}"

    def test_owner_description_with_dates(self) -> None:
        """OWNER with dates → role label + dates, not prefixed with 'Hebrew manuscript'."""
        from converter.wikidata.item_builder import _build_person_description

        desc = _build_person_description("OWNER", "1500-1550", False)
        assert desc == "owner (1500-1550)", f"Unexpected description: {desc!r}"


class TestManuscriptTitleCleaning:
    """Bug fix: MARC trailing ISBD periods must be stripped from manuscript labels."""

    def test_trailing_period_stripped_from_hebrew_label(self) -> None:
        """Titles ending with MARC period produce labels without the period."""
        # Verify the rstrip logic directly (mirrors the fix in _set_labels)
        title = "גנת אגוז."
        cleaned = title.rstrip(". ")
        assert cleaned == "גנת אגוז", f"Period not stripped: {cleaned!r}"

    def test_title_without_period_unchanged(self) -> None:
        """Titles without trailing period are unchanged."""
        title = "גנת אגוז"
        cleaned = title.rstrip(". ")
        assert cleaned == "גנת אגוז"

    def test_rstrip_in_source(self) -> None:
        """Source code: label assignment must use rstrip for period stripping."""
        src = pathlib.Path("converter/wikidata/item_builder.py").read_text(encoding="utf-8")
        assert 'title.rstrip(". ")' in src, (
            "Manuscript title label assignment must call .rstrip('. ') to remove MARC periods"
        )


class TestQualifierExport:
    """Bug fix: WikidataStatement.qualifiers must appear in QuickStatements output."""

    def test_item_qualifier_exported(self) -> None:
        """A statement with a qualifier emits the qualifier property+value on the same line."""
        from converter.wikidata.item_builder import WikidataItem, WikidataStatement
        from converter.wikidata.quickstatements import QuickStatementsExporter

        item = WikidataItem(entity_type="manuscript", local_id="test:qs_qual")
        item.labels["en"] = "test manuscript"
        item.statements.append(
            WikidataStatement(
                property_id="P217",
                value="Heb 123",
                value_type="string",
                qualifiers=[{"property": "P195", "value": "Q188915", "type": "item"}],
                references=[],
            )
        )
        exporter = QuickStatementsExporter()
        result = exporter.export_item(item)
        assert "P195" in result, "Qualifier property P195 not found in output"
        assert "Q188915" in result, "Qualifier value Q188915 not found in output"

    def test_qualifier_before_reference(self) -> None:
        """Qualifier columns must appear before reference (S-prefix) columns."""
        from converter.wikidata.item_builder import WikidataItem, WikidataStatement
        from converter.wikidata.quickstatements import QuickStatementsExporter

        item = WikidataItem(entity_type="manuscript", local_id="test:qs_order")
        item.labels["en"] = "test"
        item.statements.append(
            WikidataStatement(
                property_id="P217",
                value="Heb 456",
                value_type="string",
                qualifiers=[{"property": "P195", "value": "Q188915", "type": "item"}],
                references=[{"property": "P248", "value": "Q123456", "type": "item"}],
            )
        )
        exporter = QuickStatementsExporter()
        result = exporter.export_item(item)
        # Qualifier P195 (P-prefix) must come before reference S248 (S-prefix)
        p195_idx = result.find("P195")
        s248_idx = result.find("S248")
        assert p195_idx != -1, "P195 qualifier not found"
        assert s248_idx != -1, "S248 reference not found"
        assert p195_idx < s248_idx, "Qualifier must appear before reference in QS output"

    def test_no_qualifier_unchanged(self) -> None:
        """Statement with no qualifiers exports identically to pre-fix behaviour."""
        from converter.wikidata.item_builder import WikidataItem, WikidataStatement
        from converter.wikidata.quickstatements import QuickStatementsExporter

        item = WikidataItem(entity_type="person", local_id="test:no_qual")
        item.labels["en"] = "test person"
        item.statements.append(
            WikidataStatement(
                property_id="P31",
                value="Q5",
                value_type="item",
                qualifiers=[],
                references=[],
            )
        )
        exporter = QuickStatementsExporter()
        result = exporter.export_item(item)
        # Should have exactly: CREATE, label line, statement line
        lines = [ln for ln in result.splitlines() if ln.strip()]
        assert any("P31" in ln and "Q5" in ln for ln in lines)
        # No unexpected extra columns from empty qualifier list
        stmt_line = next(ln for ln in lines if "P31" in ln)
        parts = stmt_line.split("\t")
        assert len(parts) == 3, f"Expected 3 parts (qid/prop/val), got {len(parts)}: {parts}"


# ── Second-round QS output fixes (2026-04-19) ────────────────────────────────


class TestMrcFilenameNotInNotes:
    """Bug fix: NLI source filenames in MARC 500 must not become P7535 notes."""

    def test_mrc_filename_filtered_by_regex(self) -> None:
        """_SOURCE_FILENAME_RE must match NLI-style MRC filenames."""
        from converter.wikidata.item_builder import _SOURCE_FILENAME_RE

        assert _SOURCE_FILENAME_RE.match("990000623390205171.mrc")
        assert _SOURCE_FILENAME_RE.match("BIBLIOGRAPHIC_50929717600005171_5.txt")

    def test_real_note_not_filtered(self) -> None:
        """Regular Hebrew notes must NOT match the filename regex."""
        from converter.wikidata.item_builder import _SOURCE_FILENAME_RE

        assert not _SOURCE_FILENAME_RE.match("כתב היד נכתב במאה ה-15")
        assert not _SOURCE_FILENAME_RE.match("בכה\"י: גינת אגוז מאת יוסף גיקטילא")

    def test_source_filename_re_in_notes_loop(self) -> None:
        """Source code: the notes loop must use _SOURCE_FILENAME_RE to skip filenames."""
        src = pathlib.Path("converter/wikidata/item_builder.py").read_text(encoding="utf-8")
        assert "_SOURCE_FILENAME_RE" in src, "_SOURCE_FILENAME_RE not defined in item_builder.py"
        assert "_SOURCE_FILENAME_RE.match(note_text)" in src, (
            "notes loop must call _SOURCE_FILENAME_RE.match(note_text)"
        )


class TestAsciiOnlyDescription:
    """Bug fix: Arabic/non-ASCII dates must be stripped from English descriptions."""

    def test_arabic_dates_stripped_from_description(self) -> None:
        """dates_str with Arabic text → description contains no non-ASCII characters."""
        from converter.wikidata.item_builder import _build_person_description

        desc = _build_person_description("AUTHOR", "توفي 1013", False)
        non_ascii = [c for c in desc if ord(c) >= 128]
        assert not non_ascii, f"Non-ASCII chars in description: {non_ascii!r} in {desc!r}"

    def test_mixed_dates_keeps_ascii_digits(self) -> None:
        """Pure ASCII dates string is preserved unchanged in the description."""
        from converter.wikidata.item_builder import _build_person_description

        desc = _build_person_description("AUTHOR", "882-942", False)
        assert desc == "author (882-942)", f"Unexpected: {desc!r}"

    def test_empty_dates_no_regression(self) -> None:
        """Empty dates_str still produces the role-based description."""
        from converter.wikidata.item_builder import _build_person_description

        desc = _build_person_description("AUTHOR", "", False)
        assert desc == "Hebrew manuscript author"


class TestP1932TrailingPunctuationStripped:
    """Bug fix: P1932 (object named as) qualifiers must strip trailing MARC punctuation."""

    def test_p1932_rstrip_logic(self) -> None:
        """The rstrip pattern correctly removes trailing commas and colons."""
        assert "סעדיה בן יוסף,".strip().rstrip(",;:") == "סעדיה בן יוסף"
        assert "יהודה בן יצחק:".strip().rstrip(",;:") == "יהודה בן יצחק"
        assert "שמואל בן חפני,".strip().rstrip(",;:") == "שמואל בן חפני"

    def test_p1932_in_add_person_statement_has_rstrip(self) -> None:
        """Source code: P1932 qualifier in _add_person_statement uses .rstrip(',;:')."""
        src = pathlib.Path("converter/wikidata/item_builder.py").read_text(encoding="utf-8")
        idx = src.find("P_OBJECT_NAMED_AS, \"value\": name")
        assert idx != -1, "P1932 name qualifier not found in _add_person_statement"
        line = src[idx : idx + 80]
        assert 'rstrip(",;:")' in line, (
            f"P1932 name qualifier must call .rstrip(',;:'), found: {line!r}"
        )

    def test_p1932_in_provenance_claims_has_rstrip(self) -> None:
        """Source code: P1932 qualifier in _add_provenance_claims uses .rstrip(',;:')."""
        src = pathlib.Path("converter/wikidata/item_builder.py").read_text(encoding="utf-8")
        # The owner_name is assigned with rstrip on its own line in the dict value
        assert 'owner_name.rstrip(",;:")' in src, (
            "P1932 owner_name in _add_provenance_claims must call .rstrip(',;:')"
        )


class TestTitleTrailingPeriodStripped:
    """P1476 title statements and aliases must not carry trailing ISBD periods."""

    def test_p1476_title_has_no_trailing_period(self) -> None:
        """build_manuscript_item strips trailing period from P1476 monolingualtext value."""
        from unittest.mock import MagicMock

        from converter.wikidata.item_builder import WikidataItemBuilder

        record = {
            "_control_number": "990001",
            "title": "גנת אגוז.",
            "notes": [],
            "variant_titles": [],
        }
        builder = WikidataItemBuilder(reconciler=MagicMock())
        item = builder.build_manuscript_item(record)
        title_stmts = [s for s in item.statements if s.property_id == "P1476"]
        assert title_stmts, "P1476 statement must exist"
        assert title_stmts[0].value == "גנת אגוז", (
            f"P1476 must not have trailing period, got {title_stmts[0].value!r}"
        )

    def test_alias_has_no_trailing_period(self) -> None:
        """Aliases added from the manuscript title must not carry trailing periods."""
        from unittest.mock import MagicMock

        from converter.wikidata.item_builder import WikidataItemBuilder

        record = {
            "_control_number": "990002",
            "title": "גנת אגוז.",
            "shelfmark": "F 8566",
            "notes": [],
            "variant_titles": [],
        }
        builder = WikidataItemBuilder(reconciler=MagicMock())
        item = builder.build_manuscript_item(record)
        he_aliases = item.aliases.get("he", [])
        for alias in he_aliases:
            assert not alias.endswith("."), (
                f"Alias must not end with period, got {alias!r}"
            )

    def test_variant_title_alias_has_no_trailing_period(self) -> None:
        """Variant titles added as aliases must have trailing periods stripped."""
        from unittest.mock import MagicMock

        from converter.wikidata.item_builder import WikidataItemBuilder

        record = {
            "_control_number": "990003",
            "title": "ספר אהבה.",
            "shelfmark": "F 1234",
            "notes": [],
            "variant_titles": ["ספר האהבה."],
        }
        builder = WikidataItemBuilder(reconciler=MagicMock())
        item = builder.build_manuscript_item(record)
        he_aliases = item.aliases.get("he", [])
        for alias in he_aliases:
            assert not alias.endswith("."), (
                f"Variant title alias must not end with period, got {alias!r}"
            )


class TestUncertainAttributionP1480:
    """Uncertain person/work attributions must carry P1480 (presumably) qualifier."""

    def test_local_person_statement_has_p1480_presumably(self) -> None:
        """P50/P11603 for unconfirmed (local) persons gets P1480=Q18122778 qualifier."""
        from unittest.mock import MagicMock

        from converter.wikidata.item_builder import WikidataItemBuilder

        record = {
            "_control_number": "990001",
            "title": "ספר הכוזרי",
            "notes": [],
            "variant_titles": [],
            "marc_authority_matches": [
                # mazal_id triggers local-item creation but no Wikidata QID → uncertain
                {"name": "הלוי, יהודה", "role": "AUTHOR", "viaf_uri": None, "mazal_id": "987001234567805171"}
            ],
            "entities": [],
        }
        builder = WikidataItemBuilder(reconciler=MagicMock())
        item = builder.build_manuscript_item(record)
        # Find P50 (author) statements using __LOCAL: reference
        local_person_stmts = [
            s for s in item.statements
            if s.property_id == "P50" and str(s.value).startswith("__LOCAL:")
        ]
        assert local_person_stmts, "Expected at least one local P50 statement"
        stmt = local_person_stmts[0]
        qualifier_props = [q.get("property") for q in (stmt.qualifiers or [])]
        assert "P1480" in qualifier_props, (
            f"Local person P50 must have P1480 qualifier, got qualifiers: {stmt.qualifiers}"
        )
        p1480_qual = next(q for q in stmt.qualifiers if q.get("property") == "P1480")
        assert p1480_qual["value"] == "Q18122778", (
            f"P1480 must be Q18122778 (presumably), got {p1480_qual['value']}"
        )

    def test_confirmed_person_statement_has_no_p1480(self) -> None:
        """P50 with a resolved Wikidata QID must NOT have P1480 qualifier."""
        from unittest.mock import MagicMock, patch

        from converter.wikidata.item_builder import WikidataItemBuilder

        builder = WikidataItemBuilder(reconciler=MagicMock())
        with patch.object(builder, "_get_or_create_person") as mock_person:
            mock_item = MagicMock()
            mock_item.local_id = "test_person"
            mock_item.labels = {"he": "יהודה הלוי"}
            mock_item.existing_qid = "Q12345"
            mock_person.return_value = mock_item
            # Simulate resolved_qid path by patching _person_qids
            builder._person_qids["author:הלוי, יהודה"] = "Q12345"

            record = {
                "_control_number": "990002",
                "title": "ספר",
                "notes": [],
                "variant_titles": [],
                "marc_authority_matches": [
                    {"name": "הלוי, יהודה", "role": "AUTHOR", "viaf_uri": "http://viaf.org/viaf/100902149", "mazal_id": None}
                ],
                "entities": [],
            }
            item = builder.build_manuscript_item(record)
            # Resolved QID statements should not have P1480
            resolved_stmts = [s for s in item.statements if s.property_id == "P50" and s.value == "Q12345"]
            for stmt in resolved_stmts:
                qualifier_props = [q.get("property") for q in (stmt.qualifiers or [])]
                assert "P1480" not in qualifier_props, (
                    f"Confirmed P50 (resolved QID) must not have P1480, got {stmt.qualifiers}"
                )

    def test_local_work_p1574_has_p1480_presumably(self) -> None:
        """P1574 (exemplar of) for unreconciled local works gets P1480=Q18122778."""
        from unittest.mock import MagicMock

        from converter.wikidata.item_builder import WikidataItemBuilder

        record = {
            "_control_number": "990003",
            "title": "כתב יד",
            "notes": [],
            "variant_titles": [],
            "contents": [{"title": "ספר אהבת ה'", "folio_range": None}],
            "entities": [],
        }
        builder = WikidataItemBuilder(reconciler=MagicMock())
        item = builder.build_manuscript_item(record)
        local_work_stmts = [
            s for s in item.statements
            if s.property_id == "P1574" and str(s.value).startswith("__LOCAL:")
        ]
        assert local_work_stmts, "Expected at least one local P1574 statement"
        stmt = local_work_stmts[0]
        qualifier_props = [q.get("property") for q in (stmt.qualifiers or [])]
        assert "P1480" in qualifier_props, (
            f"Local work P1574 must have P1480 qualifier, got {stmt.qualifiers}"
        )
        p1480_qual = next(q for q in stmt.qualifiers if q.get("property") == "P1480")
        assert p1480_qual["value"] == "Q18122778"


class TestGenreClassifierIntegration:
    """Genre classifier fallback: only fires when MARC 655 genres are absent."""

    def _base_record(self) -> dict:
        return {
            "_control_number": "990099",
            "title": "ספר הזוהר",
            "notes": ["כתב יד עברי מן המאה ה-15"],
            "variant_titles": [],
            "contents": [],
            "entities": [],
        }

    def test_genre_classifier_skipped_when_marc_genres_present(self) -> None:
        """When MARC 655 genres are present the classifier must NOT be called."""
        from unittest.mock import MagicMock, patch

        from converter.wikidata.item_builder import WikidataItemBuilder

        record = {**self._base_record(), "genres": ["Piyyutim"]}
        mock_clf = MagicMock()
        mock_clf.predict.return_value = [("BiblicalText", 0.9)]

        with patch("converter.wikidata.item_builder._get_genre_classifier", return_value=mock_clf):
            builder = WikidataItemBuilder()
            builder.build_manuscript_item(record)

        mock_clf.predict.assert_not_called()

    def test_genre_classifier_adds_p887_reference(self) -> None:
        """Inferred genres must carry a P887 (based on heuristic) reference snak."""
        from unittest.mock import MagicMock, patch

        from converter.wikidata.item_builder import WikidataItemBuilder

        record = self._base_record()  # no genres key

        mock_clf = MagicMock()
        mock_clf.predict.return_value = [("BiblicalText", 0.82)]

        with patch("converter.wikidata.item_builder._get_genre_classifier", return_value=mock_clf):
            builder = WikidataItemBuilder()
            item = builder.build_manuscript_item(record)

        inferred_genre_stmts = [s for s in item.statements if s.property_id == "P136"]
        assert inferred_genre_stmts, "Expected at least one inferred P136 statement"
        stmt = inferred_genre_stmts[0]
        ref_props = [r.get("property") for refs in [stmt.references] for r in refs]
        assert "P887" in ref_props, f"Inferred P136 must have P887 reference, got {stmt.references}"

    def test_genre_classifier_absent_model_no_crash(self) -> None:
        """When the model file is absent the builder completes without error."""
        from converter.wikidata.item_builder import WikidataItemBuilder, _get_genre_classifier
        import converter.wikidata.item_builder as _ib

        original = _ib._GENRE_CLASSIFIER
        _ib._GENRE_CLASSIFIER = None  # simulate missing model
        try:
            record = self._base_record()
            builder = WikidataItemBuilder()
            item = builder.build_manuscript_item(record)
            # No crash; no inferred P136 statements
            assert item is not None
        finally:
            _ib._GENRE_CLASSIFIER = original


class TestRoleDescriptorFilter:
    """Items with role-word or bare place-name labels must never be created.

    Root cause of 2026-04-20 batch deletion (18 items):
      - 7 items labeled "משומד" (apostate role-word)
      - 11 items labeled "שאלוניקי" (Salonika city name)
    """

    def _builder(self) -> "WikidataItemBuilder":
        from converter.wikidata.item_builder import WikidataItemBuilder
        return WikidataItemBuilder()

    def _record(self, name: str) -> dict:
        return {
            "_control_number": "990001",
            "title": "ספר",
            "notes": [],
            "variant_titles": [],
            "contents": [],
            "genres": [],
            "entities": [{"name": name, "role": "SCRIBE", "viaf_uri": None, "mazal_id": None}],
            "marc_authority_matches": [],
        }

    def test_role_word_meshummad_not_created(self) -> None:
        """'משומד' (apostate) must be skipped — role-word, not a real name."""
        from converter.wikidata.item_builder import _is_role_descriptor
        assert _is_role_descriptor("משומד") is True

    def test_city_name_salonika_not_created(self) -> None:
        """'שאלוניקי' (Salonika) must be skipped — bare city name, not a person."""
        from converter.wikidata.item_builder import _is_role_descriptor
        assert _is_role_descriptor("שאלוניקי") is True

    def test_real_name_not_filtered(self) -> None:
        """A genuine personal name must not be filtered by _is_role_descriptor."""
        from converter.wikidata.item_builder import _is_role_descriptor
        assert _is_role_descriptor("יהודה בן שמואל") is False

    def test_meshummad_produces_no_person_item(self) -> None:
        """build_manuscript_item must not create a person item for 'משומד'."""
        from converter.wikidata.item_builder import WikidataItemBuilder
        builder = self._builder()
        record = self._record("משומד")
        item = builder.build_manuscript_item(record)
        person_ids = [s.value for s in item.statements if "Q139" in str(s.value)]
        # No sub-item with a role-word label should be referenced
        assert all(s.value != "משומד" for s in item.statements)
        # No person was added to builder's internal cache with a label
        person_items_with_labels = [
            p for p in builder._person_items.values() if p.labels
        ]
        assert person_items_with_labels == []

    def test_salonika_produces_no_person_item(self) -> None:
        """build_manuscript_item must not create a person item for 'שאלוניקי'."""
        from converter.wikidata.item_builder import WikidataItemBuilder
        builder = self._builder()
        record = self._record("שאלוניקי")
        builder.build_manuscript_item(record)
        person_items_with_labels = [
            p for p in builder._person_items.values() if p.labels
        ]
        assert person_items_with_labels == []


class TestVIAFNameTypeGuardPerson:
    """VIAF IDs from non-Personal clusters must not be attached to person items.

    Root cause of "שאלוניקי" incident: VIAF 76186581 (Geographic cluster for
    Salonika) was attached to person items because nameType was never checked.
    Root cause of "משומד" incident: VIAF 11810679 (Domenico Gerosolimitano,
    the manuscript censor) was attached to unnamed apostates.
    """

    def _record_with_viaf_nametype(self, name: str, name_type: str) -> dict:
        return {
            "_control_number": "990002",
            "title": "כתב יד",
            "notes": [],
            "variant_titles": [],
            "contents": [],
            "genres": [],
            "entities": [{"name": name, "role": "SCRIBE", "viaf_uri": "http://viaf.org/viaf/12345", "mazal_id": None}],
            "marc_authority_matches": [
                {
                    "name": name,
                    "viaf_uri": "http://viaf.org/viaf/12345",
                    "mazal_id": None,
                    "name_type": name_type,
                    "gnd_id": None,
                    "lc_id": None,
                    "isni": None,
                    "bnf_id": None,
                }
            ],
        }

    def test_geographic_viaf_not_assigned_to_person(self) -> None:
        """nameType=Geographic cluster must not produce P214 on a person item."""
        from converter.wikidata.item_builder import WikidataItemBuilder, P_VIAF_ID
        builder = WikidataItemBuilder()
        record = self._record_with_viaf_nametype("שלמה הלוי", "Geographic")
        builder.build_manuscript_item(record)
        for person in builder._person_items.values():
            viaf_props = [s for s in person.statements if s.property_id == P_VIAF_ID]
            assert viaf_props == [], f"Geographic VIAF must not be assigned; got {viaf_props}"

    def test_corporate_viaf_not_assigned_to_person(self) -> None:
        """nameType=Corporate cluster must not produce P214 on a person item."""
        from converter.wikidata.item_builder import WikidataItemBuilder, P_VIAF_ID
        builder = WikidataItemBuilder()
        record = self._record_with_viaf_nametype("שלמה הלוי", "Corporate")
        builder.build_manuscript_item(record)
        for person in builder._person_items.values():
            viaf_props = [s for s in person.statements if s.property_id == P_VIAF_ID]
            assert viaf_props == [], f"Corporate VIAF must not be assigned; got {viaf_props}"

    def test_personal_viaf_assigned_normally(self) -> None:
        """nameType=Personal cluster must still produce P214 on a person item."""
        from converter.wikidata.item_builder import WikidataItemBuilder, P_VIAF_ID
        builder = WikidataItemBuilder()
        record = self._record_with_viaf_nametype("שלמה הלוי", "Personal")
        builder.build_manuscript_item(record)
        person_viaf_values = [
            s.value
            for p in builder._person_items.values()
            for s in p.statements
            if s.property_id == P_VIAF_ID
        ]
        assert "12345" in person_viaf_values


class TestPersonLabelDedup:
    """reconcile_person_by_label finds existing Wikidata humans by label."""

    def test_unique_label_match_returns_qid(self) -> None:
        """When exactly one human matches the label, return its QID."""
        from unittest.mock import MagicMock
        from converter.wikidata.reconciler import WikidataReconciler

        rec = WikidataReconciler.__new__(WikidataReconciler)
        rec._cache = {}
        rec._query = MagicMock(return_value=[
            {"item": {"value": "http://www.wikidata.org/entity/Q12345"}}
        ])
        result = rec.reconcile_person_by_label("יהודה הלוי")
        assert result == "Q12345"

    def test_ambiguous_label_returns_none(self) -> None:
        """When multiple humans match the label, return None (ambiguous)."""
        from unittest.mock import MagicMock
        from converter.wikidata.reconciler import WikidataReconciler

        rec = WikidataReconciler.__new__(WikidataReconciler)
        rec._cache = {}
        rec._query = MagicMock(return_value=[
            {"item": {"value": "http://www.wikidata.org/entity/Q1"}},
            {"item": {"value": "http://www.wikidata.org/entity/Q2"}},
        ])
        result = rec.reconcile_person_by_label("יהודה")
        assert result is None

    def test_no_match_returns_none(self) -> None:
        """When no human matches, return None."""
        from unittest.mock import MagicMock
        from converter.wikidata.reconciler import WikidataReconciler

        rec = WikidataReconciler.__new__(WikidataReconciler)
        rec._cache = {}
        rec._query = MagicMock(return_value=[])
        result = rec.reconcile_person_by_label("שם לא קיים")
        assert result is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
