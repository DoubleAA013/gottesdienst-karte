#!/usr/bin/env python3
"""Holt die Gottesdienst-Termine vom Erzbistum München und Freising und baut data.json."""
import json, math, re, sys, time, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

FEED = "https://www.erzbistum-muenchen.de/worships/api/3ede8334e03c206b55f178bd210e5c3c"
ARC = "https://services1.arcgis.com/fX1IStQAZuWD7MLP/arcgis/rest/services/"
DAYS_AHEAD = 14
# Feste Korrekturen, wenn die Bistumskarte einen veralteten Link liefert
URL_OVERRIDES = {
    "München-St. Peter": "https://alterpeter.de/",
}
UA = {"User-Agent": "Mozilla/5.0 (compatible; gottesdienst-karte; +https://github.com/DoubleAA013/gottesdienst-karte)"}


def get_json(url, tries=4):
    for n in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            print("Fehler bei", url[:90], e, file=sys.stderr)
            time.sleep(3 * (n + 1))
    raise SystemExit("Abruf fehlgeschlagen: " + url[:90])


now = datetime.now(ZoneInfo("Europe/Berlin"))
base = now.date()
limit = (base + timedelta(days=DAYS_AHEAD)).isoformat()

# 1) Termine holen
items, page = [], 1
while True:
    j = get_json(f"{FEED}?_limit=300&_page={page}&_sort=date&_sort_direction=asc")
    stop = False
    for i in j["items"]:
        d = (i.get("date_start") or "")[:10]
        if d >= limit:
            stop = True
            break
        if d >= base.isoformat():
            items.append(i)
    if stop or len(j["items"]) < 300 or page > 40:
        break
    page += 1
print("Termine im Zeitraum:", len(items))

# 2) Gebiet: München, Korridor Unterschleißheim-Freising, Freising, Erding, Umgebung
KX, KY = 111.32 * math.cos(math.radians(48.2)), 111.32
xy = lambda la, ln: ((ln - 11.6) * KX, (la - 48.2) * KY)
dist = lambda a, b: math.hypot(a[0] - b[0], a[1] - b[1])


def segd(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    t = ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / (dx * dx + dy * dy)
    t = max(0, min(1, t))
    return dist(p, (a[0] + t * dx, a[1] + t * dy))


M, U, F, E, H = xy(48.137, 11.575), xy(48.28, 11.57), xy(48.40, 11.745), xy(48.306, 11.906), xy(48.328, 11.752)


def in_area(la, ln):
    p = xy(la, ln)
    return (dist(p, M) <= 13 or dist(p, U) <= 7 or dist(p, F) <= 6 or dist(p, E) <= 6 or dist(p, H) <= 4
            or segd(p, M, U) <= 5 or segd(p, U, F) <= 5 or segd(p, M, E) <= 5 or segd(p, F, E) <= 4)


cancel = re.compile(r"entfällt|entfaellt|abgesagt|findet nicht statt|^keine\b|fällt aus", re.I)
kept = []
for i in items:
    loc = i.get("location") or {}
    ml = loc.get("map_location") or {}
    if ml.get("latitude") is None or not in_area(ml["latitude"], ml["longitude"]):
        continue
    t = (i.get("title") or "").strip()
    if cancel.search(t) or re.match(r"tauf", t, re.I):
        continue
    if (i["date_start"][11:16] == "00:00") and not i.get("end_date"):
        continue
    kept.append(i)
print("Im Gebiet:", len(kept))

# 3) Pfarreien aus der Bistumskarte (ArcGIS)
def pull(url, fields):
    out, off = [], 0
    while True:
        q = urllib.parse.urlencode({"f": "json", "where": "1=1", "returnGeometry": "false", "outFields": fields,
                                    "resultRecordCount": 1000, "resultOffset": off})
        j = get_json(url + "/query?" + q)
        out += [f["attributes"] for f in j["features"]]
        if len(j["features"]) < 1000:
            return out
        off += 1000


churches = pull(ARC + "AGOL_EOM_EINRICHTUNGEN/FeatureServer/15", "KIRCHENNAME,PFARREINAME,PVNAME,PV_S_Z,TEXT_ART,POINT_X,POINT_Y")
parishes = pull(ARC + "AGOL_EOM_GEBIETE/FeatureServer/0", "PFARREINAME,PV_NAME,PV_TYP,INTERNET")


def norm_url(s):
    if not s:
        return ""
    parts = re.split(r"[\s;,]", str(s).strip())
    s = parts[0] if parts else ""
    if not s:
        return ""
    if not re.match(r"^https?://", s, re.I):
        s = "https://" + s
    return s if re.match(r"^https?://[^/ ]+\.[a-z]{2,}", s, re.I) else ""


by_name, pv_url, pv_typ = {}, {}, {}
for p in parishes:
    u = norm_url(p.get("INTERNET"))
    if p.get("PFARREINAME") and u and p["PFARREINAME"] not in by_name:
        by_name[p["PFARREINAME"]] = u
    if p.get("PV_NAME") and u and p["PV_NAME"] not in pv_url:
        pv_url[p["PV_NAME"]] = u
    if p.get("PV_NAME"):
        pv_typ[p["PV_NAME"]] = p.get("PV_TYP")

strip_re = re.compile(r"^(Pfarrkirche|Nebenkirche|Filialkirche|Kuratiekirche|Wallfahrtskirche|Klosterkirche|Privatkapelle|Kapelle|Kirche)\s+")
strip = lambda n: re.sub(r"\s+", " ", strip_re.sub("", n)).strip()
dkm = lambda la1, ln1, la2, ln2: math.hypot((ln1 - ln2) * KX, (la1 - la2) * KY)

places, pl, parish_arr, pa, parish_pv = [], {}, [], {}, {}
matched = 0
for i in kept:
    L = i["location"]
    if L["id"] in pl:
        continue
    la, ln = L["map_location"]["latitude"], L["map_location"]["longitude"]
    best, bd = None, 0.15
    for c in churches:
        if c.get("POINT_Y") is None:
            continue
        dd = dkm(la, ln, c["POINT_Y"], c["POINT_X"])
        if dd < bd:
            bd, best = dd, c
    if best and best.get("PFARREINAME"):
        matched += 1
        key = best["PFARREINAME"]
        if key not in pa:
            url = by_name.get(key) or pv_url.get(best.get("PVNAME")) or ""
            pv = best.get("PVNAME")
            label = ((pv_typ.get(pv) or "PV") + " " + pv) if pv and pv != key else ""
            pa[key] = len(parish_arr)
            parish_arr.append([key, label, URL_OVERRIDES.get(key, url)])
            parish_pv[key] = pv_url.get(pv) or ""
        pi = pa[key]
    else:
        key = "~" + (i.get("organizer") or "")
        if key not in pa:
            pa[key] = len(parish_arr)
            parish_arr.append([i.get("organizer") or "", " ", ""])
        pi = pa[key]
    pl[L["id"]] = len(places)
    addr = ", ".join(x for x in [L.get("address_line_one"), " ".join(x for x in [L.get("postal_code"), L.get("city")] if x)] if x)
    places.append([strip(L["name"]), round(la, 5), round(ln, 5), addr, pi])
print("Kirchen:", len(places), "Pfarrei zugeordnet:", matched)


# Tote Links (404/410) aussortieren: erst Pfarrverbands-Link versuchen, sonst leer lassen
def alive(url):
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status < 400
    except urllib.error.HTTPError as e:
        return e.code not in (404, 410)
    except Exception:
        return True  # Netzwerk-/SSL-Fehler: Link lieber behalten


from concurrent.futures import ThreadPoolExecutor
check = {u for p in parish_arr for u in [p[2], parish_pv.get(p[0], "")] if u}
with ThreadPoolExecutor(8) as ex:
    ok = dict(zip(check, ex.map(alive, check)))
dead = 0
for p in parish_arr:
    if p[2] and not ok.get(p[2]):
        dead += 1
        alt = parish_pv.get(p[0], "")
        print("Toter Link:", p[0], p[2], "->", alt if alt and ok.get(alt) and alt != p[2] else "(keiner)")
        p[2] = alt if alt and ok.get(alt) and alt != p[2] else ""
print("Tote Links ersetzt/entfernt:", dead)

# 4) Termine kompakt
titles, ti, ev = [], {}, []
hhmm = lambda s: int(s[11:13] + s[14:16])
for i in kept:
    t = re.sub(r"\s+", " ", i["title"]).strip()[:100]
    if t not in ti:
        ti[t] = len(titles)
        titles.append(t)
    day = (datetime.strptime(i["date_start"][:10], "%Y-%m-%d").date() - base).days
    row = [pl[i["location"]["id"]], day, hhmm(i["date_start"]), hhmm(i["end_date"]) if i.get("end_date") else 0, ti[t]]
    note = re.sub(r"\s+", " ", re.sub(r"<[^>]*>", " ", i.get("excerpt") or "")).strip()[:120]
    if note and note != t:
        row.append(note)
    ev.append(row)
ev.sort(key=lambda r: (r[1], r[2]))
print("Termine:", len(ev))

if len(ev) < 100 or len(places) < 30:
    raise SystemExit("Zu wenige Daten - data.json bleibt unverändert.")

data = {"generated": now.strftime("%Y-%m-%d"), "time": now.strftime("%H:%M"), "base": base.isoformat(),
        "places": places, "parishes": parish_arr, "titles": titles, "events": ev}
with open("data.json", "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
print("data.json geschrieben")
