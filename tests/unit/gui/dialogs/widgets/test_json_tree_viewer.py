"""Unit tests for ``mhm_pipeline.gui.dialogs.widgets.json_tree_viewer``.

Verifies the recursive tree population for dicts, lists, scalars, and
edge cases (None / empty containers), the friendly-key renaming, and
the substring ``search()`` filter.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QTreeWidgetItem  # noqa: E402

from mhm_pipeline.gui.dialogs.widgets.json_tree_viewer import (  # noqa: E402
    JsonTreeViewer,
)


@pytest.fixture(autouse=True)
def qapp() -> Iterator[QApplication]:
    app = QApplication.instance() or QApplication([])
    yield app


def _collect_all_items(viewer: JsonTreeViewer) -> list[QTreeWidgetItem]:
    """Return every QTreeWidgetItem in *viewer*, regardless of visibility."""
    out: list[QTreeWidgetItem] = []

    def walk(node: QTreeWidgetItem) -> None:
        for i in range(node.childCount()):
            child = node.child(i)
            if child is None:
                continue
            out.append(child)
            walk(child)

    walk(viewer.invisibleRootItem())
    return out


class TestJsonTreeViewerRendering:
    def test_nested_dict_renders_with_correct_depth(self) -> None:
        data = {"a": {"b": {"c": "leaf"}}}
        viewer = JsonTreeViewer(data)

        items = _collect_all_items(viewer)
        # We expect at least three intermediate items (a, b, c).
        labels = [it.text(0) for it in items]
        assert "a" in labels
        assert "b" in labels
        assert "c" in labels

        # And a leaf item carrying the scalar.
        leaf_values = [it.text(1) for it in items if it.text(0) == "c"]
        assert "leaf" in leaf_values

    def test_list_renders_with_indexed_labels(self) -> None:
        data = {"items": ["alpha", "beta", "gamma"]}
        viewer = JsonTreeViewer(data)
        items = _collect_all_items(viewer)
        labels = [it.text(0) for it in items]
        assert "[0]" in labels
        assert "[1]" in labels
        assert "[2]" in labels

    def test_search_hides_non_matching_nodes(self) -> None:
        data = {
            "title": "The Mishneh Torah",
            "author": "Maimonides",
            "shelfmark": "Heb 8 1234",
        }
        viewer = JsonTreeViewer(data)
        viewer.search("Maimonides")

        items = _collect_all_items(viewer)
        # The matching node is visible. At least one non-matching scalar
        # node is hidden.
        matching = [
            it for it in items
            if "maimonides" in it.text(0).lower() or "maimonides" in it.text(1).lower()
        ]
        assert matching
        for it in matching:
            assert it.isHidden() is False

        hidden_count = sum(1 for it in items if it.isHidden())
        assert hidden_count > 0

        # Clearing the search restores everything.
        viewer.search("")
        for it in _collect_all_items(viewer):
            assert it.isHidden() is False

    def test_handles_edge_case_payloads_without_crashing(self) -> None:
        # Each of these should construct + render without raising.
        for payload in (None, [], {}, {"a": None, "b": [], "c": {}}):
            viewer = JsonTreeViewer(payload)
            assert viewer is not None
            # And the search invocation should also be safe.
            viewer.search("anything")
            viewer.search("")

    def test_friendly_key_renaming_renders_friendly_label(self) -> None:
        data = {"_control_number": "990001234"}
        viewer = JsonTreeViewer(
            data,
            friendly_key_map={"_control_number": "Manuscript ID"},
        )
        items = _collect_all_items(viewer)
        labels = [it.text(0) for it in items]
        # The visible label uses the friendly name; the raw snake-case
        # key is hidden.
        assert "Manuscript ID" in labels
        assert "_control_number" not in labels

        # The scalar value travels with the friendly key.
        target = next(it for it in items if it.text(0) == "Manuscript ID")
        assert target.text(1) == "990001234"

    def test_set_friendly_keys_replaces_mapping_and_re_renders(self) -> None:
        viewer = JsonTreeViewer({"_control_number": "990001234"})
        # Before set_friendly_keys, raw label is visible.
        labels_before = [it.text(0) for it in _collect_all_items(viewer)]
        assert "_control_number" in labels_before

        viewer.set_friendly_keys({"_control_number": "Manuscript ID"})
        labels_after = [it.text(0) for it in _collect_all_items(viewer)]
        assert "Manuscript ID" in labels_after
        assert "_control_number" not in labels_after
