#!/usr/bin/env python3
"""
Nova Screen — actualisation du dashboard cyclonique.

Objectif:
- exécution automatique à 06:00 et 12:00 heure de Saint-Barth;
- récupération de la situation publique Météo-Tropicale;
- conservation des dernières données valides si la source est momentanément indisponible;
- mise à jour de data/cyclone.json;
- mise à jour du cartouche "ACTUALISÉ" dans index.html.

IMPORTANT:
Le dashboard public reste volontairement prudent. Le script ne crée pas de
"menace" à partir d'une simple perturbation. Les systèmes sont présentés comme
des systèmes suivis, avec leurs probabilités de développement lorsqu'elles
peuvent être extraites de la source.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent
INDEX = ROOT / "index.html"
DATA = ROOT / "data" / "cyclone.json"
URL = "https://www.meteo-tropicale.fr/dashboard/?PT=459"
TZ = ZoneInfo("America/St_Barthelemy")
UA = "NovaScreen-SaintBarthelemy/1.0"

def load_previous():
    if DATA.exists():
        return json.loads(DATA.read_text(encoding="utf-8"))
    return {"territory":"Saint-Barthélemy","alert_level":"VERT","threat":"AUCUNE","systems":[]}

def fetch_text():
    r = requests.get(URL, timeout=35, headers={"User-Agent": UA})
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    return " ".join(soup.stripped_strings)

def parse_systems(text):
    systems = []
    # Capture INVEST identifiers and nearby text. The parser is intentionally
    # permissive because presentation wording can change.
    ids = list(re.finditer(r"\b(?:INVEST\s*)?(\d{2}L)\b", text, re.I))
    for i, m in enumerate(ids):
        sid = m.group(1).upper()
        start = max(0, m.start()-150)
        end = ids[i+1].start() if i+1 < len(ids) else min(len(text), m.end()+1200)
        chunk = text[start:end]

        lat = lon = None
        coord = re.search(r"(\d{1,2}(?:[.,]\d+)?)\s*°?\s*N\s*[/, ]+\s*(\d{1,3}(?:[.,]\d+)?)\s*°?\s*W", chunk, re.I)
        if coord:
            lat = float(coord.group(1).replace(",", "."))
            lon = -float(coord.group(2).replace(",", "."))

        # Look for probabilities associated with 48 h and 7 days.
        r48 = None
        r7 = None
        p = re.search(r"48\s*h(?:eures?)?[^0-9%]{0,80}(\d{1,3})\s*%", chunk, re.I)
        if not p:
            p = re.search(r"(\d{1,3})\s*%[^.]{0,80}48\s*h", chunk, re.I)
        if p: r48 = int(p.group(1))

        p = re.search(r"7\s*j(?:ours?)?[^0-9%]{0,80}(\d{1,3})\s*%", chunk, re.I)
        if not p:
            p = re.search(r"(\d{1,3})\s*%[^.]{0,80}7\s*j", chunk, re.I)
        if p: r7 = int(p.group(1))

        if sid not in {x["id"] for x in systems}:
            systems.append({"id":sid,"lat":lat,"lon":lon,"risk_48h":r48,"risk_7d":r7})
    return systems

def patch_update_stamp(now):
    s = INDEX.read_text(encoding="utf-8")
    stamp = now.strftime("%d/%m/%Y • %H:%M")
    # Make the visible initial value current even before JS runs.
    s = re.sub(
        r'(<div class="update" id="updateTime">).*?(</div>)',
        rf'\1ACTUALISÉ • {stamp}\2',
        s, count=1
    )
    INDEX.write_text(s, encoding="utf-8")

def main():
    previous = load_previous()
    now = dt.datetime.now(TZ)
    result = dict(previous)
    result["updated_at"] = now.isoformat(timespec="seconds")
    result["source"] = URL

    try:
        text = fetch_text()
        low = text.lower()

        # Only use explicit reassuring/alert wording from the source.
        if "aucun cyclone ne menace" in low:
            result["threat"] = "AUCUNE"

        # Conservative alert-level extraction.
        m = re.search(r"\bniveau\s+(vert|jaune|orange|rouge|violet)\b", text, re.I)
        if m:
            result["alert_level"] = m.group(1).upper()

        parsed = parse_systems(text)
        # Replace the list only when parsing found systems. If the source explicitly
        # states no system is being monitored, allow an empty list.
        if parsed:
            result["systems"] = parsed
        elif re.search(r"aucun\s+(?:système|phenom[eè]ne|phénomène).{0,50}(?:surveill|suivi)", low):
            result["systems"] = []

        result["fetch_status"] = "ok"
    except Exception as exc:
        result["fetch_status"] = "fallback_last_known"
        result["fetch_error"] = str(exc)[:300]

    DATA.parent.mkdir(parents=True, exist_ok=True)
    DATA.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    patch_update_stamp(now)
    print(json.dumps(result, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
