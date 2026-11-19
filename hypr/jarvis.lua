-- Jarvis voice assistant: window rule + hold-to-talk keys. Loaded from ~/.config/hypr/bindings.lua
local jarvis = (os.getenv("HOME") or "") .. "/jarvis"

-- small floating window, bottom-right, on every workspace
o.window("^local\\.jarvis$", {
  float = true,
  pin = true,
  size = { 330, 380 },
  move = "monitor_w-window_w-20 monitor_h-window_h-20",
})

-- the full-screen Jarvis (the kiosk) lives on its OWN workspace 9, so no ordinary window ever lands next to it. (When one did, Hyprland ended its
-- fullscreen and tiled it into a quarter of the screen: the "dashed rectangle with a thin blue line".) The orb stays on top of every workspace.
o.window("^jarvis-ui$", { workspace = "9", fullscreen = true })

-- the orb (bin/jarvis-orb) is a transparent GTK layer-shell OVERLAY surface (above every window, fullscreen too, on every workspace), so it needs no window rules
-- pinned windows are still allowed over fullscreen: opened content is a floating pinned window placed over the Jarvis frame
hl.config({ binds = { allow_pin_fullscreen = true } })

-- hold SUPER+J to talk, release to send. Hold SUPER+SHIFT+L to talk about what's on screen.
o.bind_hold("SUPER + J", "Jarvis: hold to talk", jarvis .. "/bin/jarvisctl start",
            "Jarvis: send", jarvis .. "/bin/jarvisctl stop")
o.bind_hold("SUPER + SHIFT + L", "Jarvis: hold to look + talk", jarvis .. "/bin/jarvisctl look",
            "Jarvis: send", jarvis .. "/bin/jarvisctl stop")

-- SUPER+SHIFT+J toggles Jarvis between the corner orb and full screen (his own workspace)
o.bind("SUPER + SHIFT + J", "Jarvis: orb <-> full screen", jarvis .. "/bin/jarvisctl toggle")

-- hold SUPER+SHIFT+D to DICTATE: release and the words are typed into the focused window (SUPER+T is Omarchy's float toggle, so not that one)
o.bind_hold("SUPER + SHIFT + D", "Jarvis: hold to dictate", jarvis .. "/bin/jarvisctl dictate start",
            "Jarvis: type it", jarvis .. "/bin/jarvisctl dictate stop")

-- SUPER+ALT+M toggles MUTE (deaf mode: nothing listens for wake phrases; hold-to-talk still works)
o.bind("SUPER + ALT + M", "Jarvis: toggle mute (deaf mode)", jarvis .. "/bin/jarvisctl mute toggle")

-- SUPER+ALT+J: back to the Jarvis window (also: say "back to Jarvis"/"close that", or tap the dock orb)
o.bind("SUPER + ALT + J", "Jarvis: back to the Jarvis window", jarvis .. "/bin/jarvisctl back")

-- SUPER+ESCAPE: ABORT. Leaves the Jarvis full screen and returns to the normal desktop WITHOUT killing Jarvis (press it again to go back). It talks only to Hyprland,
-- so it works even if the Jarvis page hung or the server crashed.
o.bind("SUPER + ESCAPE", "Jarvis: abort to the normal desktop", jarvis .. "/bin/jarvis-escape")

-- start with the session
hl.on("hyprland.start", function()
  hl.exec_cmd(jarvis .. "/bin/jarvis-watch")   -- the supervisor: starts the backend, kiosk and orb, and brings back whatever dies (a deliberate quit stops it)
end)
