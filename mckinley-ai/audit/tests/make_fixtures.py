"""Generate a synthetic wedding archive for testing the audit toolkit.

The fixture files carry real, parseable EXIF and XMP structures — the raws are
valid TIFF containers and the delivered files are valid JPEG/APP1 — so the
parsers are genuinely exercised. They contain no image data; nothing here
decodes pixels.

The synthetic wedding is built to mirror the structure the audit looks for:
two camera bodies shooting interleaved, frames grouped into bursts inside
scenes, a delivered subset where some exports keep the camera filename and
others are renamed (forcing the capture-time join), sidecars from two different
"writers", and star ratings that correlate imperfectly with delivery.
"""

from __future__ import annotations

import random
import struct
from datetime import datetime, timedelta
from pathlib import Path

# --- TIFF / EXIF construction --------------------------------------------

T_BYTE, T_ASCII, T_SHORT, T_LONG, T_RATIONAL = 1, 2, 3, 4, 5


def _pack_value(typ: int, values) -> bytes:
    if typ == T_ASCII:
        return values.encode("ascii") + b"\x00"
    if typ == T_SHORT:
        return b"".join(struct.pack("<H", v) for v in values)
    if typ == T_LONG:
        return b"".join(struct.pack("<I", v) for v in values)
    if typ == T_RATIONAL:
        return b"".join(struct.pack("<II", n, d) for n, d in values)
    raise ValueError(f"unsupported type {typ}")


def _count(typ: int, values, payload: bytes) -> int:
    return len(payload) if typ == T_ASCII else len(values)


def _payload_len(entries) -> int:
    total = 0
    for _, typ, values in entries:
        p = _pack_value(typ, values)
        if len(p) > 4:
            total += len(p) + (len(p) % 2)
    return total


def _build_ifd(entries, data_start: int, next_ifd: int = 0) -> tuple[bytes, bytes]:
    body = b""
    data = b""
    for tag, typ, values in entries:
        payload = _pack_value(typ, values)
        count = _count(typ, values, payload)
        if len(payload) <= 4:
            field = payload.ljust(4, b"\x00")
        else:
            field = struct.pack("<I", data_start + len(data))
            data += payload
            if len(data) % 2:
                data += b"\x00"
        body += struct.pack("<HHI", tag, typ, count) + field
    ifd = struct.pack("<H", len(entries)) + body + struct.pack("<I", next_ifd)
    return ifd, data


def build_tiff_exif(
    dt: datetime,
    make: str,
    model: str,
    serial: str,
    lens: str,
    iso: int,
    fnumber: float,
    exposure: float,
    focal: float,
    flash: bool,
    subsec: str = "00",
) -> bytes:
    """A minimal but structurally valid little-endian TIFF carrying EXIF."""
    dt_str = dt.strftime("%Y:%m:%d %H:%M:%S")

    exif_entries = [
        (0x829A, T_RATIONAL, [(int(exposure * 100000), 100000)]),
        (0x829D, T_RATIONAL, [(int(fnumber * 100), 100)]),
        (0x8827, T_SHORT, [iso]),
        (0x9003, T_ASCII, dt_str),
        (0x9004, T_ASCII, dt_str),
        (0x9209, T_SHORT, [1 if flash else 0]),
        (0x920A, T_RATIONAL, [(int(focal * 100), 100)]),
        (0x9291, T_ASCII, subsec),
        (0xA431, T_ASCII, serial),
        (0xA434, T_ASCII, lens),
    ]
    ifd0_entries = [
        (0x010F, T_ASCII, make),
        (0x0110, T_ASCII, model),
        (0x0132, T_ASCII, dt_str),
        (0x8769, T_LONG, [0]),  # patched below with the real Exif IFD offset
    ]

    ifd0_start = 8
    ifd0_size = 2 + 12 * len(ifd0_entries) + 4
    data0_start = ifd0_start + ifd0_size
    data0_len = _payload_len(ifd0_entries)
    exif_start = data0_start + data0_len
    exif_size = 2 + 12 * len(exif_entries) + 4
    data1_start = exif_start + exif_size

    ifd0_entries[-1] = (0x8769, T_LONG, [exif_start])

    ifd0, data0 = _build_ifd(ifd0_entries, data0_start)
    exif_ifd, data1 = _build_ifd(exif_entries, data1_start)

    header = b"II" + struct.pack("<HI", 42, ifd0_start)
    return header + ifd0 + data0 + exif_ifd + data1


def build_jpeg_with_exif(tiff: bytes) -> bytes:
    """SOI + APP1(Exif) + EOI — enough for metadata parsing."""
    payload = b"Exif\x00\x00" + tiff
    app1 = b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload
    return b"\xff\xd8" + app1 + b"\xff\xd9"


# --- XMP construction -----------------------------------------------------

XMP_TEMPLATE = """<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="{toolkit}">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about=""
    xmlns:xmp="http://ns.adobe.com/xap/1.0/"
    xmlns:xmpMM="http://ns.adobe.com/xap/1.0/mm/"
    xmlns:stEvt="http://ns.adobe.com/xap/1.0/sType/ResourceEvent#"
    xmlns:crs="http://ns.adobe.com/camera-raw-settings/1.0/"
    xmp:CreatorTool="{creator}"
    xmp:Rating="{rating}"
{label_attr}    crs:Version="15.0"
    crs:ProcessVersion="11.0"
    crs:HasSettings="{has_settings}"
{develop}   >
   <xmpMM:History>
    <rdf:Seq>
     <rdf:li stEvt:action="derived" stEvt:softwareAgent="{creator}"/>
    </rdf:Seq>
   </xmpMM:History>
{masks}  </rdf:Description>
 </rdf:RDF>
</x:xmpmeta>
<?xpacket end="w"?>
"""

MASK_BLOCK = """   <crs:MaskGroupBasedCorrections>
    <rdf:Seq>
     <rdf:li crs:CorrectionAmount="1.0" crs:LocalExposure2012="+0.25"/>
    </rdf:Seq>
   </crs:MaskGroupBasedCorrections>
"""


def build_xmp(
    rating: int | None,
    label: str | None,
    creator: str,
    develop: dict[str, float] | None = None,
    masks: bool = False,
    grayscale: bool = False,
) -> str:
    dev_lines = ""
    if develop:
        for key, val in develop.items():
            dev_lines += f'    crs:{key}="{val:+.2f}"\n'
    if grayscale:
        dev_lines += '    crs:ConvertToGrayscale="True"\n'

    return XMP_TEMPLATE.format(
        toolkit="XMP Core 6.0.0",
        creator=creator,
        rating=rating if rating is not None else 0,
        label_attr=f'    xmp:Label="{label}"\n' if label else "",
        has_settings="True" if develop else "False",
        develop=dev_lines,
        masks=MASK_BLOCK if masks else "",
    )


# --- Wedding generation ---------------------------------------------------

BODIES = [
    {"make": "Canon", "model": "EOS R5", "serial": "BODY-A", "lens": "RF28-70mm F2 L USM"},
    {"make": "Canon", "model": "EOS R6", "serial": "BODY-B", "lens": "RF85mm F1.2 L USM"},
]

SCENES = [
    # (name, minutes from start, burst count, flash, iso)
    ("getting-ready", 0, 14, False, 800),
    ("ceremony", 150, 26, False, 1600),
    ("family", 260, 18, True, 400),
    ("couple", 330, 22, False, 200),
    ("reception", 430, 30, True, 3200),
]


def make_wedding(
    root: Path,
    seed: int = 7,
    renamed_share: float = 0.4,
    sidecar_share: float = 0.9,
) -> dict:
    """Create raw/ and delivered/ folders under ``root``. Returns a summary."""
    rng = random.Random(seed)
    raw_dir = root / "raw"
    delivered_dir = root / "delivered"
    raw_dir.mkdir(parents=True, exist_ok=True)
    delivered_dir.mkdir(parents=True, exist_ok=True)

    start = datetime(2025, 6, 14, 11, 0, 0)
    frame_no = 1000
    delivered_no = 1
    made: list[dict] = []

    for scene_name, offset_min, n_bursts, flash, iso in SCENES:
        clock = start + timedelta(minutes=offset_min)
        for _ in range(n_bursts):
            body = BODIES[rng.randrange(len(BODIES))]
            burst_len = rng.choice([1, 1, 2, 3, 3, 4, 5, 6, 8])
            # Roughly a quarter of bursts yield a keeper.
            keeper = rng.randrange(burst_len) if rng.random() < 0.45 else None

            for i in range(burst_len):
                frame_no += 1
                shot_at = clock + timedelta(milliseconds=int(i * 320))
                name = f"IMG_{frame_no}"
                kept = keeper is not None and i == keeper

                tiff = build_tiff_exif(
                    dt=shot_at,
                    make=body["make"],
                    model=body["model"],
                    serial=body["serial"],
                    lens=body["lens"],
                    iso=iso,
                    fnumber=rng.choice([1.2, 1.8, 2.0, 2.8, 4.0]),
                    exposure=rng.choice([1 / 200, 1 / 400, 1 / 1000]),
                    focal=rng.choice([28.0, 35.0, 50.0, 85.0]),
                    flash=flash,
                    subsec=f"{i * 32 % 100:02d}",
                )
                (raw_dir / f"{name}.CR2").write_bytes(tiff)

                if rng.random() < sidecar_share:
                    # Ratings track delivery, imperfectly.
                    if kept:
                        rating = rng.choice([3, 4, 4, 5, 5])
                    else:
                        rating = rng.choice([0, 1, 1, 2, 2, 3])
                    label = "Green" if kept and rng.random() < 0.7 else None
                    creator = ("Aftershoot 2.x" if rng.random() < 0.6
                               else "Adobe Lightroom Classic 14.0")
                    develop = None
                    if kept:
                        develop = {
                            "Exposure2012": round(rng.uniform(-0.6, 0.7), 2),
                            "Contrast2012": float(rng.randrange(-10, 25)),
                            "Highlights2012": float(rng.randrange(-70, -10)),
                            "Shadows2012": float(rng.randrange(5, 60)),
                            "Temperature": float(rng.randrange(4200, 7200)),
                            "Vibrance": float(rng.randrange(0, 25)),
                        }
                    (raw_dir / f"{name}.xmp").write_text(
                        build_xmp(
                            rating=rating,
                            label=label,
                            creator=creator,
                            develop=develop,
                            masks=bool(kept and rng.random() < 0.25),
                            grayscale=bool(kept and rng.random() < 0.1),
                        ),
                        encoding="utf-8",
                    )

                if kept:
                    if rng.random() < renamed_share:
                        out_name = f"McKinley-{delivered_no:04d}.jpg"
                        delivered_no += 1
                    else:
                        out_name = f"{name}.jpg"
                    (delivered_dir / out_name).write_bytes(build_jpeg_with_exif(tiff))

                made.append({"name": name, "kept": kept, "scene": scene_name})

            clock += timedelta(seconds=rng.randrange(15, 90))

    return {
        "root": root,
        "raw": raw_dir,
        "delivered": delivered_dir,
        "frames": len(made),
        "kept": sum(1 for m in made if m["kept"]),
    }


def make_archive(root: Path, n_weddings: int = 3, seed: int = 1) -> Path:
    """Create several wedding folders under one archive root."""
    root.mkdir(parents=True, exist_ok=True)
    for i in range(n_weddings):
        make_wedding(root / f"2025-0{i + 1}-Smith-Wedding", seed=seed + i)
    return root


if __name__ == "__main__":
    import sys
    target = Path(sys.argv[1] if len(sys.argv) > 1 else "./fixture-wedding")
    info = make_wedding(target)
    print(f"{info['frames']} frames, {info['kept']} delivered -> {target}")
