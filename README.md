<h1 align="center">Jarvis</h1>
<p align="center"><b>A local-first voice assistant with a full-screen sci-fi HUD, built for a small fanless laptop.</b><br>
Speak to it, it answers out loud, shows maps, pictures, videos and live camera views with labelled boxes, runs a terminal, and keeps your data on your machine.</p>

<p align="center">
<img src="docs/screenshots/jarvis-idle.jpg" width="49%" alt="Jarvis theme, idle">
<img src="docs/screenshots/rose-idle.jpg" width="49%" alt="Rose theme, idle">
</p>

> **Independent hobby project.** Not affiliated with, endorsed by or sponsored by Marvel, Disney or any other company. "Jarvis" is a working title: if the name is a problem for you, rename the folder, the window class and the persona names in `jarvis/personas.py` (see [Name](#name)).


## Seven personas

Every persona has its own name (wake phrase), voice, pace, way of talking, colour and orb look. Switch by voice ("switch to Sage", "be the captain", "next persona"), or let Jarvis pick one by topic (homework goes to Sage, building and electronics to Maker, jokes to Captain).

<p align="center">
<img src="docs/screenshots/p26-persona-jarvis.jpg" width="32%" alt="Jarvis"> <img src="docs/screenshots/p26-persona-rose.jpg" width="32%" alt="Rose"> <img src="docs/screenshots/p26-persona-sage.jpg" width="32%" alt="Sage">
</p>
<p align="center">
<img src="docs/screenshots/p26-persona-coach.jpg" width="32%" alt="Coach"> <img src="docs/screenshots/p26-persona-captain.jpg" width="32%" alt="Captain"> <img src="docs/screenshots/p26-persona-page.jpg" width="32%" alt="Page">
</p>
<p align="center"><img src="docs/screenshots/p26-persona-maker.jpg" width="32%" alt="Maker"></p>

## Widgets, maps and live data

<p align="center">
<img src="docs/screenshots/p26-weather.jpg" width="49%" alt="Weather card: temperature, hourly strip, 5-day row"> <img src="docs/screenshots/p26-news.jpg" width="49%" alt="News card with headlines, sources, times and pictures">
</p>
<p align="center">
<img src="docs/screenshots/p26-map-2d.jpg" width="49%" alt="Vector map, 2D, with an accuracy ring around you"> <img src="docs/screenshots/p26-map-3d.jpg" width="49%" alt="The same map tilted to 3D with buildings">
</p>
<p align="center">
<img src="docs/screenshots/p26-chat.jpg" width="49%" alt="Chat widget: $ for you, > for Jarvis"> <img src="docs/screenshots/p26-music.jpg" width="49%" alt="Music: play now with no account, or connect Spotify">
</p>
<p align="center">
<img src="docs/screenshots/p26-3d-engine-exploded.jpg" width="49%" alt="3D hologram, exploded view"> <img src="docs/screenshots/p26-markets-desk.jpg" width="49%" alt="Markets desk (read only)">
</p>
<p align="center">
<img src="docs/screenshots/p26-rose-weather.jpg" width="49%" alt="The Rose theme"> <img src="docs/screenshots/p26-claude-status.jpg" width="49%" alt="Claude Code status: the screen edge blinks yellow when it needs you">
</p>

## What it is

Jarvis is a voice assistant for one specific kind of machine: a **Surface Go 1** (Pentium Gold 4415Y, no fan) running **Omarchy / Hyprland** on Wayland. Speech recognition, wake phrases and text to speech run **locally**. The "brain" is [Claude Code](https://claude.com/claude-code) on **your own login** (no API key is stored or needed), and it is only ever allowed to pick from a short list of actions that the code then validates and runs.

Two themes share everything: **Jarvis** (blue) and **Rose** (red), each with its own voice, name and look.

## Features

| | |
|---|---|
| **Voice** | Wake phrases ("Hey Jarvis" / "Hey Rose"), hands-free conversation (it keeps the mic open when it asks you something), hold-to-talk key, interrupt while it speaks, speaking speed per voice, mute, dictation into any window |
| **HUD** | Full-screen Chromium page with an animated orb, cards (text, lists, charts, weather, maps, picture galleries, research reports), a native Wayland overlay orb you can drag and tap to expand, light/lite mode for the weak CPU, two themes |
| **Maps** | Dark OpenStreetMap map card, routes by car, on foot or by bike with travel times, "near me" searches, "show Starbucks on the map", route line that stays visible at every zoom, typed address with live suggestions |
| **Look** | "Look at this": a full-screen view of your phone camera, USB webcam or screen, with thin glowing labelled boxes around what the brain recognises. Tap a box to select it and ask about it. Live and freeze, send to your Mac |
| **Phone as a camera** | IP Webcam or DroidCam over your own Wi-Fi, address checker and test button |
| **Pictures and video** | Picture galleries by default for places, animals, things; videos in the Jarvis frame with play, close and full screen |
| **Windows** | Movable, resizable, minimisable windows for videos, maps, pages, a **terminal** (real shell or Claude Code in a folder you choose) and a **files** browser, all inside the Jarvis screen; a window switcher for your other apps; an always-visible **DESKTOP** button and `SUPER+ESCAPE` to leave to the normal desktop without closing anything |
| **Mac inbox** | Pair a Mac once; send it links, maps and files over a signed, outbound-only channel; the Mac replies "opened" or "dropped: reason" |
| **Phone alerts** | Send a note to your phone through ntfy with a private random topic; read back new alerts |
| **Music** | YouTube music in the frame; optional Spotify (own developer app, Premium, a player called "Jarvis") |
| **Memory and routine** | Remembers short facts (refuses passwords and card numbers), reminders and timers that survive restarts, notes, shopping lists, morning briefing, scenes, quiet hours, focus timer |
| **Research** | A research mode that runs searches in parallel and checks every cited link against what was really fetched, marking uncertain claims |
| **Machine care** | Health report, quiet warnings (a small chip only when something stays bad), a supervisor that restarts whatever dies, a restart limiter, a clock screen saver |
| **Clock** | A HUD clock screen after a quiet time you choose (30 s to 30 min or never), seconds, 12/24 hour, burn-in shift, about 15% of one core while showing |
| **Many devices** | Your Mac can ask the Go things, hand it notes, links and its clipboard (`jarvis_inbox.py ask|note|link|clip`); your iPhone can talk to it ("Hey Jarvis" through a Shortcut, home Wi-Fi only, off until you turn it on) and share notes and links to it; reminders reach your phone; a tidy outbox shows what was sent and whether the Mac opened it |
| **Self-care** | "update yourself" (backup, safe pull, health check, automatic rollback), backups, and "improve yourself": it learns from "that's wrong", proposes a small code fix on a copy with a read-and-edit-only helper, and applies it only when you approve |
| **Calendar and email** | Read-only: your private iCal address and an email app password, sender and subject lines only; "what's on my calendar", "any new email" |
| **Look extras** | Remember a place ("remember this shelf", "where did I put my charger"), read text from a picture, watch a camera for one condition |
| **Desk tools** | Launcher, clipboard history, scratchpad, named window layouts, activity panel, microphone self-test, large text and high contrast |
| **Personas** | A persona pack: Jarvis and Rose plus Sage (calm mentor), Coach, Captain, Page (quiet librarian) and Maker (workshop helper). Each has its own name (wake phrase), voice and pace, way of talking, colour, orb style and chime; "switch to the coach", Alt+T cycles; add your own in `~/.config/jarvis/personas.json` |
| **Feelings** | The brain tags each sentence with a mood; the orb's hue, pulse and spin and the voice's pace and pitch follow it, subtly (a setting turns it off). Short wake / finished / error chimes in each persona's style, with volume and an off switch |
| **Buddy** | Recurring reminders in plain speech ("every weekday at 9", "every 2 hours", "every Sunday evening"), list, pause, edit, delete; promises ("I'll call mum tomorrow") that it nudges you about and asks "done?"; gentle (no speech during a video or call or quiet hours) |
| **Show me how / Circle to ask** | "How do I change the font size in this app?": a glowing click-through ring points at the control, one step at a time, never clicking for you. Circle anything on the screen with a finger or the mouse and ask about it; the answer appears next to it |
| **Answer cards** | `explain` cards draw a diagram, timeline, labelled picture or a slowly turning 3D shape beside the answer, with the key sentence underlined |
| **Offline** | Without the internet or the brain it says so once, keeps timers, the time, volume, brightness, notes, opening things, machine status and small talk working, and switches back by itself |
| **Spotify** | play, search (a list to choose from), pause, resume, next, previous, what's playing, volume, devices and "play on the Go / my Mac / my phone", a MUSIC card with big cover art, send a song to your Mac or phone; also `python -m jarvis.spotify_cli` in a terminal |
| **3D models** | "Show me an engine in 3D": a hologram-style card you can turn, tap a part to ask about it, explode, cut with a plane that turns with the model, measure, pin notes, save as a scene, export a picture or short video, take a spoken tour. Built from a validated scene spec (no model-supplied code), five offline demos, real models only with your approval and with credit lines, flat diagram fallback |
| **Markets desk** | A read-only research desk (never trades, never advises): quotes, movers, a chart that obeys your voice ("pull up Nvidia", "show Apple for 3 months", "add the 50-day average", "mark the support and resistance", "compare Apple and Microsoft"), nine named seats (macro, flow, scout, analyst, chartist, forecast, risk, ledger) that are roles inside one brain, a watchlist and alerts, a market briefing. Every number comes from a feed with its source and time, a feed that is down shows "—", and spoken numbers are checked against the data. Forecast view is a labelled statistical range of the past, not a prediction |
| **Boot animation and widgets** | A 1.4 second power-on animation (the screen swipes away, a blue bar scans, the orb grows, the HUD pieces pop in), the orb glides to the corner when a view opens (transform only, 60 fps measured), Jarvis-style window chrome, widgets for music, chat, weather, news, timers, notes and today that remember their place and size |
| **Vector map, 2D and 3D** | MapLibre GL with OpenFreeMap tiles (no key), a tile cache on disk (100 MB), a glowing "you" dot with an accuracy ring, routes that stay visible at every zoom, 3D tilt with buildings that drops back to 2D by itself if it runs under 20 fps, and a raster fallback. Your real position comes from an iPhone Shortcut (GPS), then the home area, Wi-Fi, then an IP guess |
| **Weather and news** | Open-Meteo weather (10 minute cache): hourly strip, 5-day row, sunrise and sunset; news from public feeds refreshed in the background every 12 minutes (politics mixes outlets on purpose); every headline, source, time and picture comes from a feed, never invented |
| **Camera by voice** | "Turn on the camera", "what do you see", "what am I holding": a full-screen live view that fades to black at the edges, boxes you can tap, a three-dots options row, QR and barcode scan (zbar), "scan everything on my desk" |
| **Desk presence** | Optional and off by default: a 1/8-size grey picture every 1.5 s compared with the last one (no faces, nothing kept); "remind me to stretch when I'm back at my desk" |
| **Send anything** | A one-time local-network link serves a file once, the Mac checks its size and hash, saves it to a Jarvis Inbox folder and never opens or unzips it; executables are quarantined |
| **Claude Code status** | A localhost hook endpoint with a private token: a corner badge (working, needs me, done, failed) and a coloured screen edge |
| **Learn, build, shop (lists only)** | Flashcards that remember what you missed, parts bin and project parts lists, product lookup where a price is shown only if the exact price text is on the fetched store page, tracking and affiliate parameters stripped, nothing is ever bought |
| **Whisper mode, find my phone, Echo as a speaker** | Quiet voice and short answers on request or at night; an urgent phone alert plus the honest limits of finding an iPhone; a guided card to use an Echo as a plain Bluetooth speaker (nothing flashed) |
| **Settings that stick** | A schema version, a backup before any migration, invalid values reported not dropped, export / import with no secrets, reset one setting |
| **Type box** | Alt+Space (or Ctrl+Shift+Space) opens a small text field next to the pointer over any app; Enter sends it |
| **Tools** | Translate mode with per-language voices, read-this-to-me (screenshot + OCR), unit/currency/time answers, QR codes |

## Screenshots

<p align="center">
<img src="docs/screenshots/jarvis-cards.jpg" width="49%" alt="Cards: chart, map, note and list">
<img src="docs/screenshots/look.jpg" width="49%" alt="Look view with labelled boxes (a drawn demo scene)">
</p>
<p align="center">
<img src="docs/screenshots/jarvis-clock.jpg" width="49%" alt="Clock screen, Jarvis">
<img src="docs/screenshots/rose-clock.jpg" width="49%" alt="Clock screen, Rose">
</p>
<p align="center">
<img src="docs/screenshots/rose-cards.jpg" width="49%" alt="Cards in the Rose theme">
<img src="docs/screenshots/settings.jpg" width="49%" alt="Settings panel with grouped, collapsible sections">
</p>
<sub>The map in the cards image is the Canberra demo card. Map data © OpenStreetMap contributors. The Look image is a drawn demo scene, not a photo.</sub>

## Hardware and what to expect

Built and tested on a Surface Go 1: 2 cores / 4 threads at 1.6 GHz, 8 GB RAM, no fan, a USB webcam optional (the built-in IPU3 cameras are not supported and nothing here loads their modules). Typical timings on that machine: the first spoken word about 1 to 3 seconds after the brain starts answering (Piper on this CPU is the floor), a picture request answered by code in about a second, research about 20 seconds. Everything is written to stay light: CSS animation instead of per-frame JavaScript, a pooled browser window that is recycled when idle, a lite mode that switches on by itself when the CPU is hot.

## Install

You need Arch/Omarchy with Hyprland, a microphone and speakers on PipeWire, and a Claude account for Claude Code.

```sh
sudo pacman -S --needed python python-gobject gtk4 gtk4-layer-shell python-cairo ffmpeg pipewire wireplumber chromium grim
sudo pacman -S --needed tesseract qrencode wtype brightnessctl yt-dlp libnotify   # optional features
git clone <this repository> ~/jarvis        # it must live in ~/jarvis (the Hyprland file uses $HOME/jarvis)
cd ~/jarvis && ./install.sh
```

`install.sh` builds `.venv`, downloads two Piper voices (about 63 MB each, from Hugging Face `rhasspy/piper-voices`) and the wake-word model (about 5 MB, from the sherpa-onnx releases). The speech model (`tiny.en`, about 75 MB) downloads on first start. Nothing about you is sent anywhere by the installer.

Then:

1. Add `dofile(os.getenv("HOME") .. "/jarvis/hypr/jarvis.lua")` to `~/.config/hypr/bindings.lua` and run `hyprctl reload`.
2. Install Claude Code and log in once: run `claude`.
3. Check everything: `bin/jarvis-doctor` (it prints no private data and tells you what is missing).
4. Start it: `bin/jarvis-watch &` (the supervisor; it also starts at login after step 1).

## First run

* Say **"Hey Jarvis"** (or hold `SUPER+J`) and ask something.
* **Set your location** (nothing trusts a rough IP guess for "near me"): say "set my location" and type your address in the card, pick a suggestion, confirm it on the map. It is saved only in `~/.config/jarvis/settings.json`.
* Open **MENU > SETTINGS** for speed, volume, clock, warnings, shortcuts.

### Pair a Mac (optional)
Say "pair my Mac". A card shows the steps, the exact command and a one-time code (shown only on screen). On the Mac, get this repository's `mac/` folder (clone or ZIP), paste the command, type the code. The card turns to "Mac connected"; "send test to my Mac" proves delivery. Run `python3 jarvis_inbox.py install` there to start it at login. See [`mac/README.md`](mac/README.md).

### Set up Spotify (optional, needs Premium)
Say "set up Spotify" and follow the card: create your own app at developer.spotify.com with the redirect `http://127.0.0.1:8888/callback`, paste the Client ID (no secret is needed: PKCE login), log in on Spotify's own page, and run a Spotify Connect player named "Jarvis" (spotifyd or librespot, installed by you). Nothing is shared with anyone; tokens stay in `~/.config/jarvis` (mode 600). If Spotify refuses, music falls back to YouTube.

### Phone camera (optional)
Install IP Webcam (Android) or DroidCam (Android/iPhone), open **SETTINGS > Camera > Phone camera**, type just the phone's IP, press SAVE then TEST. Jarvis tries `http`, the app's default port and the usual video paths and saves the one that works. Only addresses on your own network are accepted.

## Keyboard shortcuts

| Keys | Does |
|---|---|
| hold `SUPER+J` | talk, release to send |
| hold `SUPER+SHIFT+L` | look at the camera/screen and talk |
| `SUPER+SHIFT+J` | orb <-> full screen |
| hold `SUPER+SHIFT+D` | dictate into the focused window |
| `SUPER+ALT+M` | mute (deaf mode) |
| `SUPER+ALT+J` | back to the Jarvis window |
| `SUPER+ESCAPE` | **abort**: leave Jarvis full screen for the normal desktop (press again to return). Works even if Jarvis hung |
| `Alt+T` | switch theme (changeable in settings; never fires while you type) |
| `Esc` | close the front card, window or settings |

## A few things to say

"set my location" · "show Starbucks on the map" · "directions to the airport" · "look at this" · "box the cup" · "freeze" · "show me pictures of a red panda" · "show me how to tie a tie" · "research how red pandas survive" · "send that to my Mac" · "text me the address" · "remind me in ten minutes" · "speak faster" · "clock after 10 minutes" · "turn off machine warnings" · "switch to the terminal" · "how's the machine" · "translate to Spanish" · "go to the corner" · "back to Jarvis"

## How it works

```
mic --> wake phrase (sherpa-onnx) --> speech to text (faster-whisper) --> local skills first (timers, volume, maps, ...)
                                                                      \--> the brain (claude -p, stream-json) --> ONE JSON intent from a fixed list
                                                                                                                       |
Piper voice <-- first sentence streamed <-- reply ------------------------ code validates the intent and runs ONLY that action
                                                                                   |
                     Chromium kiosk page (cards, maps, windows, look, clock) + native GTK4 layer-shell orb + Hyprland window control
```

* `jarvis/core.py` the assistant loop, `jarvis/intents.py` the allowlisted intents, `jarvis/actions.py` page-triggered actions, `jarvis/opener.py` windows and Hyprland, `jarvis/web/` the HUD.
* A watchdog (`jarvis/watchdog.py`) restarts the server, page, orb and brain when they die, with a restart limiter.

## Safety and privacy

* **The AI never runs shell text.** It can only name an intent (`show_map`, `directions`, `search_web`, `show_images`, `show_video`, `send_to_mac`, `send_to_phone`, `open_app`, `answer`); the code validates it. Sends to your Mac or phone only happen if your own words said send, text or share. The terminal is for your typing only: nothing in the assistant can reach it.
* **The terminal** listens on `127.0.0.1` only, needs a random per-start token the page receives over its own websocket, checks Host and Origin, starts only when you open it, and dies with its window.
* **Secrets** (pairing code, ntfy topic, tokens) are never printed, logged or committed. See [`config.example/`](config.example/README.md) for where private data lives.
* **What leaves your machine, and when:** your questions, and screenshots or camera frames when you ask it to *look*, go to Claude through Claude Code. Map tiles, geocoding, routing, weather, Wikipedia pictures and YouTube are fetched only when a request needs them (a rough city-level IP lookup only if no home location is set and you ask for "near me"; the clock screen asks Open-Meteo for the weather at your home location every ten minutes while it is showing). Alerts go through ntfy.sh to a random private topic. Nothing else is sent. Details and terms: [`THIRD_PARTY.md`](THIRD_PARTY.md).
* While Jarvis runs it raises Omarchy's screensaver timer so its own clock screen is used, and puts the original value back when it exits (`jarvis/syshold.py`).

## Troubleshooting

Run `bin/jarvis-doctor`. Logs are in `logs/`. If the full-screen page is stuck, press `SUPER+ESCAPE`.

## Credits and inspiration

Several ideas in this round came from public projects. **Only ideas were taken: no code was copied or adapted** (everything here was written from scratch).

- [Roamies](https://github.com/KenKaiii/roamies) by KenKaiii (AGPL-3.0): the ideas of an on-screen "show me how" guide, circling something on the screen to ask about it, a reminder-and-promise buddy, a voice with moods and a face that follows them, switchable personas, and drawn answer cards.
- [jarvis-mlx](https://github.com/huwprosser/jarvis-mlx) by huwprosser: a fully offline fallback and a short beep when the wake word is heard.
- [HeyClicky](https://heyclicky.com) (closed source): the idea of a 3D model you can turn and click to ask about any part, and sketching on the screen while it talks.
- My own Relay app: the earlier work on holograms (demo shapes, pull apart, cut sections, notes, compare, scenes) and its lesson that a model from one photo is a guess, so those are never shown as real.
- [three.js](https://threejs.org) (MIT) draws the 3D cards; real models can come from Wikimedia Commons (licence and creator shown on every card), and NASA, NIH and Smithsonian are on the allowlist only.
- A markets-desk build guide from "Cloud9 Markets" (closed design, no code): one master routing words to specialist seats, charts that obey voice, every number from a real feed, analysis kept apart from action. Here it is read-only by design.
- [TradingView Lightweight Charts](https://github.com/tradingview/lightweight-charts) (Apache-2.0) draws the chart.
- [spotify-cli](https://github.com/huwprosser/spotify-cli) by huwprosser: a small Spotify command set (auth, search, play, pause, resume, next, previous, status, logout) that plays on the most recent active device.

Roamies is AGPL-3.0; this repository is MIT. Nothing from it is included, so the licences do not mix. If you think a file here is too close to someone else's work, open an issue and it will be rewritten.

## Name

The persona names live in `jarvis/personas.py` and the window class in `bin/jarvis-ui`; the Hyprland rules in `hypr/jarvis.lua` match on that class. If you want a neutral project name instead of "Jarvis", good options are **Heyorb**, **Lumen Voice**, **Localvox**, **Pocket Orb** or **Halo Desk**.

## Licence

MIT, see [`LICENSE`](LICENSE). Third-party components keep their own licences: [`THIRD_PARTY.md`](THIRD_PARTY.md). Contributions: [`CONTRIBUTING.md`](CONTRIBUTING.md).
