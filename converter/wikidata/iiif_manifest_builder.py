"""Build IIIF Presentation API 3.0 manifests from the HMO RDF graph.

Phase 3 of the HMO-fidelity plan (see ``plans/smooth-humming-feather.md``
and CLAUDE.md Rule 45).

This module is the carrier for the HMO concepts Wikidata's data model
cannot express natively:

- **Folio-granular Codicological_Unit** → IIIF ``Range`` covering a span
  of Canvases.
- **Colophon / ScribalIntervention / Marginalia / MarginalAddition** →
  IIIF ``AnnotationCollection`` with one ``Annotation`` per intervention.
- **TextLocation** → Canvas fragment selector when location data is
  present.
- **Full HMO scholarly graph** → IIIF ``seeAlso`` pointing at the project's
  permalink (``https://w3id.org/mhm/manuscript/<cn>``) and the canonical
  graph slice.

The result is a Python ``dict`` ready for ``json.dumps``. The builder is
**pure** (no I/O, no network). The caller (typically
:class:`IiifManifestUploader` or the Stage 6 worker) owns persistence.

Reference: https://iiif.io/api/presentation/3.0/
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from rdflib import BNode, Graph, Literal, URIRef
from rdflib.namespace import RDF, RDFS

from converter.config.namespaces import CIDOC, HM, LRMOO

PRESENTATION_CONTEXT_V3 = "http://iiif.io/api/presentation/3/context.json"

# Default canvas dimensions when we don't have real image data. IIIF
# spec requires width/height on every Canvas; these are placeholder
# numbers that say "we don't know the real image dimensions yet".
PLACEHOLDER_CANVAS_WIDTH = 1000
PLACEHOLDER_CANVAS_HEIGHT = 1400  # roughly portrait, like a manuscript folio

# Regex to parse a folio range string like "1-50" or "fol. 1r-10v"
# from ``hm:has_folio_range`` literals.
_FOLIO_RANGE_RE = re.compile(r"(\d+)\s*[-–]\s*(\d+)")
_FOLIO_SINGLE_RE = re.compile(r"^(\d+)")


@dataclass(frozen=True)
class BuildStats:
    """Statistics for a single manifest build, returned alongside the dict."""

    canvas_count: int
    range_count: int
    annotation_count: int
    seealso_count: int


class IiifManifestBuilder:
    """Build IIIF 3.0 manifests for every manuscript in an HMO graph.

    Args:
        graph: A parsed :class:`rdflib.Graph` (typically ``output.ttl``).
        base_url: The hosting URL for the manifests
            (e.g. ``"https://mhm-hmo.wikibase.cloud"``). Used both as the
            manifest ``id`` host and as the prefix for ``seeAlso``.
        permalink_base: Optional permalink base for ``seeAlso`` entries
            (defaults to ``"https://w3id.org/mhm"``).
    """

    def __init__(
        self,
        graph: Graph,
        base_url: str,
        permalink_base: str = "https://w3id.org/mhm",
    ) -> None:
        self._graph = graph
        self._base = base_url.rstrip("/")
        self._permalink_base = permalink_base.rstrip("/")

    def build_all(self) -> Iterator[tuple[str, dict[str, Any], BuildStats]]:
        """Yield one ``(shelfmark, manifest_dict, stats)`` tuple per manuscript.

        Manuscripts are processed in deterministic order (sorted by IRI).
        """
        manuscripts = sorted(
            (
                ms
                for ms in self._graph.subjects(RDF.type, LRMOO.F4_Manifestation_Singleton)
                if isinstance(ms, URIRef)
            ),
            key=str,
        )
        for ms_uri in manuscripts:
            shelfmark = self._shelfmark(ms_uri)
            manifest, stats = self.build_for_manuscript(ms_uri)
            yield shelfmark, manifest, stats

    def build_for_manuscript(
        self, ms_uri: URIRef
    ) -> tuple[dict[str, Any], BuildStats]:
        """Build a single IIIF 3.0 manifest for the given manuscript IRI."""
        shelfmark = self._shelfmark(ms_uri)
        manifest_id = f"{self._base}/iiif/MS_{shelfmark}/manifest.json"

        canvases, canvas_index = self._build_canvases(ms_uri, manifest_id)
        ranges = self._build_ranges(ms_uri, manifest_id, canvas_index)
        annotation_pages = self._build_annotation_pages(
            ms_uri, manifest_id, canvas_index
        )
        see_also = self._build_see_also(ms_uri, shelfmark)
        # Rule 45 P6108 coexistence (2026-05-18): when the source HMO graph
        # carries NLI's IIIF manifest URL on the manuscript's DigitalAccess
        # node, declare it as the IIIF 3.0 `partOf` parent. Signals to
        # consumers that ours is a companion overlay manifest, not a
        # replacement — NLI hosts the actual Canvas images.
        nli_iiif_url = self._nli_iiif_url(ms_uri)

        manifest: dict[str, Any] = {
            "@context": PRESENTATION_CONTEXT_V3,
            "id": manifest_id,
            "type": "Manifest",
            "label": self._build_label(ms_uri),
            "metadata": self._build_metadata(ms_uri, shelfmark),
            "items": canvases,
        }
        if ranges:
            manifest["structures"] = ranges
        if annotation_pages:
            manifest["annotations"] = annotation_pages
        if see_also:
            manifest["seeAlso"] = see_also
        if nli_iiif_url:
            manifest["partOf"] = [
                {
                    "id": nli_iiif_url,
                    "type": "Manifest",
                    "label": {"en": ["NLI Ktiv IIIF manifest (source images)"]},
                }
            ]

        stats = BuildStats(
            canvas_count=len(canvases),
            range_count=len(ranges),
            annotation_count=sum(
                len(page.get("items") or []) for page in annotation_pages
            ),
            seealso_count=len(see_also),
        )
        return manifest, stats

    # ── Canvas construction ────────────────────────────────────────────

    def _build_canvases(
        self, ms_uri: URIRef, manifest_id: str
    ) -> tuple[list[dict[str, Any]], dict[int, str]]:
        """Build the list of Canvases.

        Returns the list plus a dict mapping folio number → Canvas ID so
        Range and AnnotationCollection builders can target canvases by
        folio number.
        """
        folio_numbers = self._collect_folio_numbers(ms_uri)
        if not folio_numbers:
            # Fallback: one placeholder Canvas representing the manuscript
            # as a whole, no folio-level granularity.
            placeholder_id = f"{manifest_id}/canvas/whole"
            return (
                [self._empty_canvas(placeholder_id, label_text="(no folio data)")],
                {0: placeholder_id},
            )

        canvases: list[dict[str, Any]] = []
        index: dict[int, str] = {}
        for folio in sorted(folio_numbers):
            canvas_id = f"{manifest_id}/canvas/p{folio}"
            canvases.append(self._empty_canvas(canvas_id, label_text=f"fol. {folio}"))
            index[folio] = canvas_id
        return canvases, index

    def _empty_canvas(self, canvas_id: str, label_text: str) -> dict[str, Any]:
        """Build a Canvas with no image body (metadata-only).

        IIIF 3.0 requires every Canvas to have ``items: [AnnotationPage]``,
        even when the AnnotationPage carries no annotations yet.
        """
        annotation_page_id = f"{canvas_id}/page-1"
        return {
            "id": canvas_id,
            "type": "Canvas",
            "label": {"none": [label_text]},
            "width": PLACEHOLDER_CANVAS_WIDTH,
            "height": PLACEHOLDER_CANVAS_HEIGHT,
            "items": [
                {
                    "id": annotation_page_id,
                    "type": "AnnotationPage",
                    "items": [],
                }
            ],
        }

    def _collect_folio_numbers(self, ms_uri: URIRef) -> set[int]:
        """Collect all numeric folio references reachable from the manuscript.

        Sources scanned (every literal that mentions folio numbers):

        - ``hm:has_folio_range`` on Codicological_Units reached via
          ``hm:is_composed_of`` and on works reached via ``hm:has_work``.
        - ``hm:folio_number`` on TextLocations directly attached to the
          manuscript.
        """
        folios: set[int] = set()
        # CU folio ranges
        for cu in self._graph.objects(ms_uri, HM.is_composed_of):
            folios |= self._parse_folio_range_literals(cu)
        # Work folio ranges
        for work in self._graph.objects(ms_uri, HM.has_work):
            folios |= self._parse_folio_range_literals(work)
        # TextLocation folio numbers
        for loc in self._graph.objects(ms_uri, HM.has_text_location):
            for n in self._graph.objects(loc, HM.folio_number):
                if isinstance(n, Literal):
                    parsed = _safe_int(str(n))
                    if parsed is not None:
                        folios.add(parsed)
        return folios

    def _parse_folio_range_literals(self, subject: URIRef | BNode) -> set[int]:
        """Parse folio numbers from any ``hm:has_folio_range`` literal."""
        out: set[int] = set()
        for lit in self._graph.objects(subject, HM.has_folio_range):
            if not isinstance(lit, Literal):
                continue
            text = str(lit).strip()
            range_m = _FOLIO_RANGE_RE.search(text)
            if range_m:
                start = int(range_m.group(1))
                end = int(range_m.group(2))
                if start <= end and (end - start) <= 2000:  # sanity cap
                    out.update(range(start, end + 1))
                continue
            single_m = _FOLIO_SINGLE_RE.match(text)
            if single_m:
                out.add(int(single_m.group(1)))
        return out

    # ── Range construction (Codicological Units) ────────────────────────

    def _build_ranges(
        self,
        ms_uri: URIRef,
        manifest_id: str,
        canvas_index: dict[int, str],
    ) -> list[dict[str, Any]]:
        """Build IIIF Range entries — one per Codicological Unit."""
        ranges: list[dict[str, Any]] = []
        for idx, cu in enumerate(
            sorted(self._graph.objects(ms_uri, HM.is_composed_of), key=str), start=1
        ):
            cu_folios = self._parse_folio_range_literals(cu)
            canvas_refs: list[dict[str, Any]] = []
            for folio in sorted(cu_folios):
                cid = canvas_index.get(folio)
                if cid:
                    canvas_refs.append({"id": cid, "type": "Canvas"})
            if not canvas_refs:
                continue
            range_id = f"{manifest_id}/range/cu{idx}"
            label = (
                self._literal_text(self._graph.value(cu, RDFS.label))
                or f"Codicological Unit {idx}"
            )
            ranges.append(
                {
                    "id": range_id,
                    "type": "Range",
                    "label": {"none": [label]},
                    "items": canvas_refs,
                }
            )
        return ranges

    # ── AnnotationCollection construction ───────────────────────────────

    def _build_annotation_pages(
        self,
        ms_uri: URIRef,
        manifest_id: str,
        canvas_index: dict[int, str],
    ) -> list[dict[str, Any]]:
        """Build top-level AnnotationPages for colophons + scribal interventions.

        IIIF 3.0 ``annotations`` is a list of AnnotationPage objects (each
        containing Annotations) — distinct from the per-Canvas
        AnnotationPages (which carry painting annotations).
        """
        pages: list[dict[str, Any]] = []

        colophon_annotations = self._build_colophon_annotations(
            ms_uri, manifest_id, canvas_index
        )
        if colophon_annotations:
            pages.append(
                {
                    "id": f"{manifest_id}/annotations/colophons",
                    "type": "AnnotationPage",
                    "label": {"none": ["Colophons"]},
                    "items": colophon_annotations,
                }
            )

        intervention_annotations = self._build_intervention_annotations(
            ms_uri, manifest_id, canvas_index
        )
        if intervention_annotations:
            pages.append(
                {
                    "id": f"{manifest_id}/annotations/scribal-interventions",
                    "type": "AnnotationPage",
                    "label": {"none": ["Scribal interventions"]},
                    "items": intervention_annotations,
                }
            )
        return pages

    def _build_colophon_annotations(
        self,
        ms_uri: URIRef,
        manifest_id: str,
        canvas_index: dict[int, str],
    ) -> list[dict[str, Any]]:
        """Build one Annotation per Colophon node."""
        annotations: list[dict[str, Any]] = []
        for idx, colophon in enumerate(
            sorted(self._graph.objects(ms_uri, HM.has_colophon), key=str), start=1
        ):
            text = self._literal_text(
                self._graph.value(colophon, HM.has_colophon_text)
            ) or self._literal_text(self._graph.value(colophon, HM.colophon_text))
            if not text:
                continue
            target = self._best_canvas_target(colophon, canvas_index, manifest_id)
            annotations.append(
                {
                    "id": f"{manifest_id}/annotations/colophon-{idx}",
                    "type": "Annotation",
                    "motivation": "describing",
                    "body": {
                        "type": "TextualBody",
                        "value": text,
                        "language": "he",
                        "format": "text/plain",
                    },
                    "target": target,
                }
            )
        return annotations

    def _build_intervention_annotations(
        self,
        ms_uri: URIRef,
        manifest_id: str,
        canvas_index: dict[int, str],
    ) -> list[dict[str, Any]]:
        """Build one Annotation per ScribalIntervention / Marginalia node."""
        annotations: list[dict[str, Any]] = []
        intervention_predicates = (
            HM.has_scribal_intervention,
            HM.has_marginalia,
            HM.has_marginal_addition,
        )
        seen: set[str] = set()
        idx = 1
        for predicate in intervention_predicates:
            for iv in sorted(self._graph.objects(ms_uri, predicate), key=str):
                key = str(iv)
                if key in seen:
                    continue
                seen.add(key)
                text = self._literal_text(
                    self._graph.value(iv, HM.intervention_description)
                ) or self._literal_text(self._graph.value(iv, RDFS.label))
                if not text:
                    continue
                role = self._intervention_role(iv)
                target = self._best_canvas_target(iv, canvas_index, manifest_id)
                annotations.append(
                    {
                        "id": f"{manifest_id}/annotations/intervention-{idx}",
                        "type": "Annotation",
                        "motivation": "commenting",
                        "body": {
                            "type": "TextualBody",
                            "value": text,
                            "language": "he",
                            "format": "text/plain",
                            "role": role,
                        },
                        "target": target,
                    }
                )
                idx += 1
        return annotations

    def _intervention_role(self, iv: URIRef | BNode) -> str:
        """Classify the intervention from its rdf:type predicate."""
        for rdf_type in self._graph.objects(iv, RDF.type):
            if not isinstance(rdf_type, URIRef):
                continue
            local = str(rdf_type).rsplit("#", 1)[-1].rsplit("/", 1)[-1]
            if local in (
                "Marginalia",
                "MarginalAddition",
                "ScribalIntervention",
                "HandChange",
                "TextCorrection",
            ):
                return local
        return "ScribalIntervention"

    def _best_canvas_target(
        self,
        node: URIRef | BNode,
        canvas_index: dict[int, str],
        manifest_id: str,
    ) -> str:
        """Pick the most specific Canvas to target for an annotation.

        Falls back to the whole-manifest target when no folio is known.
        """
        # Try TextLocation → folio_number
        for loc in self._graph.objects(node, HM.has_text_location):
            for n in self._graph.objects(loc, HM.folio_number):
                if isinstance(n, Literal):
                    folio = _safe_int(str(n))
                    if folio is not None and folio in canvas_index:
                        return canvas_index[folio]
        # Fall back to the manifest itself (whole-manuscript target)
        # If a placeholder canvas exists, use it.
        if 0 in canvas_index:
            return canvas_index[0]
        return manifest_id

    # ── seeAlso / label / metadata ──────────────────────────────────────

    def _nli_iiif_url(self, ms_uri: URIRef) -> str | None:
        """Return the NLI IIIF manifest URL attached to the manuscript.

        Traverses ``manuscript hm:has_digital_access → da hm:iiif_manifest_url``.
        Returns the first non-empty literal found, or None when the graph
        has no NLI manifest URL (in which case the caller skips ``partOf``).
        """
        for da in self._graph.objects(ms_uri, HM.has_digital_access):
            for url in self._graph.objects(da, HM.iiif_manifest_url):
                text = str(url).strip()
                if text:
                    return text
        return None

    def _build_see_also(
        self, ms_uri: URIRef, shelfmark: str
    ) -> list[dict[str, Any]]:
        """Point at the project permalink and the canonical HMO TTL slice."""
        return [
            {
                "id": f"{self._permalink_base}/manuscript/{shelfmark}",
                "type": "Dataset",
                "format": "text/html",
                "label": {"en": ["MHM project page"]},
            },
            {
                "id": str(ms_uri),
                "type": "Dataset",
                "format": "text/turtle",
                "profile": "http://www.w3.org/ns/dcat#Dataset",
                "label": {"en": ["HMO RDF graph node"]},
            },
        ]

    def _build_label(self, ms_uri: URIRef) -> dict[str, list[str]]:
        """IIIF label dict with Hebrew + English entries when available."""
        label = self._literal_text(self._graph.value(ms_uri, RDFS.label))
        if not label:
            return {"none": ["Hebrew manuscript"]}
        # rdfs:label is typically Hebrew in HMO; promote into the he slot.
        return {"he": [label]}

    def _build_metadata(
        self, ms_uri: URIRef, shelfmark: str
    ) -> list[dict[str, Any]]:
        """Build the IIIF ``metadata`` block — bibliographic key/value pairs."""
        rows: list[dict[str, Any]] = [
            {
                "label": {"en": ["NLI control number"]},
                "value": {"none": [shelfmark]},
            }
        ]

        for production in self._graph.objects(ms_uri, HM.has_production_event):
            for time_uri in self._graph.objects(production, CIDOC.P4_has_time_span):
                date_label = self._literal_text(self._graph.value(time_uri, RDFS.label))
                if date_label:
                    rows.append(
                        {
                            "label": {"en": ["Production date"]},
                            "value": {"none": [date_label]},
                        }
                    )
                    break
            for place in self._graph.objects(production, HM.has_production_place):
                place_label = self._literal_text(self._graph.value(place, RDFS.label))
                if place_label:
                    rows.append(
                        {
                            "label": {"en": ["Place of production"]},
                            "value": {"none": [place_label]},
                        }
                    )
                    break

        # Materials
        materials: list[str] = []
        for mat in self._graph.objects(ms_uri, HM.has_material):
            mat_label = self._literal_text(self._graph.value(mat, RDFS.label))
            if mat_label:
                materials.append(mat_label)
        if materials:
            rows.append(
                {
                    "label": {"en": ["Material"]},
                    "value": {"none": sorted(set(materials))},
                }
            )

        return rows

    # ── Utilities ───────────────────────────────────────────────────────

    def _shelfmark(self, ms_uri: URIRef) -> str:
        """Derive the NLI control number from a manuscript IRI."""
        # Look up hm:external_identifier_nli first; fall back to the fragment.
        for lit in self._graph.objects(ms_uri, HM.external_identifier_nli):
            text = str(lit).strip()
            if text:
                return text
        fragment = str(ms_uri).rsplit("#", 1)[-1].rsplit("/", 1)[-1]
        match = re.search(r"(\d{8,})", fragment)
        return match.group(1) if match else fragment

    @staticmethod
    def _literal_text(value: object) -> str:
        """Return a stripped literal/resource string or empty string."""
        if value is None:
            return ""
        return str(value).strip()


def _safe_int(text: str) -> int | None:
    """Parse a string as int; return ``None`` on failure."""
    try:
        return int(text)
    except (TypeError, ValueError):
        return None
