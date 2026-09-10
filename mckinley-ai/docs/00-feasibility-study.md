# McKinley AI — Feasibility Study & Technical Challenge

**Phase 0 deliverable.** Written to be argued with, not agreed with.

---

## 1. The distinction you drew is correct, and it is smaller than you think

You separated two things:

- "AI that knows what a good photograph is"
- "AI that has watched McKinley shoot weddings and knows what he delivers"

That distinction is real and it is the right thing to build toward. But it is
worth being precise about *where* the difference actually lives, because it
changes what you build first.

Decompose the gap between a generic culler and you:

| Component of your cull | Personal? | Hard? |
| --- | --- | --- |
| Rejecting technical failures (missed focus, motion blur, blinks, misfires) | **No** — this is universal | Solved, off-the-shelf |
| Volume calibration (how many frames survive per hour of the day) | Weakly — it's a number | Trivial once measured |
| Choosing 1 frame from a burst of 6 near-identical ones | **Yes, strongly** | **Hard, and where your data matters** |
| Subject priority (which guests matter at this wedding) | **Yes** | Hard, needs per-wedding context |
| Moment recognition (this reaction is the story of the day) | **Yes** | Hardest |

Roughly: **half the labour of a cull is universal, and the personal half is
concentrated almost entirely in near-duplicate resolution.** That is good news —
it means a first version can deliver most of the time savings using mostly
generic components, and your historical data gets spent where it is scarce and
valuable rather than re-learning what blur looks like.

The audit tool measures this split on your actual archive. The number to watch
is `share_of_drops_that_are_near_dupes`. If it comes back at 40%, near-duplicate
ranking is the project. If it comes back at 8%, most of your cull is outright
rejection and a much simpler system captures most of the value.

---

## 2. The most important number, and nobody measures it

**How often do you agree with yourself?**

No model can exceed your own self-consistency. If you re-cull the same 400
frames six months apart and pick the same keepers 76% of the time, then a model
scoring 76% is at human parity, and every hour spent chasing 85% is chasing
noise you generated yourself.

Nobody measures this before starting, and it is the single number that decides
whether the project has a ceiling worth reaching for.

**Run this in Phase 0, before writing any model code.** The procedure is in
[`01-data-audit-runbook.md`](01-data-audit-runbook.md) §6. It costs about 90
minutes.

Rough expectations, from how selection tasks behave generally: on outright
technical rejects, agreement is very high (>95%). On which frame wins inside a
burst, expect somewhere in the 60–80% range. If yours comes in under ~65% on
burst choices, that is genuinely useful information: it means your burst
selection is closer to arbitrary than principled, a model will plateau early,
and the right product is "surface the 6 candidates, let me pick in one click"
rather than "pick for me."

That is still a very good product. It is just a different one, and knowing
which you are building is worth 90 minutes.

---

## 3. What your data actually contains

### 3.1 The label you need already exists, and it isn't in Aftershoot

> **Correction, September 2026.** This section originally assumed the delivered
> galleries sat on disk. They do not: finals live in Pic-Time and were never
> kept locally. The argument below still holds, but the label is read from the
> Lightroom catalogs instead of from a folder of JPEGs. Every `.lrcat` was kept,
> and the catalog is arguably the better record anyway, because it holds pick
> flags and collection membership that a folder of exports cannot express, and
> it survives renames and re-exports. See `mck catalog` and
> [the quickstart](03-quickstart.md) step 2.

You framed this as extracting decisions from Lightroom/Aftershoot metadata. The
stronger label is simpler and you already have it for every wedding you have
ever shot:

> **Did this frame end up in the gallery the client received?**

That label is unambiguous, uncontaminated by any tool's suggestion, immune to
your rating habits drifting over the years, and available retroactively without
touching Aftershoot at all. Ratings and colour labels are *proxies* for it.

The audit's `rating_auc` metric tests how good a proxy your stars actually are.
Interpretation:

- **AUC ≥ 0.85** — your stars track your final decisions closely. Weddings
  without a recoverable delivered set can still contribute.
- **AUC 0.65–0.85** — stars are a weak signal. Train on the delivered set; use
  the rating as a feature at most.
- **AUC < 0.65** — the stars in your archive are near-noise relative to what you
  delivered. Do not train on them.

### 3.2 What XMP sidecars give you

Confirmed for your workflow: Aftershoot writes star ratings and colour labels
into XMP sidecars beside the raw file, which Lightroom then reads via
*Metadata → Read Metadata from File*. Develop settings are likewise written to
sidecars rather than into the raw.
([Aftershoot support](https://support.aftershoot.com/en/articles/9190048-how-do-i-keep-my-stars-colors-when-i-bring-my-images-from-lightroom-or-capture-one),
[workflow guide](https://aftershoot.com/blog/workflow-between-aftershoot-lightroom/))

So a sidecar typically holds: `xmp:Rating`, `xmp:Label`, the `crs:` develop
sliders (exposure, contrast, highlights, shadows, whites/blacks, temperature,
tint, vibrance, saturation, clarity, texture, dehaze), crop rectangle and angle,
tone curve, B&W conversion, camera profile / preset name, and local adjustment
masks.

**What XMP does not give you:**

- **Pick / reject flags.** These are Lightroom catalog state, not sidecar
  metadata. If your cull ever used flags rather than stars, that decision is
  only in the `.lrcat`. The `mck catalog` command checks this directly.
- **Any history.** A sidecar holds *one* state — the last one written.

That second point is the one that breaks a specific thing you asked for.

### 3.3 The disagreement data you want most does not exist yet

You wrote:

> If possible, I want to use disagreements between Aftershoot and me as
> particularly valuable training data.

You are right that this is the highest-value signal per example. Where the
machine and you agree, you learn little. Where you overrode it, you learn
exactly what makes you *you*.

**It is not recoverable from your archive.** Aftershoot writes its rating to the
sidecar; you correct it; the correction overwrites the suggestion. One file, one
state. There is no before/after unless you happen to hold a backup of the
sidecars from between those two moments.

The audit checks for this empirically — `write_session_count` clusters sidecar
modification times. More than one cluster means some history may survive in your
backups and is worth digging for. One cluster means it is gone.

**But it costs almost nothing to capture going forward:**

```bash
# after the automated cull, before you touch anything
mck snapshot --raw /path/to/wedding/raw --out ./snapshots --tag post-ai

# after you finish correcting
mck snapshot --raw /path/to/wedding/raw --out ./snapshots --tag post-review

mck diff --before ./snapshots/2026...post-ai \
         --after  ./snapshots/2026...post-review \
         --out    ./disagreements/wedding.jsonl
```

Two commands per wedding. If you shoot 25 weddings a year and override ~8% of
frames, that is roughly 8,000 high-value labelled disagreements per year,
starting the day you adopt it. **Start doing this on your next wedding,
regardless of whether you build anything else.** It is the one part of this
project where delay has a permanent cost.

### 3.4 What Aftershoot itself adds: essentially nothing you need

Their per-image AI confidence scores are internal and not exported in any
documented form. You do not need them. The delivered-set label is stronger, and
building on their scores would create a dependency on a product you might leave
and a licensing question you do not need to have (§8).

---

## 4. Is your data enough? Yes — and there is more of it than you think

The image count understates the dataset, because **one burst decision produces
many training examples.**

A burst of 6 frames where you kept 1 yields 5 ordered preference pairs: "under
near-identical conditions, McKinley preferred A over B." Those pairs are the
densest personal signal available, and they are *easier* to learn from than
absolute quality, because the model compares two frames that differ only in the
things that actually drove your decision.

Order-of-magnitude for a typical 4,000-frame wedding: roughly 1,000–1,500
bursts, of which perhaps 300–500 are decisive, yielding **1,500–3,000 preference
pairs per wedding.** Across 100 weddings that is on the order of 150,000–300,000
pairs. The audit computes the exact figure for your archive; the roll-up sums it.

Against that:

| Approach | Data needed | Verdict |
| --- | --- | --- |
| Frozen vision backbone + trained head | 10–20 weddings | ✅ **Do this** |
| Frozen backbone + pairwise burst ranker | 20–50 weddings | ✅ **Do this** |
| LoRA / partial fine-tune of the backbone | 100+ weddings | Maybe later; unlikely to be worth it |
| Full fine-tune from scratch | Millions | ❌ Never |

**You do not need to fine-tune anything.** That is the most important
architectural decision in the project, and it is a decision *for* you: a frozen
backbone means embeddings are computed once and reused forever, so retraining
after you correct the model takes seconds instead of hours. That is what makes
your Phase 3 feedback loop actually work rather than being a nice idea you never
run.

---

## 5. Recommended architecture

```
RAW file
  │
  ├─ 1. Embedded JPEG preview extraction        ← NOT raw demosaicing
  │      Every CR2/CR3/NEF/ARW contains a full-size JPEG.
  │      10–50× faster than decoding the raw. Do this.
  │
  ├─ 2. Technical gates (cheap, generic, off-the-shelf)
  │      blur / focus, exposure clipping, face + eye state, subject motion
  │      → hard rejects never reach the model
  │
  ├─ 3. Embedding (frozen backbone: DINOv2 or SigLIP-class ViT)
  │      → cached to disk, computed once per image, ever
  │
  ├─ 4. Burst + scene grouping (capture timestamp + embedding distance)
  │
  ├─ 5. Keep-probability head  (gradient boosting or small MLP)
  │      inputs: embedding + EXIF + technical scores + burst/scene context
  │
  ├─ 6. Within-burst pairwise ranker (Bradley-Terry / RankNet)
  │      operates on embedding *differences* inside a burst
  │      ← this is where your personal data is spent
  │
  └─ 7. Calibration: target delivery count per scene, from your history
```

Two notes on why this shape:

**Steps 5 and 6 are separate on purpose.** "Is this frame deliverable at all"
and "is this the best frame of these six" are different questions with different
features and different failure modes. Collapsing them into one score is the
most common way these systems go wrong — you get a model that ranks a technically
perfect but boring frame above the one where someone laughed.

**Step 1 is not a detail.** Decoding raws is the throughput bottleneck for the
entire project; every raw already contains a JPEG preview that is more than
adequate for embedding. Getting this right is the difference between a 3-hour
archive pass and a 3-day one.

---

## 6. What is unrealistic — pushing back on Phase 4

You asked to be challenged. Here is the part I would argue with.

> RAW → AI predicts Lightroom develop settings → XMP → Lightroom

**As stated, this is substantially harder than the culling problem, and I do not
think it is worth building fourth. Here is why:**

1. **It is ill-posed.** The same frame admits many defensible renderings. A model
   trained to regress your slider values is being trained on a target that has no
   unique correct answer, so it converges on the conditional mean — which is the
   safe, flat, average-looking version of every image. The characteristic failure
   is a model that is *technically close* on every slider and produces images
   that look like nobody edited them.

2. **Your settings are conditioned on information that was never recorded.**
   When you pushed exposure +0.4 on one frame and +0.1 on the next, the reason
   was often the frame's role in the gallery, or a client conversation, or
   fatigue at 1 a.m. None of that is in the data.

3. **The target is non-stationary.** Your editing changed over the years. Culling
   preferences drift slowly; editing style drifts fast, and often deliberately.
   Training on five years of edits teaches the model a blend of styles you have
   already abandoned. The roll-up's `keep_rate_spread` and per-year table are
   there to quantify this drift.

4. **Provenance contamination.** If your recent archive was edited by Aftershoot
   Edits with your corrections on top, then a large share of those `crs:` values
   were authored by their model, not by you. Training on them teaches you to
   imitate Aftershoot — which is both a quality problem (you inherit their
   defaults and their mistakes) and a licensing problem (§8). The audit's
   `xmp_writer` attribution column separates these so you can filter.

**What I would build instead, and what I think actually saves you time:**

| Instead of | Build | Why |
| --- | --- | --- |
| Predicting all sliders | **Exposure + white balance correction only** | Objectively checkable, genuinely tedious, high per-frame variance, and the two that most affect the batch |
| Predicting a "style" | **Scene classification → select among *your own* presets** | You already have presets; the hard part is picking the right one per scene, which is a well-posed classification problem |
| Predicting the crop | **Crop prediction** | Genuinely learnable, well-defined target, visible payoff |
| Slider regression | Skip | See above |

That subset is maybe 20% of the work of the full version and delivers most of
the time savings. If it works well, revisit the ambitious version with evidence
in hand.

**Also worth saying plainly:** the culling model saves you hours per wedding and
is a well-posed learning problem. The editing model saves you a different, smaller
set of hours and is a poorly-posed one. Do the first thing completely before
starting the second.

---

## 7. Local-first: your instinct is right, and the tradeoff you offered isn't needed

You said you were "willing to use cloud services selectively if there is a
compelling reason." For image processing, **there isn't one.** Everything above
runs locally on consumer hardware. Nothing in the pipeline benefits meaningfully
from a hosted model.

Worth noting: your *current* workflow already sends client photographs to a
third party. Aftershoot's terms grant them a licence to process your photographs
and editing data, including to train their AI systems, while you retain
ownership. ([Aftershoot Terms of Use](https://aftershoot.com/terms-of-use/) —
read the current version yourself; terms change.) A local system is therefore
strictly *more* private than what you do today, not a tradeoff against
convenience.

The only place a hosted model earns its keep is helping you write this software.
That is a different activity, involves no client images, and is what this session
is.

### Hardware

| Tier | Spec | Archive pass (~400k frames) |
| --- | --- | --- |
| **Minimum** | Existing machine, any 8 GB+ GPU, or Apple Silicon 32 GB+ | overnight |
| **Recommended** | 24–32 GB GPU (RTX 4090/5090 class), 64 GB RAM, NVMe scratch | 3–6 hours, decode-bound |
| **Overkill** | Anything more | Not needed — you are I/O bound, not compute bound |

Two reassuring numbers:

- **Embedding storage for 400,000 images: about 0.8 GB.** (400k × 1024 dims ×
  fp16.) The learned index over your entire career fits on a thumb drive.
- **Retraining the head after a correction session: seconds.** Because the
  embeddings are cached and the backbone is frozen.

The expensive pass happens once. Everything after is cheap. Do not buy hardware
before Phase 2 tells you whether you need it.

### Ongoing cost

Electricity and your time. No per-image API cost. The honest cost is development
hours: roughly **80–150 for Phases 0–2**, another **150–250 for Phases 3–5**, and
Phase 6 is never finished. Budget against your hourly rate and the hours a cull
currently costs you.

---

## 8. Legal, licensing and privacy

*I am not a lawyer; this flags what to look at, not what is true in your
jurisdiction.*

**Your photographs.** You hold copyright unless your contracts assign it, which
wedding contracts typically do not. Training a private model on your own images
for your own use is the clean case.

**Your decisions.** Star ratings, colour labels, and which frames you delivered
are your own records. No issue.

**Aftershoot-generated data — the one real question.** Their terms cover their
licence to *your* data. The reverse direction — you using outputs of their model
as training targets for a model of your own — is the sort of use that vendor
terms commonly restrict, and training a model on another model's outputs is
plausibly characterised as distillation. Two things follow:

1. **Read the current terms yourself** before Phase 4, specifically for
   restrictions on competing products, reverse engineering, and use of outputs.
2. **Design around it entirely, which costs you nothing.** Train the culler on
   the delivered-set label — your decision, your data, no dependency. For any
   editing work, filter to sidecars *you* authored. The audit's `xmp_writer`
   column exists precisely to make that filter one line of code.

This is why §3.1 matters beyond data quality: using the delivered set as the
label makes the licensing question mostly disappear.

**Client privacy.** Local processing addresses the bulk of it. Check your client
contract for what you have promised about third-party processing — you may
already be relying on a clause that a local system makes easier to honour.

**Biometrics — a specific flag for you.** Face recognition, as opposed to face
*detection*, generates biometric identifiers. Illinois' BIPA regulates private
entities collecting them, requires written consent and a published retention
policy, and carries a private right of action with statutory damages. **You shoot
the St. Louis metro, which spans into Illinois.** Face detection (is there a face,
are the eyes open) is not the concern; persistent face *embeddings* used to
identify specific people across a wedding are. If you build guest-identity
features — and they are genuinely useful for "prioritise the couple's immediate
family" — get advice first, and prefer designs that compute identity clusters
per wedding and discard them afterward rather than maintaining a permanent
face database.

---

## 9. Build versus buy — the argument against doing this at all

Aftershoot already offers personalised culling profiles. Narrative Select and
Optyx exist. You would be spending 200–400 hours rebuilding a category that
vendors are actively competing in.

**Honest reasons to build anyway:**

- You own it. No subscription, no vendor pivot, no terms change.
- It is strictly more private than any hosted option.
- It can be personalised past what any vendor will offer, because they optimise
  for the average customer and you are optimising for exactly one.
- The dataset you build is an asset independent of the model, and it appreciates.

**Honest reasons not to:**

- 200–400 hours is a real number, and it is hours not spent shooting or
  marketing.
- The generic 80% is already commoditised and getting cheaper every year.
- The personal 20% is the hard part, and §2 may show your own ceiling is lower
  than you would like.

### Kill criteria — decide these now, while it is cheap to walk away

Stop, or radically rescope, if the audit shows any of:

1. **Self-consistency below ~65%** on burst choices (§2). Your target is noise.
2. **Fewer than ~15% of drops are near-duplicate resolution.** The personal part
   of the problem is too small to justify a personal model; buy a generic culler.
3. **Delivered sets cannot be joined** for most of the archive (`unmatched` high
   across the roll-up). No label, no project — though §3.3's snapshot workflow
   still lets you start accumulating data from today.
4. **Keep rate swings wildly across years** with no explanation. The target moved
   too much; restrict training to recent work and re-evaluate whether enough
   remains.

Phase 0 exists to test these four things before you spend the 200 hours. That is
the entire point of doing it first.

---

## 10. What to do next, in order

1. **Start snapshotting on your very next wedding** (§3.3). This is the only
   step where waiting costs you something permanent.
2. **Audit one wedding** — [`01-data-audit-runbook.md`](01-data-audit-runbook.md).
   About 30 minutes.
3. **Run the self-consistency test** (§2, runbook §6). About 90 minutes. Do not
   skip it.
4. **Run the automator across the archive** and read the roll-up. Overnight,
   unattended.
5. **Then, and only then,** decide whether to build Phase 1.

Steps 2–4 produce numbers. Bring them back and we will decide the architecture
against evidence rather than against this document's estimates.

---

## Appendix: claims in this document, and their standing

| Claim | Standing |
| --- | --- |
| Aftershoot writes stars/labels to XMP sidecars read by Lightroom | Confirmed — vendor documentation, linked §3.2 |
| Aftershoot writes develop settings to sidecars, not the raw | Confirmed — vendor documentation, linked §3.2 |
| Aftershoot's terms licence your photos and editing data for AI training | Confirmed at time of writing — verify current terms yourself |
| Lightroom catalogs are SQLite with an undocumented, version-varying schema | Confirmed; the tool introspects rather than assumes |
| Pick/reject flags are catalog-only, not in XMP | **Assumed** — `mck catalog` tests it against your catalog |
| Preference-pair volumes per wedding (§4) | **Estimated** — the audit measures yours exactly |
| Throughput and hardware figures (§7) | **Estimated** from typical hardware; benchmark before buying |
| Self-consistency ranges (§2) | **Estimated** — measure yours; that is the point |

Some sources could not be re-verified during this session: web search hit a rate
limit and `aftershoot.com` was unreachable from this environment. Every claim
above that rests on a single unverified source is marked. The audit tool was
written to measure rather than assume precisely because of this.

**Sources:**
- [Aftershoot — Keeping stars/colours between Lightroom and Capture One](https://support.aftershoot.com/en/articles/9190048-how-do-i-keep-my-stars-colors-when-i-bring-my-images-from-lightroom-or-capture-one)
- [Aftershoot — Workflow between Aftershoot & Lightroom](https://aftershoot.com/blog/workflow-between-aftershoot-lightroom/)
- [Aftershoot — Technical answers](https://support.aftershoot.com/en/articles/10601968-technical-answers-about-aftershoot)
- [Aftershoot — Terms of Use](https://aftershoot.com/terms-of-use/)
- [Adobe — Metadata basics and actions in Lightroom Classic](https://helpx.adobe.com/lightroom-classic/help/metadata-basics-actions.html)
- [Lightroom Classic catalog database schema discussion](https://www.lightroomqueen.com/community/threads/lightroom-classic-catalog-database-schema.44883/)
- [camerahacks/lightroom-database — unofficial table catalog](https://github.com/camerahacks/lightroom-database)
