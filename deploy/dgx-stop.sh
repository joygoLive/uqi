#!/usr/bin/env bash
# UQI DGX 서비스 전체 종료
set -euo pipefail

SERVICES=(ngrok-8765 uqi-mcp uqi-rerank uqi-embed)

for svc in "${SERVICES[@]}"; do
  if systemctl is-active --quiet "$svc"; then
    sudo systemctl stop "$svc"
    echo "■ $svc 종료"
  else
    echo "- $svc 이미 중지됨"
  fi
done

echo ""
echo "✓ 모든 UQI 서비스 종료"
