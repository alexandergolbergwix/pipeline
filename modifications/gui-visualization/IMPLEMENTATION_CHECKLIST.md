# GUI Visualization Implementation Checklist

## Quick Reference Card

### New Files to Create (8 widgets)

```
src/mhm_pipeline/gui/widgets/
├── __init__.py (update exports)
├── marc_field_visualizer.py      ⬅️ Component 1: MARC field tree view
├── entity_highlighter.py         ⬅️ Component 2: Highlighted NER spans
├── authority_matcher_view.py     ⬅️ Component 3: Side-by-side matches
├── triple_graph_view.py          ⬅️ Component 4: RDF graph visualization
├── validation_result_view.py     ⬅️ Component 5: SHACL validation report
├── upload_progress_view.py       ⬅️ Component 6: Wikidata upload status
├── stage_diff_widget.py          ⬅️ Component 7: Before/after comparison
└── pipeline_flow_widget.py       ⬅️ Component 8: Pipeline overview
```

### Files to Modify (6 panels + 1 main)

```
src/mhm_pipeline/gui/
├── panels/
│   ├── convert_panel.py     ⬅️ Add MarcFieldVisualizer
│   ├── ner_panel.py         ⬅️ Add EntityHighlighter
│   ├── authority_panel.py   ⬅️ Add AuthorityMatcherView
│   ├── rdf_panel.py         ⬅️ Add TripleGraphView
│   ├── validate_panel.py    ⬅️ Add ValidationResultView
│   └── wikidata_panel.py    ⬅️ Add UploadProgressView
├── main_window.py           ⬅️ Add PipelineFlowWidget to top
└── widgets/
    └── __init__.py          ⬅️ Export new widgets
```

---

## Implementation Order (Recommended)

### Phase 1: Foundation (Week 1)
- [ ] Create `src/mhm_pipeline/gui/widgets/` directory
- [ ] Set up base widget class (`BaseVisualizationWidget`)
- [ ] Implement `MarcFieldVisualizer` (Stage 1)
- [ ] Update `ConvertPanel` to integrate widget
- [ ] Write tests for `MarcFieldVisualizer`

### Phase 2: NER & Authority (Week 2)
- [ ] Implement `EntityHighlighter` (Stage 2)
- [ ] Update `NerPanel`
- [ ] Write tests for `EntityHighlighter`
- [ ] Implement `AuthorityMatcherView` (Stage 3)
- [ ] Update `AuthorityPanel`
- [ ] Write tests for `AuthorityMatcherView`

### Phase 3: RDF & Validation (Week 3)
- [ ] Implement `TripleGraphView` (Stage 4)
- [ ] Update `RdfPanel`
- [ ] Write tests for `TripleGraphView`
- [ ] Implement `ValidationResultView` (Stage 5)
- [ ] Update `ValidatePanel`
- [ ] Write tests for `ValidationResultView`

### Phase 4: Upload & Polish (Week 4)
- [ ] Implement `UploadProgressView` (Stage 6)
- [ ] Update `WikidataPanel`
- [ ] Write tests for `UploadProgressView`
- [ ] Implement `PipelineFlowWidget` (overview)
- [ ] Update `MainWindow`
- [ ] Write tests for `PipelineFlowWidget`

### Phase 5: Advanced Features (Week 5)
- [ ] Implement `StageDiffWidget` (optional enhancement)
- [ ] Add export functionality (PNG/SVG/PDF)
- [ ] Performance optimization
- [ ] Update documentation

---

## Testing Checklist

### Unit Tests (per widget)
- [ ] `test_load_data()` - Data loading works correctly
- [ ] `test_clear_data()` - Clearing works correctly
- [ ] `test_empty_data()` - Handles empty input gracefully
- [ ] `test_large_data()` - Handles large datasets

### Integration Tests (per panel)
- [ ] `test_widget_integration()` - Widget appears in panel
- [ ] `test_data_flow()` - Data flows from controller to widget
- [ ] `test_user_interaction()` - Click/hover interactions work

### UI Tests
- [ ] Visual appearance matches mockups
- [ ] Colors follow project palette
- [ ] Layout is responsive (resizing works)
- [ ] Accessibility (keyboard navigation)

### Performance Tests
- [ ] 100 records load in < 1 second
- [ ] 1000 triples render in < 2 seconds
- [ ] Memory usage stays < 500MB

---

## Documentation Checklist

- [ ] Update `SystemDesignDocument.tex` Section 5 (GUI Design)
  - [ ] Add section on visualization widgets
  - [ ] Update component diagrams
  - [ ] Document new widget classes

- [ ] Update `CLAUDE.md` (if needed)
  - [ ] Add new key paths
  - [ ] Update learned rules

- [ ] Code Documentation
  - [ ] All classes have docstrings
  - [ ] All public methods have docstrings
  - [ ] Type hints on all functions
  - [ ] Example usage in docstrings

---

## Code Quality Checklist

### Style
- [ ] Run `ruff check src/mhm_pipeline/gui/widgets/`
- [ ] Run `ruff format src/mhm_pipeline/gui/widgets/`
- [ ] No `Any` types used
- [ ] No bare `except:` clauses

### Type Safety
- [ ] Run `mypy src/mhm_pipeline/gui/widgets/`
- [ ] All function parameters typed
- [ ] All return values typed
- [ ] No `# type: ignore` without justification

### Error Handling
- [ ] Invalid data handled gracefully
- [ ] Network errors handled (for upload widget)
- [ ] File I/O errors handled
- [ ] User-friendly error messages

---

## Pre-Commit Checklist

Before committing changes:

- [ ] All tests pass (`/run-tests`)
- [ ] Code formatted with ruff
- [ ] Type checking passes
- [ ] No debug print statements
- [ ] No commented-out code
- [ ] Commit message follows convention: `feat: add visualization widgets`
- [ ] Documentation updated

---

## Final Review Checklist

Before marking complete:

- [ ] Visual review with project stakeholders
- [ ] User testing (3 users minimum)
- [ ] Performance testing on large dataset
- [ ] Cross-platform testing (macOS, Linux)
- [ ] Accessibility review
- [ ] Update `SystemDesignDocument.tex`
- [ ] Merge to main branch

---

*Use this checklist alongside the main plan document: gui-improvement-plan.md*
