import hashlib
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from functools import wraps
from zoneinfo import ZoneInfo

import caldav
from caldav.elements import ical
import icalendar
import recurring_ical_events
import requests
from flask import Flask, Response, jsonify, request, send_from_directory

CONFIG = os.environ.get("CONFIG", "/data/config.json")
TZ = ZoneInfo(os.environ.get("TZ", "Europe/Berlin"))
CACHE_SECONDS = int(os.environ.get("CACHE_SECONDS", "300"))
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
MASK = "********"

app = Flask(__name__, static_folder="static", static_url_path="")
lock = threading.Lock()
cache = {}  # monday iso -> (timestamp, data)


def load():
    try:
        with open(CONFIG) as f:
            return json.load(f)
    except FileNotFoundError:
        return {"persons": [], "hidden": [], "dayStart": 7, "dayEnd": 22}


def save(cfg):
    tmp = CONFIG + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    os.replace(tmp, CONFIG)  # atomic, so a crash never leaves a half-written config


def sid(*parts):
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:12]


def local(v):
    """date/datetime -> naive local datetime."""
    if not isinstance(v, datetime):
        return datetime(v.year, v.month, v.day)
    if v.tzinfo is None:
        return v  # floating time = local time
    return v.astimezone(TZ).replace(tzinfo=None)


def events_from_ical(text, start, end, src):
    out = []
    cal = icalendar.Calendar.from_ical(text)
    for ev in recurring_ical_events.of(cal).between(start, end):
        s = ev["DTSTART"].dt
        if "DTEND" in ev:
            e = ev["DTEND"].dt
        elif "DURATION" in ev:
            e = s + ev["DURATION"].dt
        else:
            e = s + timedelta(days=1) if not isinstance(s, datetime) else s
        out.append({
            "src": src,
            "title": str(ev.get("SUMMARY", "")),
            "location": str(ev.get("LOCATION", "")),
            "start": local(s).isoformat(),
            "end": local(e).isoformat(),
            "allDay": not isinstance(s, datetime),
        })
    return out


def fetch_caldav(person, acc, start, end):
    client = caldav.DAVClient(url=acc["url"], username=acc.get("username") or None,
                              password=acc.get("password") or None, timeout=20)
    try:
        cals = client.principal().calendars()
    except Exception:
        cals = [caldav.Calendar(client=client, url=acc["url"])]  # URL points at a single calendar
    sources, events = [], []
    for cal in cals:
        src = {"id": sid(person["name"], str(cal.url), acc.get("label", "")), "person": person["name"],
               "name": cal.name or acc.get("label") or "Kalender"}
        try:  # server colors come as #RRGGBB or #RRGGBBAA (Apple/Nextcloud)
            c = cal.get_property(ical.CalendarColor()) or ""
            if re.fullmatch(r"#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?", c.strip()):
                src["color"] = c.strip()[:7]
        except Exception:
            pass
        try:
            for obj in cal.search(start=start, end=end, event=True):
                events += events_from_ical(obj.data, start, end, src["id"])
        except Exception as ex:
            src["error"] = str(ex)
        sources.append(src)
    return sources, events


def fetch_ics(person, feed, start, end):
    url = feed["url"].replace("webcal://", "https://", 1)
    src = {"id": sid(person["name"], feed["url"], feed.get("label", "")), "person": person["name"], "name": feed.get("label") or "Feed"}
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    return [src], events_from_ical(r.content, start, end, src["id"])


def collect(monday):
    start = datetime.combine(monday, datetime.min.time(), TZ)
    end = start + timedelta(days=7)
    cfg = load()
    jobs = []
    for p in cfg["persons"]:
        jobs += [(fetch_caldav, p, a) for a in p.get("caldav", [])]
        jobs += [(fetch_ics, p, f) for f in p.get("ics", [])]

    def run(job):
        fn, p, item = job
        try:
            return fn(p, item, start, end)
        except Exception as ex:
            return [{"id": sid(p["name"], item.get("url", ""), item.get("label", "")), "person": p["name"],
                     "name": item.get("label") or item.get("url", ""), "error": str(ex)}], []

    sources, events = [], []
    with ThreadPoolExecutor(8) as pool:
        for s, e in pool.map(run, jobs):
            sources += s
            events += e
    return {"sources": sources, "events": events}


def cached_week(monday, refresh):
    key = monday.isoformat()
    with lock:
        hit = cache.get(key)
    if not hit or time.time() - hit[0] > CACHE_SECONDS or refresh:
        hit = (time.time(), collect(monday))
        with lock:
            cache[key] = hit
    return hit[1]


@app.get("/api/week")
def week():
    d = date.fromisoformat(request.args.get("date") or date.today().isoformat())
    monday = d - timedelta(days=d.weekday())
    weeks = max(1, min(4, int(request.args.get("weeks", 1))))
    sources, events, seen = {}, [], set()
    for i in range(weeks):
        w = cached_week(monday + timedelta(weeks=i), request.args.get("refresh"))
        for src in w["sources"]:
            if src["id"] not in sources or src.get("error"):
                sources[src["id"]] = src
        for e in w["events"]:  # events crossing a week boundary come back from both weeks
            k = (e["src"], e["title"], e["start"])
            if k not in seen:
                seen.add(k)
                events.append(e)
    cfg = load()
    return jsonify({
        "sources": list(sources.values()),
        "events": events,
        "monday": monday.isoformat(),
        "persons": [{"name": p["name"], "color": p.get("color", "#4f8cff"),
                     "inDay": p.get("inDay", True)} for p in cfg["persons"]],
        "hidden": cfg.get("hidden", []),
        "colors": cfg.get("colors", {}),
        "names": cfg.get("names", {}),
        "hiddenDay": cfg.get("hiddenDay", []),
        "hiddenUp": cfg.get("hiddenUp", []),
        "dayStart": cfg.get("dayStart", 7),
        "dayEnd": cfg.get("dayEnd", 22),
    })


@app.post("/api/hidden")
def hidden():
    cfg = load()
    body = request.get_json(force=True)
    lst = body.get("list", "hidden")
    if lst not in ("hidden", "hiddenDay"):
        return jsonify(error="unknown list"), 400
    cfg[lst] = [str(x) for x in body.get("ids", [])]
    save(cfg)
    return jsonify(ok=True)


def admin(fn):
    @wraps(fn)
    def wrap(*a, **kw):
        auth = request.authorization
        if ADMIN_PASSWORD and not (auth and auth.password == ADMIN_PASSWORD):
            return Response("Login nötig", 401, {"WWW-Authenticate": 'Basic realm="center-view"'})
        return fn(*a, **kw)
    return wrap


@app.get("/settings")
@admin
def settings_page():
    return send_from_directory(app.static_folder, "settings.html")


@app.get("/api/settings")
@admin
def get_settings():
    cfg = load()
    for p in cfg["persons"]:
        for a in p.get("caldav", []):
            if a.get("password"):
                a["password"] = MASK  # never send stored passwords to the browser
    return jsonify(cfg)


@app.post("/api/settings")
@admin
def post_settings():
    new = request.get_json(force=True)
    old = load()
    known = {(a.get("url"), a.get("username")): a.get("password", "")
             for p in old["persons"] for a in p.get("caldav", [])}
    persons = []
    for p in new.get("persons", []):
        name = str(p.get("name", "")).strip()
        if not name:
            continue
        cal = []
        for a in p.get("caldav", []):
            if not a.get("url"):
                continue
            pw = a.get("password", "")
            if pw == MASK:
                pw = known.get((a["url"], a.get("username")), "")
            cal.append({"label": a.get("label", ""), "url": a["url"],
                        "username": a.get("username", ""), "password": pw})
        ics = [{"label": f.get("label", ""), "url": f["url"]} for f in p.get("ics", []) if f.get("url")]
        persons.append({"name": name, "color": p.get("color", "#4f8cff"),
                        "inDay": bool(p.get("inDay", True)), "caldav": cal, "ics": ics})
    colors = {str(k): v for k, v in (new.get("colors") or {}).items()
              if isinstance(v, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", v)}
    names = {str(k): str(v).strip() for k, v in (new.get("names") or {}).items() if str(v).strip()}
    hidden = [str(x) for x in new.get("hidden", old.get("hidden", []))]
    hidden_day = [str(x) for x in new.get("hiddenDay", old.get("hiddenDay", []))]
    hidden_up = [str(x) for x in new.get("hiddenUp", old.get("hiddenUp", []))]
    cfg = {"persons": persons, "hidden": hidden, "hiddenDay": hidden_day, "hiddenUp": hidden_up, "colors": colors, "names": names,
           "dayStart": max(0, min(23, int(new.get("dayStart", 7)))),
           "dayEnd": max(1, min(24, int(new.get("dayEnd", 22))))}
    save(cfg)
    with lock:
        cache.clear()
    return jsonify(ok=True)


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")
