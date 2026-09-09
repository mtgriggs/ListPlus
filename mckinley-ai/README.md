# McKinley AI

A private, local-first culling and editing assistant trained on McKinley
Griggs' own wedding photography decisions.

> The goal is not "AI that knows what a good photograph is."
> It is "AI that has watched McKinley shoot weddings and knows which
> photographs he delivers."

**Current stage: Phase 0 — data audit and feasibility.** No model has been
built, and none should be until the numbers in the audit say it is worth it.

---

## Start here

0. **[Quickstart](docs/03-quickstart.md)** — copy-paste commands for running the
   audit on the archive drives. Start here if you just want to run it.
1. **[Feasibility study](docs/00-feasibility-study.md)** — what is realistic,
   what is not, what the data can and cannot support, hardware, cost, licensing,
   and four explicit kill criteria. Written to be argued with.
2. **[Data audit runbook](docs/01-data-audit-runbook.md)** — step-by-step
   procedure for auditing one wedding, then the whole archive.
3. **[The two automators](docs/02-automators.md)** — the culling intake watcher
   and the editorial submission checker, and why they are different problems.

The three things worth knowing before reading either:

- **The training label is the delivered gallery**, not star ratings and not
  anything from Aftershoot. It already exists for every wedding you have shot,
  and it removes most of the licensing question along the way.
- **The "where I disagreed with the AI" data is not recoverable retroactively.**
  An XMP sidecar holds one state; your correction overwrote the suggestion. It
  is cheap to capture going forward, and that is the one thing worth starting
  immediately.
- **Your own self-consistency is the ceiling.** Measure it before building
  anything. Runbook §6.

---

## The toolkit

Read-only. Nothing here modifies, moves, or rewrites a photograph or a sidecar.
Python 3.10+, no dependencies. `exiftool` is used when present and is
recommended, not required.

```bash
cd audit

# See what an archive drive actually contains, and whether it will be matched
python3 -m mck discover --archive "/Volumes/The Beast"

# Audit one wedding
python3 -m mck scan --raw /path/RAW --delivered /path/Delivered --out ./out

# Audit an entire archive, incrementally, unattended
python3 -m mck auto --archive /Volumes/Archive --out ./out [--watch]

# Capture the AI-vs-you disagreement set (do this on your next wedding)
python3 -m mck snapshot --raw /path/RAW --out ./snapshots --tag post-ai
python3 -m mck snapshot --raw /path/RAW --out ./snapshots --tag post-review
python3 -m mck diff --before ./snapshots/...post-ai \
                    --after  ./snapshots/...post-review \
                    --out    ./disagreements.jsonl

# Read decisions out of a Lightroom catalog (close Lightroom first)
python3 -m mck catalog --lrcat /path/Catalog.lrcat
```

### The two automators

```bash
# Culling: watch an intake folder, pre-cull each wedding as it lands.
# Snapshots sidecars before proposing. Writes nothing without --write.
python3 -m mck cull --intake /Volumes/Intake --out ./cull-out --watch

# Editorial: prepare and track publication submissions.
python3 -m mck editorial profiles
python3 -m mck editorial init  --wedding 2025-06-14-smith
python3 -m mck editorial check --wedding 2025-06-14-smith \
    --delivered /path/Delivered --publication style-me-pretty
python3 -m mck editorial submit --wedding 2025-06-14-smith --publication style-me-pretty
```

The cull automator is workflow scaffolding waiting on the Phase 2 model — its
current scorer is a documented baseline, not a taste model. Adopt it anyway for
the snapshot step. The editorial automator is useful today, because what gets
submissions rejected is exclusivity, resolution and vendor credits, none of
which need a model. See [docs/02-automators.md](docs/02-automators.md).

### What the audit measures

| Output | Question it answers |
| --- | --- |
| Delivered-set join rate | Is there a usable training label? |
| Keep rate, and its spread across years | Is the target stable, or has your style drifted? |
| Star-rating AUC | Do your ratings predict what you actually delivered? |
| Near-dupe share of drops | How much of your cull is genuinely personal? |
| Within-burst preference pairs | The real dataset size — much larger than the frame count |
| Sidecar write sessions | Does any correction history survive? |
| Develop fingerprint entropy | Is there per-image style, or one batch preset? |
| Sidecar writer attribution | Which edits are yours vs a tool's — needed before Phase 4 |

Every wedding produces `AUDIT_REPORT.md` (read this), `inventory.csv` (the seed
of the training set) and `audit.json`. The archive run adds `ROLLUP.md`.

---

## Layout

```
mckinley-ai/
├── docs/
│   ├── 00-feasibility-study.md      the challenge document
│   └── 01-data-audit-runbook.md     how to run the audit
└── audit/
    ├── mck/
    │   ├── xmp.py         XMP sidecar parsing, writer attribution
    │   ├── exif.py        capture time + camera metadata (exiftool or stdlib)
    │   ├── bursts.py      burst / scene segmentation, preference pairs
    │   ├── scan.py        folder walk and the source -> delivered join
    │   ├── stats.py       AUC, entropy, contingency tables
    │   ├── report.py      analysis, findings, report rendering
    │   ├── snapshot.py    sidecar snapshots and disagreement diffs
    │   ├── catalog.py     Lightroom .lrcat introspection
    │   ├── automate.py    archive-wide audit automator, ledger, roll-up
    │   ├── cullwatch.py   culling automator: intake watcher and pre-cull
    │   ├── editorial.py   editorial automator: specs, coverage, exclusivity
    │   ├── xmpwrite.py    safe, backup-first sidecar writing
    │   └── cli.py         command-line interface
    └── tests/
        ├── make_fixtures.py   synthetic wedding generator
        └── test_audit.py      34 tests
```

```bash
cd audit && python3 tests/test_audit.py
```

Fixtures build structurally valid TIFF and JPEG/APP1 files with real EXIF, and
real XMP sidecars, so the parsers are genuinely exercised. They contain no image
data — nothing in this toolkit decodes pixels.

**Verified against synthetic fixtures only.** It has not yet run on a real
archive; expect the first real run to surface format quirks, particularly around
raw formats and export naming conventions.

---

## Roadmap

| Phase | Status |
| --- | --- |
| **0 — Data audit** | ✅ Toolkit built and tested. Awaiting a run on real data. |
| 1 — Dataset creation | Blocked on Phase 0 numbers |
| 2 — Local culling prototype | Blocked on Phase 1 |
| 3 — Human feedback loop | Design settled: frozen backbone + cached embeddings makes retraining near-instant |
| 4 — Personalized editing | **Rescoped** — see feasibility study §6 |
| 5 — Review interface | Not started |
| 6 — Production | Not started |
