"""Refresh Kalshi and Polymarket prices for every 2026 Senate race and for Senate control.

Runs every 15 minutes from .github/workflows/markets.yml. Reads only public,
unauthenticated market-data endpoints and updates, in place:
  data/all_races.json     - all 35 races: Democratic / Republican / independent win shares per venue
  data/races.json         - the seven battleground races (Democratic win share per venue)
  data/control.json       - Senate control prices
  data/kalshi_history.json - at most one Kalshi point per battleground race per hour
  data/status.json        - check time and any per-venue problems
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
GAMMA = "https://gamma-api.polymarket.com"

KALSHI_TICKERS = {"OH": ["SENATEOHS-26", "SENATEOH-26"], "FL": ["SENATEFLS-26", "SENATEFL-26"]}
KALSHI_PAGES = {
    "TX": "https://kalshi.com/markets/senatetx/texas-senate-race/senatetx-26",
    "OH": "https://kalshi.com/markets/senateohs/special-senate-election-in-ohio/senateohs-26",
    "MI": "https://kalshi.com/markets/senatemi/michigan-senate-race/senatemi-26",
    "ME": "https://kalshi.com/markets/senateme/maine-senate-race/senateme-26",
    "KS": "https://kalshi.com/markets/senateks/kansas-senate-race/senateks-26",
    "SC": "https://kalshi.com/markets/senatesc/south-carolina-senate-race/senatesc-26",
    "MN": "https://kalshi.com/markets/senatemn/minnesota-senate-race/senatemn-26",
}
CONTROL_KALSHI = ["CONTROLS-2026"]
CONTROL_POLY = ["which-party-will-win-the-senate-in-2026"]


def get(url, timeout=20):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def to_pct(v, dollars):
    """Kalshi *_dollars fields are 0-1, the plain fields are cents (0-100); return percent or None."""
    if v is None or v == "":
        return None
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return None
    return v * 100 if dollars else v


def kalshi_price(m):
    """Mid of the bid/ask when the book is tight, else last trade."""
    def field(name):
        if m.get(name + "_dollars") not in (None, ""):
            return to_pct(m.get(name + "_dollars"), True)
        return to_pct(m.get(name), False)
    bid, ask, last = field("yes_bid"), field("yes_ask"), field("last_price")
    if bid is not None and ask is not None and 0 <= ask - bid <= 6:
        return (bid + ask) / 2
    if last is not None:
        return last
    if ask is not None and ask <= 3:  # one-sided book on a long shot
        return ask / 2
    return None


def last_name(n):
    return n.split()[-1].lower() if n else None


def classify(text, race):
    """Which party a contract is for: by candidate surname first, then by party word."""
    t = (text or "").lower()
    for party, key in (("R", "r_name"), ("D", "d_name"), ("I", "i_name")):
        ln = last_name(race.get(key))
        if ln and ln in t:
            return party
    if "republican" in t:
        return "R"
    if "democrat" in t:
        return "D"
    if "independent" in t:
        return "I"
    return None


def shares(found):
    """Turn raw prices per party into shares that sum to 100."""
    if not found:
        raise ValueError("no party contracts found")
    if len(found) == 1:
        (p, v), = found.items()
        if p == "R":
            return {"D": 100 - v, "R": v, "I": 0.0}
        return {p: v, "R": 100 - v, **{q: 0.0 for q in ("D", "I") if q != p}}
    tot = sum(found.values())
    out = {q: found.get(q, 0.0) / tot * 100 for q in ("D", "R", "I")}
    return out


def kalshi_event(code):
    tickers = KALSHI_TICKERS.get(code, [f"SENATE{code}-26"])
    last_err = None
    for t in tickers:
        try:
            body = get(KALSHI_API + urllib.parse.quote(t))
            markets = body.get("markets") or (body.get("event") or {}).get("markets") or []
            if markets:
                return t, markets
        except Exception as e:
            last_err = e
    try:  # fall back to the series' open events
        body = get(f"https://api.elections.kalshi.com/trade-api/v2/events?series_ticker=SENATE{code}&status=open&with_nested_markets=true")
        for ev in body.get("events") or []:
            if "26" in str(ev.get("event_ticker", "")) and ev.get("markets"):
                return ev["event_ticker"], ev["markets"]
    except Exception as e:
        last_err = e
    raise ValueError(f"no Kalshi event among {tickers}: {last_err}")


def kalshi_for(race):
    ticker, markets = kalshi_event(race["code"])
    found = {}
    for m in markets:
        if str(m.get("status", "")).lower() in ("settled", "finalized"):
            continue
        p = classify(" ".join(str(m.get(k, "")) for k in ("yes_sub_title", "subtitle", "title", "ticker")), race)
        price = kalshi_price(m)
        if p and price is not None and p not in found:
            found[p] = price
    page = KALSHI_PAGES.get(race["code"], f"https://kalshi.com/markets/{ticker.split('-')[0].lower()}")
    return shares(found), page


def yes_price(m):
    prices, outcomes = m.get("outcomePrices"), m.get("outcomes")
    if isinstance(prices, str):
        try:
            prices = json.loads(prices)
        except ValueError:
            prices = None
    if isinstance(outcomes, str):
        try:
            outcomes = json.loads(outcomes)
        except ValueError:
            outcomes = None
    if prices and outcomes and len(prices) == len(outcomes):
        for o, p in zip(outcomes, prices):
            if str(o).lower() == "yes":
                return float(p) * 100
    if prices:
        return float(prices[0]) * 100
    return None


def poly_event(slugs, search=None, must=()):
    for slug in slugs:
        try:
            evs = get(f"{GAMMA}/events?slug={urllib.parse.quote(slug)}")
        except Exception:
            continue
        if isinstance(evs, list) and evs:
            return evs[0]
    if search:
        res = get(f"{GAMMA}/public-search?q={urllib.parse.quote(search)}&limit_per_type=10")
        for ev in res.get("events") or []:
            title = str(ev.get("title", "")).lower()
            if all(w in title for w in must) and not ev.get("closed"):
                return ev
    raise ValueError(f"no matching open Polymarket event ({slugs[0]})")


def poly_for(race):
    st = race["state"].replace(" (special)", "")
    slug = st.lower().replace(" ", "-")
    slugs = [f"{slug}-senate-election-winner"]
    if race.get("special"):
        slugs = [f"{slug}-senate-special-election-winner"] + slugs
    ev = poly_event(slugs, f"{st} Senate Election Winner", (st.lower(), "senate"))
    found = {}
    for m in ev.get("markets") or []:
        if m.get("closed"):
            continue
        p = classify(" ".join(str(m.get(k, "")) for k in ("groupItemTitle", "question")), race)
        y = yes_price(m)
        if p and y is not None and 0 < y < 100 and p not in found:
            found[p] = y
    return shares(found), f"https://polymarket.com/event/{ev.get('slug')}"


def control_prices():
    out = {}
    party = {"r_name": None, "d_name": None, "i_name": None}
    try:
        for t in CONTROL_KALSHI:
            body = get(KALSHI_API + t)
            ms = body.get("markets") or (body.get("event") or {}).get("markets") or []
            found = {}
            for m in ms:
                p = classify(" ".join(str(m.get(k, "")) for k in ("yes_sub_title", "subtitle", "title", "ticker")), party)
                price = kalshi_price(m)
                if p in ("D", "R") and price is not None and p not in found:
                    found[p] = price
            if found:
                out["kalshi"] = shares(found)["D"]
                break
    except Exception as e:
        out["kalshi_error"] = f"{type(e).__name__}: {e}"[:300]
    try:
        ev = poly_event(CONTROL_POLY, "Which party will win the Senate in 2026", ("senate", "2026"))
        found = {}
        for m in ev.get("markets") or []:
            p = classify(" ".join(str(m.get(k, "")) for k in ("groupItemTitle", "question")), party)
            y = yes_price(m)
            if p in ("D", "R") and y is not None and p not in found:
                found[p] = y
        out["poly"] = shares(found)["D"]
        out["poly_url"] = f"https://polymarket.com/event/{ev.get('slug')}"
    except Exception as e:
        out["poly_error"] = f"{type(e).__name__}: {e}"[:300]
    return out


def main():
    now = datetime.now(timezone.utc).replace(microsecond=0)
    stamp = now.isoformat().replace("+00:00", "Z")
    p = lambda name: os.path.join(DATA, name)
    all_races = json.load(open(p("all_races.json")))
    races = json.load(open(p("races.json")))
    hist = json.load(open(p("kalshi_history.json")))
    control = json.load(open(p("control.json")))
    control = control[0] if isinstance(control, list) else control
    status = {"markets_checked_at": stamp,
              "kalshi": {"updated": [], "errors": {}}, "polymarket": {"updated": [], "errors": {}},
              "control": {}}

    for r in all_races:
        for venue, fn, key in (("kalshi", kalshi_for, "kalshi"), ("polymarket", poly_for, "poly")):
            try:
                sh, url = fn(r)
                if not all(0 <= v <= 100 for v in sh.values()):
                    raise ValueError(f"implausible shares {sh}")
                r[f"{key}_d"] = round(sh["D"], 1)
                r[f"{key}_r"] = round(sh["R"], 1)
                r[f"{key}_i"] = round(sh["I"], 1)
                r[f"{key}_as_of"] = stamp
                r[f"{key}_url"] = url
                status[venue]["updated"].append(r["code"])
            except Exception as e:  # keep the previous value and timestamp
                status[venue]["errors"][r["code"]] = f"{type(e).__name__}: {e}"[:300]

    by_code = {r["code"]: r for r in all_races}
    for r in races:
        a = by_code.get(r["code"])
        if not a:
            continue
        for key, venue in (("kalshi", "kalshi"), ("poly", "polymarket")):
            if r["code"] in status[venue]["updated"]:
                d, rep = a[f"{key}_d"], a[f"{key}_r"]
                r[f"{key}_dem"] = round(d / (d + rep) * 100) if d + rep else r.get(f"{key}_dem")
                r[f"{key}_as_of"] = stamp
                r[f"{key}_url"] = a[f"{key}_url"]
        r["markets_checked_at"] = stamp
        if r["code"] in status["kalshi"]["updated"]:
            code = r["code"]
            last = max((h for h in hist if h["code"] == code), key=lambda h: h["date"], default=None)
            last_t = None
            if last:
                d = last["date"]
                last_t = datetime.fromisoformat(d.replace("Z", "+00:00")) if len(d) > 10 else datetime.fromisoformat(d + "T00:00:00+00:00")
            if last_t is None or (now - last_t).total_seconds() >= 3600 - 120:
                hist.append({"key": f"{code}-{stamp}", "code": code, "date": stamp, "kalshi_dem": r["kalshi_dem"], "url": r["kalshi_url"]})

    c = control_prices()
    if "kalshi" in c:
        control.update(kalshi_d=round(c["kalshi"], 1), kalshi_as_of=stamp, kalshi_url="https://kalshi.com/markets/controls/senate-winner/controls-2026")
    if "poly" in c:
        control.update(poly_d=round(c["poly"], 1), poly_as_of=stamp, poly_url=c["poly_url"])
    status["control"] = {k: v for k, v in c.items() if k.endswith("_error")}

    json.dump(all_races, open(p("all_races.json"), "w"), indent=1, ensure_ascii=False)
    json.dump(races, open(p("races.json"), "w"), indent=1, ensure_ascii=False)
    json.dump(hist, open(p("kalshi_history.json"), "w"), indent=1, ensure_ascii=False)
    json.dump([control], open(p("control.json"), "w"), indent=1, ensure_ascii=False)
    json.dump(status, open(p("status.json"), "w"), indent=1, ensure_ascii=False)
    print(json.dumps(status, indent=1))
    if not status["kalshi"]["updated"] and not status["polymarket"]["updated"]:
        print("No venue could be read this run.", file=sys.stderr)


if __name__ == "__main__":
    main()
