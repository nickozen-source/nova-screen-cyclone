#!/usr/bin/env python3
from __future__ import annotations
import datetime as dt, io, json, re, zipfile
from pathlib import Path
from zoneinfo import ZoneInfo
import requests
from bs4 import BeautifulSoup

ROOT=Path(__file__).resolve().parent
DATA=ROOT/"data"/"cyclone.json"
TZ=ZoneInfo("America/St_Barthelemy")
MT="https://www.meteo-tropicale.fr/"
SXM="https://www.sxmcyclone.com/"
NHC="https://www.nhc.noaa.gov/"
GTWO="https://www.nhc.noaa.gov/xgtwo/gtwo_atl.kmz"
UA="NovaScreen-SaintBarthelemy/2.0"

def get_text(url):
    r=requests.get(url,timeout=35,headers={"User-Agent":UA,"Cache-Control":"no-cache"})
    r.raise_for_status()
    return " ".join(BeautifulSoup(r.text,"html.parser").stripped_strings)

def explicit_no_systems(text):
    t=text.lower()
    return (
        "il n’y a pas de cyclone ni de système surveillé" in t or
        "il n'y a pas de cyclone ni de système surveillé" in t or
        "there are no tropical cyclones in the atlantic at this time" in t
    )

def parse_meteo_tropicale(text):
    systems=[]
    pat=re.compile(r"\b(?:INVEST\s+)?(\d{2}L)\b.{0,120}?(\d{1,2}(?:[.,]\d+)?)\s*N.{0,40}?(-?\d{1,3}(?:[.,]\d+)?)\s*W?",re.I)
    for m in pat.finditer(text):
        sid=m.group(1).upper()
        lat=float(m.group(2).replace(",","."))
        lon=float(m.group(3).replace(",","."))
        if lon>0: lon=-lon
        chunk=text[m.start():m.start()+700]
        p48=p7=None
        x=re.search(r"48\s*h.{0,80}?(\d{1,3})\s*%",chunk,re.I) or re.search(r"(\d{1,3})\s*%.{0,80}?48\s*h",chunk,re.I)
        if x:p48=int(x.group(1))
        x=re.search(r"7\s*j(?:ours?)?.{0,80}?(\d{1,3})\s*%",chunk,re.I) or re.search(r"(\d{1,3})\s*%.{0,80}?7\s*j",chunk,re.I)
        if x:p7=int(x.group(1))
        if sid not in {s["id"] for s in systems}:
            systems.append({"id":sid,"lat":lat,"lon":lon,"risk_48h":p48,"risk_7d":p7})
    return systems

def parse_gtwo():
    r=requests.get(GTWO,timeout=35,headers={"User-Agent":UA})
    r.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        kml=next((n for n in z.namelist() if n.lower().endswith(".kml")),None)
        if not kml:return []
        raw=z.read(kml).decode("utf-8","replace")
    systems=[];seq=1
    for pm in re.findall(r"<Placemark\b.*?</Placemark>",raw,re.I|re.S):
        clean=" ".join(BeautifulSoup(pm,"html.parser").stripped_strings)
        if not re.search(r"formation chance|disturbance|invest",clean,re.I):continue
        c=re.search(r"<Point>.*?<coordinates>\s*(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)",pm,re.I|re.S)
        if not c:continue
        lon,lat=float(c.group(1)),float(c.group(2))
        nm=re.search(r"\b(\d{2}L)\b",clean,re.I)
        sid=nm.group(1).upper() if nm else f"SYSTÈME {seq}";seq+=1
        p48=p7=None
        m=re.search(r"48\s*hours[^0-9]{0,60}(\d{1,3})\s*percent",clean,re.I)
        if m:p48=int(m.group(1))
        m=re.search(r"7\s*days?[^0-9]{0,60}(\d{1,3})\s*percent",clean,re.I)
        if m:p7=int(m.group(1))
        systems.append({"id":sid,"lat":lat,"lon":lon,"risk_48h":p48,"risk_7d":p7})
    return systems

def main():
    now=dt.datetime.now(TZ)
    result={
      "updated_at":now.isoformat(timespec="seconds"),
      "territory":"Saint-Barthélemy",
      "alert_level":"VERT","threat":"AUCUNE","systems":[],
      "sources":{"meteo_tropicale":MT,"sxmcyclone":SXM,"nhc":NHC}
    }
    errors=[];mt=nhc=sxm=""
    try:mt=get_text(MT)
    except Exception as e:errors.append("MT: "+str(e))
    try:nhc=get_text(NHC)
    except Exception as e:errors.append("NHC: "+str(e))
    try:sxm=get_text(SXM)
    except Exception as e:errors.append("SXM: "+str(e))

    if explicit_no_systems(mt) or explicit_no_systems(nhc):
        result["systems"]=[]
        result["status"]="confirmed_no_systems"
    else:
        systems=parse_meteo_tropicale(mt) if mt else []
        if not systems:
            try:systems=parse_gtwo()
            except Exception as e:errors.append("GTWO: "+str(e))
        result["systems"]=systems
        result["status"]="systems_found" if systems else "no_system_detected"

    if "aucun phénomène majeur ne menace les antilles" in sxm.lower():
        result["threat"]="AUCUNE"

    result["fetch_errors"]=errors
    DATA.parent.mkdir(parents=True,exist_ok=True)
    DATA.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=="__main__":
    main()
