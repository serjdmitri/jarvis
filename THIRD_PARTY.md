# Third-party software, data and services

Jarvis stands on these. Each keeps its own licence and terms; none of them is changed or bundled here except where noted. Please read the terms of every service before heavy use.

## Software it uses
| Component | Used for | Licence |
|---|---|---|
| [Piper](https://github.com/rhasspy/piper) (`piper-tts`) | text to speech | MIT. **Each voice has its own licence** (see its model card on Hugging Face `rhasspy/piper-voices`); the install script downloads two voices, check theirs before redistributing |
| [faster-whisper](https://github.com/SYSTRAN/faster-whisper) / CTranslate2 / OpenAI Whisper weights | speech to text | MIT |
| [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) and its keyword-spotting zipformer model | wake phrases | Apache-2.0 |
| [ONNX Runtime](https://github.com/microsoft/onnxruntime), [NumPy](https://numpy.org), [aiohttp](https://github.com/aio-libs/aiohttp) | runtime | MIT, BSD-3, Apache-2.0 |
| [Leaflet](https://leafletjs.com) 1.x (`jarvis/web/vendor/`) | maps | BSD-2-Clause |
| [xterm.js](https://xtermjs.org) 5.5.0 and addon-fit (`jarvis/web/vendor/`, licence file included) | the terminal window | MIT |
| [Hyprland](https://hyprland.org), [Omarchy](https://omarchy.org) | the desktop it is built for | BSD-3-Clause, MIT |
| Chromium | the full-screen page | BSD-style |
| [GTK4](https://gtk.org) + [gtk4-layer-shell](https://github.com/wmww/gtk4-layer-shell), PyGObject, pycairo | the native orb | LGPL / MIT |
| [PipeWire](https://pipewire.org) / WirePlumber, [ffmpeg](https://ffmpeg.org), [grim](https://sr.ht/~emersion/grim/), tesseract, qrencode, wtype, brightnessctl, yt-dlp | audio, screenshots, OCR, QR, typing, brightness, video | various open-source licences, called as programs, not bundled |
| [Claude Code](https://claude.com/claude-code) | the brain (your own login; no API key is stored here) | Anthropic's terms; not bundled |
| [ntfy](https://ntfy.sh) | phone alerts and the signed Mac inbox | Apache-2.0 / GPLv2 server, public service terms apply |
| spotifyd or librespot (optional) | a Spotify Connect player named "Jarvis" | GPL-3 / MIT; not bundled |

## Data and services it can call (only when you ask for something that needs them)
| Service | For | Terms |
|---|---|---|
| OpenStreetMap tiles `tile.openstreetmap.org` and map data | the map | Data © OpenStreetMap contributors, ODbL. Attribution is shown on every map. Follow the [tile usage policy](https://operations.osmfoundation.org/policies/tiles/): light personal use only |
| Esri "World Dark Gray Base" tiles | automatic fallback map | Esri's terms; attribution shown |
| Nominatim, Photon (komoot), Overpass API | place search, address suggestions, "nearest …" | OSM data (ODbL); public instances with fair-use limits (about one request a second) |
| OSRM (project-osrm.org demo server, routing.openstreetmap.de) | routes and travel times | OSM data, fair use |
| Open-Meteo | weather | CC BY 4.0, free for non-commercial use |
| ipwho.is | a rough location from your IP, only if no home location is set and you ask for "near me" | their terms |
| Wikipedia / Wikimedia Commons | pictures for "show me …" (shown with their caption; each picture keeps its own licence) | CC / public domain, see each file page |
| Frankfurter | currency rates | free, no key |
| YouTube | videos and music in the Jarvis frame (embedded player or search) | YouTube's terms |
| Spotify Web API (optional) | playback control with your own developer app | Spotify's developer terms; Premium needed |

## Map attribution
Maps drawn by Jarvis show "© OpenStreetMap contributors" (and "Esri" on the fallback layer). Keep it if you take screenshots.

## Trademarks
"Jarvis", "Rose", Marvel, Disney, Spotify, YouTube, Apple, Mac and all other names belong to their owners. This is an independent hobby project and is not affiliated with, endorsed by or sponsored by any of them.

| three.js r160 (`jarvis/web/vendor/three`) | 3D cards | MIT, licence file included |
| Wikimedia Commons model files | optional real 3D models (only free licences; credit shown on the card) | each file's own licence |
| TradingView Lightweight Charts 4.1.3 (`jarvis/web/vendor/lwc`) | the Markets Desk chart | Apache-2.0, licence file included |
| Yahoo Finance chart/search endpoints (unofficial, keyless), Finnhub (optional free key), SEC EDGAR | market data, only when you ask | each provider's terms; the desk labels the source of every number |

| MapLibre GL JS 4.7.1 | BSD-3-Clause | Vector map in the map card (vendored, jarvis/web/vendor/maplibre) |
| OpenFreeMap | free, no key (OpenStreetMap data, ODbL) | Vector map tiles, style, sprite and fonts, fetched through a local cache |
| Open-Meteo | free, no key (CC BY 4.0) | Weather |
| Photon (Komoot) | free, no key | Place search |
