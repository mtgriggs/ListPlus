# 🎬 Reels Studio

Turn a pile of photos into a vertical (9:16) Instagram-style reel — pick a
template, preview it on a phone mockup, add music and a caption, and export a
video file. Everything runs in your browser; your photos never leave your
device.

> This is the MVP described with McKinley G Photography: **upload photos →
> auto-assemble reel → preview → export.** It's a self-contained static web
> app with no backend and no build step.

## Run it

Because video export uses `canvas.captureStream` + `MediaRecorder`, run it from
a local server (not `file://`) for best browser support:

```bash
cd reels-studio
python3 -m http.server 8000
# then open http://localhost:8000
```

Use a current Chrome / Edge / Firefox. Export produces a `.webm` file.

## How to use

1. **Add photos** — click the drop zone or drag images in. Reorder with `‹ ›`,
   remove with `×`.
2. **Pick a template** — each one sets the pace, transitions, motion, and
   caption style.
3. **Details** — add a title (shown on the first clip), your handle (shown
   throughout), and optionally a music file.
4. **Export** — records the reel in real time and downloads `reel.webm`.

## What's built vs. what's deliberately out of scope

**In the MVP**
- Auto-assembly of photos into a 9:16 timeline
- 4 "trending-style" templates (pace, crossfade/cut, Ken Burns motion, captions)
- Live phone-mockup preview with scrubbing
- Optional music track mixed into the export
- Title + handle overlays
- Client-side video export (`.webm`)

**Not in the MVP (and why)**
- **Reading your camera roll automatically** — only a native mobile app can do
  that. The web app uses manual upload. The engine is reusable in a native
  shell later.
- **Real trending Instagram templates** — Instagram exposes no API for these,
  so we ship our own curated set instead.
- **Auto-posting to Instagram** — possible only for business/creator accounts
  via Meta's API, with restrictions. Could be a later phase.
- **Video clips as input** — currently photos only; video compositing is a
  natural next milestone.
- **MP4 export** — browsers record `.webm`; an MP4 transcode (e.g. ffmpeg.wasm)
  is a later add.

## Code map

| File | Role |
|------|------|
| `index.html` | App shell / layout |
| `css/styles.css` | Styling |
| `js/templates.js` | Template definitions (data only — easy to extend) |
| `js/reel.js` | `ReelEngine`: timeline, rendering, Ken Burns, export |
| `js/app.js` | UI wiring (upload, reorder, playback, export) |

## Roadmap ideas

1. Support short video clips alongside photos.
2. MP4 export via `ffmpeg.wasm`.
3. AI auto-selection of the best shots (sharpness/face/duplicate detection).
4. Beat-synced cuts to the music track.
5. Native wrapper (Capacitor / React Native) for real camera-roll access.
6. Direct publishing for IG business/creator accounts via the Meta API.
