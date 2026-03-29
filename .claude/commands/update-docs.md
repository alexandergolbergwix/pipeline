Check that SystemDesignDocument.tex and ProjectDefinitionDocument.tex are in sync with the current code, and update any sections that are out of date.

## Rule (from CLAUDE.md)

> A code change that alters the system design is not complete until the relevant `.tex` document is also updated.

This skill must be invoked — or the relevant sections must be updated inline — **after every code change that touches any of the following**:

| Code change | Document to update | Sections |
|-------------|-------------------|---------|
| New/removed pipeline stage | ProjectDefinitionDocument.tex | §stage list, interfaces, data inventory |
| Changed worker `__init__` signature | SystemDesignDocument.tex | §5 component table, §7.2 panel table |
| New/removed setting in SettingsManager | SystemDesignDocument.tex | §7.4 settings table |
| New GUI panel or widget | SystemDesignDocument.tex | §7.1 main window, §7.2 panel table |
| Changed GUI widget styling/colors | SystemDesignDocument.tex | §7.3 dark mode support |
| Changed module structure (`src/mhm_pipeline/`) | SystemDesignDocument.tex | §6.1 repo layout |
| New external API integrated | ProjectDefinitionDocument.tex | §external APIs; SystemDesignDocument.tex §5 |
| Changed distribution/installer strategy | SystemDesignDocument.tex | §4 |
| Changed dependencies in `pyproject.toml` | SystemDesignDocument.tex | §10 requirements table |
| New MARC field mapping | ProjectDefinitionDocument.tex | §MARC field mappings |

## Procedure

1. Read the last few modifications you made this session (or read recent git log if available):
   ```bash
   git log --oneline -10 2>/dev/null || echo "(no git)"
   ```
2. Read the relevant sections of `SystemDesignDocument.tex` and `ProjectDefinitionDocument.tex`.
3. List every discrepancy between the document and the code.
4. Update the document sections that are out of date, following the existing LaTeX style exactly (same `\code{}`, `longtable`, `\hline` patterns).
5. Do not change the document version number or date unless explicitly asked.

## Quick checks (run these to catch common gaps)

```bash
# Settings in code but not in doc?
grep "= \"" src/mhm_pipeline/settings/settings_manager.py | grep -v "^#" | grep "_KEY\s*="

# Worker classes in code but not in doc?
grep "^class.*Worker" src/mhm_pipeline/controller/workers.py
```

Compare outputs against the §5 component table and §7.4 settings table in SystemDesignDocument.tex.

## Key GUI widgets to document

- `PipelineFlowWidget` — Stage progress visualization with dark mode support
- `EntityHighlighter` — NER results display with colored entity spans
- `MarcFieldVisualizer` — MARC field tree view with color-coded field types
- `TripleGraphView` — Interactive RDF graph visualization
- `ValidationResultView` — SHACL validation results with filtering
- `AuthorityMatcherView` — Authority match results table
- `UploadProgressView` — Wikidata upload progress with entity status

All visualization widgets inherit from `BaseVisualizationWidget` which provides the `is_dark_mode()` utility for theme adaptation.
