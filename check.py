#!/usr/bin/env python3
"""Checks the family services from outside the house, every few minutes (GitHub Actions).

Keeps its state in data/status.json (the `data` branch) and sends a phone alert through ntfy
when something has failed twice in a row, and again when it comes back.
Secrets (repo settings): NTFY_TOPIC (where alerts go), GLUCOSE_URL (the private glucose page).
"""
import json
import os
import socket
import ssl
import time
import urllib.request
from datetime import datetime, timezone

SITE = "https://mellorsfamily.com"
SERVICES = [
    # id, name shown, kind, target
    ("site", "Family website", "http", SITE + "/"),
    ("forum", "Forum & family chat", "http", SITE + "/melforum/"),
    ("games", "Games", "http", SITE + "/games/"),
    ("podcasts", "Podcasts", "http", SITE + "/podcasts/"),
    ("wall", "Family Wall", "http", SITE + "/wall/"),
    ("bdays", "Birthday calendar", "http", SITE + "/bdaycal/"),
    ("command", "Mellfam Command", "http", SITE + "/control/"),
    ("glucose", "Glucose report", "http", os.environ.get("GLUCOSE_URL", "")),
    ("irc", "IRC chat server", "tcp", ("mellorsfamily.com", 6697)),
    ("minecraft", "Forgecraft (Minecraft)", "tcp", ("mellorsfamily.com", 25565)),
]
STATE = os.environ.get("STATE_FILE", "data/status.json")
DAYS_KEPT = 90
ALERT_AFTER = 2          # failures in a row before a phone alert (one blip doesn't wake anyone)


def check_http(url):
    if not url:
        return None, "not configured"
    req = urllib.request.Request(url, headers={"User-Agent": "mellors-status/1.0 (+github actions)"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=20, context=ssl.create_default_context()) as r:
            code = r.status
    except urllib.error.HTTPError as e:
        code = e.code
    except Exception as e:  # timeouts, refused, DNS, TLS
        return False, type(e).__name__
    ms = int((time.time() - t0) * 1000)
    return code < 500, f"HTTP {code}, {ms} ms"


def check_tcp(target):
    t0 = time.time()
    try:
        with socket.create_connection(target, timeout=15):
            pass
    except Exception as e:
        return False, type(e).__name__
    return True, f"open, {int((time.time() - t0) * 1000)} ms"


def notify(lines):
    raw = os.environ.get("NTFY_TOPIC") or ""
    # accept "topic", "ntfy.sh/topic" or a full link, with stray spaces (easy to paste on a phone)
    topic = "".join(raw.split()).rstrip("/").split("/")[-1].split("?")[0]
    if not topic or not lines:
        return
    if topic != raw:     # never print the topic itself: the logs of this repo are public
        print(f"note: NTFY_TOPIC was tidied (had {'a link, ' if '/' in raw else ''}{'spaces, ' if raw != raw.strip() or ' ' in raw else ''}{len(raw)} chars -> {len(topic)})")
    down = any(l.startswith("🔴") or l.startswith("🏠") for l in lines)
    req = urllib.request.Request(f"https://ntfy.sh/{topic}", data="\n".join(lines).encode(), method="POST", headers={
        "Title": "Mellors family services (checked from outside)",
        "Priority": "high" if down else "default",
        "Tags": "rotating_light" if down else "white_check_mark",
        "Click": "https://stevemellors.github.io/status/",
    })
    try:
        urllib.request.urlopen(req, timeout=15)
    except Exception as e:
        print("ntfy failed:", e)


def mins(s):
    m = round(s / 60)
    return f"{m} min" if m < 90 else f"{m / 60:.1f} h"


def main():
    now = time.time()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        state = json.load(open(STATE))
    except (OSError, ValueError):
        state = {"services": {}}
    svc_state = state.setdefault("services", {})
    went_down, came_back = [], []

    results = {}
    for sid, name, kind, target in SERVICES:
        ok, detail = (check_http if kind == "http" else check_tcp)(target)
        if ok is False:      # one retry, so a single dropped packet isn't an outage
            time.sleep(5)
            ok, detail = (check_http if kind == "http" else check_tcp)(target)
        results[sid] = (ok, detail)

    for sid, name, kind, _ in SERVICES:
        ok, detail = results[sid]
        s = svc_state.setdefault(sid, {"up": True, "since": now, "fails": 0, "alerted": False, "days": {}})
        s["name"], s["detail"], s["checked"] = name, detail, now
        if ok is None:
            s["up"] = None
            continue
        day = s["days"].setdefault(today, [0, 0])
        day[1] += 1
        if ok:
            day[0] += 1
            if s.get("alerted"):
                came_back.append(f"🟢 {name} is back (down {mins(now - s['since'])})")
            if s.get("up") is not True:
                s["since"] = now
            s.update(up=True, fails=0, alerted=False)
        else:
            if s.get("up") is not False:
                s["since"] = now
            s["up"] = False
            s["fails"] = s.get("fails", 0) + 1
            if s["fails"] >= ALERT_AFTER and not s.get("alerted"):
                s["alerted"] = True
                went_down.append((sid, name, detail))
        for d in sorted(s["days"])[:-DAYS_KEPT]:
            del s["days"][d]

    checkable = [sid for sid, *_ in SERVICES if results[sid][0] is not None]
    msgs = []
    if went_down and all(svc_state[sid]["up"] is False for sid in checkable):
        msgs.append("🏠 Nothing at the house can be reached: internet or power is probably out.")
    else:
        msgs += [f"🔴 {name} is down ({detail})" for _, name, detail in went_down]
    msgs += came_back
    if os.environ.get("TEST_ALERT") == "true":
        up = sum(1 for sid in checkable if svc_state[sid]["up"])
        msgs.append(f"🧪 Test alert: phone alerts from the outside watchdog work. {up} of {len(checkable)} services up.")
    notify(msgs)

    state["checked"] = now
    state["order"] = [sid for sid, *_ in SERVICES]
    os.makedirs(os.path.dirname(STATE) or ".", exist_ok=True)
    with open(STATE, "w") as f:
        json.dump(state, f, indent=1, sort_keys=True)
    for sid, name, *_ in SERVICES:
        print(f"{'UP  ' if results[sid][0] else 'DOWN' if results[sid][0] is False else 'n/a '} {name}: {results[sid][1]}")
    for m in msgs:
        print("alert:", m)


if __name__ == "__main__":
    main()
