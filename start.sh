#!/usr/bin/env bash
set -euo pipefail
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ ! -x "$APP_DIR/.venv/bin/uvicorn" ]]; then echo 'Backend未セットアップ: READMEの初回セットアップを実行してください' >&2; exit 1; fi
if [[ ! -d "$APP_DIR/frontend/node_modules" ]]; then echo 'Frontend未セットアップ: READMEの初回セットアップを実行してください' >&2; exit 1; fi
for port in 8000 5173; do
 if (echo >/dev/tcp/127.0.0.1/$port) >/dev/null 2>&1; then echo "ポート $port は使用中です" >&2; exit 1; fi
done
cleanup(){ [[ -n "${BACK_PID:-}" ]] && kill "$BACK_PID" 2>/dev/null || true; [[ -n "${FRONT_PID:-}" ]] && kill "$FRONT_PID" 2>/dev/null || true; wait 2>/dev/null || true; }
trap cleanup EXIT INT TERM
(cd "$APP_DIR/backend" && "$APP_DIR/.venv/bin/uvicorn" app.main:app --host 127.0.0.1 --port 8000) & BACK_PID=$!
(cd "$APP_DIR/frontend" && npm run dev) & FRONT_PID=$!
echo 'http://localhost:5173 を開いてください。終了は Ctrl+C。'
while kill -0 "$BACK_PID" 2>/dev/null && kill -0 "$FRONT_PID" 2>/dev/null; do sleep 1; done
exit 1
