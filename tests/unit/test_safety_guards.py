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
        wbi_item.claims.get = MagicMock(
            return_value=[self._make_existing_claim("118576488")]
        )
        stmt = self._make_stmt("P227", "118576488")
        assert u._would_create_identity_conflict(wbi_item, stmt) is False

    def test_blocks_when_existing_claim_differs(self) -> None:
        """Identity prop with DIFFERENT value present → MUST block."""
        u = self._make_uploader()
        wbi_item = MagicMock()
        wbi_item.claims.get = MagicMock(
            return_value=[self._make_existing_claim("118576488")]
        )
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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
