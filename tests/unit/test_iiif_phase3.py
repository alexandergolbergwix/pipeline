"""Phase 3 unit tests: IIIF manifest builder + Wikibase Cloud writer + uploader glue.

Covers Rule 45 invariants:

- IIIF Presentation API 3.0 conformance for the generated manifests.
- The :class:`WikibaseCloudWriter` enforces ``assert=bot``, caches CSRF
  tokens, retries on transient errors, is idempotent on identical content,
  and never leaks the password through logs or ``__repr__``.
- The :class:`IiifManifestUploader` short-circuits in dry-run mode and
  routes to the writer otherwise.

All HTTP traffic is mocked. No real network calls.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
import requests
from converter.config.namespaces import HM, LRMOO
from converter.wikibase.cloud_client import (
    EditOutcome,
    WikibaseBotCredentials,
    WikibaseCloudClient,
    WikibaseCloudWriter,
)
from converter.wikidata.iiif_manifest_builder import (
    PRESENTATION_CONTEXT_V3,
    BuildStats,
    IiifManifestBuilder,
)
from converter.wikidata.iiif_uploader import IiifManifestUploader, UploadResult
from rdflib import Graph, Literal, URIRef
from rdflib.namespace import RDF, RDFS

# ── Fixtures ─────────────────────────────────────────────────────────


def _build_minimal_graph(
    *,
    with_colophon: bool = False,
    with_intervention: bool = False,
    with_folio_range: str | None = None,
) -> Graph:
    """Build a minimal HMO graph with one manuscript and optional features."""
    g = Graph()
    ms_uri = URIRef("http://example.org/HMO#MS_990000123")
    g.add((ms_uri, RDF.type, LRMOO.F4_Manifestation_Singleton))
    g.add((ms_uri, RDFS.label, Literal("מקדש מעט", lang="he")))
    g.add((ms_uri, HM.external_identifier_nli, Literal("990000123")))

    if with_folio_range:
        cu_uri = URIRef("http://example.org/HMO#CU_990000123_main")
        g.add((cu_uri, RDF.type, HM.Codicological_Unit))
        g.add((cu_uri, RDFS.label, Literal("Main Codicological Unit")))
        g.add((cu_uri, HM.has_folio_range, Literal(with_folio_range)))
        g.add((ms_uri, HM.is_composed_of, cu_uri))

    if with_colophon:
        colophon_uri = URIRef("http://example.org/HMO#Colophon_990000123")
        g.add((colophon_uri, RDF.type, HM.Colophon))
        g.add(
            (
                colophon_uri,
                HM.has_colophon_text,
                Literal("נכתב על ידי שלמה בן יצחק", lang="he"),
            )
        )
        g.add((ms_uri, HM.has_colophon, colophon_uri))

    if with_intervention:
        iv_uri = URIRef("http://example.org/HMO#SI_990000123_1")
        g.add((iv_uri, RDF.type, HM.ScribalIntervention))
        g.add(
            (
                iv_uri,
                HM.intervention_description,
                Literal("הגהה בשולי הדף", lang="he"),
            )
        )
        g.add((ms_uri, HM.has_scribal_intervention, iv_uri))

    return g


# ── IIIF manifest builder ────────────────────────────────────────────


class TestIiifManifestBuilder:
    """IIIF Presentation API 3.0 conformance and HMO mapping behaviour."""

    def test_emits_iiif_3_context(self) -> None:
        g = _build_minimal_graph()
        b = IiifManifestBuilder(g, base_url="https://mhm-hmo.wikibase.cloud")
        shelfmark, manifest, _stats = next(b.build_all())
        assert shelfmark == "990000123"
        assert manifest["@context"] == PRESENTATION_CONTEXT_V3
        assert manifest["type"] == "Manifest"

    def test_canvas_count_matches_parsed_folios(self) -> None:
        """A folio range '1-10' should produce 10 Canvases."""
        g = _build_minimal_graph(with_folio_range="1-10")
        b = IiifManifestBuilder(g, base_url="https://mhm-hmo.wikibase.cloud")
        _shelfmark, manifest, stats = next(b.build_all())
        assert stats.canvas_count == 10
        assert len(manifest["items"]) == 10

    def test_range_built_for_codicological_unit(self) -> None:
        g = _build_minimal_graph(with_folio_range="1-5")
        b = IiifManifestBuilder(g, base_url="https://mhm-hmo.wikibase.cloud")
        _shelfmark, manifest, stats = next(b.build_all())
        assert stats.range_count == 1
        assert "structures" in manifest
        assert manifest["structures"][0]["type"] == "Range"
        assert len(manifest["structures"][0]["items"]) == 5

    def test_colophon_annotation_emitted(self) -> None:
        g = _build_minimal_graph(with_colophon=True)
        b = IiifManifestBuilder(g, base_url="https://mhm-hmo.wikibase.cloud")
        _shelfmark, manifest, stats = next(b.build_all())
        assert stats.annotation_count >= 1
        # Find the colophons AnnotationPage
        colophon_pages = [
            p for p in manifest.get("annotations", [])
            if "colophon" in p.get("id", "").lower()
        ]
        assert len(colophon_pages) == 1
        assert colophon_pages[0]["items"][0]["body"]["language"] == "he"

    def test_scribal_intervention_annotation_emitted(self) -> None:
        g = _build_minimal_graph(with_intervention=True)
        b = IiifManifestBuilder(g, base_url="https://mhm-hmo.wikibase.cloud")
        _shelfmark, manifest, stats = next(b.build_all())
        assert stats.annotation_count >= 1
        intervention_pages = [
            p for p in manifest.get("annotations", [])
            if "intervention" in p.get("id", "").lower()
        ]
        assert len(intervention_pages) == 1

    def test_placeholder_canvas_when_no_folio_data(self) -> None:
        """No folio data → exactly one placeholder canvas labelled '(no folio data)'."""
        g = _build_minimal_graph()
        b = IiifManifestBuilder(g, base_url="https://mhm-hmo.wikibase.cloud")
        _shelfmark, manifest, stats = next(b.build_all())
        assert stats.canvas_count == 1
        canvas = manifest["items"][0]
        assert "no folio data" in canvas["label"]["none"][0]
        # AnnotationPage must exist (IIIF 3.0 requirement) even when empty
        assert canvas["items"][0]["type"] == "AnnotationPage"
        assert canvas["items"][0]["items"] == []

    def test_seealso_points_at_permalink_and_hmo_iri(self) -> None:
        g = _build_minimal_graph()
        b = IiifManifestBuilder(g, base_url="https://mhm-hmo.wikibase.cloud")
        _shelfmark, manifest, _stats = next(b.build_all())
        see_also = manifest.get("seeAlso", [])
        urls = [s["id"] for s in see_also]
        # Permalink to the project's w3id permalink (after PR #6081 merges)
        assert any("w3id.org/mhm/manuscript/" in u for u in urls)
        # Direct HMO graph node IRI as a TTL Dataset
        assert any(u.endswith("#MS_990000123") for u in urls)
        # And the format/profile of the second entry is text/turtle
        ttl_entries = [s for s in see_also if s.get("format") == "text/turtle"]
        assert len(ttl_entries) >= 1

    def test_builder_is_pure_no_network(self) -> None:
        """The builder must not perform any HTTP. Patching requests.get
        and asserting zero calls is the structural guard."""
        g = _build_minimal_graph(with_colophon=True, with_folio_range="1-3")
        with patch("requests.get") as mock_get, patch("requests.post") as mock_post:
            b = IiifManifestBuilder(g, base_url="https://mhm-hmo.wikibase.cloud")
            list(b.build_all())
            assert mock_get.call_count == 0
            assert mock_post.call_count == 0


# ── Wikibase Cloud writer ────────────────────────────────────────────


def _writer(session: requests.Session | None = None) -> WikibaseCloudWriter:
    """Build a writer with default credentials and an optional mocked session."""
    config = WikibaseCloudClient.config_for_mhm_hmo_cloud()
    creds = WikibaseBotCredentials(
        username="TestBot", bot_name="manifest-writer", password="secret123"
    )
    return WikibaseCloudWriter(config, creds, session=session)


def _mock_post_response(payload: dict, status_code: int = 200) -> MagicMock:
    """Build a mock requests.Response with the given JSON payload."""
    response = MagicMock(spec=requests.Response)
    response.status_code = status_code
    response.json.return_value = payload
    response.raise_for_status = MagicMock()
    return response


class TestWikibaseCloudWriter:
    """Auth, idempotency, retry, and credential-secrecy invariants."""

    def test_credentials_redacted_in_repr(self) -> None:
        creds = WikibaseBotCredentials("u", "b", "MY_SECRET_PASSWORD")
        assert "MY_SECRET_PASSWORD" not in repr(creds)
        assert "REDACTED" in repr(creds)
        w = _writer()
        assert "secret123" not in repr(w)
        assert "REDACTED" in repr(w)

    def test_login_two_step(self) -> None:
        session = MagicMock(spec=requests.Session)
        session.headers = {}
        # First POST: get login token
        # Second POST: actual login
        session.post.side_effect = [
            _mock_post_response({"query": {"tokens": {"logintoken": "L_TOKEN"}}}),
            _mock_post_response({"login": {"result": "Success"}}),
        ]
        w = _writer(session=session)
        w.login()
        assert session.post.call_count == 2
        # First call asked for login token
        first_kwargs = session.post.call_args_list[0]
        assert first_kwargs.kwargs["data"]["type"] == "login"
        # Second call posted the login with the token
        second_kwargs = session.post.call_args_list[1]
        assert second_kwargs.kwargs["data"]["lgname"] == "TestBot@manifest-writer"
        assert second_kwargs.kwargs["data"]["lgtoken"] == "L_TOKEN"

    def test_csrf_token_cached(self) -> None:
        session = MagicMock(spec=requests.Session)
        session.headers = {}
        session.post.side_effect = [
            # Login
            _mock_post_response({"query": {"tokens": {"logintoken": "L"}}}),
            _mock_post_response({"login": {"result": "Success"}}),
            # First CSRF fetch
            _mock_post_response({"query": {"tokens": {"csrftoken": "CSRF1"}}}),
        ]
        w = _writer(session=session)
        first = w._get_csrf_token()
        second = w._get_csrf_token()
        assert first == "CSRF1"
        assert second == "CSRF1"
        # 2 login posts + 1 CSRF post = 3 total, NOT 4
        assert session.post.call_count == 3

    def test_idempotent_unchanged_skips_edit(self) -> None:
        """When existing wikitext matches new body byte-for-byte, no edit POST is sent."""
        session = MagicMock(spec=requests.Session)
        session.headers = {}
        existing_body = '{"foo": "bar"}'
        session.post.side_effect = [
            # parse (read existing) returns the same body
            _mock_post_response({"parse": {"wikitext": {"*": existing_body}}}),
        ]
        w = _writer(session=session)
        result = w.edit_page(
            title="IIIF:Test/manifest.json",
            body=existing_body,
            summary="no-op edit",
        )
        assert result.status == "unchanged"
        # Only the parse call should have happened (no login, no CSRF, no edit)
        assert session.post.call_count == 1

    def test_edit_asserts_bot_and_uses_bot_flag(self) -> None:
        session = MagicMock(spec=requests.Session)
        session.headers = {}
        session.post.side_effect = [
            # parse returns 404 → missing
            _mock_post_response({"error": {"code": "missingtitle", "info": "..."}}),
            # login token
            _mock_post_response({"query": {"tokens": {"logintoken": "L"}}}),
            # login result
            _mock_post_response({"login": {"result": "Success"}}),
            # CSRF
            _mock_post_response({"query": {"tokens": {"csrftoken": "C"}}}),
            # edit success
            _mock_post_response(
                {"edit": {"result": "Success", "pageid": 7, "newrevid": 12}}
            ),
        ]
        w = _writer(session=session)
        result = w.edit_page(
            title="IIIF:Test/manifest.json",
            body='{"new": "body"}',
            summary="test",
        )
        assert result.status == "created"
        assert result.edit_id == 7
        assert result.new_revid == 12
        # Last call must include assert=bot and bot=1
        edit_call = session.post.call_args_list[-1]
        data = edit_call.kwargs["data"]
        assert data["assert"] == "bot"
        assert data["bot"] == "1"
        assert data["contentmodel"] == "json"
        assert data["token"] == "C"

    def test_retry_on_transient_503(self) -> None:
        session = MagicMock(spec=requests.Session)
        session.headers = {}
        # First two POSTs return 503; third succeeds with siteinfo
        responses = [
            _mock_post_response({}, status_code=503),
            _mock_post_response({}, status_code=503),
            _mock_post_response({"query": {"tokens": {"logintoken": "L"}}}),
        ]
        session.post.side_effect = responses
        w = _writer(session=session)
        with patch("time.sleep") as mock_sleep:
            payload = w._post_with_retry({"action": "query"})
        assert "query" in payload
        # 2 retries (after the 503s) = 2 sleeps
        assert mock_sleep.call_count == 2

    def test_credentials_never_appear_in_writer_repr(self) -> None:
        """Structural credential-secrecy guard."""
        creds = WikibaseBotCredentials("alice", "bot1", "uniqu3passwd")
        config = WikibaseCloudClient.config_for_mhm_hmo_cloud()
        w = WikibaseCloudWriter(config, creds)
        assert "uniqu3passwd" not in repr(w)
        assert "uniqu3passwd" not in str(w)


# ── IIIF uploader (glue) ─────────────────────────────────────────────


class TestIiifManifestUploader:
    """Glue between builder and writer: dry-run + title pattern + edit-summary shape."""

    def test_dry_run_returns_without_calling_writer(self) -> None:
        writer = MagicMock(spec=WikibaseCloudWriter)
        writer.page_url.return_value = "https://mhm-hmo.wikibase.cloud/wiki/IIIF:MS_x/manifest.json"
        uploader = IiifManifestUploader(writer, dry_run=True)
        stats = BuildStats(canvas_count=10, range_count=2, annotation_count=5, seealso_count=2)
        result = uploader.upload("x", {"foo": "bar"}, stats)
        assert result.status == "dry_run"
        assert writer.edit_page.call_count == 0

    def test_upload_routes_to_writer(self) -> None:
        writer = MagicMock(spec=WikibaseCloudWriter)
        writer.page_url.return_value = "https://mhm-hmo.wikibase.cloud/wiki/IIIF:MS_x/manifest.json"
        writer.edit_page.return_value = EditOutcome(
            page_url=writer.page_url.return_value,
            status="created",
            message="ok",
            edit_id=42,
            new_revid=99,
        )
        uploader = IiifManifestUploader(writer, dry_run=False)
        stats = BuildStats(canvas_count=1, range_count=0, annotation_count=0, seealso_count=2)
        result = uploader.upload("990000123", {"@context": "x"}, stats)
        assert result.status == "created"
        assert result.edit_id == 42
        assert result.new_revid == 99
        # Confirm the writer received a JSON body and an IIIF: title
        call = writer.edit_page.call_args
        assert call.kwargs["title"] == "IIIF:MS_990000123/manifest.json"
        assert call.kwargs["content_model"] == "json"
        # Body must be valid JSON
        json.loads(call.kwargs["body"])

    def test_edit_summary_includes_stats(self) -> None:
        writer = MagicMock(spec=WikibaseCloudWriter)
        writer.page_url.return_value = "https://mhm-hmo.wikibase.cloud/wiki/X"
        writer.edit_page.return_value = EditOutcome(
            page_url="X", status="created", message="ok", edit_id=1, new_revid=2,
        )
        uploader = IiifManifestUploader(writer, dry_run=False)
        stats = BuildStats(canvas_count=10, range_count=3, annotation_count=7, seealso_count=2)
        uploader.upload("990000123", {"@context": "x"}, stats)
        summary = writer.edit_page.call_args.kwargs["summary"]
        assert "990000123" in summary
        assert "10 canvases" in summary
        assert "3 CUs" in summary
        assert "7 annotations" in summary

    def test_raw_url_pattern(self) -> None:
        writer = MagicMock(spec=WikibaseCloudWriter)
        writer.raw_url.return_value = (
            "https://mhm-hmo.wikibase.cloud/wiki/IIIF:MS_x/manifest.json"
            "?action=raw&ctype=application/json"
        )
        uploader = IiifManifestUploader(writer, dry_run=False)
        url = uploader.raw_url_for("x")
        assert "action=raw" in url
        assert "ctype=application/json" in url
        writer.raw_url.assert_called_with("IIIF:MS_x/manifest.json")


# ── P6108 precedence in item_builder ─────────────────────────────────


class TestP6108Precedence:
    """Rule 45 P6108 coexistence (updated 2026-05-18): NLI's image-rich
    manifest and our HMO-overlay manifest BOTH get emitted as P6108
    statements when both are present. Rank semantics put NLI at preferred
    (it carries the actual images) and ours at normal (overlay)."""

    def test_published_url_takes_precedence(self) -> None:
        """Both URLs present → both P6108 statements emitted, both reachable."""
        from converter.wikidata.item_builder import WikidataItemBuilder
        b = WikidataItemBuilder()
        record = {
            "_control_number": "990000123",
            "title": "Test",
            "iiif_manifest_url": "https://other-host.example/manifest.json",
            "iiif_manifest_published_url": "https://mhm-hmo.wikibase.cloud/wiki/IIIF:MS_990000123/manifest.json?action=raw&ctype=application/json",
        }
        item = b.build_manuscript_item(record)
        p6108 = [s for s in item.statements if s.property_id == "P6108"]
        assert len(p6108) == 2
        values = {str(s.value) for s in p6108}
        assert any("mhm-hmo.wikibase.cloud" in v for v in values)
        assert any("other-host.example" in v for v in values)

    def test_marc_url_used_when_published_absent(self) -> None:
        from converter.wikidata.item_builder import WikidataItemBuilder
        b = WikidataItemBuilder()
        record = {
            "_control_number": "990000123",
            "title": "Test",
            "iiif_manifest_url": "https://marc-source.example/manifest.json",
        }
        item = b.build_manuscript_item(record)
        p6108 = [s for s in item.statements if s.property_id == "P6108"]
        assert len(p6108) == 1
        assert p6108[0].value == "https://marc-source.example/manifest.json"

    def test_no_p6108_when_neither_url_present(self) -> None:
        from converter.wikidata.item_builder import WikidataItemBuilder
        b = WikidataItemBuilder()
        item = b.build_manuscript_item({"_control_number": "990000123", "title": "Test"})
        assert not [s for s in item.statements if s.property_id == "P6108"]

    def test_both_urls_present_emits_two_p6108_statements(self) -> None:
        """When iiif_manifest_url AND iiif_manifest_published_url both present,
        both P6108 statements are emitted, NLI at preferred rank, ours at normal."""
        from converter.wikidata.item_builder import WikidataItemBuilder
        b = WikidataItemBuilder()
        record = {
            "_control_number": "990000123",
            "title": "Test",
            "iiif_manifest_url": "https://iiif.nli.org.il/IIIF/manifests/990000123",
            "iiif_manifest_published_url": (
                "https://mhm-hmo.wikibase.cloud/wiki/IIIF:MS_990000123/manifest.json"
                "?action=raw&ctype=application/json"
            ),
        }
        item = b.build_manuscript_item(record)
        p6108 = [s for s in item.statements if s.property_id == "P6108"]
        assert len(p6108) == 2

        nli_stmts = [s for s in p6108 if "nli.org.il" in str(s.value)]
        ours_stmts = [s for s in p6108 if "mhm-hmo.wikibase.cloud" in str(s.value)]
        assert len(nli_stmts) == 1
        assert len(ours_stmts) == 1
        assert nli_stmts[0].rank == "preferred"
        assert ours_stmts[0].rank == "normal"
        # Both must still be URL-typed statements
        assert nli_stmts[0].value_type == "url"
        assert ours_stmts[0].value_type == "url"

    def test_nli_url_gets_preferred_rank(self) -> None:
        """NLI URL alone → rank='normal' (single-value case).
        Both present → NLI's gets rank='preferred'."""
        from converter.wikidata.item_builder import WikidataItemBuilder
        b = WikidataItemBuilder()
        # Case A: NLI URL alone → rank "normal"
        record_alone = {
            "_control_number": "990000123",
            "title": "Test",
            "iiif_manifest_url": "https://iiif.nli.org.il/IIIF/manifests/990000123",
        }
        item_alone = b.build_manuscript_item(record_alone)
        p6108_alone = [s for s in item_alone.statements if s.property_id == "P6108"]
        assert len(p6108_alone) == 1
        assert p6108_alone[0].rank == "normal"

        # Case B: both URLs → NLI's gets "preferred"
        record_both = {
            "_control_number": "990000123",
            "title": "Test",
            "iiif_manifest_url": "https://iiif.nli.org.il/IIIF/manifests/990000123",
            "iiif_manifest_published_url": (
                "https://mhm-hmo.wikibase.cloud/wiki/IIIF:MS_990000123/manifest.json"
            ),
        }
        item_both = b.build_manuscript_item(record_both)
        nli_stmt = next(
            s for s in item_both.statements
            if s.property_id == "P6108" and "nli.org.il" in str(s.value)
        )
        assert nli_stmt.rank == "preferred"

    def test_iiif_manifest_emits_partof_when_nli_url_in_graph(self) -> None:
        """Build a graph with hm:DigitalAccess + hm:iiif_manifest_url for a
        manuscript; the generated manifest must carry a partOf reference
        to that URL."""
        from converter.wikidata.iiif_manifest_builder import IiifManifestBuilder

        g = _build_minimal_graph()
        ms_uri = URIRef("http://example.org/HMO#MS_990000123")
        da_uri = URIRef("http://example.org/HMO#DigitalAccess_990000123")
        nli_url = "https://iiif.nli.org.il/IIIF/manifests/990000123"
        g.add((da_uri, RDF.type, HM.DigitalAccess))
        g.add((da_uri, HM.iiif_manifest_url, Literal(nli_url)))
        g.add((ms_uri, HM.has_digital_access, da_uri))

        b = IiifManifestBuilder(g, base_url="https://mhm-hmo.wikibase.cloud")
        _shelfmark, manifest, _stats = next(b.build_all())

        assert "partOf" in manifest
        assert isinstance(manifest["partOf"], list)
        assert len(manifest["partOf"]) == 1
        part = manifest["partOf"][0]
        assert part["id"] == nli_url
        assert part["type"] == "Manifest"
        assert "NLI" in part["label"]["en"][0]

        # When the NLI URL is absent, no partOf key
        g2 = _build_minimal_graph()
        b2 = IiifManifestBuilder(g2, base_url="https://mhm-hmo.wikibase.cloud")
        _s2, manifest2, _st2 = next(b2.build_all())
        assert "partOf" not in manifest2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
