"""End-to-end integration tests for the Rule 46 Hebrew transliteration waterfall.

Unit tests cover each tier in isolation; these tests run the full 5-tier
waterfall (override → NLI MARC → Wikidata SPARQL → DICTA Nakdan →
consonantal ALA-LC) with **real** orchestration code, exercising the
priority order, cache integration, network gating, and graceful
degradation on missing components.

These integration tests also verify the **Stage 6 pipeline integration**:
that ``WikidataItemBuilder`` actually uses the new waterfall for work-label
and person-P2093 fallbacks, and that no record produces the legacy
"work from Hebrew manuscript ..." synthetic fallback.

Network behaviour:
- The Wikidata SPARQL tier is exercised against a **pre-seeded local
  cache file** to avoid live network calls in tests. The cache file path
  is passed via the ``cache_path`` argument.
- ``MHM_NO_NETWORK=true`` is asserted under several paths to confirm
  offline-first behaviour.
- The Nakdan tier is asserted to gracefully fall through when the model
  is absent (which it is in CI/tests because the HF cache won't have it).
"""

from __future__ import annotations

import json
import pathlib
from collections.abc import Iterator

import pytest

# ── Shared waterfall fixture ─────────────────────────────────────────


@pytest.fixture()
def seeded_cache(tmp_path: pathlib.Path) -> Iterator[pathlib.Path]:
    """Pre-seed the Wikidata SPARQL cache file with a few canonical entries.

    The waterfall consults this cache instead of issuing a network request
    when the cache path is passed through. This lets us test Tier 3 (SPARQL)
    deterministically without touching the live WDQS endpoint.
    """
    cache_file = tmp_path / "wikidata_reverse_label_cache.json"
    seed = {
        "version": 1,
        "entries": {
            # A famous figure NOT in the curated override dict, so Tier 3
            # is the path that resolves him.
            "אברהם אבן עזרא": {
                "english_label": "Abraham ibn Ezra (cached canonical)",
                "fetched_at": "2099-01-01T00:00:00Z",
                "ttl_seconds": 2592000,
            },
            # Negative cache entry: a name that Wikidata doesn't have an
            # English label for (mid-tier Italian transliteration of
            # "ריקרדו"). Falls through to Tier 4/5.
            "ריקרדו": {
                "english_label": None,
                "fetched_at": "2099-01-01T00:00:00Z",
                "ttl_seconds": 86400,
            },
        },
    }
    cache_file.write_text(json.dumps(seed), encoding="utf-8")
    yield cache_file


# ── Class 1 — full 5-tier waterfall priority ─────────────────────────


class TestRule46WaterfallTierOrder:
    """Each tier handles its own input class; higher tiers shadow lower
    ones. These tests confirm the priority order by giving the waterfall
    inputs that only one tier should resolve."""

    def test_tier1_override_dict_wins(self, monkeypatch) -> None:
        """A famous figure resolves via the curated dict, not network."""
        # Confirm Tier 3 is never called by patching it to raise
        from converter.wikidata import wikidata_reverse_lookup
        monkeypatch.setattr(
            wikidata_reverse_lookup,
            "lookup_english_label",
            lambda *a, **kw: pytest.fail("Tier 3 must not be called"),
        )
        # Confirm Tier 4 is never called by patching it to raise too
        from converter.wikidata import taatiknet_translit
        monkeypatch.setattr(
            taatiknet_translit,
            "best_effort_vocalized_transliterate",
            lambda *a, **kw: pytest.fail("Tier 4 must not be called"),
        )
        from converter.wikidata.hebrew_translit import english_label_for_hebrew
        assert english_label_for_hebrew("משה בן מימון") == "Maimonides"

    def test_tier2_nli_romanization_wins_over_lower_tiers(self, monkeypatch) -> None:
        """When source_record has marc_880, that wins over SPARQL/Nakdan/ALA-LC."""
        # Tier 1: no override for "סופינו, עמנואל" (not in dict)
        # Tier 2: should fire from source_record
        # Tiers 3/4/5: must never be called
        from converter.wikidata import taatiknet_translit, wikidata_reverse_lookup
        monkeypatch.setattr(
            wikidata_reverse_lookup,
            "lookup_english_label",
            lambda *a, **kw: pytest.fail("Tier 3 must not be called"),
        )
        monkeypatch.setattr(
            taatiknet_translit,
            "best_effort_vocalized_transliterate",
            lambda *a, **kw: pytest.fail("Tier 4 must not be called"),
        )
        from converter.wikidata.hebrew_translit import english_label_for_hebrew
        result = english_label_for_hebrew(
            "סופינו, עמנואל",
            source_record={"marc_880": "Sofino, Immanuele"},
        )
        assert result == "Sofino, Immanuele"

    def test_tier3_sparql_cache_hit_wins_over_nakdan_and_consonantal(
        self, monkeypatch, seeded_cache: pathlib.Path
    ) -> None:
        """When SPARQL cache has a positive entry, it shadows lower tiers."""
        # Wire the seeded cache through the wikidata_reverse_lookup module
        from converter.wikidata import taatiknet_translit, wikidata_reverse_lookup

        original_lookup = wikidata_reverse_lookup.lookup_english_label

        def lookup_with_seeded_cache(text: str, **kw):
            return original_lookup(text, cache_path=seeded_cache, **kw)

        monkeypatch.setattr(
            wikidata_reverse_lookup,
            "lookup_english_label",
            lookup_with_seeded_cache,
        )
        # Tier 4 must not be called when Tier 3 hits
        monkeypatch.setattr(
            taatiknet_translit,
            "best_effort_vocalized_transliterate",
            lambda *a, **kw: pytest.fail("Tier 4 must not be called on Tier 3 hit"),
        )
        from converter.wikidata.hebrew_translit import english_label_for_hebrew
        result = english_label_for_hebrew("אברהם אבן עזרא")
        assert result == "Abraham ibn Ezra (cached canonical)"

    def test_tier4_nakdan_wins_when_sparql_negative(self, monkeypatch) -> None:
        """When SPARQL returns None and Nakdan is available, Tier 4 wins."""
        from converter.wikidata import taatiknet_translit, wikidata_reverse_lookup
        # SPARQL: no result
        monkeypatch.setattr(
            wikidata_reverse_lookup, "lookup_english_label",
            lambda *a, **kw: None,
        )
        # Nakdan: simulate a successful vocalize + transliterate
        monkeypatch.setattr(
            taatiknet_translit, "best_effort_vocalized_transliterate",
            lambda text: "Rikardo" if text == "ריקרדו" else None,
        )
        from converter.wikidata.hebrew_translit import english_label_for_hebrew
        assert english_label_for_hebrew("ריקרדו") == "Rikardo"

    def test_tier5_consonantal_fallback_when_all_above_fail(
        self, monkeypatch
    ) -> None:
        """With every external tier disabled or returning None, Tier 5
        (deterministic consonantal ALA-LC) always produces output for any
        Hebrew input."""
        from converter.wikidata import taatiknet_translit, wikidata_reverse_lookup
        monkeypatch.setattr(
            wikidata_reverse_lookup, "lookup_english_label",
            lambda *a, **kw: None,
        )
        monkeypatch.setattr(
            taatiknet_translit, "best_effort_vocalized_transliterate",
            lambda text: None,
        )
        from converter.wikidata.hebrew_translit import english_label_for_hebrew
        result = english_label_for_hebrew("ריקרדו")
        # Tier 5 produces a consonantal output (no vowels, but not empty).
        assert result is not None
        assert result  # non-empty
        # The result must be Latin script (Tier 5 invariant)
        assert all(ord(ch) < 256 for ch in result)


# ── Class 2 — Stage 6 pipeline integration ────────────────────────────


class TestRule46WikidataUploadPipelineIntegration:
    """The waterfall is wired into the Wikidata Upload builder. These tests
    confirm WikidataItem labels go through the new code, and that the
    deprecated 'work from Hebrew manuscript X' synthetic fallback is
    never emitted by the current code base."""

    def test_no_legacy_synthetic_label_in_codebase(self) -> None:
        """Structural regression guard: the legacy synthetic fallback must
        not reappear anywhere in the source tree (item_builder.py used to
        emit ``'work from Hebrew manuscript {shelfmark}'`` as a last
        resort). Rule 46 replaces it with the waterfall."""
        item_builder = pathlib.Path(
            "converter/wikidata/item_builder.py"
        ).read_text(encoding="utf-8")
        # The legacy literal would be in item_builder.py if regressed
        assert (
            'work from Hebrew manuscript' not in item_builder.replace(
                "# ", ""  # comments may discuss it; remove leading hash space
            ).replace('"work from Hebrew manuscript"', "")
            or 'f"work from Hebrew manuscript {' not in item_builder
        ), (
            "Rule 46 regress: 'work from Hebrew manuscript {shelfmark}' "
            "synthetic en-label fallback has returned to item_builder.py"
        )

    def test_waterfall_called_from_work_label_construction(self) -> None:
        """Structural guard: _get_or_create_work calls english_label_for_hebrew
        (the waterfall entry point)."""
        item_builder = pathlib.Path(
            "converter/wikidata/item_builder.py"
        ).read_text(encoding="utf-8")
        # Find _get_or_create_work body
        idx = item_builder.find("def _get_or_create_work")
        assert idx != -1, "_get_or_create_work not found"
        next_def = item_builder.find("\n    def ", idx + 1)
        body = item_builder[idx:next_def] if next_def != -1 else item_builder[idx:]
        assert "english_label_for_hebrew" in body, (
            "Rule 46 regress: _get_or_create_work no longer calls the waterfall"
        )

    def test_hebrew_only_work_title_produces_meaningful_label(
        self, monkeypatch
    ) -> None:
        """Build a work via the real WikidataItemBuilder with a Hebrew-only
        title. The English label MUST NOT be the legacy synthetic form;
        it must be either a Tier 1/3/4 hit, a Tier 2 NLI romanization, a
        Tier 5 algorithmic output, or absent (None / not emitted)."""
        # Disable network and Nakdan for determinism
        monkeypatch.setenv("MHM_NO_NETWORK", "true")
        from converter.wikidata.item_builder import WikidataItemBuilder
        builder = WikidataItemBuilder()
        # Use a famous-figure title that Tier 1 resolves
        record = {
            "_control_number": "990000000000001",
            "title": "מורה נבוכים",  # Maimonides' "Guide for the Perplexed" — not in override dict
            "shelfmark": "F 99999",
            "contents": [
                {
                    "title": "מורה נבוכים",
                    "author": "משה בן מימון",  # Tier 1 hit on the author
                }
            ],
        }
        _ = builder.build_manuscript_item(record)
        # Walk every WikidataItem the builder produced, including child works
        # accessible via the builder's internal state
        work_items = list(getattr(builder, "_work_items", {}).values())
        for w in work_items:
            for lang, label in w.labels.items():
                if lang == "en":
                    assert "work from Hebrew manuscript" not in label, (
                        f"Rule 46 regress: legacy label leaked: {label!r}"
                    )
                    # The label must be a real Latin string (or absent)
                    if label:
                        assert any(ch.isalpha() for ch in label)


# ── Class 3 — cache integration ──────────────────────────────────────


class TestRule46CacheIntegrationEndToEnd:
    """The Wikidata SPARQL cache must persist between calls so the second
    lookup of the same name is free. These tests use a tmp_path cache file
    and assert cache hit/miss behaviour through the orchestrator."""

    def test_second_lookup_uses_cache_no_network(
        self, monkeypatch, tmp_path: pathlib.Path
    ) -> None:
        """First lookup writes to cache; second is read-only."""
        from converter.wikidata.wikidata_reverse_lookup import lookup_english_label
        cache_file = tmp_path / "cache.json"

        call_count = {"n": 0}

        def fake_post(url, **kw):
            call_count["n"] += 1
            class FakeResp:
                status_code = 200
                def raise_for_status(self): pass
                def json(self):
                    return {
                        "results": {
                            "bindings": [
                                {"label_en": {"value": "Cached Person"}}
                            ]
                        }
                    }
            return FakeResp()

        monkeypatch.setattr(
            "converter.wikidata.wikidata_reverse_lookup.requests.get",
            fake_post,
        )

        # First call: network is hit, result cached
        result1 = lookup_english_label(
            "פלוני אלמוני", cache_path=cache_file, allow_network=True,
        )
        # Second call: must read from cache, no network
        result2 = lookup_english_label(
            "פלוני אלמוני", cache_path=cache_file, allow_network=True,
        )
        assert result1 == "Cached Person"
        assert result2 == "Cached Person"
        assert call_count["n"] == 1, (
            f"Second lookup should have used cache; saw {call_count['n']} network calls"
        )

    def test_cache_file_format_is_versioned_json(
        self, monkeypatch, tmp_path: pathlib.Path
    ) -> None:
        """Cache file must be valid JSON with the documented schema."""
        from converter.wikidata.wikidata_reverse_lookup import lookup_english_label
        cache_file = tmp_path / "cache.json"

        def fake_post(url, **kw):
            class FakeResp:
                status_code = 200
                def raise_for_status(self): pass
                def json(self):
                    return {
                        "results": {
                            "bindings": [{"label_en": {"value": "Test Name"}}]
                        }
                    }
            return FakeResp()

        monkeypatch.setattr(
            "converter.wikidata.wikidata_reverse_lookup.requests.get", fake_post,
        )
        lookup_english_label(
            "פלוני", cache_path=cache_file, allow_network=True,
        )
        cache_data = json.loads(cache_file.read_text(encoding="utf-8"))
        assert "version" in cache_data
        assert cache_data["version"] >= 1
        assert "entries" in cache_data
        entry = cache_data["entries"].get("פלוני")
        assert entry is not None
        assert "english_label" in entry
        assert "fetched_at" in entry
        assert "ttl_seconds" in entry


# ── Class 4 — graceful degradation ────────────────────────────────────


class TestRule46GracefulDegradationEndToEnd:
    """The waterfall must work under every degradation scenario without
    raising. Critical for shipping in offline / model-absent environments."""

    def test_fully_offline_no_model_still_returns_for_hebrew_input(
        self, monkeypatch
    ) -> None:
        """MHM_NO_NETWORK=true + Nakdan model absent: waterfall still
        produces an output for Hebrew input via Tier 5."""
        monkeypatch.setenv("MHM_NO_NETWORK", "true")
        from converter.wikidata import taatiknet_translit
        # Simulate Nakdan model unavailable
        monkeypatch.setattr(
            taatiknet_translit, "best_effort_vocalized_transliterate",
            lambda text: None,
        )
        from converter.wikidata.hebrew_translit import english_label_for_hebrew
        result = english_label_for_hebrew("ריקרדו")
        assert result is not None and result, (
            "Tier 5 must always produce SOMETHING for Hebrew input"
        )

    def test_waterfall_never_raises_on_pathological_inputs(
        self, monkeypatch
    ) -> None:
        """No input shape should raise — pathological cases return None."""
        from converter.wikidata.hebrew_translit import english_label_for_hebrew
        # Each of these should return None, not raise
        pathological = [
            "",                          # empty
            "   ",                       # whitespace
            None,                        # wrong type
            123,                         # wrong type
            "Latin only string",         # no Hebrew
            "\x00\x01\x02",              # control chars only
        ]
        for bad in pathological:
            try:
                result = english_label_for_hebrew(bad)  # type: ignore[arg-type]
                assert result is None
            except Exception as exc:
                pytest.fail(f"english_label_for_hebrew({bad!r}) raised: {exc}")

    def test_waterfall_when_sparql_module_raises_falls_through(
        self, monkeypatch
    ) -> None:
        """If the wikidata_reverse_lookup module itself raises, the
        waterfall catches and falls through to lower tiers."""
        from converter.wikidata import taatiknet_translit, wikidata_reverse_lookup

        def angry_lookup(*a, **kw):
            raise RuntimeError("simulated catastrophic SPARQL failure")

        monkeypatch.setattr(
            wikidata_reverse_lookup, "lookup_english_label", angry_lookup,
        )
        # Nakdan stub returns a value to confirm fall-through reached it
        monkeypatch.setattr(
            taatiknet_translit, "best_effort_vocalized_transliterate",
            lambda text: "AlternateLabel",
        )
        from converter.wikidata.hebrew_translit import english_label_for_hebrew
        # Use a name not in the override dict so we exit Tier 1
        result = english_label_for_hebrew("פלוני אלמוני")
        assert result == "AlternateLabel", (
            "Waterfall must catch SPARQL failure and fall to Tier 4"
        )

    def test_waterfall_when_nakdan_module_raises_falls_through(
        self, monkeypatch
    ) -> None:
        """If Nakdan raises, fall through to Tier 5 consonantal."""
        from converter.wikidata import taatiknet_translit, wikidata_reverse_lookup
        monkeypatch.setattr(
            wikidata_reverse_lookup, "lookup_english_label",
            lambda *a, **kw: None,
        )

        def angry_taatiknet(text):
            raise RuntimeError("simulated catastrophic Nakdan failure")

        monkeypatch.setattr(
            taatiknet_translit, "best_effort_vocalized_transliterate",
            angry_taatiknet,
        )
        from converter.wikidata.hebrew_translit import english_label_for_hebrew
        result = english_label_for_hebrew("ריקרדו")
        # Tier 5 still produces output
        assert result is not None and result

    def test_no_network_env_var_blocks_sparql(
        self, monkeypatch
    ) -> None:
        """MHM_NO_NETWORK=true blocks Tier 3 from issuing any HTTP."""
        monkeypatch.setenv("MHM_NO_NETWORK", "true")
        call_count = {"n": 0}

        def angry_post(*a, **kw):
            call_count["n"] += 1
            raise RuntimeError("should not reach network")

        monkeypatch.setattr(
            "converter.wikidata.wikidata_reverse_lookup.requests.get", angry_post,
        )
        from converter.wikidata.hebrew_translit import english_label_for_hebrew
        # Not in override dict and not in cache → must fall through cleanly
        result = english_label_for_hebrew("פלוני אלמוני חדש לגמרי")
        # Tier 5 produces something Latin
        assert result is not None
        assert call_count["n"] == 0, "MHM_NO_NETWORK must block all HTTP"


# ── Class 5 — examples from the dissertation talk ─────────────────────


class TestRule46DissertationExamples:
    """Specific examples the user flagged or that are likely to appear in
    the dissertation defense. Pin them to expected behaviour."""

    def test_maimonides_resolves_to_canonical(self) -> None:
        from converter.wikidata.hebrew_translit import english_label_for_hebrew
        assert english_label_for_hebrew("משה בן מימון") == "Maimonides"

    def test_rashi_resolves_to_canonical(self) -> None:
        from converter.wikidata.hebrew_translit import english_label_for_hebrew
        # Either the abbreviation form or the full form should resolve
        result = english_label_for_hebrew("רש״י") or english_label_for_hebrew(
            "שלמה בן יצחק"
        )
        assert result == "Rashi"

    def test_ricardo_falls_through_to_algorithmic(self, monkeypatch) -> None:
        """User's flagged example: 'ריקרדו' should produce SOMETHING
        Latin-script that resembles 'Ricardo'. With network and Nakdan
        unavailable, Tier 5 gives a consonantal form."""
        monkeypatch.setenv("MHM_NO_NETWORK", "true")
        from converter.wikidata import taatiknet_translit
        monkeypatch.setattr(
            taatiknet_translit, "best_effort_vocalized_transliterate",
            lambda text: None,
        )
        from converter.wikidata.hebrew_translit import english_label_for_hebrew
        result = english_label_for_hebrew("ריקרדו")
        assert result is not None
        # Has the consonant skeleton of "Ricardo"
        assert "r" in result.lower() and ("k" in result.lower() or "q" in result.lower())
        assert "d" in result.lower()
        # NEVER the legacy synthetic fallback
        assert "work from Hebrew manuscript" not in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
