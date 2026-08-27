"""Editorial submission automator.

Editorial selection is **not** culling with a higher threshold, and treating it
that way is why submissions get rejected. The differences are structural:

* **The objective changes.** A cull asks "would the couple want this?" A
  submission asks "would an editor run this?" Those diverge hardest on detail
  shots — invitation suites, tablescapes, florals — which are over-represented
  in published features and under-represented in a delivery cull.
* **It is a set-selection problem, not a ranking.** A publication wants 60-150
  images that tell the whole day, with vendor work visible. Twenty superb
  portraits and nothing else is a rejection. Coverage and diversity constraints
  bind harder than per-image quality.
* **The binding constraints are business rules, not aesthetics.** Exclusivity,
  resolution, complete vendor credits, and prior publication are what actually
  disqualify a submission, and none of them need a model.
* **There is almost no training data.** A photographer has hundreds of culled
  weddings and perhaps a handful of published ones. This will not be a learned
  model for a long time, if ever.

So this module implements the rules — which work today and are where
submissions fail — and leaves image-quality ranking to McKinley's own picks,
with the learned scorer as an optional input once it exists.

Requirements encoded in the built-in profiles come from published submission
guidelines; they change, so verify against the current page before submitting.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from pathlib import Path

from .exif import JPEG_EXTS, jpeg_dimensions, read_exif_many
from .scan import find_files

# Detail categories editors expect to see in a real-wedding feature. Detecting
# these from pixels needs a vision model; until then they are a checklist
# McKinley confirms once per wedding, which takes about a minute.
DETAIL_CATEGORIES = [
    "invitation-suite",
    "rings",
    "dress-hanging",
    "shoes-accessories",
    "bouquet-florals",
    "ceremony-space",
    "tablescape",
    "cake",
    "signage-stationery",
]

# Vendor roles publications require credited. An incomplete list is one of the
# most common rejection reasons and costs nothing to fix in advance.
VENDOR_ROLES = [
    "photographer",
    "venue",
    "planner",
    "florist",
    "caterer",
    "hair-makeup",
    "attire",
    "stationery",
    "cake",
    "music",
    "rentals",
]


@dataclass
class PublicationProfile:
    """Submission requirements for one outlet."""

    key: str
    name: str
    min_images: int
    max_images: int
    min_short_edge: int = 0
    min_width: int = 0
    max_file_mb: float = 0.0
    requires_exclusivity: bool = True
    response_days: int = 28
    notes: str = ""
    source: str = ""


PROFILES: dict[str, PublicationProfile] = {
    "style-me-pretty": PublicationProfile(
        key="style-me-pretty",
        name="Style Me Pretty",
        min_images=60,
        max_images=150,
        min_short_edge=900,
        max_file_mb=5.0,
        requires_exclusivity=True,
        response_days=28,
        notes=("All applicable vendors must be listed and a photography credit is "
               "required. Work already published on another blog or magazine is not "
               "considered; disclose simultaneous submissions."),
        source="https://www.stylemepretty.com/real-wedding-submissions/",
    ),
    "junebug": PublicationProfile(
        key="junebug",
        name="Junebug Weddings",
        min_images=60,
        max_images=120,
        min_short_edge=900,
        requires_exclusivity=True,
        response_days=28,
        notes=("Editorial submissions are open to Junebug vendor members only — the "
               "form lives inside the vendor account. Favours creative details and "
               "artistic photography."),
        source="https://junebugweddings.com/submission-guidelines",
    ),
    "jet-fete": PublicationProfile(
        key="jet-fete",
        name="Jet Fete by Bridal Bar",
        min_images=60,
        max_images=80,
        min_width=600,
        requires_exclusivity=True,
        response_days=28,
        notes="Destination focus. Requests low-resolution (72 dpi) images.",
        source="https://jetfeteblog.com/submit",
    ),
    "generic": PublicationProfile(
        key="generic",
        name="Generic blog profile",
        min_images=60,
        max_images=100,
        min_short_edge=900,
        max_file_mb=5.0,
        requires_exclusivity=True,
        response_days=28,
        notes="Conservative defaults for an outlet without published specs.",
    ),
}


# --- submission ledger ----------------------------------------------------


class SubmissionLedger:
    """Tracks where each wedding has been submitted, and to what outcome.

    This exists because exclusivity is the rule most easily broken by accident.
    Submitting the same wedding to two outlets at once, or to a second outlet
    while the first is still deciding, is a reputational problem with editors
    and is trivially preventable by writing it down.
    """

    def __init__(self, path: Path):
        self.path = path
        self.data = {"version": 1, "weddings": {}}
        if path.exists():
            try:
                self.data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=2, default=str), encoding="utf-8")

    def submissions(self, wedding: str) -> list[dict]:
        return self.data["weddings"].get(wedding, [])

    def record(self, wedding: str, publication: str, status: str = "pending",
               when: str | None = None, response_days: int = 28) -> dict:
        entry = {
            "publication": publication,
            "status": status,
            "submitted": when or datetime.now().date().isoformat(),
            "response_days": response_days,
        }
        self.data["weddings"].setdefault(wedding, []).append(entry)
        self.save()
        return entry

    def set_status(self, wedding: str, publication: str, status: str) -> bool:
        for entry in self.data["weddings"].get(wedding, []):
            if entry["publication"] == publication:
                entry["status"] = status
                entry["updated"] = datetime.now().date().isoformat()
                self.save()
                return True
        return False

    def blockers(self, wedding: str, profile: PublicationProfile) -> tuple[list[str], list[str]]:
        """Exclusivity check. Returns (blockers, warnings)."""
        blockers: list[str] = []
        warnings: list[str] = []

        for entry in self.submissions(wedding):
            pub = entry["publication"]
            status = entry.get("status", "pending")

            if status == "published":
                if pub == profile.key:
                    blockers.append(f"Already published by {pub}.")
                elif profile.requires_exclusivity:
                    blockers.append(
                        f"Already published by {pub}. {profile.name} does not consider "
                        "work published elsewhere."
                    )
            elif status == "pending":
                try:
                    submitted = datetime.fromisoformat(entry["submitted"])
                except (ValueError, KeyError):
                    submitted = datetime.now()
                due = submitted + timedelta(days=entry.get("response_days", 28))
                overdue = datetime.now() > due

                if pub == profile.key:
                    blockers.append(f"Already pending at {pub} since {entry['submitted']}.")
                elif overdue:
                    warnings.append(
                        f"Pending at {pub} since {entry['submitted']}, past their "
                        f"{entry.get('response_days', 28)}-day window. Withdraw it "
                        "(`mck editorial status --set declined`) before submitting elsewhere."
                    )
                else:
                    blockers.append(
                        f"Pending at {pub} since {entry['submitted']}; their window runs "
                        f"to {due.date().isoformat()}. Submitting elsewhere now breaks "
                        "exclusivity."
                    )
        return blockers, warnings


# --- selection ------------------------------------------------------------


@dataclass
class Candidate:
    path: Path
    width: int | None = None
    height: int | None = None
    size_mb: float = 0.0
    capture_epoch: float | None = None
    scene_id: int | None = None
    orientation: str = "unknown"
    spec_problems: list[str] = field(default_factory=list)

    @property
    def short_edge(self) -> int | None:
        if self.width and self.height:
            return min(self.width, self.height)
        return None


def _orientation(w: int | None, h: int | None) -> str:
    if not w or not h:
        return "unknown"
    if abs(w - h) / max(w, h) < 0.05:
        return "square"
    return "landscape" if w > h else "portrait"


def gather_candidates(
    delivered_roots: list[Path],
    profile: PublicationProfile,
    prefer_exiftool: bool = True,
    scene_gap: float = 420.0,
) -> list[Candidate]:
    """Read the delivered gallery and check each frame against the spec."""
    paths = find_files(delivered_roots, JPEG_EXTS)
    exif = read_exif_many(paths, prefer_exiftool=prefer_exiftool) if paths else {}

    candidates: list[Candidate] = []
    for path in paths:
        dims = jpeg_dimensions(path)
        try:
            size_mb = path.stat().st_size / (1024 * 1024)
        except OSError:
            size_mb = 0.0

        cand = Candidate(
            path=path,
            width=dims[0] if dims else None,
            height=dims[1] if dims else None,
            size_mb=round(size_mb, 2),
            capture_epoch=exif.get(path).capture_epoch if exif.get(path) else None,
        )
        cand.orientation = _orientation(cand.width, cand.height)

        if dims is None:
            cand.spec_problems.append("could not read image dimensions")
        else:
            if profile.min_short_edge and (cand.short_edge or 0) < profile.min_short_edge:
                cand.spec_problems.append(
                    f"short edge {cand.short_edge}px < {profile.min_short_edge}px required")
            if profile.min_width and (cand.width or 0) < profile.min_width:
                cand.spec_problems.append(
                    f"width {cand.width}px < {profile.min_width}px required")
        if profile.max_file_mb and size_mb > profile.max_file_mb:
            cand.spec_problems.append(
                f"{size_mb:.1f} MB exceeds {profile.max_file_mb:.0f} MB limit")

        candidates.append(cand)

    # Scene grouping over the delivered set, so coverage can be checked against
    # the shape of the day rather than an arbitrary ordering.
    timed = sorted(
        [c for c in candidates if c.capture_epoch is not None],
        key=lambda c: c.capture_epoch,
    )
    scene = 0
    for i, cand in enumerate(timed):
        if i and cand.capture_epoch - timed[i - 1].capture_epoch > scene_gap:
            scene += 1
        cand.scene_id = scene
    return candidates


def select(candidates: list[Candidate], profile: PublicationProfile) -> list[Candidate]:
    """Choose a submission set that spans the day.

    Selection round-robins across scenes rather than taking a global top-N.
    A set that over-weights one phase reads as an incomplete story to an editor,
    which is the failure mode this guards against — it is a coverage constraint,
    not a quality judgement, and quality ordering within a scene is McKinley's
    to supply.
    """
    eligible = [c for c in candidates if not c.spec_problems]
    by_scene: dict[int | None, list[Candidate]] = {}
    for cand in eligible:
        by_scene.setdefault(cand.scene_id, []).append(cand)
    for members in by_scene.values():
        members.sort(key=lambda c: c.capture_epoch or 0.0)

    ordered_scenes = sorted(by_scene, key=lambda s: (s is None, s))
    chosen: list[Candidate] = []
    depth = 0
    while len(chosen) < profile.max_images:
        added = False
        for scene in ordered_scenes:
            members = by_scene[scene]
            if depth < len(members):
                chosen.append(members[depth])
                added = True
                if len(chosen) >= profile.max_images:
                    break
        if not added:
            break
        depth += 1

    chosen.sort(key=lambda c: c.capture_epoch or 0.0)
    return chosen


# --- wedding metadata -----------------------------------------------------


def load_wedding_meta(path: Path) -> dict:
    """Per-wedding vendor credits and confirmed detail categories."""
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def meta_template(wedding: str) -> dict:
    return {
        "wedding": wedding,
        "date": "",
        "location": "",
        "couple": "",
        "vendors": {role: "" for role in VENDOR_ROLES},
        "details_present": {cat: False for cat in DETAIL_CATEGORIES},
        "previously_posted_socially": False,
        "notes": "",
    }


# --- evaluation -----------------------------------------------------------


def evaluate(
    wedding: str,
    delivered_roots: list[Path],
    profile: PublicationProfile,
    ledger: SubmissionLedger,
    meta: dict,
    prefer_exiftool: bool = True,
) -> dict:
    """Assemble the submission package and everything blocking it."""
    candidates = gather_candidates(delivered_roots, profile, prefer_exiftool)
    chosen = select(candidates, profile)

    blockers, warnings = ledger.blockers(wedding, profile)

    failing = [c for c in candidates if c.spec_problems]
    if len(candidates) == 0:
        blockers.append("No delivered images found. Point --delivered at the gallery.")
    elif len(chosen) < profile.min_images:
        blockers.append(
            f"Only {len(chosen)} images meet {profile.name}'s spec; they require at "
            f"least {profile.min_images}."
            + (f" {len(failing)} were excluded on spec." if failing else "")
        )

    vendors = meta.get("vendors", {})
    missing_vendors = [r for r in VENDOR_ROLES if not str(vendors.get(r, "")).strip()]
    if not str(vendors.get("photographer", "")).strip():
        blockers.append("Photography credit is missing — required for consideration.")
    if missing_vendors:
        warnings.append(
            f"{len(missing_vendors)} vendor credit(s) blank: {', '.join(missing_vendors)}. "
            "Incomplete vendor lists are a common rejection reason."
        )

    details = meta.get("details_present", {})
    missing_details = [c for c in DETAIL_CATEGORIES if not details.get(c)]
    if missing_details:
        warnings.append(
            f"{len(missing_details)} detail category(ies) unconfirmed: "
            f"{', '.join(missing_details)}. Confirm or shoot them — detail coverage is "
            "what editorial features are built around."
        )

    if meta.get("previously_posted_socially"):
        warnings.append(
            "Marked as already posted on social. Some outlets treat that as prior "
            "publication — check before submitting."
        )

    orientations: dict[str, int] = {}
    for c in chosen:
        orientations[c.orientation] = orientations.get(c.orientation, 0) + 1
    if chosen and orientations.get("landscape", 0) / len(chosen) < 0.2:
        warnings.append(
            "Under 20% landscape. Features need horizontals for hero and banner "
            "placements; an all-vertical set is hard to lay out."
        )

    scenes_covered = len({c.scene_id for c in chosen if c.scene_id is not None})
    scenes_total = len({c.scene_id for c in candidates if c.scene_id is not None})

    return {
        "wedding": wedding,
        "publication": asdict(profile),
        "generated": datetime.now().isoformat(timespec="seconds"),
        "counts": {
            "delivered": len(candidates),
            "spec_eligible": len(candidates) - len(failing),
            "spec_failures": len(failing),
            "selected": len(chosen),
        },
        "coverage": {
            "scenes_covered": scenes_covered,
            "scenes_total": scenes_total,
            "orientations": orientations,
            "missing_details": missing_details,
        },
        "vendors": {"provided": {k: v for k, v in vendors.items() if str(v).strip()},
                    "missing": missing_vendors},
        "blockers": blockers,
        "warnings": warnings,
        "ready": not blockers,
        "selection": [
            {
                "path": str(c.path),
                "width": c.width,
                "height": c.height,
                "size_mb": c.size_mb,
                "orientation": c.orientation,
                "scene_id": c.scene_id,
            }
            for c in chosen
        ],
        "spec_failures": [
            {"path": str(c.path), "problems": c.spec_problems} for c in failing[:50]
        ],
        "prior_submissions": ledger.submissions(wedding),
    }


def render_submission(report: dict) -> str:
    """Human-readable submission package."""
    p = report["publication"]
    c = report["counts"]
    cov = report["coverage"]
    lines: list[str] = []
    w = lines.append

    w(f"# Editorial submission — {report['wedding']}")
    w("")
    w(f"**Target:** {p['name']}  ")
    w(f"_Prepared {report['generated']}_")
    w("")

    if report["ready"]:
        w(f"## ✅ Ready to submit — {c['selected']} images selected")
    else:
        w(f"## ❌ Not ready — {len(report['blockers'])} blocker(s)")
    w("")

    if report["blockers"]:
        w("### Blockers")
        w("")
        for b in report["blockers"]:
            w(f"- **{b}**")
        w("")
    if report["warnings"]:
        w("### Warnings")
        w("")
        for warn in report["warnings"]:
            w(f"- {warn}")
        w("")

    w("## Requirements")
    w("")
    w("| Requirement | Target | This submission |")
    w("| --- | --- | --- |")
    w(f"| Image count | {p['min_images']}–{p['max_images']} | {c['selected']} |")
    if p["min_short_edge"]:
        w(f"| Min short edge | {p['min_short_edge']}px | "
          f"{c['spec_failures']} below spec |")
    if p["min_width"]:
        w(f"| Min width | {p['min_width']}px | {c['spec_failures']} below spec |")
    if p["max_file_mb"]:
        w(f"| Max file size | {p['max_file_mb']:.0f} MB | "
          f"{c['spec_failures']} over |")
    w(f"| Exclusivity | {'required' if p['requires_exclusivity'] else 'not required'} | "
      f"{'clear' if not report['blockers'] else 'see blockers'} |")
    w(f"| Typical response | {p['response_days']} days | — |")
    w("")
    if p["notes"]:
        w(f"> {p['notes']}")
        w("")
    if p.get("source"):
        w(f"Guidelines: {p['source']} — verify before submitting; requirements change.")
        w("")

    w("## Coverage")
    w("")
    w(f"- Phases represented: **{cov['scenes_covered']} of {cov['scenes_total']}** "
      "in the delivered gallery")
    orient = ", ".join(f"{k} {v}" for k, v in sorted(cov["orientations"].items()))
    w(f"- Orientation mix: {orient or 'n/a'}")
    if cov["missing_details"]:
        w(f"- Detail categories unconfirmed: {', '.join(cov['missing_details'])}")
    else:
        w("- All detail categories confirmed present")
    w("")

    w("## Vendor credits")
    w("")
    provided = report["vendors"]["provided"]
    if provided:
        w("| Role | Credit |")
        w("| --- | --- |")
        for role, name in provided.items():
            w(f"| {role} | {name} |")
        w("")
    if report["vendors"]["missing"]:
        w(f"**Missing:** {', '.join(report['vendors']['missing'])}")
        w("")

    if report["prior_submissions"]:
        w("## Submission history")
        w("")
        w("| Publication | Submitted | Status |")
        w("| --- | --- | --- |")
        for entry in report["prior_submissions"]:
            w(f"| {entry['publication']} | {entry['submitted']} | {entry.get('status', '?')} |")
        w("")

    if report["spec_failures"]:
        w("## Excluded on spec")
        w("")
        for item in report["spec_failures"][:20]:
            w(f"- `{Path(item['path']).name}` — {'; '.join(item['problems'])}")
        if len(report["spec_failures"]) > 20:
            w(f"- …and {len(report['spec_failures']) - 20} more")
        w("")

    w("---")
    w("")
    w("_Selection spans the day by construction; ordering within each phase is "
      "chronological. Reorder to taste before sending — an editor reads the set as a "
      "story._")
    return "\n".join(lines)
