"""XMP sidecar parsing.

XMP is RDF/XML. Adobe writes the same logical field either as an attribute on
``rdf:Description`` or as a child element, depending on version and writer, so
both forms are collected here and flattened into one ``prefix:Local`` keyspace.

This module deliberately makes no assumptions about *which* fields a given
archive contains — it reports whatever it finds. The audit then measures
coverage empirically rather than trusting documentation about what Lightroom or
Aftershoot "should" write.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

# Namespace URI -> conventional prefix. Anything not listed keeps its raw URI so
# unknown vendor namespaces still show up in the audit instead of vanishing.
NS_PREFIX = {
    "http://ns.adobe.com/xap/1.0/": "xmp",
    "http://ns.adobe.com/xap/1.0/mm/": "xmpMM",
    "http://ns.adobe.com/xap/1.0/sType/ResourceEvent#": "stEvt",
    "http://ns.adobe.com/camera-raw-settings/1.0/": "crs",
    "http://ns.adobe.com/camera-raw-saved-settings/1.0/": "crss",
    "http://purl.org/dc/elements/1.1/": "dc",
    "http://ns.adobe.com/exif/1.0/": "exif",
    "http://ns.adobe.com/exif/1.0/aux/": "aux",
    "http://ns.adobe.com/tiff/1.0/": "tiff",
    "http://ns.adobe.com/photoshop/1.0/": "photoshop",
    "http://www.w3.org/1999/02/22-rdf-syntax-ns#": "rdf",
    "http://ns.adobe.com/lightroom/1.0/": "lr",
    "http://iptc.org/std/Iptc4xmpCore/1.0/xmlns/": "Iptc4xmpCore",
}

# Develop fields that carry actual tonal/colour intent. Used to decide whether a
# sidecar contains an *edit* or is only a ratings carrier.
DEVELOP_FIELDS = [
    "crs:Exposure2012",
    "crs:Contrast2012",
    "crs:Highlights2012",
    "crs:Shadows2012",
    "crs:Whites2012",
    "crs:Blacks2012",
    "crs:Temperature",
    "crs:Tint",
    "crs:Vibrance",
    "crs:Saturation",
    "crs:Clarity2012",
    "crs:Texture",
    "crs:Dehaze",
    "crs:Sharpness",
    "crs:LuminanceSmoothing",
]

CROP_FIELDS = [
    "crs:CropTop",
    "crs:CropLeft",
    "crs:CropBottom",
    "crs:CropRight",
    "crs:CropAngle",
    "crs:HasCrop",
]

# Legacy (pre-2012 process) equivalents, kept so older archive years are not
# silently reported as "no develop data".
LEGACY_DEVELOP_FIELDS = [
    "crs:Exposure",
    "crs:Brightness",
    "crs:Contrast",
    "crs:Highlights",
    "crs:Shadows",
    "crs:FillLight",
    "crs:Clarity",
]

_NUM_RE = re.compile(r"^[+-]?\d+(\.\d+)?$")


def _qname(tag: str) -> str:
    """Convert ``{uri}local`` into ``prefix:local``."""
    if not tag.startswith("{"):
        return tag
    uri, _, local = tag[1:].partition("}")
    return f"{NS_PREFIX.get(uri, uri)}:{local}"


@dataclass
class XmpDoc:
    """Flattened view of one XMP sidecar."""

    path: Path
    fields: dict[str, str] = field(default_factory=dict)
    # rdf:Seq / rdf:Bag / rdf:Alt contents, keyed the same way as `fields`.
    lists: dict[str, list[str]] = field(default_factory=dict)
    # Number of local-adjustment / masking groups found.
    mask_count: int = 0
    # Software agents named anywhere in the file (xmp:CreatorTool, xmpMM history).
    agents: list[str] = field(default_factory=list)
    parse_error: str | None = None

    def get(self, key: str, default=None):
        return self.fields.get(key, default)

    def get_float(self, key: str) -> float | None:
        raw = self.fields.get(key)
        if raw is None:
            return None
        raw = raw.strip()
        if _NUM_RE.match(raw):
            return float(raw)
        return None

    def get_int(self, key: str) -> int | None:
        v = self.get_float(key)
        return int(v) if v is not None else None

    def get_bool(self, key: str) -> bool | None:
        raw = self.fields.get(key)
        if raw is None:
            return None
        return raw.strip().lower() == "true"

    # -- derived properties the audit cares about -------------------------

    @property
    def rating(self) -> int | None:
        return self.get_int("xmp:Rating")

    @property
    def label(self) -> str | None:
        v = self.get("xmp:Label")
        return v.strip() if v else None

    @property
    def process_version(self) -> str | None:
        return self.get("crs:ProcessVersion")

    @property
    def has_settings(self) -> bool | None:
        return self.get_bool("crs:HasSettings")

    @property
    def already_applied(self) -> bool | None:
        """``crs:AlreadyApplied`` is set by tools that bake their own render.

        Its presence is a useful hint that a sidecar was authored by an
        automated editor rather than by hand in Lightroom.
        """
        return self.get_bool("crs:AlreadyApplied")

    @property
    def converted_to_grayscale(self) -> bool | None:
        return self.get_bool("crs:ConvertToGrayscale")

    @property
    def preset_name(self) -> str | None:
        for key in ("crs:LookName", "crs:Look", "crss:Name", "crs:PresetType"):
            v = self.get(key)
            if v:
                return v
        return None

    def develop_values(self) -> dict[str, float]:
        """Non-null modern-process develop slider values."""
        out = {}
        for k in DEVELOP_FIELDS:
            v = self.get_float(k)
            if v is not None:
                out[k] = v
        return out

    def legacy_develop_values(self) -> dict[str, float]:
        out = {}
        for k in LEGACY_DEVELOP_FIELDS:
            v = self.get_float(k)
            if v is not None:
                out[k] = v
        return out

    def crop_values(self) -> dict[str, float]:
        out = {}
        for k in CROP_FIELDS:
            v = self.get_float(k)
            if v is not None:
                out[k] = v
        return out

    @property
    def has_develop(self) -> bool:
        return bool(self.develop_values()) or bool(self.legacy_develop_values())

    @property
    def has_nontrivial_develop(self) -> bool:
        """True when at least one tonal slider is actually off its default.

        A sidecar full of zeroes is a Lightroom "touched but unedited" state and
        carries no styling signal, so it must not be counted as an edit.
        """
        vals = self.develop_values()
        vals.update(self.legacy_develop_values())
        return any(abs(v) > 1e-9 for v in vals.values())


def _walk(elem, doc: XmpDoc) -> None:
    """Recursively flatten an RDF element tree into doc.fields / doc.lists."""
    for raw_key, raw_val in elem.attrib.items():
        key = _qname(raw_key)
        if key.startswith("rdf:"):
            continue
        doc.fields.setdefault(key, raw_val)

    for child in elem:
        ckey = _qname(child.tag)

        if ckey in ("rdf:Seq", "rdf:Bag", "rdf:Alt"):
            continue  # handled by the parent below

        # A container element whose single child is an rdf collection.
        collection = None
        for grand in child:
            if _qname(grand.tag) in ("rdf:Seq", "rdf:Bag", "rdf:Alt"):
                collection = grand
                break

        if collection is not None:
            items = []
            for li in collection:
                if _qname(li.tag) != "rdf:li":
                    continue
                if len(li) or li.attrib:
                    # Structured list item (e.g. a mask group or history event).
                    items.append(_structured_summary(li))
                    _walk(li, doc)
                else:
                    items.append((li.text or "").strip())
            if items:
                doc.lists.setdefault(ckey, items)
                # dc:title / dc:description style Alt: expose the first value.
                if ckey not in doc.fields and isinstance(items[0], str) and items[0]:
                    doc.fields.setdefault(ckey, items[0])
            if _is_mask_container(ckey):
                doc.mask_count += len(items)
            continue

        if ckey.startswith("rdf:"):
            _walk(child, doc)
            continue

        text = (child.text or "").strip()
        if text:
            doc.fields.setdefault(ckey, text)
        if child.attrib or len(child):
            _walk(child, doc)


def _is_mask_container(key: str) -> bool:
    k = key.lower()
    return (
        "maskgroupbasedcorrections" in k
        or "circularbasedcorrections" in k
        or "gradientbasedcorrections" in k
        or "paintbasedcorrections" in k
        or "retouchareas" in k
    )


def _structured_summary(li) -> str:
    """Compact string for a structured rdf:li so lists stay readable."""
    bits = []
    for raw_key, raw_val in li.attrib.items():
        key = _qname(raw_key)
        if key.startswith("rdf:"):
            continue
        bits.append(f"{key}={raw_val}")
    for grand in li:
        gkey = _qname(grand.tag)
        gtext = (grand.text or "").strip()
        if gtext:
            bits.append(f"{gkey}={gtext}")
    return "; ".join(bits[:8])


AGENT_KEYS = (
    "xmp:CreatorTool",
    "stEvt:softwareAgent",
    "tiff:Software",
    "photoshop:History",
    "crs:RawFileName",
)


def parse_xmp(path: Path) -> XmpDoc:
    """Parse one sidecar. Never raises — errors are recorded on the document."""
    doc = XmpDoc(path=path)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        doc.parse_error = f"read failed: {exc}"
        return doc

    # Strip anything outside the xpacket, and tolerate a stray BOM.
    text = raw.decode("utf-8", errors="replace").lstrip("﻿")

    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        # Some writers emit trailing padding that upsets strict parsers; retry
        # on just the outermost x:xmpmeta / rdf:RDF element.
        m = re.search(r"(<(?:\w+:)?xmpmeta.*</(?:\w+:)?xmpmeta>)", text, re.S)
        if not m:
            m = re.search(r"(<(?:\w+:)?RDF.*</(?:\w+:)?RDF>)", text, re.S)
        if not m:
            doc.parse_error = f"xml parse failed: {exc}"
            return doc
        try:
            root = ET.fromstring(m.group(1))
        except ET.ParseError as exc2:
            doc.parse_error = f"xml parse failed: {exc2}"
            return doc

    _walk(root, doc)

    agents = []
    for key in AGENT_KEYS:
        v = doc.fields.get(key)
        if v:
            agents.append(v)
    for items in doc.lists.values():
        for item in items:
            if isinstance(item, str) and "softwareAgent" in item:
                for part in item.split(";"):
                    if "softwareAgent" in part:
                        agents.append(part.split("=", 1)[-1].strip())
    # De-duplicate, preserve order.
    seen = set()
    doc.agents = [a for a in agents if not (a in seen or seen.add(a))]
    return doc


def guess_writer(doc: XmpDoc) -> str:
    """Best-effort attribution of who authored a sidecar.

    Returns one of ``aftershoot`` / ``lightroom`` / ``camera-raw`` / ``other`` /
    ``unknown``. This is a *heuristic over observed evidence*, not a documented
    contract — the audit reports it alongside the raw agent strings so the guess
    can always be checked against the underlying data.
    """
    blob = " ".join(doc.agents).lower()
    if "aftershoot" in blob:
        return "aftershoot"
    if "lightroom" in blob:
        return "lightroom"
    if "camera raw" in blob or "adobe camera raw" in blob:
        return "camera-raw"
    if blob.strip():
        return "other"
    return "unknown"
