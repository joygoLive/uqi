#!/usr/bin/env bash
# UQI DGX 서비스 상태 확인
SERVICES=(uqi-embed uqi-rerank uqi-mcp ngrok-8765)

for svc in "${SERVICES[@]}"; do
  if systemctl is-active --quiet "$svc"; then
    pid=$(systemctl show -p MainPID --value "$svc")
    echo "✓ $svc (PID $pid)"
  else
    state=$(systemctl is-active "$svc" 2>/dev/null; true)
    echo "✗ $svc ($state)"
  fi
done

echo ""
curl -s http://127.0.0.1:7997/health && echo " ← embed" || echo "✗ embed 미응답"
curl -s http://127.0.0.1:7998/health && echo " ← rerank" || echo "✗ rerank 미응답"
curl -s http://127.0.0.1:8765/ -o /dev/null -w "%{http_code}" | grep -q "200" && echo "✓ mcp 8765" || echo "✗ mcp 미응답"
