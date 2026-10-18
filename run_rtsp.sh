#!/usr/bin/env bash
# run_rtsp.sh - Start MediaMTX RTSP server with V380 Bridge
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PATH="$HOME/.local/bin:$PATH"

if ! command -v mediamtx &>/dev/null; then
    echo "[ERROR] mediamtx binary not found in PATH or ~/.local/bin"
    exit 1
fi

# Load .env aman tanpa eval/source
if [ -f "$DIR/.env" ]; then
    while IFS="=" read -r k v || [ -n "$k" ]; do
        [[ "$k" =~ ^[[:space:]]*# ]] && continue
        [ -z "$k" ] && continue
        k="${k// /}"
        v="${v#\"}"
        v="${v%\"}"
        v="${v#\'}"
        v="${v%\'}"
        export "$k=$v"
    done < "$DIR/.env"
fi

MODE="${1:-${RTSP_MODE:-${MODE:-production}}}"

# ponytail: mode switch MTX_LOGLEVEL warn/debug. Upgrade jika butuh filter log ffmpeg terpisah.
case "$MODE" in
    prod|production|--prod|-p)
        export MTX_LOGLEVEL="warn"
        MODE_NAME="production"
        ;;
    debug|--debug|-d)
        export MTX_LOGLEVEL="debug"
        MODE_NAME="debug"
        ;;
    *)
        echo "[ERROR] Mode tidak valid: $MODE"
        echo "Usage: $0 [prod|debug] atau set RTSP_MODE di .env"
        exit 1
        ;;
esac

# Generate self-signed cert jika belum ada
if [ ! -f "$DIR/auto.crt" ] || [ ! -f "$DIR/auto.key" ]; then
    echo "[TLS] auto.crt/auto.key tidak ada, generating..."
    openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -days 3650 -nodes \
        -keyout "$DIR/auto.key" -out "$DIR/auto.crt" -subj "/CN=mediamtx" 2>/dev/null
    chmod 600 "$DIR/auto.key" "$DIR/auto.crt"
fi

IP="$(hostname -I | awk '{print $1}')"
echo "[RTSP] Mode      : $MODE_NAME (log: $MTX_LOGLEVEL)"
echo "[RTSP] RTSP URL  : rtsp://$IP:8554/v380"
echo "[RTSP] WebRTC URL: http://$IP:8889/v380 (buka di browser)"
echo "[RTSP] HLS URL   : http://$IP:8888/v380"
echo "[RTSP] Tekan Ctrl+C untuk stop."

cd "$DIR"
exec mediamtx "$DIR/mediamtx.yml"
