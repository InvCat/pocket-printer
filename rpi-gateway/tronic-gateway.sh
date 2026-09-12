#!/bin/bash
# Wrapper so systemd can source /etc/tronic-pocket-printer.conf safely.
set -euo pipefail
CONF=/etc/tronic-pocket-printer.conf
if [[ -f "$CONF" ]]; then
  # shellcheck disable=SC1090
  set -a
  # Strip comments / blank lines before sourcing
  # shellcheck disable=SC1091
  source <(grep -E '^[A-Za-z_][A-Za-z0-9_]*=' "$CONF" || true)
  set +a
fi

URI="${DEVICE_URI:-}"
if [[ -z "$URI" ]]; then
  : "${TRONIC_ADDR:?Set TRONIC_ADDR or DEVICE_URI in $CONF}"
  # CUPS-safe dash MAC (also fine for our backend parser)
  URI="tronic:${TRONIC_ADDR//:/-}"
fi

exec /usr/local/lib/tronic-pocket-printer/tronic_cups_backend.py \
  --tcp \
  --host "${TCP_HOST:-0.0.0.0}" \
  --port "${TCP_PORT:-9100}" \
  --uri "$URI" \
  --density "${TRONIC_DENSITY:-1}"
