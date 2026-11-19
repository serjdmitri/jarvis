"""What Jarvis can do with videos. He cannot watch a video like a person: this reads the text around it (title, creator, description, top comments),
the captions if they exist, otherwise a transcript of the first 3 minutes of audio (the warm faster-whisper), and one contact sheet of six small frames
(one every 30 s) that goes to the brain as an image. yt-dlp and ffmpeg are run from THIS code with fixed arguments; the model never runs a command.
Caps: 40 s for the page data, 60 s per download, 20 MB audio / 30 MB video, only the first 3 minutes. Temp files are removed afterwards; the result is cached
in run/videocache/<id>.json (+ the contact sheet). Instagram, TikTok and X often block this or need a login: that is reported plainly, never worked around
(no passwords, no cookies are ever used)."""
import glob
import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.parse

from . import config, stt

CACHE = config.RUN_DIR / "videocache"
URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.I)
CLIP_S = 180
LAST = {"url": None, "results": []}            # the last video link seen / the last search results ("open the second one")
SITES = ("youtube.com", "youtu.be", "tiktok.com", "instagram.com", "x.com", "twitter.com", "vimeo.com", "facebook.com", "fb.watch", "reddit.com", "twitch.tv")


def find_url(text):
    m = URL_RE.search(text or "")
    if not m:
        return None
    u = m.group(0).rstrip(".,);]")
    p = urllib.parse.urlsplit(u)
    return u if p.scheme in ("http", "https") and p.hostname and not p.username else None


def is_video_url(u):
    h = (urllib.parse.urlsplit(u).hostname or "").lower()
    return any(h == s or h.endswith("." + s) for s in SITES)


def _run(cmd, timeout):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def blocked_reason(stderr):
    s = (stderr or "").lower()
    if any(k in s for k in ("login", "log in", "sign in", "cookies", "private", "rate-limit", "rate limit", "403", "401", "not available", "unable to extract", "requested content is not available")):
        return "needs a login or blocks automatic downloads"
    return "could not be read"


def _vtt_text(path):
    out, last = [], ""
    for line in open(path, encoding="utf-8", errors="ignore"):
        line = line.strip()
        if not line or line.startswith(("WEBVTT", "Kind:", "Language:", "NOTE")) or "-->" in line or re.fullmatch(r"\d+", line):
            continue
        line = re.sub(r"<[^>]+>", "", line)
        if line and line != last:
            out.append(line)
            last = line
    return " ".join(out)


def gather(url, log=print):
    """-> dict (title, channel, description, comments, transcript, source, sheet) or {"error": plain text}. Cached by video id."""
    CACHE.mkdir(parents=True, exist_ok=True)
    m = re.search(r"(?:v=|youtu\.be/|shorts/|embed/)([A-Za-z0-9_-]{11})", url)
    if m and (CACHE / f"{m.group(1)}.json").exists():                      # cached: no network at all
        return json.load(open(CACHE / f"{m.group(1)}.json"))
    try:
        r = _run(["yt-dlp", "--no-warnings", "--no-playlist", "--skip-download", "--dump-single-json", "--write-comments",
                  "--extractor-args", "youtube:max_comments=12,all,0,0;comment_sort=top", url], 40)
    except subprocess.TimeoutExpired:
        return {"error": "That took too long to read."}
    if r.returncode != 0 or not r.stdout.strip():
        why = blocked_reason(r.stderr)
        host = (urllib.parse.urlsplit(url).hostname or "that site").replace("www.", "")
        return {"error": f"I couldn't read that video: {host} {why}.", "blocked": True}
    info = json.loads(r.stdout)
    vid = re.sub(r"[^A-Za-z0-9_-]", "", str(info.get("id") or "x"))[:40]
    cache = CACHE / f"{vid}.json"
    if cache.exists():
        return json.load(open(cache))
    out = {"id": vid, "url": url, "title": info.get("title") or "", "channel": info.get("uploader") or info.get("channel") or "",
           "duration": info.get("duration"), "description": (info.get("description") or "")[:900],
           "comments": [c.get("text", "")[:240].replace("\n", " ") for c in (info.get("comments") or [])[:10] if c.get("text")],
           "transcript": "", "source": "none", "sheet": None}
    tmp = tempfile.mkdtemp(prefix="jarvis-vid-", dir=str(config.RUN_DIR))
    try:
        # 1) captions, if the video has them
        try:
            _run(["yt-dlp", "--no-warnings", "--no-playlist", "--skip-download", "--write-subs", "--write-auto-subs", "--sub-langs", "en.*,en",
                  "--sub-format", "vtt", "-o", f"{tmp}/c", url], 40)
            vtts = sorted(glob.glob(f"{tmp}/c*.vtt"))
            if vtts:
                txt = _vtt_text(vtts[0])
                if txt:
                    out["transcript"], out["source"] = txt[:6000], "captions"
        except subprocess.TimeoutExpired:
            pass
        # 2) no captions: the audio of the first 3 minutes, low quality, transcribed by the warm tiny.en
        if not out["transcript"]:
            try:
                _run(["yt-dlp", "--no-warnings", "--no-playlist", "-f", "worstaudio/worst", "--download-sections", f"*0-{CLIP_S}", "--max-filesize", "20M",
                      "-x", "--audio-format", "wav", "--postprocessor-args", "ffmpeg:-ar 16000 -ac 1", "-o", f"{tmp}/a.%(ext)s", url], 60)
                wav = f"{tmp}/a.wav"
                if os.path.exists(wav):
                    txt = stt.transcribe(wav)
                    if txt:
                        out["transcript"], out["source"] = txt[:6000], "audio (first 3 minutes, transcribed locally)"
            except subprocess.TimeoutExpired:
                pass
        # 3) six small frames (one every 30 s of the first 3 minutes) as one contact sheet
        try:
            _run(["yt-dlp", "--no-warnings", "--no-playlist", "-f", "worst[height<=360]/worst", "--download-sections", f"*0-{CLIP_S}", "--max-filesize", "30M",
                  "-o", f"{tmp}/v.%(ext)s", url], 60)
            vids = [f for f in glob.glob(f"{tmp}/v.*") if not f.endswith((".part", ".ytdl"))]
            if vids:
                sheet = str(CACHE / f"{vid}.jpg")
                _run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", vids[0], "-vf", "fps=1/30,scale=320:-2,tile=3x2", "-frames:v", "1", "-q:v", "5", sheet], 40)
                if os.path.exists(sheet):
                    out["sheet"] = sheet
        except subprocess.TimeoutExpired:
            pass
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    json.dump(out, open(cache, "w"))
    return out


def prompt_for(data, question=None):
    """The text sent to the brain with the contact sheet."""
    d = data
    parts = [f"Title: {d['title']}", f"Creator: {d['channel']}"]
    if d.get("duration"):
        parts.append(f"Length: {int(d['duration'])} seconds")
    if d.get("description"):
        parts.append("Description: " + d["description"])
    if d.get("comments"):
        parts.append("Top comments: " + " | ".join(d["comments"]))
    if d.get("transcript"):
        parts.append(f"Transcript ({d['source']}): " + d["transcript"])
    else:
        parts.append("No captions and no audio transcript were available; go by the title, description, comments and frames only, and say so.")
    head = "Here is what I could read about a video. I cannot watch it like a person: this is text plus one contact sheet of six frames (30 seconds apart) if attached.\n"
    ask = question or "Give me the gist in two or three short spoken sentences."
    return head + "\n".join(parts) + "\n\n" + ask + " If something is unclear, say what you could not tell."


def search(query, n=6):
    """yt-dlp YouTube search -> [{"title", "channel", "duration", "url"}]."""
    q = re.sub(r"[^\w\s'.,-]", " ", query).strip()[:100]
    try:
        r = _run(["yt-dlp", "--no-warnings", "--flat-playlist", "--dump-json", f"ytsearch{n}:{q}"], 40)
    except subprocess.TimeoutExpired:
        return []
    out = []
    for line in r.stdout.splitlines():
        try:
            j = json.loads(line)
        except ValueError:
            continue
        if j.get("id"):
            out.append({"title": j.get("title") or "", "channel": j.get("channel") or j.get("uploader") or "", "duration": j.get("duration"),
                        "url": f"https://www.youtube.com/watch?v={j['id']}"})
    return out


def fmt_dur(s):
    if not s:
        return ""
    s = int(s)
    return f"{s // 60}:{s % 60:02d}"


# ----------------------------------------------------------------------------- what the user says
ORD = {"first": 1, "1st": 1, "one": 1, "second": 2, "2nd": 2, "two": 2, "third": 3, "3rd": 3, "three": 3, "fourth": 4, "four": 4, "fifth": 5, "five": 5, "sixth": 6, "six": 6}


def parse_command(text):
    """-> {"kind": "gist"|"find"|"pick"|"page", ...} or None."""
    t = text.strip()
    low = t.lower().strip(" .?!")
    url = find_url(t)
    if re.search(r"\b(?:gist|summari[sz]e|summary|what(?:'s| is) (?:this|that|the) .*about|tell me about)\b.*\b(?:video|reel|short|clip|tiktok)\b|\b(?:video|reel|short|clip)\b.*\b(?:gist|summari[sz]e|about)\b", low):
        return {"kind": "gist", "url": url}
    if url and is_video_url(url) and re.search(r"\b(?:gist|summari[sz]e|summary|what(?:'s| is) this|watch)\b", low):
        return {"kind": "gist", "url": url}
    m = re.search(r"\b(?:find|search for|look for|show) (?:me )?(?:some )?(?:youtube )?videos? (?:about|on|of|for) (.+)$", low)
    if m:
        return {"kind": "find", "query": t[m.start(1):m.end(1)].strip(" .?!")}
    m = re.search(r"\b(?:open|play|show|pick|watch|send)\b.*?\b(first|1st|second|2nd|third|3rd|fourth|fifth|sixth|(?:number )?(?:one|two|three|four|five|six|[1-6]))\b(?: one| video| result)?(.*)$", low)
    if m and LAST["results"]:
        k = re.sub(r"number ", "", m.group(1))
        n = ORD.get(k) or (int(k) if k.isdigit() else None)
        if n and n <= len(LAST["results"]):
            return {"kind": "pick", "n": n, "mac": bool(re.search(r"\bmac\b", low))}
    if re.search(r"\bsummari[sz]e (?:this|that|the) (?:page|article|site|website|link)\b|\bwhat(?:'s| is) (?:this|that) (?:page|article) about\b", low):
        return {"kind": "page", "url": url}
    return None
