# Data Audit Runbook

How to audit one wedding and decide whether McKinley AI is worth building.

**Time:** ~30 minutes for the audit, ~90 minutes for the self-consistency test.
**Risk:** none. Every command here is read-only with respect to your photographs
and sidecars. The only files created go to an output directory you name.

---

## 0. Before you start

**Install exiftool.** Not required, but it takes capture-time coverage from
"most raw formats" to "all of them", and capture time drives burst detection,
timeline segmentation, and the matching of renamed exports.

```bash
# macOS
brew install exiftool
# Windows
winget install OliverBetz.ExifTool
# Debian/Ubuntu
sudo apt install libimage-exiftool-perl
```

Python 3.10+ is the only other requirement. There are no package dependencies.

```bash
cd mckinley-ai/audit
python3 -m mck --help
```

**Pick a representative wedding.** Not your best one and not your hardest. You
want a typical full day, ideally recent, where you still have both the raws and
the delivered gallery. Two shooters is better than one — it exercises the
per-body burst logic.

---

## 1. Write your sidecars out first

If your cull decisions live in Lightroom and were never written to disk, the
audit will see an archive with no ratings. Fix that before scanning:

1. Open the wedding's catalog in Lightroom Classic.
2. *Edit → Catalog Settings → Metadata* → tick **Automatically write changes
   into XMP**.
3. Select all (`Ctrl/Cmd+A`), then `Ctrl/Cmd+S` to write metadata to files.

This writes ratings, labels and develop settings into `.xmp` sidecars beside
each raw. It does not modify the raws themselves.

> If you would rather not touch the catalog at all, skip this and run `mck
> catalog` (§5) instead — it reads decisions straight out of the `.lrcat`
> without writing anything.

---

## 2. Audit the wedding

```bash
python3 -m mck scan \
  --raw       "/Volumes/Archive/2025-06-14 Smith Wedding/RAW" \
  --delivered "/Volumes/Archive/2025-06-14 Smith Wedding/Delivered" \
  --out       "./audit-out/smith-2025" \
  --title     "Smith Wedding 2025"
```

`--raw` and `--delivered` can each be repeated if the folders are split across
cards, shooters, or drives:

```bash
  --raw "/.../RAW/Shooter-A" --raw "/.../RAW/Shooter-B"
```

**Point `--delivered` at the final client gallery** — the JPEGs that actually
shipped. That is the ground truth. If you only have a highlights subset, say so
when reading the results; the keep rate will read low and the report will warn
you.

Three files land in the output directory:

| File | What it is |
| --- | --- |
| `AUDIT_REPORT.md` | Read this. Verdict, findings, all the tables. |
| `inventory.csv` | One row per frame — the seed of your training set. |
| `audit.json` | The same analysis, machine-readable. |

---

## 3. Reading the report

Work down the findings in this order. The first three decide viability; the rest
shape what you build.

### The delivered-set join

> ✅ *100% of delivered files joined to a source frame — 812 by filename, 340 by
> capture timestamp*

This is the label. Two matching strategies run: filename stem first, then
capture timestamp for exports that were renamed on the way out (`Smith-0123.jpg`).

**If the unmatched count is high**, the usual causes are: the delivered folder
is a highlights subset rather than the full gallery; exports were stripped of
EXIF; or the delivered files came from a different shoot. Check the
`unmatched_examples` list in the report before concluding anything.

### Keep rate

Expect **10–30%**. Outside that range, something is off with the folders rather
than with your culling — see the report's warning text.

### Capture-time coverage

Should be ~100%. If it is not, install exiftool and re-run.

### Star rating AUC

How well your stars predict what you actually delivered. 0.5 is noise, 1.0 is
perfect. See feasibility study §3.1 for how to act on each range. **A low number
here is not a problem** — it just means the delivered set is doing the work, as
it should.

### Timeline — when you shot, and what survived

Built entirely from capture timestamps, with no manual labelling:

- **Scenes** — the day cut at gaps of more than 7 minutes, which approximates
  the phases (getting ready, ceremony, portraits, reception). Each row gives the
  clock start, duration, how many bodies were shooting, frames, delivered, keep
  rate, flash share and median ISO. Scenes merge both shooters, because a phase
  of the day is shared; bursts stay per-body, because two photographers firing at
  the same instant are not alternatives to each other.
- **By hour of day** — frames and keep rate per hour, with a bar for shot volume.

What to look for: **does your keep rate move with the phase?** If the ceremony
runs at 20% and the reception at 8%, a model that ignores context will
systematically over-deliver one and under-deliver the other. That is what
`scene_keep_rate_spread` measures, and a large value means scene features are
not optional.

Flash share and median ISO per scene are there to confirm the segmentation found
real phases rather than arbitrary gaps — a flash-lit, ISO 3200 block at 19:00 is
a reception whatever you call it.

### Burst structure

The two numbers that matter:

- **`share_of_drops_that_are_near_dupes`** — how much of your culling is
  choosing between near-identical frames rather than rejecting outright. This is
  the personal part of the problem. Under 15%, see kill criterion 2.
- **`preference_pairs`** — training examples this wedding contributes to the
  ranker. Multiply by your wedding count for the real dataset size.

### Sidecar provenance and write sessions

`xmp_writer` attributes each sidecar to Aftershoot, Lightroom, Camera Raw, or
unknown, based on the software agents recorded inside it. You will need this
filter before any editing work (feasibility study §8).

`write_session_count` clusters sidecar modification times. **One cluster means
no correction history survives** — expected, and the reason for §4.

### Develop-setting fingerprints

`top_fingerprint_share` above ~80% means one preset was batch-applied and there
is no per-image styling to learn from this wedding. High entropy means genuine
per-image decisions.

---

## 4. Start capturing disagreements — do this on your next wedding

The archive cannot tell you where you overrode the AI (feasibility study §3.3).
Going forward it can, for two commands per wedding.

**Immediately after the automated cull, before you review anything:**

```bash
python3 -m mck snapshot --raw "/.../RAW" --out ./snapshots/smith --tag post-ai
```

**After you finish correcting:**

```bash
python3 -m mck snapshot --raw "/.../RAW" --out ./snapshots/smith --tag post-review

python3 -m mck diff \
  --before ./snapshots/smith/20260614T101500_post-ai \
  --after  ./snapshots/smith/20260614T164500_post-review \
  --out    ./disagreements/smith.jsonl
```

Output:

```
  post-ai -> post-review
  unchanged   3,612
  changed       288 (7.4%)
  promoted      104
  demoted       184
```

Those 288 rows are worth more per example than the 3,612 agreements. Each says:
*here is a frame the machine judged one way and McKinley judged another.*

**Adopt this before you build anything else.** It is the only part of the project
where waiting has a permanent cost.

---

## 5. Check the Lightroom catalog

Especially worth running if the sidecar audit came back thin, or if you ever
culled with pick/reject flags rather than stars.

```bash
# Close Lightroom first.
python3 -m mck catalog --lrcat "/Users/mckinley/Lightroom/Smith-2025.lrcat"
```

The catalog is copied before opening and opened read-only. Because Adobe's
schema is undocumented and changes between versions, the tool introspects what
is present rather than assuming — an absent table is reported, not an error.

What to look for:

- **Pick/reject flags.** Catalog-only state. If flagged counts are high, that is
  a decision record the sidecars do not have.
- **Develop history steps.** Timestamped edit steps — the closest thing to a
  retroactive record of what you changed after an automated pass. Worth
  investigating if you want *any* historical disagreement data.
- **Develop settings.** Present, but serialized as a Lua table rather than XML.
  Get slider values from sidecars (§1) instead; it is not worth reverse
  engineering the binary format.

---

## 6. The self-consistency test — do not skip this

This measures your own ceiling. See feasibility study §2 for why it decides the
project.

1. Pick a wedding you culled **at least six months ago**. Old enough that you do
   not remember the frames.

2. Pull a sample from the inventory: about 300 frames, drawn from decisive
   bursts (where you originally kept some and dropped others), so you are testing
   the hard decisions rather than the obvious ones.

   ```bash
   python3 - <<'PY'
   import csv, random, shutil
   from pathlib import Path
   rows = list(csv.DictReader(open('audit-out/smith-2025/inventory.csv')))
   by_burst = {}
   for r in rows:
       if r['burst_id']:
           by_burst.setdefault(r['burst_id'], []).append(r)
   decisive = [v for v in by_burst.values()
               if 0 < sum(x['delivered'] == 'True' for x in v) < len(v)]
   random.seed(0); random.shuffle(decisive)
   out = Path('selftest'); out.mkdir(exist_ok=True)
   picked, n = [], 0
   for burst in decisive:
       if n >= 300: break
       picked.append(burst); n += len(burst)
   # Copy previews out under neutral names so no rating or filename leaks.
   with open(out/'key.csv', 'w', newline='') as fh:
       wtr = csv.writer(fh); wtr.writerow(['blind_id','burst','path','was_delivered'])
       for bi, burst in enumerate(picked):
           for fi, r in enumerate(burst):
               wtr.writerow([f'{bi:03d}_{fi:02d}', bi, r['path'], r['delivered']])
   print(f'{n} frames across {len(picked)} bursts -> selftest/key.csv')
   PY
   ```

3. **Re-cull them blind.** Import into a fresh Lightroom catalog with ratings
   stripped, grouped by burst, filenames not visible. For each burst, pick the
   frame you would deliver.

4. **Compare against `was_delivered`.** Your agreement rate on burst choices is
   the ceiling for the entire project.

Report two numbers separately:

- **Agreement on outright rejects** (frames you dropped then and drop now).
  Expect >95%. If it is low, your technical standards are drifting, not your taste.
- **Agreement on burst winners.** This is the number that matters. Under ~65%,
  re-read kill criterion 1 before going further.

---

## 7. Run the automator across the archive

Once one wedding audits cleanly, do all of them. Unattended.

```bash
python3 -m mck auto --archive "/Volumes/Archive" --out ./audit-out
```

The automator looks for wedding folders one level under the archive root, each
containing a recognisable raw folder (`RAW`, `Originals`, `Source`, …) and
delivered folder (`Delivered`, `Final`, `Gallery`, `Exports`, …). If your naming
differs:

```bash
  --raw-names "raw,originals,captures" \
  --delivered-names "delivered,client-gallery,final-jpegs"
```

It keeps a ledger, so re-running only processes what changed — safe to run
repeatedly, and safe to interrupt. One wedding failing does not stop the run.

To keep it running and absorb new weddings as you finish them:

```bash
python3 -m mck auto --archive "/Volumes/Archive" --out ./audit-out --watch
```

Read `ROLLUP.md`. It answers the questions one wedding cannot:

- **Total preference pairs** across the archive — the real dataset size.
- **Keep rate by year** — has your selectivity drifted? A large spread means
  older weddings need down-weighting, or excluding.
- **Excluded weddings** — which ones failed the audit and why. Filter these out
  before training rather than letting them poison the set.

---

## 8. What to bring back

After steps 2, 6 and 7 you will have:

| Number | From | Decides |
| --- | --- | --- |
| Delivered-join rate | `AUDIT_REPORT.md` | Whether a label exists at all |
| Keep rate + spread across years | `ROLLUP.md` | Whether the target is stable |
| Star rating AUC | `AUDIT_REPORT.md` | Whether ratings add data beyond the gallery |
| Near-dupe share of drops | `AUDIT_REPORT.md` | Whether the problem is personal enough to be worth it |
| Total preference pairs | `ROLLUP.md` | Dataset size for the ranker |
| **Self-consistency on burst winners** | §6 | **The ceiling for the whole project** |

Those six numbers decide the architecture, and they decide whether to build at
all. Everything in the feasibility study before them is estimate; these are
measurement.
