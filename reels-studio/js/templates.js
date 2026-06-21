// Built-in, "trending-style" reel templates.
// NOTE: These are our own designs. Instagram does not expose its real
// trending templates through any API, so we ship a curated set inspired
// by popular reel formats. Add/edit freely — each one is just data.

const TEMPLATES = [
  {
    id: "quick-cut-pop",
    name: "Quick Cut Pop",
    desc: "Fast hard cuts, punchy and energetic. Great for a photo montage.",
    clipDuration: 0.8,
    transition: "cut",
    transitionDuration: 0,
    kenBurns: true,
    kenBurnsIntensity: 0.06,
    bg: "#000000",
    title: { size: 96, weight: 800, color: "#ffffff", position: "center", uppercase: true, shadow: true },
    handle: { size: 34, color: "#ffffff" },
  },
  {
    id: "cinematic-fade",
    name: "Cinematic Fade",
    desc: "Slow crossfades with gentle zoom. Elegant and moody.",
    clipDuration: 2.6,
    transition: "crossfade",
    transitionDuration: 0.7,
    kenBurns: true,
    kenBurnsIntensity: 0.14,
    bg: "#0a0a0a",
    vignette: true,
    title: { size: 78, weight: 600, color: "#f5f0e8", position: "bottom", uppercase: false, shadow: true },
    handle: { size: 32, color: "#e8e0d4" },
  },
  {
    id: "photo-dump",
    name: "Photo Dump",
    desc: "Casual crossfades at a relaxed pace. The classic IG photo dump.",
    clipDuration: 1.3,
    transition: "crossfade",
    transitionDuration: 0.35,
    kenBurns: true,
    kenBurnsIntensity: 0.08,
    bg: "#111111",
    title: { size: 70, weight: 700, color: "#ffffff", position: "bottom", uppercase: false, shadow: true },
    handle: { size: 32, color: "#ffffff" },
  },
  {
    id: "slow-aesthetic",
    name: "Slow Aesthetic",
    desc: "Long, calm holds with a barely-there zoom. Minimal and clean.",
    clipDuration: 3.2,
    transition: "crossfade",
    transitionDuration: 0.9,
    kenBurns: true,
    kenBurnsIntensity: 0.1,
    bg: "#000000",
    vignette: true,
    title: { size: 64, weight: 400, color: "#ffffff", position: "center", uppercase: false, shadow: false },
    handle: { size: 30, color: "#dddddd" },
  },
];

const TEMPLATES_BY_ID = Object.fromEntries(TEMPLATES.map((t) => [t.id, t]));
