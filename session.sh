#!/bin/bash
# Field session driver for the 9N3 cluster work.
#
# Why this exists: kw1281test is one-shot (it re-runs the KWP handshake on every
# invocation), so a session is dozens of separate commands. This wrapper gives them a
# single port to talk to, and — more importantly — appends every command and its full
# output to a timestamped log, so the session is reconstructible afterwards. The protocol
# in CLAUDE.md requires logging every command and its output; this is what enforces it.
#
#   ./session.sh port                 detect and remember the adapter's port
#   ./session.sh run <COMMAND> [args] run a kw1281test command against the cluster
#   ./session.sh log                  show the current session log
#
# Everything goes to controller address 17 (cluster) at 10400 baud.

set -u
DIR="$(cd "$(dirname "$0")" && pwd)"
PORTFILE="$DIR/.session_port"
LOGDIR="$DIR/logs"
BAUD=10400
ADDR=17

mkdir -p "$LOGDIR"
LOG="$LOGDIR/session_$(date +%Y%m%d).log"

detect_port() {
    # Prefer real USB serial adapters; the Bluetooth and debug-console nodes are always
    # present on macOS and are never the KKL cable.
    local found
    found=$(ls /dev/cu.* 2>/dev/null | grep -v -E "Bluetooth|debug-console")
    local n
    n=$(printf '%s\n' "$found" | grep -c . )

    if [ "$n" -eq 0 ]; then
        echo "No USB serial adapter found." >&2
        echo "Plug the KKL cable into USB, then run this again." >&2
        echo "(present: $(ls /dev/cu.* 2>/dev/null | tr '\n' ' '))" >&2
        return 1
    fi
    if [ "$n" -gt 1 ]; then
        echo "More than one candidate — pass the right one explicitly:" >&2
        printf '  %s\n' $found >&2
        return 1
    fi
    # .NET's SerialPort.BreakState does not drive the K-line on macOS, so the 5-baud
    # wakeup never reaches the cluster through a /dev/cu.* node. kw1281test has to use its
    # FTDI D2XX path instead, which is selected by passing the adapter's bare SERIAL
    # NUMBER rather than a device path. Hand back the serial number, not the node.
    printf '%s\n' "${found##*/dev/cu.usbserial-}"
}

case "${1:-}" in
port)
    if [ -n "${2:-}" ]; then
        PORT="$2"
    else
        PORT=$(detect_port) || exit 1
    fi
    printf '%s' "$PORT" > "$PORTFILE"
    echo "port = $PORT"
    echo "log  = $LOG"
    ;;

run)
    shift
    if [ ! -f "$PORTFILE" ]; then
        echo "No port set. Run: ./session.sh port" >&2
        exit 1
    fi
    PORT=$(cat "$PORTFILE")
    {
        echo
        echo "===== $(date '+%Y-%m-%d %H:%M:%S')  kw1281test $PORT $BAUD $ADDR $*"
    } >> "$LOG"
    # tee so the operator sees it live AND it lands in the log
    "$DIR/kw1281test" "$PORT" "$BAUD" "$ADDR" "$@" 2>&1 | tee -a "$LOG"
    exit "${PIPESTATUS[0]}"
    ;;

log)
    if [ -f "$LOG" ]; then cat "$LOG"; else echo "(no log yet: $LOG)"; fi
    ;;

*)
    awk 'NR>1 && /^#/ {sub(/^# ?/,""); print; next} NR>1 {exit}' "$0"
    ;;
esac
