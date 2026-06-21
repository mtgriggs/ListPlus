// ReelEngine — auto-assembles a list of images into a vertical (9:16) reel,
// renders it to a canvas in real time, and can export it to a video file.
//
// Rendering is resolution-independent: we render at EXPORT_W x EXPORT_H and
// the canvas is scaled down by CSS for the on-screen preview.

const EXPORT_W = 720;
const EXPORT_H = 1280; // 9:16

class ReelEngine {
  constructor(canvas) {
    this.canvas = canvas;
    this.canvas.width = EXPORT_W;
    this.canvas.height = EXPORT_H;
    this.ctx = canvas.getContext("2d");

    this.clips = []; // [{ img, duration }]
    this.template = TEMPLATES[0];
    this.title = "";
    this.handle = "";

    this._raf = null;
    this._playStart = 0;
    this._pausedAt = 0;
    this.playing = false;
    this.onTick = null; // (currentTime, duration) => void
  }

  // --- configuration -------------------------------------------------------

  setImages(images) {
    this.clips = images.map((img) => ({ img, duration: this.template.clipDuration }));
  }

  setTemplate(template) {
    this.template = template;
    // Re-stamp clip durations to the new template's pace.
    this.clips.forEach((c) => (c.duration = template.clipDuration));
  }

  setTitle(t) { this.title = t || ""; }
  setHandle(h) { this.handle = h || ""; }

  get duration() {
    return this.clips.reduce((sum, c) => sum + c.duration, 0);
  }

  // Cumulative start time of each clip.
  _starts() {
    const starts = [];
    let acc = 0;
    for (const c of this.clips) {
      starts.push(acc);
      acc += c.duration;
    }
    return starts;
  }

  // --- drawing -------------------------------------------------------------

  // Draw `img` to cover the destination rect (object-fit: cover) with an
  // extra zoom factor and normalized pan in [-1, 1].
  _drawCover(img, dx, dy, dw, dh, scale, panX, panY) {
    const ir = img.width / img.height;
    const br = dw / dh;
    let w, h;
    if (ir > br) { h = dh * scale; w = h * ir; }
    else { w = dw * scale; h = w / ir; }
    const x = dx + (dw - w) / 2 + (panX * (w - dw)) / 2;
    const y = dy + (dh - h) / 2 + (panY * (h - dh)) / 2;
    this.ctx.drawImage(img, x, y, w, h);
  }

  _easeInOut(p) { return p < 0.5 ? 2 * p * p : 1 - Math.pow(-2 * p + 2, 2) / 2; }

  // Ken Burns transform for clip `i` at local progress p (0..1).
  _kenBurns(i, p) {
    const t = this.template;
    if (!t.kenBurns) return { scale: 1.001, panX: 0, panY: 0 };
    const intensity = t.kenBurnsIntensity || 0.1;
    const e = this._easeInOut(p);
    const scale = 1 + intensity * e + 0.02;
    // Deterministic, varied direction per clip.
    const dirs = [[1, 0], [-1, 0], [0, 1], [0, -1], [1, 1], [-1, -1]];
    const [dx, dy] = dirs[i % dirs.length];
    const reach = 0.5;
    return { scale, panX: dx * reach * (e - 0.5) * 2, panY: dy * reach * (e - 0.5) * 2 };
  }

  _drawClip(clipIndex, localT, alpha) {
    const c = this.clips[clipIndex];
    if (!c) return;
    const p = Math.min(1, localT / c.duration);
    const { scale, panX, panY } = this._kenBurns(clipIndex, p);
    this.ctx.save();
    this.ctx.globalAlpha = alpha;
    this._drawCover(c.img, 0, 0, EXPORT_W, EXPORT_H, scale, panX, panY);
    this.ctx.restore();
  }

  _drawVignette() {
    const g = this.ctx.createRadialGradient(
      EXPORT_W / 2, EXPORT_H / 2, EXPORT_H * 0.3,
      EXPORT_W / 2, EXPORT_H / 2, EXPORT_H * 0.72
    );
    g.addColorStop(0, "rgba(0,0,0,0)");
    g.addColorStop(1, "rgba(0,0,0,0.55)");
    this.ctx.fillStyle = g;
    this.ctx.fillRect(0, 0, EXPORT_W, EXPORT_H);
  }

  _drawText(t, currentTime) {
    const ctx = this.ctx;

    // Title — shown over the first clip, fading in then out.
    if (this.title && this.clips.length) {
      const firstDur = this.clips[0].duration;
      let alpha = 1;
      const fade = Math.min(0.4, firstDur / 3);
      if (currentTime < fade) alpha = currentTime / fade;
      else if (currentTime > firstDur - fade) alpha = Math.max(0, (firstDur - currentTime) / fade);
      else if (currentTime > firstDur) alpha = 0;

      if (alpha > 0) {
        const ts = t.title;
        const text = ts.uppercase ? this.title.toUpperCase() : this.title;
        ctx.save();
        ctx.globalAlpha = alpha;
        ctx.font = `${ts.weight} ${ts.size}px "Helvetica Neue", Arial, sans-serif`;
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        if (ts.shadow) {
          ctx.shadowColor = "rgba(0,0,0,0.6)";
          ctx.shadowBlur = 18;
          ctx.shadowOffsetY = 3;
        }
        ctx.fillStyle = ts.color;
        const y = ts.position === "center" ? EXPORT_H / 2 : EXPORT_H - 320;
        this._wrapText(text, EXPORT_W / 2, y, EXPORT_W - 120, ts.size * 1.15);
        ctx.restore();
      }
    }

    // Handle — persistent, bottom of frame.
    if (this.handle) {
      const hs = t.handle;
      const text = this.handle.startsWith("@") ? this.handle : "@" + this.handle;
      ctx.save();
      ctx.font = `600 ${hs.size}px "Helvetica Neue", Arial, sans-serif`;
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.shadowColor = "rgba(0,0,0,0.6)";
      ctx.shadowBlur = 12;
      ctx.fillStyle = hs.color;
      ctx.fillText(text, EXPORT_W / 2, EXPORT_H - 90);
      ctx.restore();
    }
  }

  _wrapText(text, x, y, maxWidth, lineHeight) {
    const ctx = this.ctx;
    const words = text.split(" ");
    const lines = [];
    let line = "";
    for (const word of words) {
      const test = line ? line + " " + word : word;
      if (ctx.measureText(test).width > maxWidth && line) {
        lines.push(line);
        line = word;
      } else {
        line = test;
      }
    }
    if (line) lines.push(line);
    const startY = y - ((lines.length - 1) * lineHeight) / 2;
    lines.forEach((l, i) => ctx.fillText(l, x, startY + i * lineHeight));
  }

  // Render the whole frame at absolute time `currentTime`.
  renderAt(currentTime) {
    const t = this.template;
    const ctx = this.ctx;
    ctx.fillStyle = t.bg || "#000";
    ctx.fillRect(0, 0, EXPORT_W, EXPORT_H);

    if (!this.clips.length) {
      ctx.fillStyle = "rgba(255,255,255,0.35)";
      ctx.font = "32px 'Helvetica Neue', Arial, sans-serif";
      ctx.textAlign = "center";
      ctx.fillText("Add photos to build your reel", EXPORT_W / 2, EXPORT_H / 2);
      return;
    }

    const starts = this._starts();
    // Find the active clip.
    let i = 0;
    while (i < this.clips.length - 1 && currentTime >= starts[i] + this.clips[i].duration) i++;
    const localT = currentTime - starts[i];
    const dur = this.clips[i].duration;

    this._drawClip(i, localT, 1);

    // Crossfade into the next clip near the end of this one.
    if (t.transition === "crossfade" && i < this.clips.length - 1) {
      const td = t.transitionDuration;
      if (localT > dur - td) {
        const a = (localT - (dur - td)) / td;
        this._drawClip(i + 1, localT - dur, a);
      }
    }

    if (t.vignette) this._drawVignette();
    this._drawText(t, currentTime);
  }

  // --- playback ------------------------------------------------------------

  play() {
    if (this.playing || !this.clips.length) return;
    this.playing = true;
    this._playStart = performance.now() - this._pausedAt * 1000;
    const loop = () => {
      if (!this.playing) return;
      let t = (performance.now() - this._playStart) / 1000;
      if (t >= this.duration) {
        t = 0;
        this._playStart = performance.now(); // loop
      }
      this._pausedAt = t;
      this.renderAt(t);
      if (this.onTick) this.onTick(t, this.duration);
      this._raf = requestAnimationFrame(loop);
    };
    loop();
  }

  pause() {
    this.playing = false;
    if (this._raf) cancelAnimationFrame(this._raf);
  }

  seek(t) {
    this._pausedAt = Math.max(0, Math.min(t, this.duration));
    this.renderAt(this._pausedAt);
    if (this.onTick) this.onTick(this._pausedAt, this.duration);
  }

  // --- export --------------------------------------------------------------

  // Records a real-time pass of the reel (plus optional audio) to a webm Blob.
  // onProgress: (fraction 0..1) => void
  async export(audioEl, onProgress) {
    if (!this.clips.length) throw new Error("No photos to export.");
    this.pause();

    const fps = 30;
    const stream = this.canvas.captureStream(fps);
    const tracks = [...stream.getVideoTracks()];

    let audioCtx = null;
    if (audioEl && audioEl.src) {
      audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      // A media element can only be wired into one source node; cache it.
      if (!audioEl._mediaSource) {
        audioEl._mediaSource = audioCtx.createMediaElementSource(audioEl);
      }
      const dest = audioCtx.createMediaStreamDestination();
      audioEl._mediaSource.connect(dest);
      tracks.push(...dest.stream.getAudioTracks());
    }

    const mixed = new MediaStream(tracks);
    const mime = MediaRecorder.isTypeSupported("video/webm;codecs=vp9")
      ? "video/webm;codecs=vp9"
      : "video/webm";
    const recorder = new MediaRecorder(mixed, { mimeType: mime, videoBitsPerSecond: 8_000_000 });
    const chunks = [];
    recorder.ondataavailable = (e) => e.data.size && chunks.push(e.data);

    const done = new Promise((resolve) => {
      recorder.onstop = () => resolve(new Blob(chunks, { type: "video/webm" }));
    });

    recorder.start();
    if (audioEl && audioEl.src) {
      audioEl.currentTime = 0;
      try { await audioEl.play(); } catch (_) {}
    }

    // Drive the render loop in real time until the reel completes.
    const start = performance.now();
    await new Promise((resolve) => {
      const step = () => {
        const t = (performance.now() - start) / 1000;
        if (t >= this.duration) {
          this.renderAt(this.duration - 0.001);
          resolve();
          return;
        }
        this.renderAt(t);
        if (onProgress) onProgress(t / this.duration);
        requestAnimationFrame(step);
      };
      step();
    });

    recorder.stop();
    if (audioEl) audioEl.pause();
    const blob = await done;
    if (audioCtx) audioCtx.close();
    if (onProgress) onProgress(1);
    return blob;
  }
}
