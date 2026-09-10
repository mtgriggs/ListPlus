# Quickstart

Copy-paste steps for running the Phase 0 audit on a Mac, against the archive
drives. Everything here is read-only with respect to photographs and sidecars.

> **The label comes from the Lightroom catalog, not from a delivered folder.**
> The client galleries live in Pic-Time and were never kept on disk, so there is
> no folder of finals to join against. Every `.lrcat` was kept, and the catalog
> records which frames were chosen. Steps 2 and 3 below are that path.

## Install

Python 3.10+ is already on macOS. There are no package dependencies.

```bash
cd ~/Sandbox/"McKinley G Photography"
mkdir -p "R&D Lab" && cd "R&D Lab"

git clone --branch claude/mckinley-ai-photography-6d53ak \
    https://github.com/mtgriggs/ListPlus.git mckinley-ai-repo

cd mckinley-ai-repo/mckinley-ai/audit
python3 -m mck --help
```

If `git` prompts to install the Xcode command line tools, accept, then re-run
the clone. Every later command assumes you are in that `audit` directory.

Optional but recommended, and worth the two minutes. It takes capture-time
coverage from "most raw formats" to "all of them", and capture time drives
burst detection, phase segmentation, and matching renamed gallery exports back
to source frames:

```bash
brew install exiftool
```

## Step 1: see what is actually on the drives

The automator finds weddings by folder name, and it does not yet know yours.
Run this first so a zero-match result explains itself instead of looking like a
bug:

```bash
python3 -m mck discover --archive "/Volumes/The Beast"
python3 -m mck discover --archive "/Volumes/MG Photography Main"
```

Output looks like:

```
/Volumes/The Beast
  84 folder(s), 12 would be audited, 72 would be skipped

  [OK  ] 2025-06-14 Smith
         subfolders: Delivered, RAW
         matched: raw=RAW; delivered=Delivered
         files seen: 4218 raw, 812 jpeg
  [SKIP] 2024-09-21 Alvarez
         subfolders: CR3 Files, Client Gallery, Culled
         files seen: 3901 raw, 0 jpeg

  Most common subfolder names across this archive:
    cr3 files                     41
    client gallery                38
```

If folders are being skipped, feed your real names back in. Every later command
accepts the same two flags:

```bash
python3 -m mck discover --archive "/Volumes/The Beast" \
    --raw-names "raw,cr3 files,originals" \
    --delivered-names "delivered,client gallery,final jpegs"
```

Keep whichever pair of `--raw-names` / `--delivered-names` gets most folders to
`OK`. You will reuse them below.

## Step 2: find the label in a catalog

**Close Lightroom first.** The catalog is copied before opening and opened
read-only, but SQLite will refuse on a live lock.

```bash
python3 -m mck catalog --lrcat "/path/to/Your-Catalog.lrcat"
```

This reports every candidate label the catalog holds, strongest first, and tells
you which flag to use:

```
Candidate labels, strongest first:

  [strongest] Published photos (publish service upload log)
              38,204 images (18% of catalog)   --label-source published
              A literal record of frames uploaded through a Lightroom publish
              service. If galleries were delivered this way, this is the
              delivered set, exactly.
                - Pic-Time Gallery: 38,204

  [strong   ] Pick / reject flags
              41,880 images (19% of catalog)   --label-source pick
```

Which one you get depends on how you actually worked:

| If the catalog shows | It means | Use |
| --- | --- | --- |
| Published photos | You uploaded through the Pic-Time Lightroom plugin | `--label-source published` |
| Delivery-named collections | You gathered finals into a collection before exporting | `--label-source collection --collection "name"` |
| Pick flags | You culled with P/X rather than stars | `--label-source pick` |
| Only ratings | You culled with stars and exported by filter | `--label-source rating` |

Then extract the per-image rows. `--folder-filter` isolates one wedding out of a
catalog holding many; it is a case-insensitive substring of the folder path, and
the report above lists your largest folders so you can copy one:

```bash
python3 -m mck catalog --lrcat "/path/to/Your-Catalog.lrcat" \
    --folder-filter "2025-06-14 Smith" \
    --extract ./labels-smith.csv
```

## Step 3: audit one wedding

Pick a typical recent wedding where both the raws and the delivered gallery are
intact. Two shooters is better than one, it exercises the per-body burst
grouping.

```bash
python3 -m mck scan \
  --raw          "/Volumes/The Beast/2025-06-14 Smith/RAW" \
  --labels       ./labels-smith.csv \
  --label-source published \
  --out          ~/Sandbox/"McKinley G Photography"/"R&D Lab"/mckinley-audit/smith \
  --title        "Smith 2025"
```

`--delivered` still works if a folder of finals ever does exist. Give one or the
other; without a label there is nothing to audit against.

Takes a few minutes. Then read `AUDIT_REPORT.md` in that output folder.

Before running it, if your cull decisions live in Lightroom and were never
written to disk, write the sidecars out first: open the catalog, *Catalog
Settings → Metadata → Automatically write changes into XMP*, then select all
and `Cmd+S`. This does not modify the raws. If you would rather not touch the
catalog, skip it and run `python3 -m mck catalog --lrcat ...` instead, with
Lightroom closed.

## Step 4: the self-consistency test

This is the one that sets the ceiling for the whole project, and it is the only
step the tooling cannot do for you. Procedure is in
[the runbook](01-data-audit-runbook.md), section 6. About 90 minutes.

## Step 5: the whole archive, unattended

Once one wedding audits cleanly:

```bash
python3 -m mck auto \
  --archive "/Volumes/The Beast" \
  --out ~/Sandbox/"McKinley G Photography"/R\&D\ Lab/mckinley-audit \
  --raw-names "raw,cr3 files" \
  --delivered-names "delivered,client gallery"
```

Run it overnight with the drive connected and the Mac set not to sleep. It keeps
a ledger, so it is safe to interrupt and safe to re-run: only changed weddings
are reprocessed. One bad wedding does not stop the run.

Read `ROLLUP.md` when it finishes.

## Step 6: start capturing disagreements

Do this on your next wedding regardless of what the audit says. It is the only
part of the project where waiting costs something permanent.

```bash
CULL=~/Sandbox/"McKinley G Photography"/R\&D\ Lab/mckinley-audit/snapshots

# right after the automated cull, before you review anything
python3 -m mck snapshot --raw "/Volumes/The Beast/2026-09-20 Wedding/RAW" \
    --out "$CULL" --tag post-ai

# after you finish correcting
python3 -m mck snapshot --raw "/Volumes/The Beast/2026-09-20 Wedding/RAW" \
    --out "$CULL" --tag post-review

python3 -m mck diff --before "$CULL"/2026*post-ai --after "$CULL"/2026*post-review \
    --out "$CULL"/../disagreements/2026-09-20.jsonl
```

## What to send back

Six numbers decide whether Phase 1 is worth building:

| Number | Where |
| --- | --- |
| Delivered-join rate | `AUDIT_REPORT.md` |
| Keep rate, and its spread by year | `ROLLUP.md` |
| Star-rating AUC | `AUDIT_REPORT.md` |
| Near-duplicate share of drops | `AUDIT_REPORT.md` |
| Total preference pairs | `ROLLUP.md` |
| Self-consistency on burst winners | Step 4 |

`AUDIT_REPORT.md` and `ROLLUP.md` are small text files and contain no images,
so pasting them back is easy. They do contain file paths and client folder
names, which is why the output directories are gitignored.

## If something goes wrong

The first real run will probably surface a format or naming quirk. Useful flags:

- `--limit 200` on `scan` to test quickly against part of a wedding
- `--limit-weddings 3` on `auto` for a trial run
- `--no-exiftool` to force the built-in reader if exiftool misbehaves
- `--json` on `discover` and `catalog` for the raw output

Paste the error and the `discover` output back and I can fix it.
