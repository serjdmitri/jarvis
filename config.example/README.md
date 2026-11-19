# Where your private data lives

Nothing private is stored in this repository. At runtime Jarvis keeps it here, outside the code folder:

| What | Where | Notes |
|---|---|---|
| Settings (home location, speed, clock, phone camera address) | `~/.config/jarvis/settings.json` | see `settings.example.json`; set the location by saying "set my location" |
| Phone alerts topic (ntfy) | `~/.config/jarvis/ntfy_topic` | mode 600, random, printed only by `bin/jarvis-phone topic` to a terminal |
| Mac pairing (derived topic + key; the code itself is never saved) | `~/.config/jarvis/mac.json` | mode 600 |
| Spotify client id and tokens | `~/.config/jarvis/spotify_client_id`, `spotify_tokens.json` | mode 600 |
| Orb position | `~/.config/jarvis/orb.json` | |
| Memory, notes, lists, reminders, scenes | `<project>/data/`, `<project>/notes/` | ignored by git |
| Logs, screenshots, voice cache | `<project>/logs/`, `shots/`, `run/` | ignored by git |
