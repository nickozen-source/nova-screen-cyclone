#!/usr/bin/env python3
"""
Nova Screen — mise à jour cyclonique automatique.

Source principale:
  NHC Graphical Tropical Weather Outlook (KMZ officiel)
  https://www.nhc.noaa.gov/xgtwo/gtwo_atl.kmz

Le script:
  1. télécharge le GTWO Atlantique,
  2. cherche la perturbation avec la probabilité 48 h la plus élevée,
  3. met à jour data/cyclone.json,
  4. met à jour index.html sans modifier le design.

IMPORTANT:
- Tant qu'un système est un Invest, les positions H+3...H+24 sont une
  extrapolation indicative du mouvement renseigné dans cyclone.json.
- Elles NE SONT PAS présentées comme une trajectoire officielle NHC.
- La vigilance locale n'est jamais déduite du NHC: elle reste séparée.
"""

from __future__ import annotations

import datetime as dt
import html as htmlmod
import io
import json
import math
import re
import sys
import zipfile
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

ROOT = Path(__file__).resolve().parent
DATA_FILE = ROOT / "data" / "cyclone.json"
INDEX_FILE = ROOT / "index.html"

NHC_KMZ = "https://www.nhc.noaa.gov/xgtwo/gtwo_atl.kmz"
TZ = ZoneInfo("America/St_Barthelemy")

UA = "NovaScreen-SBH/1.0 (+public weather information dashboard)"


def log(msg: str) -> None:
    print(f"[NovaScreen] {msg}")


def load_existing() -> dict:
    if DATA_FILE.exists():
        return json.loads(DATA_FILE.read_text(encoding="utf-8"))
    return {}


def strip_html(value: str) -> str:
    value = htmlmod.unescape(value or "")
    value = re.sub(r"<br\s*/?>", "\n", value, flags=re.I)
    value = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def pct(text: str, hours: int) -> int | None:
    patterns = [
        rf"Formation chance through {hours} hours[^0-9]*(\d+)\s*percent",
        rf"{hours}[- ]hour[^0-9]*(\d+)\s*percent",
        rf"{hours}\s*hours[^0-9]*(\d+)\s*%",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, flags=re.I)
        if m:
            return int(m.group(1))
    return None


def parse_kml(kml: str) -> list[dict]:
    placemarks = re.findall(r"<Placemark\b.*?</Placemark>", kml, flags=re.I | re.S)
    systems = []

    for pm in placemarks:
        name_m = re.search(r"<name>(.*?)</name>", pm, flags=re.I | re.S)
        desc_m = re.search(r"<description>(.*?)</description>", pm, flags=re.I | re.S)
        name = strip_html(name_m.group(1)) if name_m else ""
        desc = strip_html(desc_m.group(1)) if desc_m else ""
        text = f"{name} {desc}"

        p48 = pct(text, 48)
        p7 = pct(text, 168) or pct(text, 7 * 24)

        # GTWO may include point placemarks and polygon placemarks.
        coords = re.findall(r"(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)", pm)
        if not coords:
            continue

        # Prefer a Point coordinate. Otherwise use centroid of coordinates.
        point_m = re.search(
            r"<Point>.*?<coordinates>\s*(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)",
            pm, flags=re.I | re.S
        )
        if point_m:
            lon, lat = float(point_m.group(1)), float(point_m.group(2))
        else:
            vals = [(float(lon), float(lat)) for lon, lat in coords]
            lon = sum(v[0] for v in vals) / len(vals)
            lat = sum(v[1] for v in vals) / len(vals)

        if p48 is not None or "disturbance" in text.lower() or "formation" in text.lower():
            systems.append({
                "name": name or "Perturbation",
                "description": desc,
                "latitude": round(lat, 2),
                "longitude": round(lon, 2),
                "development_48h": p48,
                "development_7d": p7,
            })

    # Deduplicate roughly by coordinates/probability.
    unique = []
    seen = set()
    for s in systems:
        key = (round(s["latitude"], 1), round(s["longitude"], 1), s["development_48h"])
        if key not in seen:
            unique.append(s)
            seen.add(key)
    return unique


def fetch_gtwo() -> list[dict]:
    log("Téléchargement du GTWO Atlantique NHC...")
    r = requests.get(NHC_KMZ, timeout=30, headers={"User-Agent": UA})
    r.raise_for_status()

    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        kml_names = [n for n in zf.namelist() if n.lower().endswith(".kml")]
        if not kml_names:
            raise RuntimeError("KMZ NHC sans fichier KML")
        kml = zf.read(kml_names[0]).decode("utf-8", errors="replace")

    return parse_kml(kml)


def destination(lat: float, lon: float, bearing_deg: float, distance_km: float):
    """Destination géodésique simple sur sphère."""
    R = 6371.0
    brng = math.radians(bearing_deg)
    lat1 = math.radians(lat)
    lon1 = math.radians(lon)
    d = distance_km / R

    lat2 = math.asin(
        math.sin(lat1) * math.cos(d)
        + math.cos(lat1) * math.sin(d) * math.cos(brng)
    )
    lon2 = lon1 + math.atan2(
        math.sin(brng) * math.sin(d) * math.cos(lat1),
        math.cos(d) - math.sin(lat1) * math.sin(lat2),
    )
    return round(math.degrees(lat2), 2), round(math.degrees(lon2), 2)


def projection_steps(data: dict) -> list[dict]:
    lat = float(data["latitude"])
    lon = float(data["longitude"])
    speed = float(data.get("speed_kmh", 22))
    bearing = float(data.get("movement_bearing_deg", 280))
    steps = []
    for h in range(0, 25, 3):
        la, lo = destination(lat, lon, bearing, speed * h)
        steps.append({"h": h, "lat": la, "lon": lo})
    return steps


def js_steps(steps: list[dict]) -> str:
    lines = ["  const steps = ["]
    for i, s in enumerate(steps):
        comma = "," if i < len(steps) - 1 else ""
        lines.append(f"    {{h:{s['h']}, lat:{s['lat']:.2f}, lon:{s['lon']:.2f}}}{comma}")
    lines.append("  ];")
    return "\n".join(lines)


def patch_index(data: dict) -> None:
    if not INDEX_FILE.exists():
        raise RuntimeError("index.html introuvable à la racine du dépôt")

    content = INDEX_FILE.read_text(encoding="utf-8")
    system = data.get("system", "PHÉNOMÈNE")
    p48 = data.get("development_48h")
    movement = data.get("movement", "O / ONO")

    # Update visible values.
    if p48 is not None:
        content = re.sub(
            r'(<div class="metric"><strong>)\d+%(</strong><span>développement NHC</span>)',
            rf'\g<1>{int(p48)}%\2',
            content,
            count=1
        )

    content = re.sub(
        r'(<div class="metric"><strong>)[^<]+(</strong><span>direction générale</span>)',
        rf'\g<1>{movement}\2',
        content,
        count=1
    )

    # Hero title and timeline title references.
    content = re.sub(r"<h2>[^<]+</h2>", f"<h2>{system}</h2>", content, count=1)
    content = content.replace("VENT + 92L • MÊME HORLOGE", f"VENT + {system} • MÊME HORLOGE")

    # Marker tooltip system name.
    content = re.sub(
        r"\.bindTooltip\('[^']+ • H\+0'",
        f".bindTooltip('{system} • H+0'",
        content,
        count=1
    )
    content = re.sub(
        r"stormMarker\.setTooltipContent\('[^']+ • H\+'\+displayHour\)",
        f"stormMarker.setTooltipContent('{system} • H+'+displayHour)",
        content
    )
    content = re.sub(
        r"stormMarker\.setTooltipContent\('[^']+ • H\+24'\)",
        f"stormMarker.setTooltipContent('{system} • H+24')",
        content
    )
    content = re.sub(
        r"stormMarker\.setTooltipContent\('[^']+ • H\+0'\)",
        f"stormMarker.setTooltipContent('{system} • H+0')",
        content
    )

    # Replace indicative 24h step array.
    new_steps = js_steps(projection_steps(data))
    content, n = re.subn(
        r"  const steps = \[\s*.*?\s*\];",
        new_steps,
        content,
        count=1,
        flags=re.S
    )
    if n != 1:
        log("⚠️ tableau 'steps' non trouvé dans index.html; trajectoire non patchée")

    INDEX_FILE.write_text(content, encoding="utf-8")


def main() -> int:
    existing = load_existing()
    data = dict(existing)

    try:
        systems = fetch_gtwo()
        candidates = [s for s in systems if s.get("development_48h") is not None]

        if candidates:
            chosen = max(candidates, key=lambda x: x.get("development_48h") or 0)
            data.update({
                "source": "NHC Graphical Tropical Weather Outlook",
                "latitude": chosen["latitude"],
                "longitude": chosen["longitude"],
                "development_48h": chosen.get("development_48h"),
                "development_7d": chosen.get("development_7d"),
                "nhc_label": chosen.get("name"),
                "nhc_description": chosen.get("description"),
            })
            log(
                f"Perturbation NHC retenue: {chosen['name']} "
                f"({chosen.get('development_48h')}% / 48 h)"
            )
        else:
            log("Aucune perturbation NHC avec probabilité chiffrée trouvée; valeurs existantes conservées.")

    except Exception as exc:
        # Never destroy a valid public dashboard because a source is temporarily unreachable.
        log(f"⚠️ NHC indisponible ou format inattendu: {exc}")
        log("Conservation des dernières données connues.")

    data.setdefault("system", "92L")
    data.setdefault("status", "Invest")
    data.setdefault("latitude", 10.8)
    data.setdefault("longitude", -41.0)
    data.setdefault("development_48h", 80)
    data.setdefault("development_7d", 80)
    data.setdefault("movement", "O / ONO")
    data.setdefault("movement_bearing_deg", 280)
    data.setdefault("speed_kmh", 22)

    # Local vigilance stays deliberately independent from NHC.
    data.setdefault("local_vigilance", "none")
    data.setdefault(
        "local_vigilance_label",
        "AUCUNE VIGILANCE CYCLONIQUE EN COURS"
    )

    data["generated_at"] = dt.datetime.now(TZ).isoformat(timespec="seconds")

    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    patch_index(data)

    log("✅ data/cyclone.json et index.html mis à jour")
    return 0


if __name__ == "__main__":
    sys.exit(main())
