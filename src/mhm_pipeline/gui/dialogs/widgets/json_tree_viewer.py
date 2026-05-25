"""``QTreeWidget``-based viewer for arbitrary JSON-like data.

Used in two places by ``AiVerificationDialog``:

* the "What the AI looked at" cards — collapsed by default, opened
  with friendly key labels (``_control_number`` → "Manuscript ID")
  so the curator sees what the run consumed without learning the
  internal schema.
* the "Advanced details" disclosure inside the **About this check**
  tab — same widget but with the raw keys preserved for engineers.

The widget is read-only. Search/filter is provided so very large
inputs (a 30-record MARC extract) remain navigable.
"""

from __future__ import annotations

from typing import Any

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QTreeWidget, QTreeWidgetItem, QWidget

from mhm_pipeline.gui import theme


def _stringify_scalar(value: Any) -> str:
    """Coerce *value* to a short display string."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        # Compress whitespace so a multi-line string doesn't blow up
        # the row height. Tooltip carries the full original payload.
        compact = " ".join(value.split())
        if len(compact) > 200:
            return compact[:197] + "…"
        return compact
    # Fallback for unexpected types — never raise.
    return repr(value)


class JsonTreeViewer(QTreeWidget):
    """Two-column tree (Key / Value) over a dict-or-list payload."""

    def __init__(
        self,
        data: Any,
        friendly_key_map: dict[str, str] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._data = data
        self._friendly_keys: dict[str, str] = dict(friendly_key_map or {})

        self.setColumnCount(2)
        self.setHeaderLabels(["Field", "Value"])
        self.setAlternatingRowColors(True)
        self.setUniformRowHeights(True)
        self.setRootIsDecorated(True)
        self.setStyleSheet(
            f"QTreeWidget {{"
            f" background: rgba(0,0,0, 70);"
            f" color: #e5e7eb;"
            f" border: 1px solid rgba(255,255,255, 22);"
            f" border-radius: {theme.RADIUS_MD}px;"
            f" font-size: {theme.FONT_SM}px;"
            f" }}"
            f"QTreeWidget::item {{ padding: 2px 6px; }}"
            f"QHeaderView::section {{"
            f" background: rgba(255,255,255, 12);"
            f" color: #e5e7eb;"
            f" padding: 4px 6px;"
            f" border: none;"
            f" border-bottom: 1px solid rgba(255,255,255, 22);"
            f" font-weight: 600;"
            f" }}"
        )

        self._populate()

    # ── Public API ──────────────────────────────────────────────────

    def set_friendly_keys(self, mapping: dict[str, str]) -> None:
        """Replace the friendly-key map and re-render the tree."""
        self._friendly_keys = dict(mapping)
        self._populate()

    def search(self, text: str) -> None:
        """Hide items that don't match *text*.

        Match is a case-insensitive substring check across both
        columns (key + value). An empty query restores all items.
        """
        needle = (text or "").strip().lower()
        if not needle:
            self._set_all_visible()
            return

        root = self.invisibleRootItem()
        self._apply_search(root, needle)

    # ── Population ──────────────────────────────────────────────────

    def _populate(self) -> None:
        self.clear()
        root = self.invisibleRootItem()
        self._add_value(root, label="root", raw_key="", value=self._data, is_root=True)
        # Expand the top level so the user immediately sees the shape.
        for i in range(self.topLevelItemCount()):
            item = self.topLevelItem(i)
            if item is not None:
                item.setExpanded(True)

    def _friendly_label(self, raw_key: str) -> str:
        return self._friendly_keys.get(raw_key, raw_key)

    def _add_value(
        self,
        parent: QTreeWidgetItem,
        *,
        label: str,
        raw_key: str,
        value: Any,
        is_root: bool = False,
    ) -> None:
        if isinstance(value, dict):
            self._add_dict(parent, label=label, raw_key=raw_key, value=value, is_root=is_root)
        elif isinstance(value, list):
            self._add_list(parent, label=label, raw_key=raw_key, value=value, is_root=is_root)
        else:
            item = QTreeWidgetItem([self._friendly_label(label) if not is_root else label,
                                    _stringify_scalar(value)])
            # Tooltip carries the raw key (engineers grepping for a
            # field name in a bug report can still find it).
            if raw_key and raw_key != label:
                item.setToolTip(0, f"Field: {raw_key}")
            if isinstance(value, str) and len(value) > 80:
                item.setToolTip(1, value)
            parent.addChild(item)

    def _add_dict(
        self,
        parent: QTreeWidgetItem,
        *,
        label: str,
        raw_key: str,
        value: dict[str, Any],
        is_root: bool,
    ) -> None:
        if is_root:
            container = parent
        else:
            container = QTreeWidgetItem([self._friendly_label(label), f"({len(value)} fields)"])
            if raw_key and raw_key != label:
                container.setToolTip(0, f"Field: {raw_key}")
            parent.addChild(container)

        for key, sub_value in value.items():
            self._add_value(container, label=str(key), raw_key=str(key), value=sub_value)

    def _add_list(
        self,
        parent: QTreeWidgetItem,
        *,
        label: str,
        raw_key: str,
        value: list[Any],
        is_root: bool,
    ) -> None:
        size_label = "1 item" if len(value) == 1 else f"{len(value)} items"
        if is_root:
            container = QTreeWidgetItem(["List", f"[{size_label}]"])
            parent.addChild(container)
        else:
            container = QTreeWidgetItem([self._friendly_label(label), f"[{size_label}]"])
            if raw_key and raw_key != label:
                container.setToolTip(0, f"Field: {raw_key}")
            parent.addChild(container)

        for idx, sub_value in enumerate(value):
            self._add_value(container, label=f"[{idx}]", raw_key="", value=sub_value)

    # ── Search helpers ──────────────────────────────────────────────

    def _set_all_visible(self) -> None:
        root = self.invisibleRootItem()
        self._walk(root, lambda it: it.setHidden(False))

    def _walk(self, node: QTreeWidgetItem, fn: Any) -> None:
        for i in range(node.childCount()):
            child = node.child(i)
            if child is None:
                continue
            fn(child)
            self._walk(child, fn)

    def _apply_search(self, node: QTreeWidgetItem, needle: str) -> bool:
        """Return True when *node* (or any descendant) matches."""
        any_match = False
        for i in range(node.childCount()):
            child = node.child(i)
            if child is None:
                continue
            descendant_match = self._apply_search(child, needle)
            self_match = (
                needle in child.text(0).lower()
                or needle in child.text(1).lower()
            )
            visible = descendant_match or self_match
            child.setHidden(not visible)
            if visible:
                child.setExpanded(True)
                any_match = True
        # Honor Qt root sentinel — invisible root has no text() of its own,
        # so we only report descendant matches up the chain.
        if node.parent() is None:
            return any_match
        return any_match


__all__ = ["JsonTreeViewer"]
