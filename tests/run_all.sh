#!/bin/sh
# Runs every test that needs no running server and prints one line each. The routing test (run_phrases.py) needs the throwaway server: start bin/jarvis-testserver first.
cd "$(dirname "$0")/.." || exit 1
for t in apps_test connect_test track_test track_real_test look_select_test window_test spotify_fake_test spotify_player_test maps_a7_test vault_test context_test desk_test phone_dl_test links_test \
         settings_test location_test machelper_test presence_test prompt27_parts_print map_tiles product_test round26_routing markets_routing camera_source sendmac_helper_test transfer_matrix live_snapshot; do
  r=$(timeout 300 .venv/bin/python tests/$t.py 2>&1 | grep -E "^RESULT|passed|FAIL" | tail -2 | tr '\n' ' ')
  echo "$t: ${r:-(no summary)}"
done
