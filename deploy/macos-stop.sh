#!/usr/bin/env bash
# UQI 맥북 서비스 전체 종료
set -euo pipefail

LAUNCH_AGENTS="$HOME/Library/LaunchAgents"
SERVICES=(com.uqi.ngrok com.uqi.mcp com.uqi.rerank com.uqi.embed)

for svc in "${SERVICES[@]}"; do
  plist="$LAUNCH_AGENTS/$svc.plist"
  if launchctl list "$svc" &>/dev/null; then
    launchctl unload "$plist" 2>/dev/null || true
    echo "■ $svc 종료"
  else
    echo "- $svc 이미 중지됨"
  fi
done

echo ""
echo "✓ 모든 UQI 서비스 종료"
