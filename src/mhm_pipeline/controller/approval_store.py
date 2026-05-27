"""Shared approval state across the edit UIs and the eval-agent verdict UI.

A single ``approvals.json`` sidecar in the pipeline output dir is the source
of truth for which extracted entities / authority matches the curator has
approved. The NER editor, the authority editor, and the AI-verification
dialog all read AND write this one store, keyed by a canonical identity that
each surface can compute independently:

    approval_key = "<control_number>|<group>|<sub_type>|<normalized_text>"

- ``group``      — "ner" (person/provenance/contents/genre entities) or
                   "authority" (Stage-3 matches).
- ``sub_type``   — the role/type (AUTHOR / OWNER / WORK / PLACE / …), upper.
- ``normalized_text`` — entity text / matched name, casefolded with ISBD
                   punctuation collapsed (Hebrew preserved verbatim).

Live sync: a ``QFileSystemWatcher`` on the file makes a write in one window
re-load in any other open window, so "approve here, see it there" is instant.
A write guard suppresses the self-triggered reload.

Result files (``ner_results.json`` / ``authority_enriched.json``) are still
written filtered-to-approved on the editors' Save — the sidecar is additive
and does not change the downstream Wikidata-export contract.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timezone
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QFileSystemWatcher, QObject, QTimer, pyqtSignal

APPROVALS_FILENAME = "approvals.json"

_PUNCT_RE = re.compile(r"[\s,.;:\"'()\[\]{}<>!?\-—/\\]+")


def normalize_text(text: str) -> str:
    """Casefold + collapse ISBD punctuation; Hebrew kept verbatim."""
    if not text:
        return ""
    out = str(text).strip()
    if not out:
        return ""
    out = out.casefold()
    out = _PUNCT_RE.sub(" ", out).strip()
    return out


def control_number_of(record_id: str) -> str:
    """Reduce a record id / URI to the bare control number (last URI segment)."""
    raw = str(record_id or "").strip()
    return raw.split("/")[-1] if raw else ""


def approval_key(control_number: str, group: str, sub_type: str, text: str) -> str:
    """Canonical, surface-independent key for one approvable entity."""
    cn = control_number_of(control_number)
    grp = (group or "").strip().lower()
    st = (sub_type or "").strip().upper()
    return f"{cn}|{grp}|{st}|{normalize_text(text)}"


# Map a verdict's evaluator_id to the approval ``group``.
_EVALUATOR_GROUP: dict[str, str] = {
    "person_ner": "ner",
    "provenance_ner": "ner",
    "contents_ner": "ner",
    "genre_classifier": "ner",
    "place_ner": "ner",
    "authority": "authority",
}


def group_for_evaluator(evaluator_id: str) -> str:
    """Return the approval group ("ner"/"authority") for an evaluator id."""
    return _EVALUATOR_GROUP.get((evaluator_id or "").strip().lower(), "ner")


class ApprovalStore(QObject):
    """Read/write the shared ``approvals.json`` with live cross-window sync."""

    changed = pyqtSignal()

    def __init__(self, output_dir: Path | str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._path = Path(output_dir) / APPROVALS_FILENAME
        self._entries: dict[str, dict[str, Any]] = {}
        self._writing = False
        self._watcher: QFileSystemWatcher | None = None
        self._reload_timer = QTimer(self)
        self._reload_timer.setSingleShot(True)
        self._reload_timer.setInterval(120)  # debounce rapid editor toggles
        self._reload_timer.timeout.connect(self._reload_from_disk)
        self.load()
        self._install_watcher()

    # ── Public read API ─────────────────────────────────────────────────

    @property
    def path(self) -> Path:
        return self._path

    def is_approved(self, key: str) -> bool:
        entry = self._entries.get(key)
        return bool(entry and entry.get("approved"))

    def has(self, key: str) -> bool:
        return key in self._entries

    def approved_keys(self) -> set[str]:
        return {k for k, v in self._entries.items() if v.get("approved")}

    # ── Public write API ────────────────────────────────────────────────

    def set_approved(self, key: str, approved: bool, *, by: str = "") -> None:
        """Set one key's approval and persist. No-op if unchanged."""
        if not key:
            return
        if self.is_approved(key) == bool(approved) and key in self._entries:
            return
        self._entries[key] = {
            "approved": bool(approved),
            "by": by or "unknown",
            "at": datetime.now(UTC).isoformat(),
        }
        self._save()

    def bulk_set(self, items: dict[str, bool], *, by: str = "") -> None:
        """Set many keys at once, persisting a single write."""
        now = datetime.now(UTC).isoformat()
        dirty = False
        for key, approved in items.items():
            if not key:
                continue
            if key in self._entries and self.is_approved(key) == bool(approved):
                continue
            self._entries[key] = {"approved": bool(approved), "by": by or "unknown", "at": now}
            dirty = True
        if dirty:
            self._save()

    # ── Persistence ─────────────────────────────────────────────────────

    def load(self) -> None:
        """Load entries from disk; tolerates a missing/corrupt file."""
        self._entries = {}
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if isinstance(data, dict):
            for key, val in data.items():
                if isinstance(val, dict):
                    self._entries[key] = {
                        "approved": bool(val.get("approved")),
                        "by": str(val.get("by", "")),
                        "at": str(val.get("at", "")),
                    }
                elif isinstance(val, bool):  # tolerate a flat {key: bool} shape
                    self._entries[key] = {"approved": val, "by": "", "at": ""}

    def _save(self) -> None:
        self._writing = True
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps(self._entries, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            # Re-arm the watcher: some platforms drop the path after a
            # write-by-replace, so a missing path must be re-added.
            self._ensure_watched()
        except OSError:
            pass
        finally:
            self._writing = False

    # ── Live sync ───────────────────────────────────────────────────────

    def _install_watcher(self) -> None:
        self._watcher = QFileSystemWatcher(self)
        self._watcher.fileChanged.connect(self._on_file_changed)
        self._watcher.directoryChanged.connect(self._on_file_changed)
        self._ensure_watched()

    def _ensure_watched(self) -> None:
        if self._watcher is None:
            return
        watched = set(self._watcher.files()) | set(self._watcher.directories())
        if self._path.exists() and str(self._path) not in watched:
            self._watcher.addPath(str(self._path))
        # Also watch the parent dir so the first-ever create is caught.
        parent = str(self._path.parent)
        if self._path.parent.exists() and parent not in watched:
            self._watcher.addPath(parent)

    def _on_file_changed(self, _path: str) -> None:
        if self._writing:
            return  # our own write — ignore
        self._reload_timer.start()

    def _reload_from_disk(self) -> None:
        before = dict(self._entries)
        self.load()
        self._ensure_watched()
        if self._entries != before:
            self.changed.emit()


__all__ = [
    "ApprovalStore",
    "approval_key",
    "normalize_text",
    "control_number_of",
    "group_for_evaluator",
    "APPROVALS_FILENAME",
]
