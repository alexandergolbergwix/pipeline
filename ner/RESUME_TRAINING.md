# Genre Classifier Training — Resume Instructions

## Current State (interrupted 2026-04-20 ~16:47)

Training v12b was interrupted at **Fold 1, Epoch 11, val_F1 = 0.9060**.

Since the model was mid-fold-1, no fold checkpoint was saved. Restart from fold 1 (the default).

Progress log is saved at: `ner/genre_train_v12b_progress.log`

---

## Command to resume (restart from fold 1)

```bash
cd /Users/alexandergo/Documents/Doctorat/pipeline
nohup .venv/bin/python -u -m ner.train_genre_classifier \
  --exclude-genres "Literature (Miscellaneous, in manuscript)" \
  --min-class-size 100 --top-k 8 --focal-gamma 2.0 \
  --epochs 30 --patience 5 --freeze-layers 10 \
  --batch-size 64 --max-length 64 \
  > /private/tmp/genre_train_v12b_r2.log 2>&1 &
echo "PID: $!"
```

Monitor:
```bash
tail -f /private/tmp/genre_train_v12b_r2.log
```

---

## If interrupted AFTER a fold completes

The script now saves `ner/genre_classifier_fold_N.pt` after each fold. If interrupted after fold K, resume with:

```bash
# Example: resume from fold 3 (folds 1+2 already saved as .pt files)
nohup .venv/bin/python -u -m ner.train_genre_classifier \
  --exclude-genres "Literature (Miscellaneous, in manuscript)" \
  --min-class-size 100 --top-k 8 --focal-gamma 2.0 \
  --epochs 30 --patience 5 --freeze-layers 10 \
  --batch-size 64 --max-length 64 \
  --start-fold 3 \
  > /private/tmp/genre_train_resume.log 2>&1 &
```

The `--start-fold 3` flag loads fold 1 and 2 results from their `.pt` files and continues training fold 3 onward.

---

## Expected results (based on fold 1 progress before interruption)

| Epoch | val_F1 | Threshold |
|-------|--------|-----------|
| 5     | 0.8704 | 0.65      |
| 8     | 0.8969 | 0.65      |
| 10    | 0.9021 | 0.65      |
| 11    | 0.9060 | 0.65      |

Expected final mean F1 across all 5 folds: **~0.88–0.90**

---

## Training configuration

| Parameter | Value |
|-----------|-------|
| Model | dicta-il/dictabert warm-started from provenance NER |
| Freeze layers | 10 (bottom 10 frozen, top 2 + head trainable) |
| Classes | 8 genres + NOTA (Literature Misc. excluded) |
| Samples | ~25,421 (from genre_samples.tsv) |
| Loss | Focal Loss γ=2.0, per-class pos_weight |
| Batch size | 64 |
| Max length | 64 tokens |
| LR | 2e-5 (head), 2e-6 (encoder) |
| Epochs | 30 max, patience=5 |
| Device | MPS (Apple M4 Pro) |
| Est. time | ~40 min total (17 sec/epoch × ~30 epochs × 5 folds) |
