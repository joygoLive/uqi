#!/usr/bin/env bash
# UQI 맥북 서비스 상태 확인
SERVICES=(com.uqi.embed com.uqi.rerank com.uqi.mcp com.uqi.ngrok)

for svc in "${SERVICES[@]}"; do
  if launchctl list "$svc" &>/dev/null; then
    pid=$(launchctl list "$svc" | awk 'NR==2{print $1}')
    echo "✓ $svc (PID $pid)"
  else
    echo "✗ $svc 중지됨"
  fi
done

echo ""
curl -s http://127.0.0.1:7997/health && echo " ← embed" || echo "✗ embed 미응답"
curl -s http://127.0.0.1:7998/health && echo " ← rerank" || echo "✗ rerank 미응답"
curl -s http://127.0.0.1:8765/ -o /dev/null -w "%{http_code}" | grep -q "200" && echo "✓ mcp 8765" || echo "✗ mcp 미응답"
ngrok_url=$(curl -s http://127.0.0.1:4040/api/tunnels 2>/dev/null | grep -o '"public_url":"[^"]*"' | head -1 | sed 's/"public_url":"//;s/"//')
[ -n "$ngrok_url" ] && echo "✓ ngrok $ngrok_url" || echo "✗ ngrok 미응답"
