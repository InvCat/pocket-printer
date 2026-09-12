#!/usr/bin/env bash
# Install Tronic Mini Pocket Printer as a shared CUPS/IPP (+ optional :9100) gateway on Raspberry Pi OS.
set -euo pipefail

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  echo "Run as root: sudo $0 [--address AA:BB:CC:DD:EE:FF]"
  exit 1
fi

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
REPO_ROOT=$(cd "$SCRIPT_DIR/.." && pwd)
PREFIX=/usr/local/lib/tronic-pocket-printer
CUPS_BACKEND_DIR=/usr/lib/cups/backend
CUPS_MODEL_DIR=/usr/share/cups/model
QUEUE_NAME=TronicPocket
ADDRESS=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --address) ADDRESS="$2"; shift 2 ;;
    -h|--help)
      echo "Usage: sudo $0 [--address AA:BB:CC:DD:EE:FF]"
      exit 0
      ;;
    *) echo "Unknown arg: $1"; exit 1 ;;
  esac
done

echo "==> Installing packages"
export DEBIAN_FRONTEND=noninteractive
# Seeed reTerminal DM / ostree images keep /usr read-only until remounted.
if findmnt -n -o OPTIONS /usr 2>/dev/null | grep -q '\bro\b'; then
  mount -o remount,rw /usr || true
  mount -o remount,rw / || true
fi
apt-get -o Acquire::Check-Valid-Until=false update -qq || true
apt-get -o Acquire::Check-Valid-Until=false install -y -qq \
  cups cups-client cups-bsd cups-filters \
  poppler-utils ghostscript \
  python3 python3-pil python3-pip \
  bluez bluez-tools \
  avahi-daemon \
  >/dev/null

echo "==> Installing Python library + CUPS backend"
mkdir -p "$PREFIX"
install -m 0644 "$REPO_ROOT/tronic_printer.py" "$PREFIX/tronic_printer.py"
install -m 0755 "$SCRIPT_DIR/tronic_cups_backend.py" "$PREFIX/tronic_cups_backend.py"
install -m 0755 "$SCRIPT_DIR/tronic-gateway.sh" "$PREFIX/tronic-gateway.sh"

# CUPS backends must be root-owned and executable.
install -o root -g root -m 0755 "$SCRIPT_DIR/tronic_cups_backend.py" "$CUPS_BACKEND_DIR/tronic"

install -m 0644 "$SCRIPT_DIR/tronic-pocket.ppd" "$CUPS_MODEL_DIR/tronic-pocket.ppd"

if [[ ! -f /etc/tronic-pocket-printer.conf ]]; then
  install -m 0644 "$SCRIPT_DIR/tronic-pocket.conf.example" /etc/tronic-pocket-printer.conf
fi

if [[ -n "$ADDRESS" ]]; then
  sed -i "s/^TRONIC_ADDR=.*/TRONIC_ADDR=${ADDRESS}/" /etc/tronic-pocket-printer.conf
fi

# shellcheck disable=SC1091
source /etc/tronic-pocket-printer.conf
if [[ -z "${TRONIC_ADDR:-}" || "$TRONIC_ADDR" == "55:55:00:00:00:00" ]]; then
  echo "WARNING: Set TRONIC_ADDR in /etc/tronic-pocket-printer.conf (printer Bluetooth MAC)."
fi

# CUPS rejects URIs with many colons (looks like broken IPv6). Use AA-BB-… form.
if [[ -n "${DEVICE_URI:-}" ]]; then
  DEVICE_URI_VALUE="$DEVICE_URI"
else
  DEVICE_URI_VALUE="tronic:${TRONIC_ADDR//:/-}"
fi


echo "==> System user for optional TCP gateway"
if ! id tronic-print >/dev/null 2>&1; then
  useradd --system --home /nonexistent --shell /usr/sbin/nologin tronic-print
fi
usermod -aG bluetooth,dialout,lp tronic-print || true

# CUPS (lp) also needs BT/serial access when using some setups
usermod -aG bluetooth,dialout lp || true

install -m 0644 "$SCRIPT_DIR/tronic-gateway.service" /etc/systemd/system/tronic-gateway.service
systemctl daemon-reload

echo "==> Configuring CUPS for LAN IPP sharing"
CUPS_CONF=/etc/cups/cupsd.conf
cp -a "$CUPS_CONF" "$CUPS_CONF.bak.$(date +%s)" || true

# Listen on all interfaces (idempotent-ish replacements)
if grep -q '^Listen localhost:631' "$CUPS_CONF"; then
  sed -i 's/^Listen localhost:631/Port 631/' "$CUPS_CONF"
fi
if ! grep -q '^Port 631' "$CUPS_CONF" && ! grep -q '^Listen \*:631' "$CUPS_CONF"; then
  echo 'Port 631' >> "$CUPS_CONF"
fi

# Allow local network access to the web/IPP interface
python3 - <<'PY'
from pathlib import Path
path = Path("/etc/cups/cupsd.conf")
text = path.read_text()
needle_locations = [
    ("<Location />", """<Location />
  Order allow,deny
  Allow @LOCAL
</Location>"""),
    ("<Location /admin>", """<Location /admin>
  Order allow,deny
  Allow @LOCAL
</Location>"""),
    ("<Location /admin/conf>", """<Location /admin/conf>
  AuthType Default
  Require user @SYSTEM
  Order allow,deny
  Allow @LOCAL
</Location>"""),
]
# Only patch if Allow @LOCAL missing inside Location /
if "Allow @LOCAL" not in text:
    # Minimal append of access rules — keep existing file mostly intact
    extra = """
# --- tronic-pocket-printer gateway ---
<Location />
  Order allow,deny
  Allow @LOCAL
</Location>
<Location /printers>
  Order allow,deny
  Allow @LOCAL
</Location>
<Location /admin>
  Order allow,deny
  Allow @LOCAL
</Location>
"""
    path.write_text(text.rstrip() + "\n" + extra + "\n")
PY

# Enable sharing on the LAN (Admin from LAN; not wide-open Internet)
cupsctl --share-printers --remote-admin WebInterface=yes || true

systemctl enable --now cups avahi-daemon
systemctl restart cups

echo "==> Creating / updating CUPS queue: $QUEUE_NAME"
# Remove old queue if present
lpadmin -x "$QUEUE_NAME" 2>/dev/null || true
lpadmin -p "$QUEUE_NAME" -E \
  -v "$DEVICE_URI_VALUE" \
  -P "$CUPS_MODEL_DIR/tronic-pocket.ppd" \
  -D "Tronic Mini Pocket Printer" \
  -L "Raspberry Pi gateway" \
  -o printer-is-shared=true

# Accept jobs even if printer currently offline
cupsenable "$QUEUE_NAME" || true
cupsaccept "$QUEUE_NAME" || true

echo "==> Enabling TCP :9100 gateway service"
systemctl enable --now tronic-gateway.service || {
  echo "NOTE: tronic-gateway.service failed to start — check TRONIC_ADDR and Bluetooth pairing."
  systemctl status tronic-gateway.service --no-pager || true
}

HOST=$(hostname -I 2>/dev/null | awk '{print $1}')
echo
echo "============================================================"
echo " Installed."
echo " CUPS queue : $QUEUE_NAME"
echo " Device URI : $DEVICE_URI_VALUE"
echo " IPP URL    : http://${HOST}:631/printers/${QUEUE_NAME}"
echo " TCP raw    : ${HOST}:9100"
echo " Config     : /etc/tronic-pocket-printer.conf"
echo
echo " Pair printer (once):"
echo "   bluetoothctl"
echo "   > power on"
echo "   > scan on"
echo "   > pair ${TRONIC_ADDR}"
echo "   > trust ${TRONIC_ADDR}"
echo "   > connect ${TRONIC_ADDR}"
echo
echo " Windows: Settings → Bluetooth & devices → Printers →"
echo "   Add device → The printer that I want isn't listed →"
echo "   Select shared printer by name →"
echo "   http://${HOST}:631/printers/${QUEUE_NAME}"
echo "============================================================"
