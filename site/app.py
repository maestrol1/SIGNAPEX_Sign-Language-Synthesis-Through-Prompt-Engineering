#!/usr/bin/env python3
"""
app.py  -  Local FastAPI server for the ASL corpus retrieval site.

  pip install fastapi uvicorn
  python app.py
  open http://127.0.0.1:8000

Serves:
  GET /                     -> index.html
  GET /api/facets           -> distinct values for dropdowns + counts
  GET /api/stats            -> aggregate accuracy by generator / strategy / letter
  GET /api/videos?...       -> faceted query + optional FTS5 search (q=)
  GET /video/{id}           -> streams the mp4 with HTTP range support
"""
import sqlite3
from pathlib import Path
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, Response
import uvicorn

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DB_PATH = HERE / "asl.db"
INDEX_HTML = HERE / "index.html"

STRAT_ORDER = ['zero_shot','role_prompted','chain_of_thought',
               'schema_constrained','few_shot']

app = FastAPI(title="ASL Corpus Retrieval")

def db():
    if not DB_PATH.exists():
        raise HTTPException(500, "asl.db not found - run  python build_db.py  first")
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con

@app.get("/", response_class=HTMLResponse)
def index():
    return INDEX_HTML.read_text(encoding="utf-8")

@app.get("/favicon.ico")
def favicon():
    p = HERE / "assets" / "signapex_logo.png"
    if p.exists():
        return FileResponse(p, media_type="image/png")
    raise HTTPException(404)

@app.get("/assets/{name}")
def asset(name: str):
    p = (HERE / "assets" / name).resolve()
    if not p.exists() or HERE / "assets" not in p.parents:
        raise HTTPException(404, "asset not found")
    mt = "image/png" if name.lower().endswith(".png") else "application/octet-stream"
    return FileResponse(p, media_type=mt)

@app.get("/api/facets")
def facets():
    con = db(); cur = con.cursor()
    out = {}
    for col in ("letter", "generator", "vlm", "strategy"):
        rows = cur.execute(f"SELECT {col}, COUNT(*) c FROM videos GROUP BY {col}").fetchall()
        out[col] = {r[0]: r[1] for r in rows}
    out["total"] = cur.execute("SELECT COUNT(*) FROM videos").fetchone()[0]
    out["linked"] = cur.execute("SELECT COUNT(*) FROM videos WHERE video_path!=''").fetchone()[0]
    con.close()
    # order strategies canonically
    out["strategy_order"] = [s for s in STRAT_ORDER if s in out["strategy"]]
    return out

@app.get("/api/stats")
def stats():
    con = db(); cur = con.cursor()
    def agg(col, order=None):
        rows = cur.execute(f"""
            SELECT {col} k,
                   AVG(h_recog_correct)*1.0       human,
                   AVG(h_handshape)               handshape,
                   AVG(h_quality)                 quality,
                   AVG(h_prompt_following)        prompt_following,
                   AVG(mp_correct)*1.0            mp,
                   AVG(sg_correct_restricted)*1.0 sg,
                   AVG(sg_correct_raw)*1.0        sg_raw,
                   COUNT(*) n
            FROM videos GROUP BY {col}""").fetchall()
        data = [dict(r) for r in rows]
        if order:
            data.sort(key=lambda d: order.index(d['k']) if d['k'] in order else 99)
        return data
    res = {
        "by_generator": agg("generator"),
        "by_strategy":  agg("strategy", STRAT_ORDER),
        "by_letter":    agg("letter"),
    }
    con.close()
    return res

@app.get("/api/scatter")
def scatter():
    """Per-cell points for the human-vs-machine scatter.
    Each point = one (generator, vlm, strategy, letter) cell (n=1 video),
    so we aggregate to the (generator, vlm, strategy) level (n=5 letters)
    to get stable 0..1 rates worth plotting."""
    con = db(); cur = con.cursor()
    rows = cur.execute("""
        SELECT generator, vlm, strategy,
               AVG(h_recog_correct)*1.0        human,
               AVG(mp_correct)*1.0             mp,
               AVG(sg_correct_restricted)*1.0  sg,
               COUNT(*) n
        FROM videos
        GROUP BY generator, vlm, strategy""").fetchall()
    con.close()
    return [dict(r) for r in rows]

@app.get("/api/videos")
def videos(letter: str = "", generator: str = "", vlm: str = "",
           strategy: str = "", q: str = "", sort: str = "",
           limit: int = 500, offset: int = 0):
    con = db(); cur = con.cursor()
    where, params = [], []
    for col, val in (("letter", letter), ("generator", generator),
                     ("vlm", vlm), ("strategy", strategy)):
        if val:
            where.append(f"v.{col}=?"); params.append(val)

    # ---- sort options -------------------------------------------------
    # Individual videos are pass/fail (1/0), so we sort by the relevant
    # correctness flag and break ties with the softer scores so ordering
    # is meaningful within a group rather than arbitrary.
    SORTS = {
        "human_desc": ("v.h_recog_correct DESC, v.h_handshape DESC, "
                       "v.h_quality DESC, v.mp_correct DESC"),
        "human_asc":  ("v.h_recog_correct ASC, v.h_handshape ASC, "
                       "v.h_quality ASC, v.mp_correct ASC"),
        "machine_desc": ("(COALESCE(v.mp_correct,0)+COALESCE(v.sg_correct_restricted,0)) DESC, "
                         "v.mp_conf DESC, v.h_recog_correct DESC"),
        "machine_asc":  ("(COALESCE(v.mp_correct,0)+COALESCE(v.sg_correct_restricted,0)) ASC, "
                         "v.mp_conf ASC, v.h_recog_correct ASC"),
        "combined_desc": ("(COALESCE(v.h_recog_correct,0)+COALESCE(v.mp_correct,0)"
                          "+COALESCE(v.sg_correct_restricted,0)) DESC, "
                          "v.h_handshape DESC, v.mp_conf DESC"),
        "combined_asc":  ("(COALESCE(v.h_recog_correct,0)+COALESCE(v.mp_correct,0)"
                          "+COALESCE(v.sg_correct_restricted,0)) ASC, "
                          "v.h_handshape ASC, v.mp_conf ASC"),
    }
    default_order = "v.letter, v.generator, v.vlm, v.strategy"
    sort_order = SORTS.get(sort)
    # NULL human values sort last on DESC and first on ASC by default in SQLite;
    # push unrated/unlinked rows to the bottom regardless of direction.
    null_guard = ""
    if sort in ("human_desc", "human_asc", "combined_desc", "combined_asc"):
        null_guard = "v.h_recog_correct IS NULL, "

    if q.strip():
        sql = """SELECT v.* FROM prompts_fts f JOIN videos v ON v.id=f.rowid
                 WHERE prompts_fts MATCH ?"""
        params = [q.strip()] + params
        if where:
            sql += " AND " + " AND ".join(where)
        sql += f" ORDER BY {null_guard}{sort_order}" if sort_order else " ORDER BY rank"
        sql += " LIMIT ? OFFSET ?"
    else:
        sql = "SELECT v.* FROM videos v"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += (f" ORDER BY {null_guard}{sort_order}" if sort_order
                else f" ORDER BY {default_order}")
        sql += " LIMIT ? OFFSET ?"
    params += [limit, offset]

    rows = [dict(r) for r in cur.execute(sql, params).fetchall()]
    con.close()
    return JSONResponse({"count": len(rows), "results": rows})

@app.get("/video/{vid}")
def video(vid: int, request: Request):
    con = db(); cur = con.cursor()
    row = cur.execute("SELECT video_path FROM videos WHERE id=?", (vid,)).fetchone()
    con.close()
    if not row or not row["video_path"]:
        raise HTTPException(404, "No video file linked for this row")
    path = (ROOT / row["video_path"]).resolve()
    if not path.exists():
        raise HTTPException(404, f"File missing on disk: {row['video_path']}")

    file_size = path.stat().st_size
    range_header = request.headers.get("range")
    if range_header:
        # bytes=start-end
        try:
            unit, rng = range_header.split("=")
            start_s, end_s = rng.split("-")
            start = int(start_s)
            end = int(end_s) if end_s else file_size - 1
        except Exception:
            start, end = 0, file_size - 1
        end = min(end, file_size - 1)
        length = end - start + 1
        with open(path, "rb") as f:
            f.seek(start); data = f.read(length)
        return Response(data, status_code=206, headers={
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Accept-Ranges": "bytes",
            "Content-Length": str(length),
            "Content-Type": "video/mp4",
        })
    return FileResponse(path, media_type="video/mp4")

if __name__ == "__main__":
    if not DB_PATH.exists():
        print("asl.db not found. Run  python build_db.py  first."); raise SystemExit(1)
    print("Serving on http://127.0.0.1:8000  (Ctrl+C to stop)")
    uvicorn.run(app, host="127.0.0.1", port=8000)
