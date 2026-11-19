# Contributing

Thanks for looking. This is a personal hobby project for one specific machine (a fanless Surface Go on Omarchy/Hyprland), so small, focused changes are easiest to review.

- **Bugs:** open an issue with what you said or pressed, what you expected, and the output of `bin/jarvis-doctor` (it prints no private data).
- **Pull requests:** keep them small; say what you tested and on what hardware. Run `.venv/bin/python tests/intent_phrases.py` (needs the Claude login) when you touch routing, and open the page with `?selftest=1` when you touch the UI.
- **House rules, please keep them:**
  1. The AI never runs shell text. It returns structured intents from a fixed list and the code decides what runs (`jarvis/intents.py`, `jarvis/actions.py`).
  2. Secrets (pairing codes, ntfy topics, tokens, keys) are never printed, logged or committed.
  3. Nothing is sent anywhere unless the user asked for it.
  4. Keep it light: this runs on a weak fanless CPU. Prefer CSS transforms, avoid per-frame JavaScript, no new always-on loops.
- **Style:** plain Python, short functions, a one-line docstring that says why.
