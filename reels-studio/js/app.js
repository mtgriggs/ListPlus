// UI wiring for Reels Studio.
(function () {
  const $ = (id) => document.getElementById(id);

  const engine = new ReelEngine($("canvas"));
  let images = []; // [{ img, url }]

  // --- template list -------------------------------------------------------
  const tplWrap = $("templates");
  TEMPLATES.forEach((t, i) => {
    const btn = document.createElement("button");
    btn.className = "tpl" + (i === 0 ? " active" : "");
    btn.innerHTML = `<b>${t.name}</b><small>${t.desc}</small>`;
    btn.onclick = () => {
      [...tplWrap.children].forEach((c) => c.classList.remove("active"));
      btn.classList.add("active");
      engine.setTemplate(t);
      engine.seek(0);
      updateScrubMax();
    };
    tplWrap.appendChild(btn);
  });

  // --- file loading --------------------------------------------------------
  function loadFiles(fileList) {
    const files = [...fileList].filter((f) => f.type.startsWith("image/"));
    let pending = files.length;
    if (!pending) return;
    files.forEach((file) => {
      const url = URL.createObjectURL(file);
      const img = new Image();
      img.onload = () => {
        images.push({ img, url });
        if (--pending === 0) syncEngine();
      };
      img.onerror = () => { if (--pending === 0) syncEngine(); };
      img.src = url;
    });
  }

  function syncEngine() {
    engine.setImages(images.map((x) => x.img));
    renderThumbs();
    updateScrubMax();
    engine.seek(engine._pausedAt || 0);
  }

  function renderThumbs() {
    const wrap = $("thumbs");
    wrap.innerHTML = "";
    images.forEach((x, i) => {
      const li = document.createElement("li");
      li.innerHTML = `
        <img src="${x.url}" alt="" />
        <span class="idx">${i + 1}</span>
        <button class="rm" title="Remove">×</button>
        <div class="move">
          <button class="up" title="Move left">‹</button>
          <button class="down" title="Move right">›</button>
        </div>`;
      li.querySelector(".rm").onclick = () => { images.splice(i, 1); syncEngine(); };
      li.querySelector(".up").onclick = () => { if (i > 0) { [images[i-1], images[i]] = [images[i], images[i-1]]; syncEngine(); } };
      li.querySelector(".down").onclick = () => { if (i < images.length-1) { [images[i+1], images[i]] = [images[i], images[i+1]]; syncEngine(); } };
      wrap.appendChild(li);
    });
  }

  const dz = $("dropzone");
  $("fileInput").addEventListener("change", (e) => loadFiles(e.target.files));
  ["dragenter", "dragover"].forEach((ev) =>
    dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add("drag"); })
  );
  ["dragleave", "drop"].forEach((ev) =>
    dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.remove("drag"); })
  );
  dz.addEventListener("drop", (e) => loadFiles(e.dataTransfer.files));

  // --- details -------------------------------------------------------------
  $("titleInput").addEventListener("input", (e) => { engine.setTitle(e.target.value); engine.seek(engine._pausedAt || 0); });
  $("handleInput").addEventListener("input", (e) => { engine.setHandle(e.target.value); engine.seek(engine._pausedAt || 0); });

  const audioEl = $("audio");
  $("audioInput").addEventListener("change", (e) => {
    const f = e.target.files[0];
    if (f) audioEl.src = URL.createObjectURL(f);
  });

  // --- playback ------------------------------------------------------------
  const playBtn = $("playBtn");
  const scrub = $("scrub");
  const timeLbl = $("time");

  function updateScrubMax() { scrub.max = Math.max(1, Math.round(engine.duration * 100)); }

  engine.onTick = (t, dur) => {
    scrub.value = Math.round(t * 100);
    timeLbl.textContent = t.toFixed(1) + "s";
    if (audioEl.src && engine.playing && Math.abs(audioEl.currentTime - t) > 0.3) {
      try { audioEl.currentTime = t % (audioEl.duration || dur); } catch (_) {}
    }
  };

  playBtn.onclick = () => {
    if (engine.playing) {
      engine.pause(); audioEl.pause(); playBtn.textContent = "▶ Play";
    } else {
      engine.play();
      if (audioEl.src) { audioEl.currentTime = engine._pausedAt % (audioEl.duration || engine.duration); audioEl.play().catch(() => {}); }
      playBtn.textContent = "⏸ Pause";
    }
  };

  scrub.addEventListener("input", () => {
    engine.pause(); audioEl.pause(); playBtn.textContent = "▶ Play";
    engine.seek(scrub.value / 100);
  });

  // --- export --------------------------------------------------------------
  const exportBtn = $("exportBtn");
  exportBtn.onclick = async () => {
    if (!images.length) { alert("Add some photos first."); return; }
    engine.pause(); audioEl.pause(); playBtn.textContent = "▶ Play";
    exportBtn.disabled = true;
    exportBtn.textContent = "Recording…";
    $("progressWrap").classList.remove("hidden");
    const bar = $("progressBar");
    try {
      const blob = await engine.export(audioEl.src ? audioEl : null, (f) => {
        bar.style.width = Math.round(f * 100) + "%";
      });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "reel.webm";
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      alert("Export failed: " + err.message);
    } finally {
      exportBtn.disabled = false;
      exportBtn.textContent = "Export reel (.webm)";
      setTimeout(() => { $("progressWrap").classList.add("hidden"); bar.style.width = "0"; }, 600);
      engine.seek(0);
    }
  };

  // First paint.
  engine.renderAt(0);
})();
