"""Markets Desk routing test: phrase -> (seat, intent) or a refusal with no action. Run: .venv/bin/python tests/markets_routing.py"""
import os, sys, tempfile
os.environ["JARVIS_DATA_DIR"] = tempfile.mkdtemp(); os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from jarvis import markets
CASES = [
 ("open the desk", "MASTER", "open_desk"), ("markets", "MASTER", "open_desk"), ("how are the markets", "MASTER", "open_desk"),
 ("what is the Fed doing", "MACRO", "macro"), ("what's on the economic calendar", "MACRO", "macro"), ("market briefing", "MACRO", "briefing"), ("is the market open", "MACRO", "session"),
 ("what is hot today", "SCOUT", "movers"), ("top gainers", "SCOUT", "movers"), ("what's moving the market", "SCOUT", "movers"),
 ("what do you think of Apple", "ANALYST", "analyst"), ("tell me about Tesla", "ANALYST", "analyst"),
 ("pull up Nvidia", "CHARTIST", "pull_up"), ("show Apple for 3 months", "CHARTIST", "pull_up"), ("chart Microsoft", "CHARTIST", "pull_up"),
 ("add the 50-day average", "CHARTIST", "indicator"), ("add volume", "CHARTIST", "indicator"), ("mark the support and resistance", "CHARTIST", "levels"), ("mark the 52-week high", "CHARTIST", "levels"),
 ("switch to a line chart", "CHARTIST", "chart_type"), ("compare Apple and Microsoft", "CHARTIST", "compare"), ("zoom in", "CHARTIST", "zoom"), ("go back", "CHARTIST", "back"),
 ("add Tesla to my watchlist", "MASTER", "watch_add"), ("remove Tesla from my watchlist", "MASTER", "watch_remove"), ("what is on my watchlist", "MASTER", "watch_list"),
 ("tell me if Apple moves 5 percent", "MASTER", "alert_add"), ("congressional trades in Nvidia", "FLOW", "flow"), ("what could go wrong with Tesla", "RISK", "risk"),
 ("forecast for Apple", "FORECAST", "forecast"), ("what did you tell me earlier", "LEDGER", "ledger"), ("how are the data feeds", "LEDGER", "health"),
 # refusals: no action at all
 ("buy 10 Tesla", "MASTER", "refuse_trade"), ("sell everything", "MASTER", "refuse_trade"), ("should I buy Nvidia", "MASTER", "refuse_advice"),
 ("put my savings in bitcoin", "MASTER", "refuse_trade"), ("send money to John", "MASTER", "refuse_trade"), ("sell my Apple shares", "MASTER", "refuse_trade"),
 # not the desk at all
 ("what is the weather", None, None), ("buy milk", None, None), ("play some jazz", None, None), ("show me a red panda", None, None), ("set a timer for 5 minutes", None, None)]
bad = 0
for text, seat, intent in CASES:
    r = markets.route(text)
    got = (r["seat"], r["intent"]) if r else (None, None)
    ok = got == (seat, intent)
    bad += not ok
    print(("PASS" if ok else "FAIL"), f"{text!r:44} want {str(intent):14} got {got[1]}")
print(f"\n{len(CASES) - bad}/{len(CASES)} passed")
sys.exit(1 if bad else 0)
