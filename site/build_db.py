#!/usr/bin/env python3
"""
build_db.py  -  Build the ASL corpus SQLite database for the retrieval site.

Run this ONCE (and re-run whenever data changes) from the folder that contains
this script.  It scans your local Videos\\ and Prompts\\ folders, joins the
Track 2 (human) and Track 3 (machine) CSVs on (generator, vlm, strategy, letter),
and writes asl.db  -  including an FTS5 full-text index over the prompt text.

Expected layout (edit ROOT below if yours differs):

  C:\\Users\\User\\Desktop\\FYP ASL\\
    Videos\\{generator}\\{VlmDisplay}\\{prefix}_{vlm}_{STRAT}_{letter}.mp4   (500)
    Prompts\\{vlm}\\{full_strategy}__{letter}.txt                            (125)
    site\\  (this folder: build_db.py, app.py, index.html, CSVs)

Place the four CSVs next to this script (or fix the CSV_* paths):
    slr_predictions_2.csv
    Track2_Human_Evaluation__Ratings.csv
    accuracy_by_generator_2.csv         (optional, for the stats page)
    accuracy_by_strategy_2.csv          (optional)
"""

import csv, os, re, sqlite3, sys
from pathlib import Path

# --------------------------------------------------------------------------
# CONFIG  -  edit these two if your folders live elsewhere
# --------------------------------------------------------------------------
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent                 # assumes site\ sits inside  FYP ASL\
VIDEOS_DIR  = ROOT / "Videos"
PROMPTS_DIR = ROOT / "Prompts"

CSV_T3 = HERE / "slr_predictions_2.csv"
CSV_T2 = HERE / "Track2_Human_Evaluation_-_Ratings.csv"
DB_PATH = HERE / "asl.db"

VALID_STRATEGIES = ['zero_shot','role_prompted','chain_of_thought',
                    'schema_constrained','few_shot']
LETTERS = ['A','B','C','O','V']

# --------------------------------------------------------------------------
# Normalisers  -  the only real VLM mismatch is chatgpt (T3) vs openai (T2)
# --------------------------------------------------------------------------
def norm_vlm(v):
    v = (v or "").strip().lower().strip('"')
    return 'openai' if v == 'chatgpt' else v

def norm_gen(g):
    return (g or "").strip().lower().strip('"')

def norm_strat(s):
    return (s or "").strip().strip('"')

def key(gen, vlm, strat, letter):
    return (norm_gen(gen), norm_vlm(vlm), norm_strat(strat), (letter or "").strip().upper())

def to_float(x):
    try: return float(x)
    except (TypeError, ValueError): return None

def to_int(x):
    try: return int(float(x))
    except (TypeError, ValueError): return None

# --------------------------------------------------------------------------
# 1) Load Track 3 (machine recognition)  -  the spine: one row per video
# --------------------------------------------------------------------------
records = {}   # key -> dict of all fields
if not CSV_T3.exists():
    sys.exit(f"ERROR: missing {CSV_T3}")
with open(CSV_T3, newline='', encoding='utf-8-sig') as f:
    for r in csv.DictReader(f):
        k = key(r['model_dir'], r['vlm'], r['strategy'], r['true_class'])
        records[k] = {
            'generator': k[0], 'vlm': k[1], 'strategy': k[2], 'letter': k[3],
            't3_file': r['file'].strip(),
            'mp_pred': r.get('mp_pred','').strip(),
            'mp_conf': to_float(r.get('mp_conf')),
            'landmarks_found': 1 if str(r.get('landmarks_found')).strip().lower()=='true' else 0,
            'sg_pred_restricted': r.get('sg_pred_restricted','').strip(),
            'sg_conf_raw': to_float(r.get('sg_conf_raw')),
            'mp_correct': to_int(r.get('mp_correct')),
            'sg_correct_restricted': to_int(r.get('sg_correct_restricted')),
            'sg_correct_raw': to_int(r.get('sg_correct_raw')),
        }
print(f"Track 3: {len(records)} videos loaded")

# --------------------------------------------------------------------------
# 2) Join Track 2 (human ratings)
# --------------------------------------------------------------------------
def clean_header(name):
    # ratings CSV headers can carry multi-line descriptions; keep leading token
    return (name or "").split('\n')[0].strip()

t2_hits = 0
if CSV_T2.exists():
    with open(CSV_T2, newline='', encoding='utf-8-sig') as f:
        reader = csv.reader(f)
        raw_header = next(reader)
        header = [clean_header(h) for h in raw_header]
        idx = {h: i for i, h in enumerate(header)}
        for row in reader:
            if not any(c.strip() for c in row):  # blank line
                continue
            def cell(col):
                i = idx.get(col)
                return row[i] if (i is not None and i < len(row)) else ''
            gen = cell('generator')
            if not gen.strip() or gen.strip() == '"':
                continue
            k = key(gen, cell('vlm'), cell('strategy'), cell('target_letter'))
            if k in records:
                records[k].update({
                    'h_recognized_as': cell('recognized_as').strip(),
                    'h_recog_correct': to_int(cell('recog_correct')),
                    'h_handshape': to_float(cell('handshape_score')),
                    'h_quality': to_int(cell('sign_quality')),
                    'h_prompt_following': to_int(cell('prompt_following')),
                    'h_notes': cell('notes').strip(),
                })
                t2_hits += 1
    print(f"Track 2: {t2_hits} rows joined")
else:
    print(f"WARNING: {CSV_T2} not found - human columns will be blank")

# --------------------------------------------------------------------------
# 3) Deterministic path manifest  -  ported verbatim from rate.py so video
#    linking is guaranteed, not scan-dependent. Same source of truth the
#    rating tool already used across a full 500-video pass.
#
#    Video path : Videos/{GenDisplay}/{VlmDisplay}/{filename}
#    Wan quirk  : lowercase filename + ROLE (not RP) for role_prompted
#    Prompt path: Prompts/{project_vlm}/{full_strategy}__{Letter}.txt
# --------------------------------------------------------------------------
# (display_folder, filename_prefix) -- folder and prefix can differ (e.g. Kling)
GENS = [("Kling 3.0", "Kling"), ("Seedance", "Seedance"), ("Veo", "Veo"), ("Wan", "wan")]
VLMS = [("ChatGPT", "openai"), ("Claude", "claude"), ("Gemini", "gemini"),
        ("Llama", "llama"), ("Qwen", "qwen")]
STRAT_ABBR = {"zero_shot": "ZS", "role_prompted": "RP", "chain_of_thought": "COT",
              "schema_constrained": "SC", "few_shot": "FS"}

# (generator, vlm, strategy, letter) -> {video, prompt} relative paths
# generator key in the CSVs is lowercase of the filename PREFIX (kling/seedance/veo/wan)
PATHS = {}
for gf, prefix in GENS:                       # gf = display folder, prefix = filename token
    is_wan = (prefix == "wan")
    gen_key = prefix.lower()                   # matches model_dir / generator in CSVs
    for vd, proj in VLMS:                      # vd = display folder, proj = project/CSV vlm
        for strat, ab in STRAT_ABBR.items():
            for L in LETTERS:
                if is_wan:
                    s = "ROLE" if strat == "role_prompted" else ab
                    fn = f"{prefix.lower()}_{vd.lower()}_{s}_{L}.mp4"
                else:
                    fn = f"{prefix}_{vd}_{ab}_{L}.mp4"
                PATHS[(gen_key, proj, strat, L)] = {
                    "video":  f"Videos/{gf}/{vd}/{fn}",
                    "prompt": f"Prompts/{proj}/{strat}__{L}.txt",
                }

def resolve_video(rec):
    return PATHS.get((rec['generator'], rec['vlm'], rec['strategy'], rec['letter']), {}).get("video", "")

def load_prompt(vlm, strategy, letter):
    rel = PATHS.get(("kling", vlm, strategy, letter), {}).get("prompt")  # prompt path is gen-independent
    if not rel:
        return ''
    p = ROOT / rel
    if p.exists():
        try:
            return p.read_text(encoding='utf-8', errors='replace')
        except Exception:
            return ''
    return ''

# --------------------------------------------------------------------------
# 5) Write SQLite
# --------------------------------------------------------------------------
if DB_PATH.exists():
    DB_PATH.unlink()
con = sqlite3.connect(DB_PATH)
cur = con.cursor()
cur.executescript("""
CREATE TABLE videos (
  id INTEGER PRIMARY KEY,
  generator TEXT, vlm TEXT, strategy TEXT, letter TEXT,
  video_path TEXT, t3_file TEXT,
  mp_pred TEXT, mp_conf REAL, landmarks_found INTEGER,
  sg_pred_restricted TEXT, sg_conf_raw REAL,
  mp_correct INTEGER, sg_correct_restricted INTEGER, sg_correct_raw INTEGER,
  h_recognized_as TEXT, h_recog_correct INTEGER, h_handshape REAL,
  h_quality INTEGER, h_prompt_following INTEGER, h_notes TEXT,
  prompt_text TEXT
);
CREATE INDEX ix_letter    ON videos(letter);
CREATE INDEX ix_generator ON videos(generator);
CREATE INDEX ix_vlm       ON videos(vlm);
CREATE INDEX ix_strategy  ON videos(strategy);
CREATE VIRTUAL TABLE prompts_fts USING fts5(
  prompt_text, content='videos', content_rowid='id'
);
""")

rows_inserted = 0
for k, rec in sorted(records.items()):
    rec['video_path'] = resolve_video(rec)
    rec['prompt_text'] = load_prompt(rec['vlm'], rec['strategy'], rec['letter'])
    cur.execute("""INSERT INTO videos
        (generator,vlm,strategy,letter,video_path,t3_file,
         mp_pred,mp_conf,landmarks_found,sg_pred_restricted,sg_conf_raw,
         mp_correct,sg_correct_restricted,sg_correct_raw,
         h_recognized_as,h_recog_correct,h_handshape,h_quality,
         h_prompt_following,h_notes,prompt_text)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (rec['generator'],rec['vlm'],rec['strategy'],rec['letter'],
         rec['video_path'],rec['t3_file'],
         rec.get('mp_pred'),rec.get('mp_conf'),rec.get('landmarks_found'),
         rec.get('sg_pred_restricted'),rec.get('sg_conf_raw'),
         rec.get('mp_correct'),rec.get('sg_correct_restricted'),rec.get('sg_correct_raw'),
         rec.get('h_recognized_as'),rec.get('h_recog_correct'),rec.get('h_handshape'),
         rec.get('h_quality'),rec.get('h_prompt_following'),rec.get('h_notes'),
         rec['prompt_text']))
    rows_inserted += 1

cur.execute("INSERT INTO prompts_fts(rowid, prompt_text) SELECT id, prompt_text FROM videos")
con.commit()

linked = cur.execute("SELECT COUNT(*) FROM videos WHERE video_path != ''").fetchone()[0]
on_disk = 0
for row in con.execute("SELECT video_path FROM videos WHERE video_path != ''") if False else []:
    pass
# count how many resolved paths actually exist on disk
tmp = sqlite3.connect(DB_PATH); tmp.row_factory = sqlite3.Row
for r in tmp.execute("SELECT video_path FROM videos WHERE video_path != ''"):
    if (ROOT / r["video_path"]).exists():
        on_disk += 1
tmp.close()
withprompt = cur.execute("SELECT COUNT(*) FROM videos WHERE prompt_text != ''").fetchone()[0]
con.close()

print("-"*60)
print(f"asl.db written: {rows_inserted} videos")
print(f"  paths assigned (deterministic)  : {linked}/500")
print(f"  videos actually found on disk   : {on_disk}/500")
print(f"  videos with prompt text loaded  : {withprompt}/500")
if on_disk < 500:
    print("  (videos not found still appear in the site; just no playback.")
    print("   If this is 0, fix ROOT at the top of build_db.py and re-run.)")
print("Done. Now run:  python app.py")
