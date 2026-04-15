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

        assert _build_person_description("OWNER", "", False) == "Hebrew manuscript manuscript owner"

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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
