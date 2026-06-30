#!/usr/bin/env bash
# SessionStart hook: make the repo ready to develop and test.
# Best-effort and non-fatal — never block a session if offline.
set +e

echo "[setup] KiloVault HLX+ Monitor — preparing environment"

# Tests need only pytest; bleak/pyserial are optional (real-hardware transports).
python3 -m pytest --version >/dev/null 2>&1 || pip3 install --quiet pytest 2>/dev/null

# Quick import sanity check (core must work with no extra deps).
python3 - <<'PY' 2>/dev/null
try:
    import kilovault
    from kilovault.cli import build_parser
    from kilovault.protocol import decode_frame, encode_frame
    print("[setup] core imports OK (v%s)" % kilovault.__version__)
except Exception as e:
    print("[setup] core import problem:", e)
PY

echo "[setup] Run tests with:   python3 -m pytest -q"
echo "[setup] Try the app with: python3 -m kilovault.cli serve --simulate"
exit 0
