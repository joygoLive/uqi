#!/usr/bin/env bash
# UQI DGX 서비스 전체 시작
set -euo pipefail

SERVICES=(uqi-embed uqi-rerank uqi-mcp ngrok-8765)

for svc in "${SERVICES[@]}"; do
  if systemctl is-active --quiet "$svc"; then
    echo "✓ $svc 이미 실행 중"
  else
    sudo systemctl start "$svc"
    echo "▶ $svc 시작"
  fi
done

echo ""
echo "헬스체크:"
sleep 3
curl -s http://127.0.0.1:7997/health && echo " ← embed" || echo "✗ embed 아직 미준비 (bge-m3 로딩 중)"
curl -s http://127.0.0.1:7998/health && echo " ← rerank" || echo "✗ rerank 아직 미준비"
curl -s http://127.0.0.1:8765/ -o /dev/null -w "%{http_code}" | grep -q "200" && echo "✓ mcp 8765" || echo "✗ mcp 미준비"
ngrok_url=$(curl -s http://127.0.0.1:4040/api/tunnels 2>/dev/null | grep -o '"public_url":"[^"]*"' | head -1 | sed 's/"public_url":"//;s/"//')
[ -n "$ngrok_url" ] && echo "✓ ngrok $ngrok_url" || echo "✗ ngrok 미준비"
