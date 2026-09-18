# SignApex — A Prompt-to-Sign Corpus

<img width="2816" height="1536" alt="signapex_logo png" src="https://github.com/user-attachments/assets/13b3a9c4-3d30-4c02-a5b3-83dd0a588b11" />

A local-only web app for browsing the 500 generated ASL videos with their
Track 2 (human) and Track 3 (machine) recognition scores, full-text search
over the prompt text, and an aggregate statistics view. Built for the viva demo.

Stack: SQLite + FTS5 · FastAPI · single HTML page. No internet required once installed.
Videos are loaded only after a letter or filter is chosen, so the homepage stays fast.

---

## 1. Where to put this folder

Drop this `site\` folder **inside** your project root so it sits next to
`Videos\` and `Prompts\`:

```
C:\Users\User\Desktop\FYP ASL\
├── Videos\            (500 .mp4)
    ├── Kling 3.0       (125 Videos)
    ├── Seedance        (125 Videos)
    ├── Veo             (125 Videos)
    ├── Wan             (125 Videos)
├── Prompts\           (125 .txt)
└── site\              <-- this folder
    ├── build_db.py
    ├── app.py
    ├── index.html
    ├── assets\              (the SignApex logo — keep this folder)
    │   ├── signapex_logo_t.png
    │   └── signapex_logo.png
    ├── slr_predictions_2.csv
    ├── Track2_Human_Evaluation__Ratings.csv
    └── README.md
```

If you keep it somewhere else, open `build_db.py` and `app.py` and set
`ROOT` to the full path of your `FYP ASL` folder.

The video dataset naming convention can be seen as:
{T2V}_ {VLM}_ {Strategy}_{Class}

---

## 2. One-time setup

Install Python 3.9+ then:

```
pip install fastapi uvicorn
```

(SQLite + FTS5 ship with Python — nothing else needed.)

---

## 3. Build the database

From inside the `site\` folder:

```
python build_db.py
```

You want to see `videos linked to a file on disk : 500/500`. If it says fewer,
the script could not find your `Videos\` folder — fix `ROOT` at the top of
`build_db.py` and re-run. Videos that don't link still appear in the site;
they just won't play.

Re-run this anytime the CSVs or files change.

---

## 4. Run the site

```
python app.py
```

Open **http://127.0.0.1:8000** in a browser.

---

## 5. Using it

- **Letter buttons (A B C O V)** — the entry point. Click one to filter; click
  again to clear.
- **Generator / VLM / Strategy** dropdowns — secondary facets, combine freely.
- **Search prompt text** — FTS5 over the 125 prompts. Supports `AND`, `OR`,
  `NOT`, quoted phrases, e.g. `thumb AND curved` or `"index finger"`.
  Matched terms are highlighted in the expandable prompt panel on each card.
- Each card shows the video plus **both score sets side by side**: Track 3
  (MediaPipe / SigLIP2 prediction + ✓/✗, landmark detection) and Track 2
  (human read-as + ✓/✗, handshape score, quality). Human rater notes appear
  in italics when present.
- **Statistics tab** — recognition rates by generator, strategy, and letter,
  pulled live from the DB so they always match the videos shown.

---

## 6. Notes / troubleshooting

- The CSVs join on `(generator, vlm, strategy, letter)`, not filename, so the
  Wan lowercase / `ROLE`-vs-`RP` naming quirk doesn't matter for scores. It is
  handled separately when locating the .mp4 on disk.
- `chatgpt` (Track 3) and `openai` (Track 2) are treated as the same VLM.
- Video playback uses HTTP range requests, so seeking/scrubbing works.
- Everything is read-only and local; nothing is uploaded anywhere.
- Blue-and-white theme; safe to screen-share in the viva.
