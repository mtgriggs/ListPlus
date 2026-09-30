# Burst labelling

The one Phase 0 measurement still outstanding, and the whole Phase 1 dataset,
come out of the same sitting.

## Why this exists

Your stars are overwhelmingly Aftershoot's judgments that you accepted, so
training on them would reproduce their model rather than learn yours. The one
place you consistently disagree is **which frame wins among near-duplicates**.

That signal is not in the archive. A sidecar holds one state, so a burst where
you switched the winner looks identical today to one you accepted. It has to be
regenerated, and this regenerates it directly.

## Running it

```bash
python3 -m mck label --raw "/Volumes/The Beast/2025-06-14 Smith/RAW"
```

Then open http://127.0.0.1:8765/ and start picking.

- <kbd>1</kbd>–<kbd>9</kbd> picks a frame, <kbd>S</kbd> skips
- Your original pick stays hidden until after you choose, then it is revealed
- Every decision is written to disk the moment you make it
- Close the tab, come back next week, it resumes where you stopped

Previews come from the JPEG already embedded in each raw, so nothing is
demosaiced and nothing is written next to your photographs.

### Modes

| Mode | Keeps | Use when |
| --- | --- | --- |
| `decisive` (default) | Bursts holding both a 4-or-5 and a 3 | Measuring self-consistency, since only these carry a recorded pick |
| `all` | Every multi-frame burst | Once the number is in hand and you want pair volume |

Other flags worth knowing: `--burst-gap` (default 2.0s) if your bursts are being
split or merged wrongly, `--mode all` if a wedding predates star culling, and
`--summary` to print results so far without starting the server.

## What comes out

Two things, from the same clicks.

**Preference pairs**, in `decisions.jsonl`. Each burst of *n* frames yields
*n−1* ordered comparisons: under near-identical conditions, McKinley preferred
this one over those. That is the densest personal signal available and the only
one in this project that is unambiguously yours.

**Your self-consistency**, printed at the end and written to a summary file.
Agreement between the frame you pick now and the one picked at cull time.

This is the number I have been asking for since the start, and it bounds the
whole project:

| Agreement | What it means |
| --- | --- |
| under ~65% | Your burst choices are closer to arbitrary than to a rule. A model plateaus early, and the right product is "surface the candidates, let me pick in one click" rather than "pick for me". Still a good product, and a much smaller build. |
| ~65–80% | Normal for this kind of judgement. A model matching it is at human parity, and there is real signal to learn. |
| over ~80% | Your choices follow a consistent rule. Best case: a stable target and a ceiling worth reaching for. |

**Pick a wedding you culled at least six months ago.** Recent enough to
remember and the agreement figure measures your memory, not your taste.

## How much is enough

Rough shape, to be replaced by your real numbers:

- A 4,000-frame wedding yields on the order of 300–500 decisive bursts
- Each burst is one decision and several pairs
- An hour of clicking covers a few hundred bursts

So one wedding is a sitting, and a handful of weddings is a usable first
dataset. Starting with one is the right move regardless, because the
self-consistency figure may change what gets built.
