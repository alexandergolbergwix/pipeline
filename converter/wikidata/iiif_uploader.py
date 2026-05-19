"""Upload IIIF manifest JSON pages to the project Wikibase Cloud.

Phase 3 of the HMO-fidelity plan (see CLAUDE.md Rule 45). Combines
:class:`IiifManifestBuilder` (pure RDF → IIIF dict) with
:class:`WikibaseCloudWriter` (authenticated MediaWiki API edits) so the
Stage 6 worker can publish manifests as JSON pages under the ``IIIF:``
namespace on ``mhm-hmo.wikibase.cloud``.

Idempotency, retry-with-backoff, and ``assert=bot`` enforcement all
live inside :class:`WikibaseCloudWriter`. This module is just the glue.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from converter.wikibase.cloud_client import EditOutcome, WikibaseCloudWriter
from converter.wikidata.iiif_manifest_builder import BuildStats


@dataclass(frozen=True)
class UploadResult:
    """Single-manifest upload outcome."""

    shelfmark: str
    page_url: str
    status: str  # "created" | "updated" | "unchanged" | "failed" | "dry_run"
    message: str
    edit_id: int | None
    new_revid: int | None
    canvas_count: int
    range_count: int
    annotation_count: int


class IiifManifestUploader:
    """IIIF manifest upload glue.

    Args:
        writer: An authenticated :class:`WikibaseCloudWriter` instance.
        dry_run: When ``True``, returns ``UploadResult(status='dry_run')``
            without contacting the API. Useful for the Wikidata projection
            dry-run path and for testing.
    """

    def __init__(
        self,
        writer: WikibaseCloudWriter,
        *,
        dry_run: bool = False,
    ) -> None:
        self._writer = writer
        self._dry_run = dry_run

    def upload(
        self,
        shelfmark: str,
        manifest: dict[str, Any],
        stats: BuildStats,
    ) -> UploadResult:
        """Upload a single IIIF manifest.

        Page title pattern: ``IIIF:MS_<shelfmark>/manifest.json``.
        """
        title = self._title_for_shelfmark(shelfmark)
        body = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True)
        summary = self._edit_summary(shelfmark, stats)

        if self._dry_run:
            return UploadResult(
                shelfmark=shelfmark,
                page_url=self._writer.page_url(title),
                status="dry_run",
                message=f"dry run; would write {len(body)} bytes",
                edit_id=None,
                new_revid=None,
                canvas_count=stats.canvas_count,
                range_count=stats.range_count,
                annotation_count=stats.annotation_count,
            )

        outcome: EditOutcome = self._writer.edit_page(
            title=title,
            body=body,
            summary=summary,
            content_model="json",
        )
        return UploadResult(
            shelfmark=shelfmark,
            page_url=outcome.page_url,
            status=outcome.status,
            message=outcome.message,
            edit_id=outcome.edit_id,
            new_revid=outcome.new_revid,
            canvas_count=stats.canvas_count,
            range_count=stats.range_count,
            annotation_count=stats.annotation_count,
        )

    def raw_url_for(self, shelfmark: str) -> str:
        """Return the consumer-facing raw URL for the manifest page.

        IIIF clients (Mirador, Universal Viewer) expect the manifest body
        as raw JSON; the ``action=raw&ctype=application/json`` query
        suffix delivers exactly that from MediaWiki.
        """
        return self._writer.raw_url(self._title_for_shelfmark(shelfmark))

    @staticmethod
    def _title_for_shelfmark(shelfmark: str) -> str:
        """Title pattern: ``IIIF:MS_<shelfmark>/manifest.json``."""
        return f"IIIF:MS_{shelfmark}/manifest.json"

    @staticmethod
    def _edit_summary(shelfmark: str, stats: BuildStats) -> str:
        """Build the human-readable edit summary written to page history."""
        return (
            f"MHM Pipeline: IIIF manifest for MS_{shelfmark} "
            f"({stats.canvas_count} canvas{'es' if stats.canvas_count != 1 else ''}, "
            f"{stats.range_count} CU{'s' if stats.range_count != 1 else ''}, "
            f"{stats.annotation_count} "
            f"annotation{'s' if stats.annotation_count != 1 else ''})"
        )
