"""Markets Desk: runs a validated route from markets.route() and returns a skill-style result {"speech", "cards", "mkt": [ops for the page]}.

The master speaks one data-derived sentence, then at most one seat line (RISK's caution when something warrants it), then stops. Every number in a spoken line comes from a Datum fetched in
this call; text the BRAIN writes (ANALYST) is checked by markets.check_numbers and any sentence with a number that is not in the data is dropped. Read-only: nothing here can trade."""
import re
import time

from . import markets as M
from . import settings

STATE = {"symbol": None, "range": "3mo", "ind": [], "type": "candle", "levels": False, "band": False, "compare": None, "stack": [], "said_notadvice": False, "open": False, "sources": []}


def enabled():
    return settings.get("markets_desk") is not False


def _fmt(x, nd=2):
    return "—" if x is None else f"{x:,.{nd}f}"


def _pct_words(p):
    return "—" if p is None else f"{'up' if p >= 0 else 'down'} {abs(p):.2f} percent"


def _src(*datums):
    for d in datums:
        if d:
            STATE["sources"].append({"source": d.get("source"), "t": d.get("t"), "stale": bool(d.get("stale"))})
    del STATE["sources"][:-12]


def _pretty(sym):
    return {"^GSPC": "the S and P 500", "^IXIC": "the Nasdaq", "^DJI": "the Dow", "^VIX": "the VIX", "BTC-USD": "Bitcoin", "ETH-USD": "Ethereum", "GC=F": "gold", "CL=F": "oil"}.get(sym, sym)


def _caution(sym, cl=None, q=None):
    """RISK's single line: descriptive, built from the data, never a signal. None when nothing stands out."""
    c = M.candles(sym, "1y")
    if not c:
        return None
    st = M.stats(c["value"]["c"])
    bits = []
    if st.get("vol20", 0) >= 2.5:
        bits.append(f"it has been moving about {st['vol20']:.1f} percent a day")
    if st.get("from_high") is not None and st["from_high"] > -3:
        bits.append("it is near its 52-week high")
    v = c["value"]["v"]
    if len(v) > 25 and sum(v[-5:]) / 5 < 0.5 * sum(v[-25:-5]) / 20:
        bits.append("volume is thin")
    if sym in M.watchlist() and len(M.watchlist()) <= 3:
        bits.append("your watchlist is concentrated in very few names")
    _src(c)
    return ("Caution: " + " and ".join(bits[:2]) + ".") if bits else None


def desk_card():
    return {"type": "markets", "title": "MARKETS", "full": False, "notadvice": M.NOTADVICE, "seats": list(M.SEATS.values()), "indexes": [{"name": n, "symbol": s} for n, s in M.INDEXES],
            "watchlist": M.watchlist(), "has_key": bool(M.key()), "symbol": STATE["symbol"]}


def pulse():
    """The tiles and the session for the desk's left side (also fetched by the page every minute while it is open): -> dict, values None when a feed is down."""
    tiles = []
    for name, sym in M.INDEXES + [(s, s) for s in M.watchlist()[:8]]:
        q = M.quote(sym)
        v = (q or {}).get("value") or {}
        tiles.append({"name": name, "symbol": sym, "price": v.get("price"), "pct": v.get("pct"), "source": (q or {}).get("source"), "t": (q or {}).get("t"), "stale": bool((q or {}).get("stale"))})
    return {"tiles": tiles, "session": M.session(), "health": M.health(), "updated": time.strftime("%H:%M"), "econ": M.econ_upcoming(2)[0]}


def _sym(r):
    """The symbol for a route: the named one, else the one on the chart now. -> (symbol, ask_text|None, candidates|None)"""
    name = r.get("name") or r.get("symbol_name")
    if not name:
        return STATE["symbol"], None, None
    s, cand = M.resolve(name)
    if s:
        return s, None, None
    if cand:
        return None, "Which one: " + " or ".join(c["name"] for c in cand) + "?", cand
    return None, f"I couldn't find a symbol for {name}.", None


def _chart_ops():
    p = M.chart_payload(STATE["symbol"], STATE["range"], STATE["ind"], STATE["levels"], STATE["band"], STATE["compare"])
    _src({"source": p.get("source"), "t": p.get("t")} if not p.get("error") else None)
    return {"op": "chart", "type": STATE["type"], "payload": p}


def _remember_chart():
    STATE["stack"].append({k: STATE[k] for k in ("symbol", "range", "ind", "type", "levels", "band", "compare")})
    del STATE["stack"][:-8]


BRAIN = None                                                         # set by core: a function(prompt) -> text, used only by the ANALYST seat


def answer(r, text, brain=None):
    """-> {"speech", "cards"?, "mkt"?, "expect"?, "retry"?} for a validated route. `brain(prompt)` (optional) writes the ANALYST's plain-words text."""
    intent = r["intent"]
    brain = brain or BRAIN
    out = {"speech": "", "cards": [], "mkt": []}
    quoted = []

    def say(s):
        out["speech"] = (out["speech"] + " " + s).strip()

    def done(sym=None):
        if not STATE["said_notadvice"]:
            STATE["said_notadvice"] = True
            say(M.NOTADVICE)                                           # spoken once per session, at the first market answer
        M.ledger_add(text, r["seat"], sym, quoted)
        STATE["open"] = True
        return out
    if intent == "refuse_trade":
        out["speech"] = M.REFUSE_TRADE
        return out
    if intent == "refuse_advice":
        out["speech"] = M.REFUSE_ADVICE
        return out
    if intent == "close":
        out["mkt"].append({"op": "close"})
        out["speech"] = "Closing the desk."
        return out
    if intent in ("open_desk",):
        out["cards"].append(desk_card())
        p = pulse()
        first = [t for t in p["tiles"][:4] if t["pct"] is not None]
        if first:
            say(", ".join(f"{_pretty(t['symbol']).capitalize() if t['symbol'].startswith('^') else t['name']} {_pct_words(t['pct'])}" for t in first[:3]) + f". The market is {p['session']['state']}.")
            quoted += [{"label": t["name"], "value": t["pct"], "source": t["source"], "t": t["t"]} for t in first]
        else:
            say("Price feed is down. I can show the desk, but the numbers are unavailable.")
        return done()
    if intent == "session":
        s = M.session()
        say(f"The market is {s['state']}; it {s['countdown']}. The holiday calendar may be incomplete.")
        return done()
    if intent == "health":
        h = M.health()
        say("Data feeds: " + (", ".join(f"{k} {v}" for k, v in h.items()) if h else "none used yet") + ".")
        return done()
    if intent == "sources":
        s = STATE["sources"][-6:]
        say("That came from " + ", ".join(sorted({x["source"] or "?" for x in s})) + "." if s else "Nothing has been fetched yet.")
        out["cards"].append({"type": "list", "title": "Sources behind the last answer", "items": [f"{x['source']}  {time.strftime('%H:%M', time.localtime(x['t'])) if x.get('t') else '—'}" + ("  (stale)" if x.get("stale") else "") for x in s] or ["nothing yet"]})
        return done()
    if intent == "ledger":
        rows = M.ledger_tail(8)
        say(f"The desk journal has {len(rows)} recent entries." if rows else "The desk journal is empty.")
        out["cards"].append({"type": "list", "title": "Desk journal (what I told you)", "items": [f"{time.strftime('%d %b %H:%M', time.localtime(x['t']))}  {x['seat']}  {x['q'][:60]}" for x in reversed(rows)] or ["nothing yet"]})
        return done()
    if intent == "watch_list":
        w = M.watchlist()
        say("Your watchlist: " + ", ".join(w) + "." if w else "Your watchlist is empty.")
        return done()
    if intent in ("watch_add", "watch_remove"):
        s, ask, cand = _sym(r)
        if not s:
            out["speech"] = ask or "Which stock?"
            if cand:
                out["expect"], out["retry"] = "mkt_pick", intent
            return out
        (M.watch_add if intent == "watch_add" else M.watch_remove)(s)
        say(f"{'Added' if intent == 'watch_add' else 'Removed'} {s} {'to' if intent == 'watch_add' else 'from'} your watchlist.")
        out["mkt"].append({"op": "watchlist"})
        return done(s)
    if intent == "alert_add":
        s, ask, cand = _sym(r)
        if not s:
            out["speech"] = ask or "Which stock?"
            return out
        if r.get("earnings"):
            say("Earnings dates need the Finnhub key. Paste it in the desk settings and ask again." if not M.key() else "Earnings alerts are not available on the free plan yet.")
        else:
            M.alert_add(s, r.get("pct"))
            say(f"I'll tell you if {s} moves {r.get('pct') or 5:g} percent in a day. It is only a notification.")
        return done(s)
    if intent == "movers":
        g, l = M.movers("day_gainers", 3), M.movers("day_losers", 3)
        _src(g, l)
        if not g and not l:
            say("The movers feed is down, so I can't say what is moving.")
            return done()
        parts = []
        if g:
            parts.append("Biggest gainers: " + ", ".join(f"{x['name']} {_pct_words(x['pct'])}" for x in g["value"][:3]))
            quoted += [{"label": x["symbol"], "value": x["pct"], "source": g["source"], "t": g["t"]} for x in g["value"][:3]]
        if l:
            parts.append("losers: " + ", ".join(f"{x['name']} {_pct_words(x['pct'])}" for x in l["value"][:3]))
        say(". ".join(parts) + ".")
        out["cards"].append({"type": "list", "title": "Movers today", "items": [f"{x['symbol']}  {x['name']}  {_fmt(x['pct'])}%  {_fmt(x['price'])}" for x in ((g or {}).get("value") or [])[:5]] + [f"{x['symbol']}  {x['name']}  {_fmt(x['pct'])}%  {_fmt(x['price'])}" for x in ((l or {}).get("value") or [])[:5]]})
        return done()
    if intent in ("macro", "briefing"):
        up, how = M.econ_upcoming(2)
        s = M.session()
        tnx = M.quote("^TNX")
        line = f"The market is {s['state']}."
        if tnx and (tnx.get("value") or {}).get("price") is not None:
            line += f" The 10-year yield is {_fmt(tnx['value']['price'])} percent."
            quoted.append({"label": "10-year yield", "value": tnx["value"]["price"], "source": tnx["source"], "t": tnx["t"]})
            _src(tnx)
        say(line)
        say("Next on the calendar: " + "; ".join(f"{i['date']} {i['what']}" for i in up[:2]) + f" (the calendar is {how}: edit data/econ_calendar.json)." if up else "I have no calendar dates saved.")
        if intent == "briefing":
            w = []
            for sym in M.watchlist()[:12]:
                q = M.quote(sym)
                if q and (q.get("value") or {}).get("pct") is not None:
                    w.append((abs(q["value"]["pct"]), sym, q["value"]["pct"], q))
            w.sort(reverse=True)
            if w:
                say("Biggest watchlist moves: " + ", ".join(f"{sy} {_pct_words(p)}" for _, sy, p, _q in w[:3]) + ".")
                quoted += [{"label": sy, "value": p, "source": q_["source"], "t": q_["t"]} for _, sy, p, q_ in w[:3]]
            c = _caution(M.watchlist()[0], None, None) if M.watchlist() else None
            if c:
                say(c)
        return done()
    # everything below needs a symbol
    s, ask, cand = _sym(r)
    if intent in ("pull_up", "analyst", "flow", "forecast", "risk", "compare") and not s and intent != "compare":
        out["speech"] = ask or "Which stock?"
        if cand:
            out["expect"], out["retry"] = "mkt_pick", intent
        return out
    if intent == "flow":
        f = M.filings(s)
        if f == "needs_contact":
            say("Public filings come from the SEC and need a contact string in the desk settings first.")
        elif not f:
            say(f"I couldn't read public filings for {s} right now.")
        else:
            _src(f)
            say(f"Public filings for {s}: " + "; ".join(f"form {x['form']} filed {x['date']}" for x in f["value"][:3]) + ". These are disclosed late, not live.")
            out["cards"].append({"type": "list", "title": f"Public filings: {s} (SEC EDGAR)", "items": [f"{x['date']}  form {x['form']}" for x in f["value"]] + ["Disclosed late, not live."]})
        return done(s)
    if intent == "risk":
        c = _caution(s) or "Caution: nothing unusual stands out in the numbers I can see, but that is not a reason to relax."
        say(c)
        return done(s)
    if intent == "analyst":
        q = M.quote(s)
        cd = M.candles(s, "1y")
        nw = M.news(s, 3)
        _src(q, cd, nw)
        if not q:
            say("Price feed is down, so I can't read this one.")
            return done(s)
        v, st = q["value"], (M.stats(cd["value"]["c"]) if cd else {})
        data = {"symbol": s, "price": v["price"], "pct": v["pct"], "vol20": st.get("vol20"), "from_high": st.get("from_high"), "headlines": [n["title"] for n in ((nw or {}).get("value") or [])]}
        quoted += [{"label": s + " price", "value": v["price"], "source": q["source"], "t": q["t"]}]
        base = f"{(cd['value']['meta'].get('name') if cd else None) or s} is at {_fmt(v['price'])}, {_pct_words(v['pct'])} today."
        text_out = base
        if brain and nw:
            try:
                reply = brain("[MARKETS ANALYST. In at most 3 short plain sentences say what this company does and one recent theme from its headlines. Use ONLY these data and general company knowledge with NO numbers of your own: "
                              + str(data) + ". Do not give advice or say whether to buy or sell. No markdown.]")
                clean, dropped = M.check_numbers(reply, data)
                if clean != "—":
                    text_out = base + " " + clean
            except Exception:
                pass
        say(text_out)
        if st.get("from_high") is not None:
            say(f"It is {abs(st['from_high']):.1f} percent {'below' if st['from_high'] < 0 else 'at'} its 52-week high.")
        c = _caution(s)
        if c:
            say(c)
        STATE["symbol"] = s
        out["mkt"].append(_chart_ops())
        return done(s)
    if intent == "forecast":
        STATE.update(symbol=s, band=True)
        out["mkt"].append(_chart_ops())
        say(f"This is a statistical summary of the past for {s}, not a prediction: a trend line with a range based on how much it has moved. Model output, not a prediction.")
        return done(s)
    # chart intents
    if intent == "compare":
        a, ca, _c1 = M.resolve(r.get("a", ""))
        b, cb, _c2 = M.resolve(r.get("b", ""))
        if not a or not b:
            out["speech"] = "I couldn't find both symbols to compare."
            return out
        _remember_chart()
        STATE.update(symbol=a, compare=b)
        say(f"Comparing {a} and {b}, both rebased to the same start.")
        out["mkt"].append(_chart_ops())
        out["cards"].append(desk_card()) if not STATE["open"] else None
        return done(a)
    if intent == "back":
        if STATE["stack"]:
            STATE.update(STATE["stack"].pop())
            out["mkt"].append(_chart_ops())
            say("Back.")
        else:
            say("There is nothing to go back to.")
        return done(STATE["symbol"])
    if intent == "zoom":
        order = ["1d", "5d", "1mo", "3mo", "6mo", "1y", "5y"]
        i = order.index(STATE["range"]) if STATE["range"] in order else 3
        STATE["range"] = order[max(0, i - 1)] if r.get("dir") == "in" else order[min(len(order) - 1, i + 1)]
        out["mkt"].append(_chart_ops())
        say("Zoomed " + (r.get("dir") or "in") + ".")
        return done(STATE["symbol"])
    if intent == "chart_type":
        STATE["type"] = r.get("type") or "candle"
        out["mkt"].append({"op": "type", "type": STATE["type"]})
        say("Line chart." if STATE["type"] == "line" else "Candles.")
        return done(STATE["symbol"])
    if not STATE["symbol"] and not s:
        out["speech"] = "Which stock?"
        return out
    _remember_chart()
    if s:
        STATE["symbol"] = s
    if r.get("range"):
        STATE["range"] = r["range"]
    if intent == "indicator" and r.get("ind"):
        if r["ind"] not in STATE["ind"]:
            STATE["ind"].append(r["ind"])
    if intent == "levels":
        STATE["levels"] = True
    if intent == "pull_up" and s and not (r.get("range")):
        STATE.update(ind=[], levels=False, band=False, compare=None)
    if not STATE["open"]:
        out["cards"].append(desk_card())
    op = _chart_ops()
    out["mkt"].append(op)
    p = op["payload"]
    if p.get("error"):
        say("Price feed is down.")
        return done(STATE["symbol"])
    q = M.quote(STATE["symbol"])
    _src(q)
    v = (q or {}).get("value") or {}
    if intent == "levels":
        lv = p.get("levels") or []
        say(f"I marked {len(lv)} levels on {STATE['symbol']}, each labelled with how it was worked out. They describe history; they are not signals.")
    else:
        say(f"{p.get('name') or STATE['symbol']}: {_fmt(v.get('price'))}, {_pct_words(v.get('pct'))}." if v.get("price") is not None else f"{STATE['symbol']}: price unavailable.")
        if v.get("price") is not None:
            quoted.append({"label": STATE["symbol"], "value": v["price"], "source": q["source"], "t": q["t"]})
        if intent == "pull_up":
            c = _caution(STATE["symbol"])
            if c:
                say(c)
    return done(STATE["symbol"])
