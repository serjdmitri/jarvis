-- Jarvis voice assistant: window rule + hold-to-talk keys. Loaded from ~/.config/hypr/bindings.lua
local jarvis = (os.getenv("HOME") or "") .. "/jarvis"

-- small floating window, bottom-right, on every workspace
o.window("^local\\.jarvis$", {
  float = true,
  pin = true,
  size = { 330, 380 },
  move = "monitor_w-window_w-20 monitor_h-window_h-20",
})

-- the pooled Chromium that shows opened pages (and is re-created every few minutes to free memory) opens DIRECTLY on its hidden workspace, floating and without focus. Without this rule
-- every new one opened tiled on the workspace you were using: your full-screen terminal shrank to half for about a second (the "random window glitch", proved with logs/windows.log).
o.window("^chrome-127\\.0\\.0\\.1__static_blank\\.html-Default$", { workspace = "special:jarvis-pool silent", float = true, no_initial_focus = true, no_anim = true, size = { 876, 550 } })
-- the quick popup window: floating, never takes focus, never part of the layout
-- (an --app window's class comes from its address: chrome-127.0.0.1__-Default; the quiet rule covers the popup and anything it opens)
o.window("^chrome-127\\.0\\.0\\.1__-Default$", { float = true, no_initial_focus = true, no_anim = true, size = { 340, 340 } })

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

-- Quick type box next to the pointer: Alt+Space or Ctrl+Shift+Space (which of them works is a setting; the server ignores the other). Works over any app.
o.bind("ALT + SPACE", "Jarvis: type box near the pointer", jarvis .. "/bin/jarvisctl typebox alt")
o.bind("CTRL + SHIFT + SPACE", "Jarvis: type box near the pointer (second shortcut)", jarvis .. "/bin/jarvisctl typebox ctrl")

-- SUPER+SHIFT+C: circle to ask. Draw a circle or rectangle on the screen with a finger or the mouse and ask out loud (hold SUPER+J while you draw, or ask first).
o.bind("SUPER + SHIFT + C", "Jarvis: circle to ask", jarvis .. "/bin/jarvisctl circle")

-- start with the session
hl.on("hyprland.start", function()
  hl.exec_cmd(jarvis .. "/bin/jarvis-watch")   -- the supervisor: starts the backend, kiosk and orb, and brings back whatever dies (a deliberate quit stops it)
end)
