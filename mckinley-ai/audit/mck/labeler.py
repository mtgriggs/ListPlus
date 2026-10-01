"""Burst labelling: generating the preference data the archive never recorded.

The archive's stars are overwhelmingly Aftershoot's judgments that McKinley
accepted. The one place he consistently disagrees is **which frame wins among
near-duplicates**, and a sidecar holds only the final state, so those overrides
are indistinguishable today from the ones he simply accepted.

This regenerates that signal directly. It pulls burst groups out of a wedding,
shows the frames side by side, and records which one he picks. Two outputs from
one sitting:

* **Preference pairs.** "Under near-identical conditions, McKinley preferred A
  over B." The densest and most personal training signal available, and the
  only one in this project not contaminated by the vendor's model.
* **The self-consistency number.** Agreement between his pick now and the pick
  recorded at cull time is the ceiling on what any model can achieve. It has
  been the outstanding Phase 0 measurement since the beginning, and it falls out
  of this for free.

The original pick is hidden until after he chooses. Showing it first would
anchor the decision and make the agreement figure meaningless.

Runs as a local web app because a browser is the only thing on the machine that
can lay out images side by side and take a keystroke. It binds to localhost,
serves previews by index rather than by path so a crafted request cannot reach
arbitrary files, and appends every decision to disk immediately so a closed
laptop costs nothing.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .exif import RAW_EXTS
from .preview import extract_preview
from .scan import ImageRecord, scan_wedding

KEEP_STAR = 4
DUPLICATE_STAR = 3


@dataclass
class Frame:
    index: int
    stem: str
    path: Path
    rating: int | None
    capture_time: str | None


@dataclass
class BurstTask:
    burst_id: int
    frames: list[Frame]
    original_winner: str | None      # stem rated highest at cull time
    scene_id: int | None = None

    def as_json(self) -> dict:
        return {
            "burst_id": self.burst_id,
            "scene_id": self.scene_id,
            "frames": [
                {"index": f.index, "stem": f.stem, "capture_time": f.capture_time}
                for f in self.frames
            ],
            # original_winner is deliberately omitted; it is revealed only in
            # the response to a choice, so the decision stays unanchored.
        }


def build_tasks(
    records: list[ImageRecord],
    mode: str = "decisive",
    min_size: int = 2,
    max_size: int = 12,
) -> list[BurstTask]:
    """Group frames into the bursts worth asking about.

    ``decisive`` keeps only bursts that already contain a recorded decision —
    a 4 or 5 alongside a 3 — because those are the ones where agreement can be
    measured. ``all`` keeps every multi-frame burst, which is the right mode
    once the self-consistency figure is in hand and the goal is pair volume.
    """
    by_burst: dict[int, list[ImageRecord]] = {}
    for r in records:
        if r.burst_id is not None:
            by_burst.setdefault(r.burst_id, []).append(r)

    tasks: list[BurstTask] = []
    index = 0
    for burst_id in sorted(by_burst):
        members = sorted(
            by_burst[burst_id], key=lambda r: r.exif.capture_epoch or 0.0
        )
        if not min_size <= len(members) <= max_size:
            continue

        winners = [m for m in members if (m.rating or 0) >= KEEP_STAR]
        losers = [m for m in members if m.rating == DUPLICATE_STAR]
        if mode == "decisive" and not (winners and losers):
            continue

        frames = []
        for m in members:
            frames.append(Frame(index=index, stem=m.stem, path=m.path,
                                rating=m.rating, capture_time=m.exif.capture_time))
            index += 1

        best = max(members, key=lambda m: (m.rating or 0))
        tasks.append(BurstTask(
            burst_id=burst_id,
            frames=frames,
            original_winner=best.stem if (best.rating or 0) >= KEEP_STAR else None,
            scene_id=members[0].scene_id,
        ))
    return tasks


def find_wedding_folders(archive: Path, max_depth: int = 2) -> list[dict]:
    """Immediate subfolders of the archive that contain raw files.

    Deliberately ignores folder naming. Every other part of this toolkit has to
    guess whether a folder is called RAW or Originals or CR3 Files; for
    labelling the only question that matters is whether raws are in there, and
    that can simply be checked.
    """
    found: list[dict] = []
    if not archive.exists():
        return found

    def count_raws(root: Path, depth: int) -> tuple[int, list[Path]]:
        total = 0
        holders: list[Path] = []
        if depth < 0:
            return total, holders
        try:
            entries = list(root.iterdir())
        except OSError:
            return total, holders
        here = sum(1 for e in entries
                   if e.is_file() and e.suffix.lower() in RAW_EXTS
                   and not e.name.startswith("."))
        if here:
            total += here
            holders.append(root)
        for entry in entries:
            if entry.is_dir() and not entry.name.startswith("."):
                sub_total, sub_holders = count_raws(entry, depth - 1)
                total += sub_total
                holders.extend(sub_holders)
        return total, holders

    try:
        children = [c for c in sorted(archive.iterdir())
                    if c.is_dir() and not c.name.startswith(".")]
    except OSError:
        return found

    for child in children:
        total, holders = count_raws(child, max_depth)
        if total:
            found.append({"name": child.name, "root": child,
                          "raw_count": total, "raw_folders": holders})
    return found


def survey_labelling(
    weddings: list[dict],
    mode: str = "decisive",
    burst_gap: float = 2.0,
    prefer_exiftool: bool = True,
    log=print,
) -> dict:
    """Count the decisions waiting across an archive, without labelling anything.

    Answers the question that decides how much of a job this is, and how large
    the eventual dataset can get, before committing an afternoon to it.
    """
    rows = []
    for wedding in weddings:
        try:
            tasks, _ = prepare(
                raw_roots=[Path(p) for p in wedding["raw_folders"]],
                out_path=Path("/dev/null"),
                mode=mode,
                burst_gap=burst_gap,
                prefer_exiftool=prefer_exiftool,
            )
        except Exception as exc:  # noqa: BLE001 - a bad wedding must not stop the survey
            rows.append({"name": wedding["name"], "error": str(exc),
                         "bursts": 0, "pairs": 0, "frames": wedding["raw_count"]})
            log(f"  {wedding['name']}: failed ({exc})")
            continue
        pairs = sum(max(0, len(t.frames) - 1) for t in tasks)
        rows.append({
            "name": wedding["name"],
            "frames": wedding["raw_count"],
            "bursts": len(tasks),
            "pairs": pairs,
            "with_recorded_pick": sum(1 for t in tasks if t.original_winner),
        })
        log(f"  {wedding['name']}: {len(tasks):,} bursts, {pairs:,} pairs")

    return {
        "mode": mode,
        "weddings": rows,
        "total_weddings": len(rows),
        "total_frames": sum(r["frames"] for r in rows),
        "total_bursts": sum(r["bursts"] for r in rows),
        "total_pairs": sum(r["pairs"] for r in rows),
        "total_with_recorded_pick": sum(r.get("with_recorded_pick", 0) for r in rows),
    }


def render_survey_labelling(s: dict) -> str:
    lines: list[str] = []
    w = lines.append
    w("# Labelling survey")
    w("")
    w(f"Mode: `{s['mode']}`")
    w("")
    w("| Metric | Value |")
    w("| --- | --- |")
    w(f"| Weddings with raws | {s['total_weddings']:,} |")
    w(f"| Source frames | {s['total_frames']:,} |")
    w(f"| Bursts to decide | {s['total_bursts']:,} |")
    w(f"| With a recorded pick | {s['total_with_recorded_pick']:,} |")
    w(f"| **Preference pairs available** | **{s['total_pairs']:,}** |")
    w("")
    if s["total_bursts"]:
        hours = s["total_bursts"] / 250.0
        w(f"At roughly 250 bursts an hour, labelling everything is about "
          f"**{hours:.1f} hours**. You do not need all of it: one wedding gives the "
          "self-consistency figure, and a handful gives a usable first dataset.")
        w("")
    w("| Wedding | Frames | Bursts | Pairs |")
    w("| --- | --- | --- | --- |")
    for r in sorted(s["weddings"], key=lambda r: -r["bursts"])[:40]:
        if r.get("error"):
            w(f"| {r['name']} | {r['frames']:,} | failed | {r['error'][:40]} |")
        else:
            w(f"| {r['name']} | {r['frames']:,} | {r['bursts']:,} | {r['pairs']:,} |")
    return "\n".join(lines)


# --- decision log ---------------------------------------------------------


class DecisionLog:
    """Append-only JSONL of choices, resumable across sittings."""

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.done: dict[int, dict] = {}
        if path.exists():
            with path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    self.done[row["burst_id"]] = row

    def record(self, row: dict) -> None:
        self.done[row["burst_id"]] = row
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, default=str) + "\n")

    def stats(self) -> dict:
        rows = [r for r in self.done.values() if not r.get("skipped")]
        comparable = [r for r in rows if r.get("original")]
        agreed = sum(1 for r in comparable if r.get("agreed"))
        pairs = sum(max(0, len(r.get("frames", [])) - 1) for r in rows)
        return {
            "decided": len(rows),
            "skipped": sum(1 for r in self.done.values() if r.get("skipped")),
            "comparable": len(comparable),
            "agreed": agreed,
            "self_consistency": round(agreed / len(comparable), 4) if comparable else None,
            "preference_pairs": pairs,
        }


# --- web app --------------------------------------------------------------

PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>Burst labelling</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { margin:0; background:#0d0f12; color:#e8ebef;
         font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }
  header { display:flex; align-items:center; gap:24px; padding:12px 20px;
           border-bottom:1px solid #232830; position:sticky; top:0; background:#0d0f12; z-index:5;}
  h1 { font-size:15px; font-weight:600; margin:0; letter-spacing:-0.01em; }
  .stat { font:12px ui-monospace,Menlo,monospace; color:#8b95a3; }
  .stat b { color:#e8ebef; font-weight:500; }
  .bar { flex:1; height:4px; background:#232830; border-radius:2px; overflow:hidden; }
  .bar div { height:100%; background:#5aa9d6; width:0; transition:width .2s; }
  main { padding:20px; }
  .grid { display:flex; gap:12px; flex-wrap:wrap; align-items:flex-start; }
  .card { position:relative; border:2px solid #232830; border-radius:4px;
          overflow:hidden; cursor:pointer; background:#151a20; flex:1 1 320px;
          max-width:560px; transition:border-color .12s; }
  .card:hover { border-color:#5aa9d6; }
  .card.chosen { border-color:#6fbf8f; }
  .card.was-original { border-color:#d8a75c; }
  .card img { display:block; width:100%; height:auto; }
  .card .meta { display:flex; justify-content:space-between; gap:8px;
                padding:7px 10px; font:12px ui-monospace,Menlo,monospace; color:#8b95a3; }
  .key { position:absolute; top:8px; left:8px; background:#0d0f12cc; color:#e8ebef;
         border-radius:3px; padding:2px 8px; font:12px ui-monospace,Menlo,monospace; }
  .tag { position:absolute; top:8px; right:8px; border-radius:3px; padding:2px 8px;
         font:11px ui-monospace,Menlo,monospace; display:none; }
  .card.chosen .tag.you { display:block; background:#6fbf8f; color:#0d0f12; }
  .card.was-original .tag.orig { display:block; background:#d8a75c; color:#0d0f12; }
  footer { padding:14px 20px 40px; color:#8b95a3; font-size:13px; }
  button { background:#1d242d; color:#e8ebef; border:1px solid #2f3742;
           border-radius:4px; padding:7px 14px; font-size:13px; cursor:pointer; }
  button:hover { background:#252d38; }
  .verdict { margin:16px 0 0; font-size:14px; min-height:22px; }
  .agree { color:#6fbf8f; } .differ { color:#d8a75c; }
  .done { padding:60px 20px; text-align:center; }
  .done h2 { font-weight:600; font-size:20px; }
  kbd { background:#1d242d; border:1px solid #2f3742; border-radius:3px;
        padding:1px 6px; font:12px ui-monospace,Menlo,monospace; }
</style></head><body>
<header>
  <h1>Which one would you deliver?</h1>
  <div class="bar"><div id="prog"></div></div>
  <div class="stat"><b id="n">0</b>/<span id="total">0</span> done</div>
  <div class="stat">agreement <b id="agree">&mdash;</b></div>
  <div class="stat">pairs <b id="pairs">0</b></div>
</header>
<main id="main"></main>
<footer>
  <kbd>1</kbd>&ndash;<kbd>9</kbd> pick &nbsp; <kbd>S</kbd> skip &nbsp;
  <kbd>&rarr;</kbd> next. Your original pick is hidden until you choose.
</footer>
<script>
let tasks=[], i=0, locked=false;
const $ = s => document.querySelector(s);

async function boot(){
  const r = await fetch('/api/tasks'); const d = await r.json();
  tasks = d.tasks; $('#total').textContent = d.total;
  updateStats(d.stats); render();
}
function updateStats(s){
  $('#n').textContent = s.decided + s.skipped;
  $('#pairs').textContent = s.preference_pairs.toLocaleString();
  $('#agree').textContent = s.self_consistency === null ? '\\u2014'
      : Math.round(s.self_consistency*100)+'%';
  const total = parseInt($('#total').textContent)||1;
  $('#prog').style.width = Math.min(100,(s.decided+s.skipped)/total*100)+'%';
}
function render(){
  const m = $('#main');
  if(i >= tasks.length){
    m.innerHTML = '<div class="done"><h2>That is the lot.</h2>'
      + '<p>Every burst in this wedding has been decided. The results are saved '
      + 'as you went; nothing further to do here.</p></div>';
    return;
  }
  const t = tasks[i]; locked=false;
  m.innerHTML = '<div class="grid">' + t.frames.map((f,n) =>
    `<div class="card" data-stem="${f.stem}" onclick="choose('${f.stem}')">
       <span class="key">${n+1}</span>
       <span class="tag you">your pick</span>
       <span class="tag orig">picked before</span>
       <img loading="lazy" src="/api/preview?i=${f.index}" alt="">
       <div class="meta"><span>${f.stem}</span><span>${f.capture_time||''}</span></div>
     </div>`).join('') + '</div><p class="verdict" id="verdict"></p>';
}
async function choose(stem){
  if(locked) return; locked=true;
  const t = tasks[i];
  const r = await fetch('/api/choose',{method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({burst_id:t.burst_id, chosen:stem})});
  const d = await r.json();
  document.querySelectorAll('.card').forEach(c=>{
    if(c.dataset.stem === stem) c.classList.add('chosen');
    if(d.original && c.dataset.stem === d.original) c.classList.add('was-original');
  });
  $('#verdict').innerHTML = !d.original
    ? 'No recorded pick for this burst.'
    : (d.agreed ? '<span class="agree">Same as before.</span>'
                : '<span class="differ">Different from before.</span>');
  updateStats(d.stats);
  setTimeout(()=>{ i++; render(); }, d.original && !d.agreed ? 1100 : 450);
}
async function skip(){
  if(locked) return; locked=true;
  const t = tasks[i];
  const r = await fetch('/api/choose',{method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({burst_id:t.burst_id, skipped:true})});
  updateStats((await r.json()).stats); i++; render();
}
document.addEventListener('keydown', e=>{
  if(i >= tasks.length) return;
  if(e.key >= '1' && e.key <= '9'){
    const f = tasks[i].frames[parseInt(e.key)-1];
    if(f) choose(f.stem);
  } else if(e.key.toLowerCase() === 's') skip();
  else if(e.key === 'ArrowRight' && locked){ i++; render(); }
});
boot();
</script></body></html>
"""


class _Handler(BaseHTTPRequestHandler):
    tasks: list[BurstTask] = []
    frames_by_index: dict[int, Frame] = {}
    log: DecisionLog | None = None
    max_edge: int = 1400

    def log_message(self, *args):  # keep the console clean
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload: dict, code: int = 200) -> None:
        self._send(code, json.dumps(payload, default=str).encode("utf-8"),
                   "application/json")

    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
            return

        if parsed.path == "/api/tasks":
            pending = [t for t in type(self).tasks
                       if t.burst_id not in type(self).log.done]
            self._json({
                "tasks": [t.as_json() for t in pending],
                "total": len(type(self).tasks),
                "stats": type(self).log.stats(),
            })
            return

        if parsed.path == "/api/preview":
            qs = parse_qs(parsed.query)
            try:
                idx = int(qs.get("i", ["-1"])[0])
            except ValueError:
                idx = -1
            # Previews are addressed by index into a set built at startup, so a
            # crafted request cannot name a path of its own choosing.
            frame = type(self).frames_by_index.get(idx)
            if frame is None:
                self._json({"error": "unknown frame"}, 404)
                return
            result = extract_preview(frame.path, max_edge=type(self).max_edge)
            if result is None:
                self._json({"error": "no embedded preview"}, 404)
                return
            self._send(200, result[0], "image/jpeg")
            return

        if parsed.path == "/api/stats":
            self._json(type(self).log.stats())
            return

        self._json({"error": "not found"}, 404)

    def do_POST(self):  # noqa: N802
        if urlparse(self.path).path != "/api/choose":
            self._json({"error": "not found"}, 404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            self._json({"error": "bad request"}, 400)
            return

        burst_id = payload.get("burst_id")
        task = next((t for t in type(self).tasks if t.burst_id == burst_id), None)
        if task is None:
            self._json({"error": "unknown burst"}, 404)
            return

        skipped = bool(payload.get("skipped"))
        chosen = payload.get("chosen")
        if not skipped and chosen not in [f.stem for f in task.frames]:
            self._json({"error": "chosen frame is not in this burst"}, 400)
            return

        row = {
            "burst_id": burst_id,
            "scene_id": task.scene_id,
            "frames": [f.stem for f in task.frames],
            "chosen": None if skipped else chosen,
            "original": task.original_winner,
            "agreed": (not skipped and task.original_winner is not None
                       and chosen == task.original_winner),
            "skipped": skipped,
            "decided_at": datetime.now().isoformat(timespec="seconds"),
        }
        type(self).log.record(row)
        self._json({
            "original": task.original_winner,
            "agreed": row["agreed"],
            "stats": type(self).log.stats(),
        })


def serve(
    tasks: list[BurstTask],
    log: DecisionLog,
    port: int = 8765,
    max_edge: int = 1400,
    log_fn=print,
) -> None:
    _Handler.tasks = tasks
    _Handler.frames_by_index = {f.index: f for t in tasks for f in t.frames}
    _Handler.log = log
    _Handler.max_edge = max_edge

    pending = sum(1 for t in tasks if t.burst_id not in log.done)
    server = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    log_fn(f"  {len(tasks):,} bursts, {pending:,} still to decide")
    log_fn(f"  decisions append to {log.path}")
    log_fn("")
    log_fn(f"  Open http://127.0.0.1:{port}/  (Ctrl-C when you are done)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log_fn("")
        log_fn("Stopped.")
    finally:
        server.server_close()


def prepare(
    raw_roots: list[Path],
    out_path: Path,
    mode: str = "decisive",
    burst_gap: float = 2.0,
    prefer_exiftool: bool = True,
    limit: int | None = None,
) -> tuple[list[BurstTask], DecisionLog]:
    """Scan a wedding and build the labelling queue."""
    records, _meta = scan_wedding(
        raw_roots=raw_roots,
        delivered_roots=[],
        burst_gap=burst_gap,
        prefer_exiftool=prefer_exiftool,
        limit=limit,
    )
    tasks = build_tasks(records, mode=mode)
    return tasks, DecisionLog(out_path)


def render_summary(stats: dict) -> str:
    """What the sitting produced, in the terms that matter."""
    lines: list[str] = []
    w = lines.append
    w("# Burst labelling results")
    w("")
    w(f"- Bursts decided: **{stats['decided']:,}** ({stats['skipped']:,} skipped)")
    w(f"- Preference pairs generated: **{stats['preference_pairs']:,}**")
    w("")
    if stats["self_consistency"] is None:
        w("No burst carried a recorded original pick, so self-consistency could "
          "not be measured. Re-run with `--mode decisive` against a wedding that "
          "was culled with stars.")
        return "\n".join(lines)

    sc = stats["self_consistency"]
    w(f"## Self-consistency: {sc:.1%}")
    w("")
    w(f"Of {stats['comparable']:,} bursts with a recorded pick, you chose the same "
      f"frame again {stats['agreed']:,} times.")
    w("")
    if sc < 0.65:
        w("> Under 65%. Your burst choices are closer to arbitrary than to a rule, "
          "which means a model will plateau early and chasing accuracy past this "
          "is chasing your own noise. The right product is 'surface the candidates, "
          "let me pick in one click' rather than 'pick for me'. That is still a "
          "good product, and it is a much smaller build.")
    elif sc < 0.8:
        w("> A normal range for this kind of judgement. It sets a realistic target: "
          "a model matching this rate is at human parity, and there is genuine "
          "signal to learn.")
    else:
        w("> High. Your burst choices follow a consistent rule, which is the best "
          "case for this project: there is a stable target to learn, and the "
          "ceiling is high enough to be worth reaching for.")
    return "\n".join(lines)
