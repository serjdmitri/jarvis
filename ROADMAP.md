# Jarvis roadmap

## Keyboardless checklist (goal: detach the keyboard and use Jarvis only by touch and voice)

Rule: Jarvis never disables the keyboard in software. You detach it yourself after the drill passes.
Status words: DONE (built and tested here), CHECKED (looked at the real configuration), NEEDS YOU (a system change or a physical test that only you can do).

| # | Item | Status | What was found / built |
|---|------|--------|------------------------|
| 1 | Boot straight into Jarvis | PARTLY | Hyprland starts Jarvis by itself (`hypr/jarvis.lua`: `hyprland.start` runs `bin/jarvis-watch`, which brings back the backend, kiosk and orb). **The login screen (SDDM) has no automatic login**, so after a boot it waits for a password. Automatic login is a system file and needs your yes: see "Needs you" below. |
| 2 | Quiet self-heal | DONE | The "restarted because: ..." bar now appears only when the supervisor has restarted something 3 or more times inside its window. A normal single restart shows nothing. |
| 3 | On-screen keyboard | DONE | Built into the Jarvis page (no install): large keys, a hold-to-dictate button, opens only after a TOUCH on a text box, never by itself, closes with HIDE or when you tap away. Settings > Wi-Fi and system > On-screen keyboard turns it off. `squeekboard` is also installed on the machine (not running); nothing new was installed. |
| 4 | Wi-Fi by touch | DONE | Settings > Wi-Fi and system: scan, tap a network, type the password on the on-screen keyboard. The password goes to NetworkManager on stdin (never on a command line, never saved or logged by Jarvis). The screen opens by itself when no known network is in range for 30 seconds. Connecting was tested with fakes only (a real connect would drop this machine's network). |
| 5 | System controls | DONE | Restart Jarvis, restart the Go, shut down (each asks for a confirm: a CONFIRM button by touch, "say yes" by voice), screen off/on, brightness, volume, mute. Fixed commands only. |
| 6 | Power button | CHECKED | `HandlePowerKey=ignore` (Omarchy's logind setting): a press does nothing, a long press does nothing. Proposal (needs your yes, not changed): short press = screen off/on through a Hyprland key bind for `XF86PowerOff`. |
| 7 | Type Cover detach / attach | CHECKED, NEEDS YOU | Nothing on the machine reacts to the cover: no auto-rotate service (`iio-sensor-proxy` is not installed), no switch binds in the Hyprland config, `squeekboard` is not running, and the monitor transform is fixed at 0. So by configuration it cannot rotate the screen or pop up a keyboard. I cannot detach the cover from here: the physical test is in the drill. |
| 8 | Remote rescue | CHECKED, NEEDS YOU | SSH is **off** (`sshd` inactive and disabled, nothing listening on port 22). With it off there is no way to repair Jarvis from the Mac. Turning it on is your decision; the exact steps and the one-line Mac commands are in PROGRESS.md under "Needs Sergey". |
| 9 | Offline behaviour | DONE | Works with no internet: the clock, timers and reminders, the time and date, volume and brightness, notes and lists, opening things, music from files, machine status, the maps you already opened (cached tiles). Everything else says "no internet" plainly. Ask "what works offline". |
| 10 | The drill | NEEDS YOU | The software parts are tested (see PROGRESS.md). The full drill needs the keyboard physically detached and a reboot: reboot, then by touch and voice only: wake from the clock, ask a question, play music, open the map and get a travel time, open the camera and close it, send a file to the phone and to the Mac, change Wi-Fi, restart Jarvis, shut down. |

### Still needs a keyboard

| What | Why | Plan to remove it |
|------|-----|-------------------|
| Typing the login password after a boot | SDDM has no automatic login | Add `/etc/sddm.conf.d/20-jarvis-autologin.conf` (see Needs Sergey). The disk is not encrypted, so nothing else asks for a passphrase before the desktop. |
| Pasting the Spotify client ID, the Hugging Face token (on the Mac) | Needs a text box | Use the on-screen keyboard here; the Mac token is typed on the Mac. |
| Any other app that is not Jarvis (a terminal, Claude Code) | They are ordinary Wayland apps | `squeekboard` is installed and can be started when needed; Jarvis's own dictate button also types into the focused window (`wtype`). |
| Fixing a broken Jarvis from far away | SSH is off | Turn on key-only SSH (your decision). |

## Ideas not built yet

See the feature list at the end of each round's report in PROGRESS.md.
