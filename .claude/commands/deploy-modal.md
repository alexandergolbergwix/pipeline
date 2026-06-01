# /deploy-modal

The Modal NER app lives in the **sibling web repo**, not here.

```
/Users/alexandergo/Documents/Doctorat/mhm-pipeline-web/modal/modal_app.py
```

To deploy it, run:

```bash
cd /Users/alexandergo/Documents/Doctorat/mhm-pipeline-web/modal
modal deploy modal_app.py
```

The web repo's `.claude/commands/deploy-modal.md` has the full
procedure, the pitfall index, and the `modal app logs mhm-ner`
debug command.

## Desktop ↔ Modal relationship (CLAUDE.md Rule 58)

The Modal app **vendors** the desktop's `ner/` + `converter/authority/`
modules via `image.add_local_dir(..., copy=True)`. That means:

- Any edit to `pipeline/ner/inference_pipeline.py`,
  `pipeline/ner/ner_inference_pipeline.py`, or
  `pipeline/converter/authority/genre_classifier.py` requires a
  `modal deploy` in the web repo to take effect on the live endpoint.
- The desktop pipeline is unchanged — it still loads the four `.pt`
  files locally. The Modal app is "the same inference code on
  someone else's box, billed per second."
- Adding a new top-level Python dep to any vendored desktop module
  means adding it to `mhm-pipeline-web/modal/requirements.txt`. The
  sklearn fix in web commit 4f5b765 is the canonical example.

If you change anything under `ner/` or `converter/authority/` that
affects inference shape, push the change here AND redeploy Modal
from the web repo. The two trees must stay in sync.
