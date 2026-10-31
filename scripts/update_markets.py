"""Refresh Kalshi and Polymarket prices for the seven Senate races.

Runs every 15 minutes from .github/workflows/markets.yml. Reads only public,
unauthenticated market-data endpoints, updates data/races.json in place, adds
at most one Kalshi point per race per hour to data/kalshi_history.json, and
writes data/status.json with the check time and any per-venue problems.
A price that can't be read keeps its previous value and timestamp.
"""
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
DATA = os.path.join(ROOT, "data")
UA = {"User-Agent": "senate-2026-battleground-board (github actions)", "Accept": "application/json"}

KALSHI_API = "https://api.elections.kalshi.com/trade-api/v2/events/"
KALSHI = {
    "TX": ("SENATETX-26", "https://kalshi.com/markets/senatetx/texas-senate-race/senatetx-26"),
    "OH": ("SENATEOHS-26", "https://kalshi.com/markets/senateohs/special-senate-election-in-ohio/senateohs-26"),
    "MI": ("SENATEMI-26", "https://kalshi.com/markets/senatemi/michigan-senate-race/senatemi-26"),
    "ME": ("SENATEME-26", "https://kalshi.com/markets/senateme/maine-senate-race/senateme-26"),
    "KS": ("SENATEKS-26", "https://kalshi.com/markets/senateks/kansas-senate-race/senateks-26"),
    "SC": ("SENATESC-26", "https://kalshi.com/markets/senatesc/south-carolina-senate-race/senatesc-26"),
    "MN": ("SENATEMN-26", "https://kalshi.com/markets/senatemn/minnesota-senate-race/senatemn-26"),
}
GAMMA = "https://gamma-api.polymarket.com"
STATE_NAME = {"TX": "Texas", "OH": "Ohio", "MI": "Michigan", "ME": "Maine", "KS": "Kansas", "SC": "South Carolina", "MN": "Minnesota"}
POLY_SLUGS = {
    "TX": ["texas-senate-election-winner"],
    "OH": ["ohio-senate-special-election-winner", "ohio-senate-election-winner"],
    "MI": ["michigan-senate-election-winner"],
    "ME": ["maine-senate-election-winner"],
    "KS": ["kansas-senate-election-winner"],
    "SC": ["south-carolina-senate-election-winner"],
    "MN": ["minnesota-senate-election-winner"],
}


def get(url, timeout=20):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def to_pct(v):
    """Kalshi prices arrive in cents (0-100) or dollars (0-1); return percent or None."""
    if v is None or v == "":
        return None
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return None
    return v * 100 if v <= 1 else v


def kalshi_price(m):
    """Mid of the bid/ask when the book is tight, else last trade."""
    bid = to_pct(m.get("yes_bid_dollars", m.get("yes_bid")))
    ask = to_pct(m.get("yes_ask_dollars", m.get("yes_ask")))
    last = to_pct(m.get("last_price_dollars", m.get("last_price")))
    if bid is not None and ask is not None and 0 <= ask - bid <= 6:
        return (bid + ask) / 2
    return last


def side_of(text, race):
    t = (text or "").lower()
    dem, rep = race["dem"].lower(), race["rep"].lower()
    if dem in t or "democrat" in t:
        return "D"
    if rep in t or "republican" in t:
        return "R"
    return None


def kalshi_for(code, race):
    ticker, page = KALSHI[code]
    body = get(KALSHI_API + urllib.parse.quote(ticker))
    markets = body.get("markets") or (body.get("event") or {}).get("markets") or []
    found = {}
    for m in markets:
        s = side_of(" ".join(str(m.get(k, "")) for k in ("yes_sub_title", "subtitle", "title")), race)
        if s is None:
            tk = str(m.get("ticker", "")).upper()
            s = "D" if tk.endswith("-D") or tk.endswith("-DEM") else "R" if tk.endswith("-R") or tk.endswith("-REP") else None
        p = kalshi_price(m)
        if s and p is not None and s not in found:
            found[s] = p
    if "D" in found and "R" in found:
        dem = found["D"] / (found["D"] + found["R"]) * 100
    elif "D" in found:
        dem = found["D"]
    elif "R" in found:
        dem = 100 - found["R"]
    else:
        raise ValueError(f"no Democratic or Republican contract found in {ticker} ({len(markets)} markets)")
    return dem, page


def poly_events(code):
    for slug in POLY_SLUGS[code]:
        try:
            evs = get(f"{GAMMA}/events?slug={urllib.parse.quote(slug)}")
        except Exception:
            continue
        if isinstance(evs, list) and evs:
            return evs[0]
    q = urllib.parse.quote(f"{STATE_NAME[code]} Senate Election Winner")
    res = get(f"{GAMMA}/public-search?q={q}&limit_per_type=10")
    for ev in res.get("events") or []:
        title = str(ev.get("title", "")).lower()
        if STATE_NAME[code].lower() in title and "senate" in title and "winner" in title and not ev.get("closed"):
            return ev
    raise ValueError("no matching open Polymarket event")


def poly_for(code, race):
    ev = poly_events(code)
    found = {}
    for m in ev.get("markets") or []:
        if m.get("closed"):
            continue
        s = side_of(" ".join(str(m.get(k, "")) for k in ("groupItemTitle", "question")), race)
        prices = m.get("outcomePrices")
        if isinstance(prices, str):
            try:
                prices = json.loads(prices)
            except ValueError:
                prices = None
        outcomes = m.get("outcomes")
        if isinstance(outcomes, str):
            try:
                outcomes = json.loads(outcomes)
            except ValueError:
                outcomes = None
        yes = None
        if prices and outcomes and len(prices) == len(outcomes):
            for o, p in zip(outcomes, prices):
                if str(o).lower() == "yes":
                    yes = float(p) * 100
        elif prices:
            yes = float(prices[0]) * 100
        if s and yes is not None and 0 < yes < 100 and s not in found:
            found[s] = yes
    if "D" in found and "R" in found:
        dem = found["D"] / (found["D"] + found["R"]) * 100
    elif "D" in found:
        dem = found["D"]
    elif "R" in found:
        dem = 100 - found["R"]
    else:
        raise ValueError(f"no Democratic or Republican market in event {ev.get('slug')}")
    return dem, f"https://polymarket.com/event/{ev.get('slug')}"


def main():
    now = datetime.now(timezone.utc).replace(microsecond=0)
    stamp = now.isoformat().replace("+00:00", "Z")
    races_path = os.path.join(DATA, "races.json")
    hist_path = os.path.join(DATA, "kalshi_history.json")
    races = json.load(open(races_path))
    hist = json.load(open(hist_path))
    status = {"markets_checked_at": stamp, "kalshi": {"updated": [], "errors": {}}, "polymarket": {"updated": [], "errors": {}}}

    for r in races:
        code = r["code"]
        for venue, fn, key in (("kalshi", kalshi_for, "kalshi"), ("polymarket", poly_for, "poly")):
            try:
                dem, url = fn(code, r)
                if not (1 <= dem <= 99):
                    raise ValueError(f"implausible price {dem:.1f}")
                r[f"{key}_dem"] = round(dem)
                r[f"{key}_as_of"] = stamp
                r[f"{key}_url"] = url
                status[venue]["updated"].append(code)
            except Exception as e:  # keep the previous value and timestamp
                status[venue]["errors"][code] = f"{type(e).__name__}: {e}"[:300]
        r["markets_checked_at"] = stamp

        if code in status["kalshi"]["updated"]:
            last = max((h for h in hist if h["code"] == code), key=lambda h: h["date"], default=None)
            last_t = None
            if last:
                d = last["date"]
                last_t = datetime.fromisoformat(d.replace("Z", "+00:00")) if len(d) > 10 else datetime.fromisoformat(d + "T00:00:00+00:00")
            if last_t is None or (now - last_t).total_seconds() >= 3600 - 120:
                hist.append({"key": f"{code}-{stamp}", "code": code, "date": stamp, "kalshi_dem": r["kalshi_dem"], "url": r["kalshi_url"]})

    json.dump(races, open(races_path, "w"), indent=1, ensure_ascii=False)
    json.dump(hist, open(hist_path, "w"), indent=1, ensure_ascii=False)
    json.dump(status, open(os.path.join(DATA, "status.json"), "w"), indent=1, ensure_ascii=False)
    print(json.dumps(status, indent=1))
    if not status["kalshi"]["updated"] and not status["polymarket"]["updated"]:
        print("No venue could be read this run.", file=sys.stderr)


if __name__ == "__main__":
    main()
