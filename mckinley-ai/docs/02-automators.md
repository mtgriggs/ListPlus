# The Two Automators

Two workflows, two different problems. They are not the same model with
different thresholds, and building them that way is the main mistake to avoid.

| | **Cull** | **Editorial** |
| --- | --- | --- |
| Question | Would the couple want this? | Would an editor run this? |
| Output | A ranking over ~4,000 frames | A *set* of 60–150 that tells the day |
| Binds on | Per-image quality | Coverage, exclusivity, specs, credits |
| Training data | Every wedding you've delivered | A handful of published features |
| Needs a model? | Yes, eventually | Mostly no — the blockers are rules |

The cull automator is workflow scaffolding waiting on a model. The editorial
automator is useful today, because what actually gets submissions rejected is
paperwork, not taste.

---

## 1. Cull automator

```bash
python3 -m mck cull --intake /Volumes/Intake --out ./cull-out
python3 -m mck cull --intake /Volumes/Intake --out ./cull-out --watch
```

Drop a wedding folder into the intake directory. The automator:

1. **Waits for the copy to finish.** A folder must be quiet for `--settle`
   seconds (default 120) before it is touched. A card still copying looks
   exactly like a finished wedding except that its files keep changing, and
   culling a half-copied set produces garbage.
2. **Indexes every frame** — capture time, camera body, lens, ISO, shutter.
3. **Groups bursts and scenes** from capture time.
4. **Snapshots the sidecars** before anything is written. This is the
   pre-correction state your disagreement dataset is measured against.
5. **Proposes a rating per frame**, calibrated per scene rather than globally.
6. **Writes `proposal.json`** — and, only with `--write`, the XMP sidecars.

### On the scorer

**The model that ranks frames by "would McKinley deliver this" does not exist
yet.** This module does not pretend otherwise. Today it ships a baseline that
encodes two things true of any wedding — frames at the extremes of the exposure
envelope are more often technical rejects, and later frames in a burst are more
often the keeper — and nothing about your taste.

Do not mistake it for a cull. It exists so the workflow can run end to end, and
so step 4 starts accumulating data now. At Phase 2 the learned scorer drops into
the same interface and nothing else changes:

```python
Scorer = Callable[[list[ImageRecord]], list[float]]
```

### Why adopt it before the model exists

Step 4. Every wedding you shoot before the snapshot discipline is in place is a
wedding whose machine-versus-you disagreements are gone permanently. The
automator makes that automatic instead of something you remember to do.

### Safety

Sidecars are **not** modified unless you pass `--write`. When you do, every
existing sidecar is copied to a timestamped backup folder under `--out` before
any change, and edits are textual substitutions of the rating attribute — not a
re-serialization of the XML. Develop settings, masks, and history survive
byte-for-byte. There is a test asserting exactly that.

Start without `--write`, read a few `proposal.json` files, and only turn it on
once the proposals look sane.

---

## 2. Editorial automator

```bash
# What outlets are configured, and what each requires
python3 -m mck editorial profiles

# Create the metadata file for a wedding
python3 -m mck editorial init --wedding 2025-06-14-smith

# Build a submission package
python3 -m mck editorial check --wedding 2025-06-14-smith \
    --delivered "/Volumes/Archive/2025-06-14 Smith/Delivered" \
    --publication style-me-pretty

# Record that you sent it, and later what happened
python3 -m mck editorial submit --wedding 2025-06-14-smith --publication style-me-pretty
python3 -m mck editorial status --wedding 2025-06-14-smith \
    --publication style-me-pretty --set published
```

### What it checks

**Exclusivity** — the rule most easily broken by accident. The ledger tracks
where each wedding has been sent and what came back:

- Pending elsewhere, inside their response window → **blocked**, with the date
  their window closes.
- Pending elsewhere, past their window → **warning**, with the command to
  withdraw so you are free to move on.
- Already published anywhere → **blocked permanently** for outlets that require
  exclusivity.

This is the check worth having even if you ignore everything else here.
Submitting the same wedding to two outlets at once is a reputational problem
with editors and is trivially preventable by writing it down.

**Technical spec** — real pixel dimensions read from each JPEG's SOF marker,
not from EXIF, because an export pipeline can resize an image and leave stale
dimension tags behind. File size too. Frames failing the spec are excluded from
the count and listed so you can re-export them.

**Coverage** — the selection round-robins across the phases of the day rather
than taking a global top-N. Twenty superb portraits and nothing else reads as an
incomplete story to an editor. This is a coverage constraint, not a quality
judgement; ordering within a phase is yours to set.

**Orientation mix** — warns under 20% landscape. Features need horizontals for
hero and banner placements, and an all-vertical set is hard to lay out.

**Vendor credits** — a blank photography credit blocks; other blank roles warn.
Incomplete vendor lists are one of the most common rejection reasons and cost
nothing to fix in advance.

**Detail categories** — invitation suite, rings, dress, shoes, florals,
tablescape, cake, signage. Detecting these from pixels needs a vision model;
until then it is a checklist you tick once per wedding in the metadata file,
which takes about a minute and is what editorial features are built around.

### Output

`SUBMISSION-<publication>.md` — blockers, warnings, the requirement table, the
selected set, coverage, a copy-paste vendor credit block, and the submission
history. Plus `submission-<publication>.json` for anything you want to script.

The command exits `0` when ready and `1` when not, so it composes into a script.

### Publication profiles

Built in: `style-me-pretty`, `junebug`, `jet-fete`, `generic`. Each carries its
image count range, resolution floor, file size cap, exclusivity rule, response
window, and a link to its guidelines.

**Verify against the live guidelines before submitting.** These change, and the
profiles are a convenience, not an authority. Add outlets by extending `PROFILES`
in `mck/editorial.py`.

Sources for the built-in profiles:
[Style Me Pretty](https://www.stylemepretty.com/real-wedding-submissions/) ·
[Junebug](https://junebugweddings.com/submission-guidelines) ·
[Jet Fete](https://jetfeteblog.com/submit)

Note on Junebug specifically: editorial submissions are open to Junebug vendor
members only, and the form lives inside the vendor account. The profile will
happily prepare a package; membership is a prerequisite it cannot check.

---

## What neither automator does yet

- **Rank frames by your taste.** Needs Phase 2.
- **Classify detail shots.** Needs a vision model; checklist for now.
- **Judge whether an image is editorial-grade.** Needs published-set training
  data you do not have yet — a handful of features is not a training set. Once
  you have ~10 published weddings, the same frozen-backbone approach applies,
  and the ledger you are keeping now becomes its labels.

That last point is the reason to start recording submissions today even though
nothing learns from them yet.
