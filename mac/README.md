# Jarvis inbox (Mac side), in plain words

This lets the Jarvis Go send a link or a file to your Mac, and your Mac opens it. It only listens to a private channel on the internet. Nothing on your Mac is opened to the network. The Mac also replies on a second private channel so the Go knows if the message was opened or refused (the reply never contains the link or the file).

## Easiest way: let the Go show you the steps
Say **"pair my Mac"** to the Go. It shows one card with the steps, the command to paste, and the code. The card says **Mac connected** when your Mac answers. Press **Send test to my Mac**: a harmless link opens on the Mac and the card says delivered, or "Mac did not open it" and why.

## The same steps by hand
Have this project's `mac/` folder on your Mac (clone the repository or download its ZIP once).
1. Open **Terminal** on the Mac (Cmd + Space, type Terminal, Enter).
2. Paste this one command. It finds the folder by itself, then asks for the code that is on the Go's screen (type it, it stays hidden) and starts listening. Leave the window open:
   `cd "$(dirname "$(find ~ -maxdepth 5 -name jarvis_inbox.py 2>/dev/null | head -1)")" && python3 jarvis_inbox.py pair && python3 jarvis_inbox.py run`
   If it finds nothing, `cd` into the `mac` folder yourself and run the two python commands.
3. Optional, so you never do this again: `python3 jarvis_inbox.py install`. The Mac then starts listening by itself when you log in.

To get newer Mac files: `git pull` in your clone, or download the ZIP again. After an update, run `pair` again once so the Mac can reply to the Go.

## What the Mac says when it refuses something
It prints one plain line for every message it drops and why: not an https link, file type not allowed, file too big, the file changed on the way, bad signature, too old, or already handled. If the message was genuinely signed, the Go shows "Mac did not open it: <reason>" on a card. A message with a bad signature is only printed on the Mac (anyone could have sent it, so the Go is not told).

## Safety rules (unchanged)
Only the Go and your Mac know the code, and the code is only shown on the Go's screen, never spoken or saved in a log. Every message is signed with it and is valid for 5 minutes, once. Only https links open, never links containing a password. Files must be pictures, video, PDF or text, under 25 MB, and must match their checksum.
