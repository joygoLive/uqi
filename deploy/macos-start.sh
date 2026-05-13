#!/usr/bin/env bash
# UQI 맥북 서비스 전체 시작
set -euo pipefail

LAUNCH_AGENTS="$HOME/Library/LaunchAgents"
SERVICES=(com.uqi.embed com.uqi.rerank com.uqi.mcp com.uqi.ngrok)

for svc in "${SERVICES[@]}"; do
  plist="$LAUNCH_AGENTS/$svc.plist"
  if [ ! -f "$plist" ]; then
    echo "⚠ $svc.plist 없음 — setup.sh 먼저 실행하세요"
    continue
  fi
  if launchctl list "$svc" &>/dev/null; then
    echo "✓ $svc 이미 실행 중"
  else
    launchctl load "$plist"
    echo "▶ $svc 시작"
  fi
done

echo ""
echo "헬스체크:"
sleep 2
curl -s http://127.0.0.1:7997/health && echo " ← embed" || echo "✗ embed 아직 미준비 (bge-m3 로딩 중)"
curl -s http://127.0.0.1:7998/health && echo " ← rerank" || echo "✗ rerank 아직 미준비"
curl -s http://127.0.0.1:8765/ -o /dev/null -w "%{http_code}" | grep -q "200" && echo "✓ mcp 8765" || echo "✗ mcp 미준비"
