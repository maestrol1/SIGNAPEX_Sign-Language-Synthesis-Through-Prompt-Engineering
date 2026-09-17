#!/usr/bin/env python3
"""
Track 2 Human Evaluation — local rating tool
=============================================
Drop this file into:  C:\\Users\\User\\Desktop\\FYP ASL\\
Then run:             python rate.py
Open in browser:      http://localhost:8000

- Plays each of the 500 videos one at a time with its source prompt.
- Blinded flow: you watch -> guess the letter -> the target is revealed -> you score closeness.
- Keyboard driven. Autosaves after every rating to ratings.csv in the same folder.
- Resumes where you left off (skips rows already in ratings.csv).
- No pip installs. Python 3.8+ standard library only.

ratings.csv columns match the Track2_Human_Evaluation Ratings sheet:
video_id, generator, vlm, strategy, target_letter, recognized_as,
recog_correct, handshape_score, sign_quality, prompt_following, notes
"""
import http.server, socketserver, os, json, csv, urllib.parse, re, mimetypes

ROOT = os.path.dirname(os.path.abspath(__file__))
# Reads/writes your existing progress file if present; else creates it.
CSV_PATH = os.path.join(ROOT, "Track2_Human_Evaluation_-_Ratings.csv")
PORT = 8000

# Map any messy/multi-line header (e.g. "handshape_score\n(how close...)")
# back to the canonical field name by matching on the leading token.
HEADER_ALIASES = {
    "video_id": "video_id", "generator": "generator", "vlm": "vlm",
    "strategy": "strategy", "target_letter": "target_letter",
    "recognized_as": "recognized_as", "recog_correct": "recog_correct",
    "handshape_score": "handshape_score", "sign_quality": "sign_quality",
    "prompt_following": "prompt_following", "notes": "notes",
}


def _canon(key):
    if key is None:
        return None
    k = key.strip().lower().split("\n")[0].split("(")[0].strip()
    return HEADER_ALIASES.get(k, k)

GENS = [("Kling 3.0", "Kling"), ("Seedance", "Seedance"), ("Veo", "Veo"), ("Wan", "wan")]
VLMS = [("ChatGPT", "openai"), ("Claude", "claude"), ("Gemini", "gemini"),
        ("Llama", "llama"), ("Qwen", "qwen")]
STRAT = {"zero_shot": "ZS", "role_prompted": "RP", "chain_of_thought": "COT",
         "schema_constrained": "SC", "few_shot": "FS"}
LETTERS = ["A", "B", "C", "O", "V"]
FIELDS = ["video_id", "generator", "vlm", "strategy", "target_letter",
          "recognized_as", "recog_correct", "handshape_score",
          "sign_quality", "prompt_following", "notes"]


def build_manifest():
    rows = []
    for gf, prefix in GENS:
        wan = gf == "Wan"
        for vd, proj in VLMS:
            for strat, ab in STRAT.items():
                for L in LETTERS:
                    if wan:
                        s = "ROLE" if strat == "role_prompted" else ab
                        fn = f"{prefix.lower()}_{vd.lower()}_{s}_{L}.mp4"
                    else:
                        fn = f"{prefix}_{vd}_{ab}_{L}.mp4"
                    rows.append({
                        "video_id": f"{gf.split()[0]}_{proj}_{strat}_{L}",
                        "generator": gf.split()[0], "vlm": proj,
                        "strategy": strat, "letter": L,
                        "video": f"Videos/{gf}/{vd}/{fn}",
                        "prompt": f"Prompts/{proj}/{strat}__{L}.txt",
                    })
    return rows


MANIFEST = build_manifest()


def load_done():
    """Read existing CSV, normalizing any messy headers. A row counts as
    'done' only if recognized_as is filled, so partial/blank rows are skipped."""
    done = {}
    if os.path.exists(CSV_PATH):
        with open(CSV_PATH, newline="", encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            try:
                raw_header = next(reader)
            except StopIteration:
                return done
            cols = [_canon(h) for h in raw_header]
            for row in reader:
                if not any(c.strip() for c in row):
                    continue
                rec = {cols[i]: row[i] for i in range(min(len(cols), len(row)))}
                if (rec.get("recognized_as") or "").strip():
                    done[rec.get("video_id", "")] = {k: rec.get(k, "") for k in FIELDS}
    return done


def save_rating(rec):
    done = load_done()
    done[rec["video_id"]] = rec
    order = {r["video_id"]: i for i, r in enumerate(MANIFEST)}
    rows = sorted(done.values(), key=lambda r: order.get(r["video_id"], 1e9))
    with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDS})


PAGE = r"""<!doctype html><html><head><meta charset="utf-8">
<title>Track 2 Rating</title>
<style>
:root{--blue:#2f6fed;--blue-d:#1f4fb0;--ink:#14233b;--muted:#6b7a90;
--line:#e3e9f2;--bg:#f5f8fd;--card:#fff;--good:#1f9d57;--warn:#e08a00;}
*{box-sizing:border-box;font-family:'Segoe UI',system-ui,Arial,sans-serif}
body{margin:0;background:var(--bg);color:var(--ink)}
header{background:var(--card);border-bottom:1px solid var(--line);padding:12px 20px;
display:flex;align-items:center;gap:16px;position:sticky;top:0;z-index:5}
header h1{font-size:15px;margin:0;font-weight:650;color:var(--blue-d)}
.bar{flex:1;height:8px;background:var(--line);border-radius:6px;overflow:hidden}
.bar>i{display:block;height:100%;background:var(--blue);width:0%}
.count{font-size:13px;color:var(--muted);white-space:nowrap}
.wrap{max-width:1080px;margin:18px auto;padding:0 18px;display:grid;
grid-template-columns:1.1fr .9fr;gap:18px;align-items:start}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;
padding:18px;box-shadow:0 1px 3px rgba(20,40,80,.04)}
video{width:100%;border-radius:10px;background:#000;aspect-ratio:1/1;object-fit:contain}
.meta{font-size:12px;color:var(--muted);margin-top:10px;line-height:1.7}
.meta b{color:var(--ink);font-weight:600}
.prompt{margin-top:12px;border-top:1px dashed var(--line);padding-top:10px;
max-height:230px;overflow:auto;font-size:12.5px;line-height:1.55;white-space:pre-wrap;
color:#33425c;font-family:ui-monospace,Consolas,monospace}
h3{font-size:12px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);
margin:0 0 8px}
.seg{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:16px}
.seg button{flex:1;min-width:42px;padding:11px 0;border:1.5px solid var(--line);
background:#fff;border-radius:9px;font-size:15px;font-weight:600;cursor:pointer;
color:var(--ink);transition:.12s}
.seg button:hover{border-color:var(--blue)}
.seg button.on{background:var(--blue);border-color:var(--blue);color:#fff}
.seg button.none.on{background:var(--muted);border-color:var(--muted)}
.reveal{background:#eef4ff;border:1px solid #d4e2ff;border-radius:9px;padding:9px 12px;
font-size:13px;margin-bottom:14px;color:var(--blue-d)}
.reveal b{font-size:15px}
.locked{opacity:.4;pointer-events:none}
textarea{width:100%;border:1.5px solid var(--line);border-radius:9px;padding:9px;
font-size:13px;resize:vertical;min-height:46px;font-family:inherit}
.row{display:flex;gap:10px;margin-top:14px}
.row button{flex:1;padding:12px;border-radius:10px;border:none;font-size:14px;
font-weight:650;cursor:pointer}
.next{background:var(--blue);color:#fff}.next:hover{background:var(--blue-d)}
.back{background:#fff;border:1.5px solid var(--line)!important;color:var(--ink)}
.skip{background:#fff;border:1.5px solid var(--line)!important;color:var(--muted)}
.exit{background:#fff;border:1.5px solid var(--warn)!important;color:var(--warn);width:100%}
.exit:hover{background:var(--warn);color:#fff}
.hint{font-size:11.5px;color:var(--muted);margin-top:10px;line-height:1.6}
kbd{background:#eef2f8;border:1px solid var(--line);border-radius:4px;padding:1px 5px;
font-size:11px;font-family:ui-monospace,monospace}
.done-screen{text-align:center;padding:60px 20px}
.done-screen h2{color:var(--good)}
</style></head><body>
<header>
<h1>Track 2 — Human Evaluation</h1>
<div class="bar"><i id="prog"></i></div>
<div class="count" id="count">–</div>
</header>
<div id="app"></div>
<script>
let M=[], idx=0, cur=null, R={};
const $=s=>document.querySelector(s);

async function boot(){
  M=await (await fetch('/api/manifest')).json();
  const done=await (await fetch('/api/done')).json();
  R=done;
  // resume at first unrated
  idx=M.findIndex(m=>!done[m.video_id]); if(idx<0)idx=0;
  render();
}
function setProg(){
  const n=Object.keys(R).length;
  $('#prog').style.width=(n/M.length*100)+'%';
  $('#count').textContent=n+' / '+M.length+' rated';
}
function blank(){return{recognized_as:'',handshape_score:'',sign_quality:'',prompt_following:'',notes:''};}

async function render(){
  setProg();
  if(idx>=M.length){doneScreen();return;}
  cur=M[idx];
  const ex=R[cur.video_id]||blank();
  const prompt=await (await fetch('/api/prompt?p='+encodeURIComponent(cur.prompt))).text();
  $('#app').innerHTML=`
  <div class="wrap">
    <div class="card">
      <video id="vid" src="/file?p=${encodeURIComponent(cur.video)}" controls autoplay loop muted></video>
      <div class="meta">
        <b>${cur.video_id}</b><br>
        generator <b>${cur.generator}</b> &nbsp;·&nbsp; vlm <b>${cur.vlm}</b> &nbsp;·&nbsp; strategy <b>${cur.strategy}</b>
      </div>
      <div class="prompt">${prompt.replace(/</g,'&lt;')||'(prompt file not found)'}</div>
    </div>
    <div class="card">
      <h3>1 · What letter does it look like?</h3>
      <div class="seg" id="recog">
        ${['A','B','C','O','V'].map(l=>`<button data-v="${l}">${l}</button>`).join('')}
        <button class="none" data-v="none">none</button>
      </div>
      <div id="reveal" class="reveal" style="display:none">
        Target letter: <b id="tgt"></b> — now score how close it is to this target.
      </div>
      <div id="scores" class="locked">
        <h3>2 · Handshape vs target (1 poor – 5 correct)</h3>
        <div class="seg" data-k="handshape_score">${[1,2,3,4,5].map(n=>`<button data-v="${n}">${n}</button>`).join('')}</div>
        <h3>3 · Sign quality / naturalness</h3>
        <div class="seg" data-k="sign_quality">${[1,2,3,4,5].map(n=>`<button data-v="${n}">${n}</button>`).join('')}</div>
        <h3>4 · Prompt following</h3>
        <div class="seg" data-k="prompt_following">${[1,2,3,4,5].map(n=>`<button data-v="${n}">${n}</button>`).join('')}</div>
        <h3>Notes (optional)</h3>
        <textarea id="notes" placeholder="artifacts, extra fingers, confusable with…"></textarea>
      </div>
      <div class="row">
        <button class="back" onclick="go(-1)">← Back</button>
        <button class="skip" onclick="go(1)">Skip</button>
        <button class="next" onclick="saveNext()">Save & Next →</button>
      </div>
      <div class="row">
        <button class="exit" onclick="saveExit()">⏸ Save & Exit (resume later)</button>
      </div>
      <div class="hint">
        <kbd>A B C O V</kbd> or <kbd>N</kbd> = recognise &nbsp;·&nbsp;
        <kbd>1–5</kbd> = current score &nbsp;·&nbsp;
        <kbd>Enter</kbd> = save+next &nbsp;·&nbsp; <kbd>←</kbd> back<br>
        Watch first, pick the letter from your own perception, then score against the revealed target.
      </div>
    </div>
  </div>`;
  // restore previous
  state={...blank(),...ex};
  applyState();
  bind();
}

let state=blank();
function applyState(){
  document.querySelectorAll('#recog button').forEach(b=>b.classList.toggle('on',b.dataset.v===state.recognized_as));
  if(state.recognized_as){showScores();}
  ['handshape_score','sign_quality','prompt_following'].forEach(k=>{
    document.querySelectorAll(`[data-k="${k}"] button`).forEach(b=>b.classList.toggle('on',b.dataset.v==state[k]));
  });
  if($('#notes'))$('#notes').value=state.notes||'';
}
function showScores(){
  $('#reveal').style.display='block';
  $('#tgt').textContent=cur.letter;
  $('#scores').classList.remove('locked');
}
function bind(){
  document.querySelectorAll('#recog button').forEach(b=>b.onclick=()=>{
    state.recognized_as=b.dataset.v;
    document.querySelectorAll('#recog button').forEach(x=>x.classList.remove('on'));
    b.classList.add('on'); showScores();
  });
  ['handshape_score','sign_quality','prompt_following'].forEach(k=>{
    document.querySelectorAll(`[data-k="${k}"] button`).forEach(b=>b.onclick=()=>{
      state[k]=b.dataset.v;
      document.querySelectorAll(`[data-k="${k}"] button`).forEach(x=>x.classList.remove('on'));
      b.classList.add('on');
    });
  });
  $('#notes').oninput=e=>state.notes=e.target.value;
}

async function saveNext(){
  if(!state.recognized_as){alert('Pick what letter it looks like first.');return;}
  const rec={
    video_id:cur.video_id,generator:cur.generator,vlm:cur.vlm,strategy:cur.strategy,
    target_letter:cur.letter,recognized_as:state.recognized_as,
    recog_correct:(state.recognized_as===cur.letter)?1:0,
    handshape_score:state.handshape_score,sign_quality:state.sign_quality,
    prompt_following:state.prompt_following,notes:state.notes
  };
  await fetch('/api/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(rec)});
  R[cur.video_id]=rec; idx++; render();
}
function go(d){idx=Math.max(0,Math.min(M.length,idx+d));if(idx<M.length)render();else doneScreen();}

async function saveExit(){
  // save the current row too if it's been rated
  if(state.recognized_as){
    const rec={
      video_id:cur.video_id,generator:cur.generator,vlm:cur.vlm,strategy:cur.strategy,
      target_letter:cur.letter,recognized_as:state.recognized_as,
      recog_correct:(state.recognized_as===cur.letter)?1:0,
      handshape_score:state.handshape_score,sign_quality:state.sign_quality,
      prompt_following:state.prompt_following,notes:state.notes
    };
    await fetch('/api/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(rec)});
    R[cur.video_id]=rec;
  }
  await fetch('/api/exit',{method:'POST'});
  document.body.innerHTML=`<div class="done-screen"><h2>⏸ Saved — ${Object.keys(R).length} / ${M.length} done</h2>
  <p>Progress is in <b>Track2_Human_Evaluation_-_Ratings.csv</b>.<br>
  The server has stopped. To continue later, run <kbd>python rate.py</kbd> again — it resumes from here.<br>
  You can close this tab.</p></div>`;
}
function doneScreen(){
  $('#app').innerHTML=`<div class="done-screen"><h2>✓ All ${Object.keys(R).length} ratings saved</h2>
  <p>ratings.csv is in your FYP ASL folder. Upload it back to finish Track 2.</p>
  <button class="next" style="max-width:200px" onclick="idx=0;render()">Review from start</button></div>`;
  setProg();
}
document.addEventListener('keydown',e=>{
  if(e.target.tagName==='TEXTAREA')return;
  const k=e.key.toUpperCase();
  if(['A','B','C','O','V','N'].includes(k)){
    const v=k==='N'?'none':k;
    const b=document.querySelector(`#recog button[data-v="${v}"]`); if(b)b.click();
  }else if('12345'.includes(e.key)){
    // apply to first not-yet-scored scale, else handshape
    for(const key of ['handshape_score','sign_quality','prompt_following']){
      if(!state[key]){document.querySelector(`[data-k="${key}"] button[data-v="${e.key}"]`).click();return;}
    }
    document.querySelector(`[data-k="handshape_score"] button[data-v="${e.key}"]`).click();
  }else if(e.key==='Enter'){saveNext();}
  else if(e.key==='ArrowLeft'){go(-1);}
});
boot();
</script></body></html>"""


class H(http.server.BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="text/html; charset=utf-8"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        if u.path == "/":
            return self._send(200, PAGE)
        if u.path == "/api/manifest":
            return self._send(200, json.dumps(MANIFEST), "application/json")
        if u.path == "/api/done":
            return self._send(200, json.dumps(load_done()), "application/json")
        if u.path == "/api/prompt":
            rel = q.get("p", [""])[0]
            fp = os.path.join(ROOT, *rel.split("/"))
            if os.path.exists(fp):
                return self._send(200, open(fp, encoding="utf-8", errors="replace").read(),
                                  "text/plain; charset=utf-8")
            return self._send(200, "", "text/plain; charset=utf-8")
        if u.path == "/file":
            return self.serve_video(q.get("p", [""])[0])
        return self._send(404, "not found")

    def serve_video(self, rel):
        fp = os.path.join(ROOT, *rel.split("/"))
        if not os.path.exists(fp):
            return self._send(404, "missing: " + rel)
        size = os.path.getsize(fp)
        ctype = mimetypes.guess_type(fp)[0] or "video/mp4"
        rng = self.headers.get("Range")
        if rng:
            m = re.match(r"bytes=(\d+)-(\d*)", rng)
            start = int(m.group(1)); end = int(m.group(2)) if m.group(2) else size - 1
            end = min(end, size - 1); length = end - start + 1
            self.send_response(206)
            self.send_header("Content-Type", ctype)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.send_header("Content-Length", str(length))
            self.end_headers()
            with open(fp, "rb") as f:
                f.seek(start); self.wfile.write(f.read(length))
        else:
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(size))
            self.end_headers()
            with open(fp, "rb") as f:
                self.wfile.write(f.read())

    def do_POST(self):
        if self.path == "/api/save":
            n = int(self.headers.get("Content-Length", 0))
            rec = json.loads(self.rfile.read(n))
            save_rating(rec)
            return self._send(200, json.dumps({"ok": True}), "application/json")
        if self.path == "/api/exit":
            self._send(200, json.dumps({"ok": True}), "application/json")
            import threading
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return
        return self._send(404, "not found")

    def log_message(self, *a):
        pass


def main():
    miss = [m for m in MANIFEST if not os.path.exists(os.path.join(ROOT, *m["video"].split("/")))]
    print(f"Loaded {len(MANIFEST)} videos. Missing files: {len(miss)}")
    if miss:
        print("  First few missing:", [m["video"] for m in miss[:3]])
        print("  -> Make sure rate.py is inside the 'FYP ASL' folder.")
    done = load_done()
    print(f"Already rated: {len(done)}  (resuming where you left off)")
    print(f"\n  Open  http://localhost:{PORT}  in your browser")
    print(f"  Stop anytime: click 'Save & Exit' in the page, or press Ctrl+C here.\n")
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer(("", PORT), H) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nStopped. Progress saved in ratings.csv")


if __name__ == "__main__":
    main()
