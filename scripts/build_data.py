#!/usr/bin/env python3
"""Download shelters, metro stations and bunkers for European countries from the
Overpass API and write them as 1x1 degree tiles under data/.

Each tile is a JSON array of records:
  [lat, lon, kind, name, bunker_type, start_date, operator, access, abandoned, osm_id]
kind: "s" shelter, "m" metro station, "b" historic/other bunker.
"""
import json, os, re, socket, sys, time, urllib.parse, urllib.request

# GitHub runners have no IPv6 route and overpass-api.de publishes IPv6 first: use IPv4 only.
_gai = socket.getaddrinfo
socket.getaddrinfo = lambda host, port, family=0, *a, **k: _gai(host, port, socket.AF_INET, *a, **k)
from collections import defaultdict
from datetime import datetime, timezone

ENDPOINTS = ["https://overpass-api.de/api/interpreter",
             "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
             "https://overpass.private.coffee/api/interpreter"]
COUNTRIES = ("AL AD AT BA BE BG BY CH CY CZ DE DK EE ES FI FR GB GR HR HU IE IS IT "
             "LI LT LU LV MC MD ME MK MT NL NO PL PT RO RS SE SI SK SM UA VA XK").split()
HIDDEN = {"missile_silo"}
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")


STATEMENTS = [
    # places to take cover
    'nwr["amenity"="shelter"]["shelter_type"="bomb_shelter"]{a};'
    'nwr["bunker_type"~"^(bomb_shelter|personnel_shelter)$"]{a};'
    'nwr["station"="subway"]{a};'
    'nwr["railway"="station"]["subway"="yes"]{a};',
    # historic and other bunkers
    'nwr["military"="bunker"]{a};'
    'nwr["building"="bunker"]{a};'
    'nwr["disused:military"="bunker"]{a};',
]


def query(cc, stmt):
    # One tag per request keeps each query short: the public servers cut requests after ~1-2 minutes.
    return (f'[out:json][timeout:400];'
            f'area["ISO3166-1"="{cc}"][admin_level=2]->.a;'
            f'({stmt.format(a="(area.a)")});out center tags;')


def fetch(q):
    body = urllib.parse.urlencode({"data": q}).encode()
    last = None
    for attempt in range(3):
        for ep in ENDPOINTS:
            try:
                req = urllib.request.Request(ep, data=body, headers={"User-Agent": "karta-ubezhishta/1.0 (github.com/Dimana200/karta-ubezhishta)"})
                with urllib.request.urlopen(req, timeout=660) as r:
                    js = json.load(r)
                # On timeout or memory errors Overpass still answers 200, with an empty result and a "remark".
                remark = js.get("remark") or ""
                if re.search(r"error|timed out|out of memory", remark, re.I):
                    raise RuntimeError("server remark: " + remark[:160])
                return js
            except Exception as e:  # noqa: BLE001 - retry on any network/server error
                last = e
                print(f"   {ep}: {e}", flush=True)
                time.sleep(30)
    raise RuntimeError(f"all endpoints failed: {last}")


def classify(t):
    if t.get("station") == "subway" or (t.get("railway") == "station" and t.get("subway") == "yes"):
        if t.get("railway") in ("station", "halt") or t.get("public_transport") == "station":
            return "m"
    if (t.get("amenity") == "shelter" and t.get("shelter_type") == "bomb_shelter") or \
       t.get("bunker_type") in ("bomb_shelter", "personnel_shelter"):
        return "s"
    if "bunker" in (t.get("military"), t.get("building"), t.get("disused:military"), t.get("abandoned:military")):
        return None if t.get("bunker_type") in HIDDEN else "b"
    return None


def abandoned(t):
    if t.get("disused") == "yes" or t.get("abandoned") == "yes" or t.get("ruins") == "yes" or t.get("historic") == "ruins":
        return 1
    return 1 if any(re.match(r"^(disused|abandoned|ruins|demolished):", k) for k in t) else 0


def records(js):
    for el in js.get("elements", []):
        lat = el.get("lat", (el.get("center") or {}).get("lat"))
        lon = el.get("lon", (el.get("center") or {}).get("lon"))
        if lat is None or lon is None:
            continue
        t = el.get("tags") or {}
        k = classify(t)
        if not k:
            continue
        name = t.get("name") or t.get("official_name") or t.get("ref") or ""
        yield (f'{el["type"][0]}{el["id"]}',
               [round(lat, 5), round(lon, 5), k, name, t.get("bunker_type", ""),
                t.get("start_date") or t.get("construction_date") or "",
                t.get("operator", ""), t.get("access", ""), abandoned(t), f'{el["type"]}/{el["id"]}'])


def main():
    """Download each country into data/countries/XX.json, then rebuild all tiles from every
    country file. A country that fails keeps its previous file, so a bad run never empties the map."""
    args = sys.argv[1:]
    fetch_only = "--fetch-only" in args
    tiles_only = "--tiles-only" in args
    shard = next((a.split("=", 1)[1] for a in args if a.startswith("--shard=")), None)
    only = [a for a in args if not a.startswith("--")] or COUNTRIES
    if shard:
        k, n = map(int, shard.split("/"))
        only = only[k::n]
    if tiles_only:
        only = []
    cdir = os.path.join(OUT, "countries")
    os.makedirs(cdir, exist_ok=True)
    failed = []
    for cc in only:
        print(f"{cc} ...", flush=True)
        elements, ok = [], True
        for stmt in STATEMENTS:
            t0 = time.time()
            try:
                part = fetch(query(cc, stmt)).get("elements", [])
            except Exception as e:  # noqa: BLE001
                print(f"   FAILED {cc}: {e}", flush=True)
                ok = False
                break
            print(f"   group {STATEMENTS.index(stmt) + 1}: {len(part)} in {time.time() - t0:.0f}s", flush=True)
            elements += part
            time.sleep(3)
        if not ok:
            failed.append(cc)
            continue
        recs = dict(records({"elements": elements}))
        old_file = os.path.join(cdir, cc + ".json")
        if os.path.exists(old_file):
            old_n = len(json.load(open(old_file, encoding="utf-8")))
            if old_n >= 50 and len(recs) < old_n * 0.5:
                print(f"   SUSPICIOUS {cc}: {len(recs)} now vs {old_n} before, keeping old data", flush=True)
                failed.append(cc)
                continue
        with open(os.path.join(cdir, cc + ".json"), "w", encoding="utf-8") as fh:
            json.dump(list(recs.items()), fh, ensure_ascii=False, separators=(",", ":"))
        n = {"s": 0, "m": 0, "b": 0}
        for r in recs.values():
            n[r[2]] += 1
        print(f"   {n}", flush=True)
    if failed:
        with open(os.path.join(cdir, "_failed_" + "_".join(failed) + ".txt"), "w") as fh:
            fh.write(" ".join(failed))
    if fetch_only:
        print("fetched; failed:", failed)
        return

    failed = sorted({c for f in os.listdir(cdir) if f.startswith("_failed_") for c in open(os.path.join(cdir, f)).read().split()})
    for f in os.listdir(cdir):
        if f.startswith("_failed_"):
            os.remove(os.path.join(cdir, f))
    seen, tiles, stats = set(), defaultdict(list), {}
    for f in sorted(os.listdir(cdir)):
        if not f.endswith(".json"):
            continue
        cc = f[:-5]
        n = {"s": 0, "m": 0, "b": 0}
        for oid, rec in json.load(open(os.path.join(cdir, f), encoding="utf-8")):
            if oid in seen:
                continue
            seen.add(oid)
            n[rec[2]] += 1
            tiles[f"{int(rec[0] // 1)}_{int(rec[1] // 1)}"].append(rec)
        stats[cc] = n
    if not tiles:
        print("no data at all"); sys.exit(1)
    tdir = os.path.join(OUT, "tiles")
    os.makedirs(tdir, exist_ok=True)
    for f in os.listdir(tdir):
        os.remove(os.path.join(tdir, f))
    index = {}
    for key, recs in tiles.items():
        with open(os.path.join(tdir, key + ".json"), "w", encoding="utf-8") as fh:
            json.dump(recs, fh, ensure_ascii=False, separators=(",", ":"))
        index[key] = len(recs)
    meta = {"generated": datetime.now(timezone.utc).strftime("%Y-%m-%d"), "countries": stats,
            "failed": failed, "tiles": index}
    with open(os.path.join(OUT, "index.json"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False, separators=(",", ":"))
    print("done:", sum(index.values()), "places in", len(index), "tiles; failed:", failed)


if __name__ == "__main__":
    main()
