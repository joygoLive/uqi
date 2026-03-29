#!/usr/bin/env python3
"""
UQI MCP Bridge: Claude Desktop stdio <-> DGX SSE 서버
DGX 서버 재시작 시 자동 재연결 + 재초기화
"""
import sys
import json
import threading
import time
import requests

SSE_URL = "http://100.99.17.55:8765/sse"
RECONNECT_DELAY = 3

def log(msg):
    print(f"[bridge] {msg}", file=sys.stderr, flush=True)

def send_to_stdout(data: str):
    sys.stdout.write(data + "\n")
    sys.stdout.flush()

class SSEBridge:
    def __init__(self):
        self.messages_url = None
        self.session = requests.Session()
        self.connected = False
        self.pending = []        # 재연결 전 버퍼
        self.lock = threading.Lock()
        self.request_id = 0      # 재초기화용 ID 카운터

    def _next_id(self):
        self.request_id += 1
        return self.request_id

    def _reinitialize(self):
        """재연결 후 Claude Desktop 대신 initialize + tools/list 수행"""
        log("재초기화 시작...")
        
        init_msg = json.dumps({
            "jsonrpc": "2.0",
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "claude-ai", "version": "0.1.0"}
            },
            "id": self._next_id()
        })
        self._post(init_msg)
        time.sleep(0.3)

        notif_msg = json.dumps({
            "jsonrpc": "2.0",
            "method": "notifications/initialized"
        })
        self._post(notif_msg)
        time.sleep(0.1)

        tools_msg = json.dumps({
            "jsonrpc": "2.0",
            "method": "tools/list",
            "params": {},
            "id": self._next_id()
        })
        self._post(tools_msg)
        log("재초기화 완료")

    def _post(self, message: str):
        if not self.messages_url:
            return
        try:
            self.session.post(
                self.messages_url,
                data=message,
                headers={"Content-Type": "application/json"},
                timeout=30,
            )
        except Exception as e:
            log(f"POST 오류: {e}")

    def connect(self):
        """SSE 서버에 연결 - 끊기면 자동 재연결"""
        first_connect = True
        while True:
            try:
                log(f"SSE 서버 연결 시도: {SSE_URL}")
                response = self.session.get(SSE_URL, stream=True, timeout=None)

                for line in response.iter_lines():
                    if not line:
                        continue
                    line = line.decode("utf-8")

                    if line.startswith("data:"):
                        data = line[5:].strip()
                        if data.startswith("/messages"):
                            base = SSE_URL.rsplit("/sse", 1)[0]
                            with self.lock:
                                self.messages_url = base + data
                                self.connected = True
                            log(f"messages endpoint: {self.messages_url}")

                            # 재연결 시 재초기화 (첫 연결 제외)
                            if not first_connect:
                                self._reinitialize()

                            # 첫 연결 시에만 버퍼 전송
                            with self.lock:
                                if not self.pending:
                                    pass
                                else:
                                    for msg in self.pending:
                                        self._post(msg)
                                self.pending.clear()

                            first_connect = False

                            self._read_sse(response)
                            break

            except Exception as e:
                log(f"연결 오류: {e}")

            with self.lock:
                self.connected = False
                self.messages_url = None
            log(f"{RECONNECT_DELAY}초 후 재연결...")
            time.sleep(RECONNECT_DELAY)

    def _read_sse(self, response):
        """SSE 스트림 읽어서 stdout으로 전달"""
        try:
            for line in response.iter_lines():
                if not line:
                    continue
                line = line.decode("utf-8")
                if line.startswith("data:"):
                    data = line[5:].strip()
                    try:
                        obj = json.loads(data)
                        if "result" in obj and "tools" in obj.get("result", {}):
                            tools = obj["result"]["tools"]
                            log(f"tools/list 전송: {len(tools)}개 - {[t['name'] for t in tools]}")
                        send_to_stdout(data)
                    except json.JSONDecodeError as e:
                        log(f"JSON 파싱 오류 (데이터 버림): {e} / data[:100]={data[:100]}")
        except Exception as e:
            log(f"SSE 읽기 오류: {e}")

    def send(self, message: str):
        """stdin 메시지를 SSE 서버로 POST, 미연결 시 버퍼"""
        with self.lock:
            if not self.messages_url:
                log("미연결 상태, 메시지 버퍼링")
                self.pending.append(message)
                return
        self._post(message)


def main():
    bridge = SSEBridge()

    t = threading.Thread(target=bridge.connect, daemon=True)
    t.start()

    # 첫 연결 대기
    for _ in range(50):
        if bridge.connected:
            break
        time.sleep(0.1)

    sent = set()  # 중복 전송 방지

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            msg_id = obj.get("id")
            if msg_id is not None:
                if msg_id in sent:
                    log(f"중복 메시지 무시: id={msg_id}")
                    continue
                sent.add(msg_id)
        except json.JSONDecodeError:
            pass
        bridge.send(line)

if __name__ == "__main__":
    main()