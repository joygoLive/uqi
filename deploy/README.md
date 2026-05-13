# UQI Deploy — 인프라 / 서비스 설정

이 디렉토리는 UQI 가 DGX (또는 동등한 NVIDIA aarch64 Linux) 머신에서 어떻게 배포·운영되는지를 코드로 박제한다. 코드 (src/) 가 아닌, **서비스 레이어** 의 ground truth.

## 구성

```
deploy/
├── Dockerfile           # uqi-rag 컨테이너 이미지 (bge-m3 + reranker 추론 환경)
├── embed_server.py      # 임베딩 서버 (FastAPI, OpenAI-compatible /embeddings)
├── rerank_server.py     # 크로스인코더 reranker 서버 (FastAPI /rerank)
├── setup.sh             # 전체 셋업 자동화 (clone → venv → pip → docker → systemd)
└── systemd/
    ├── uqi-mcp.service     # MCP 메인 서버 (호스트 venv 직접 실행, :8765 SSE + webapp)
    ├── uqi-embed.service   # bge-m3 컨테이너 (loopback :7997)
    ├── uqi-rerank.service  # bge-reranker-v2-m3 컨테이너 (loopback :7998)
    └── ngrok-8765.service  # ngrok 터널 (:8765 → public reserved URL)
```

## 아키텍처

```
   브라우저 (외부)                     Claude Desktop / 클라이언트
        │                                  │
        │ HTTPS                            │ SSE
        ▼                                  │
   ngrok-8765.service                      │
   (public URL → :8765)                    │
        │                                  │
        └──────────► :8765 ◄───────────────┘
                       │
              uqi-mcp.service (host venv, uqi/.venv_transpile)
                       │   • /            → webapp/uqi_webapp.html
                       │   • /sse         → MCP SSE endpoint
                       │   • /notion-backup → quartz-site/public (symlink)
                       │
                       ├──► sqlite-vec (RAG storage)
                       │
                ┌──────┴──────┐
                ▼             ▼
         uqi-embed       uqi-rerank
         :7997 (Docker)  :7998 (Docker)
         GPU            GPU
                │
          bge-m3 / reranker
          (~/models/hf)
```

- **uqi-mcp** 는 호스트 venv 에서 실행 (CUDA quantum sim, qiskit-aer 등 무거운 의존성).
  webapp 정적 서빙 + notion-backup mount 모두 같은 :8765 포트에서 처리.
- **uqi-embed / uqi-rerank** 는 격리된 컨테이너 (PyTorch + sentence-transformers 만 필요)
- **ngrok-8765** 는 :8765 를 reserved public URL 로 노출 (외부 접근 시 필요)
- MCP → embed/rerank 는 로컬 HTTP loopback (외부 노출 X)

## 사전 요건

- Linux (aarch64 검증, x86_64 도 가능)
- NVIDIA GPU + 드라이버 + `nvidia-container-toolkit`
- Docker (`docker.service` enabled)
- Python 3.12 venv: `<TARGET_DIR>/uqi/.venv_transpile` (self-contained)
  - `requirements.txt` 기준 의존성 설치 완료
- 모델 가중치: `/home/sean/models/hf/` (bge-m3 + bge-reranker-v2-m3, 약 8.6 GB)
  - 미리 받지 않아도 첫 기동 시 HuggingFace 에서 자동 다운로드

## 설치 절차

### 빠른 길 — `setup.sh` 자동화

```bash
bash deploy/setup.sh           # 인터랙티브 (각 단계 확인)
bash deploy/setup.sh --yes     # 모든 prompt 'yes'
```

자동화 범위: clone (uqi 의무 + 선택 quartz/aer-fork) → venv → pip install →
docker build → systemd install + enable. 자세한 옵션은 `setup.sh --help`.

> .env 채우기, ngrok authtoken 등록, 서비스 start 는 자동화 후 수동 단계로 남음.

---

### 수동 절차 (참고)

#### 1. Docker 이미지 빌드

```bash
cd <TARGET_DIR>/uqi/deploy
docker build -t uqi-rag:0.1 .
```

이미지는 NGC PyTorch 25.06 (arm64, CUDA 13) 기반이라 첫 빌드 시 시간이 걸린다 (대략 24 GB). NVIDIA GPU 가 있는 호스트에서만 빌드/실행 가능.

#### 2. systemd 유닛 설치

```bash
sudo cp deploy/systemd/uqi-embed.service   /etc/systemd/system/
sudo cp deploy/systemd/uqi-rerank.service  /etc/systemd/system/
sudo cp deploy/systemd/uqi-mcp.service     /etc/systemd/system/
sudo cp deploy/systemd/ngrok-8765.service  /etc/systemd/system/
sudo systemctl daemon-reload
```

> Unit 파일에 hardcoded 된 `/home/sean/...` 경로를 본인 환경에 맞게 보정 (또는 setup.sh 사용).

#### 3. ngrok authtoken (외부 접근 시)

```bash
sudo snap install ngrok          # 미설치 시
ngrok config add-authtoken <YOUR_TOKEN>
# reserved domain 쓰려면 ngrok-8765.service 의 --url= 교체
sudo sed -i 's|superelegant-terrence-grittiest.ngrok-free.dev|<YOUR_DOMAIN>|' \
  /etc/systemd/system/ngrok-8765.service
sudo systemctl daemon-reload
```

#### 4. 서비스 enable + 기동

`After=` 가 의존성 보장하지만 처음 한 번은 명시적으로:

```bash
sudo systemctl enable --now uqi-embed.service
sudo systemctl enable --now uqi-rerank.service
sudo systemctl enable --now uqi-mcp.service
sudo systemctl enable --now ngrok-8765.service     # 외부 접근 시
```

#### 5. 헬스체크

```bash
curl -s http://127.0.0.1:7997/health  # → {"status":"ok","model":"BAAI/bge-m3",...}
curl -s http://127.0.0.1:7998/health  # → {"status":"ok","model":"BAAI/bge-reranker-v2-m3",...}
ss -ltn 'sport = :8765'               # uqi-mcp SSE listener
curl -s http://127.0.0.1:4040/api/tunnels | jq '.tunnels[].public_url'  # ngrok local API
```

## 운영 메모

- **재시작:** `sudo systemctl restart uqi-mcp` (초기화 1~2분, embed/rerank 모델 로딩)
- **로그:** `journalctl -u uqi-mcp -f`, `journalctl -u uqi-embed -f`, `journalctl -u uqi-rerank -f`
- **컨테이너 직접 접근:** `docker exec -it uqi-embed sh`
- **모델 캐시 정리:** `/home/sean/models/hf/` 비우면 다음 기동 시 재다운로드
- **포트 충돌:** embed=7997, rerank=7998, mcp=8765. 모두 loopback 외 노출 안 함 (mcp 만 0.0.0.0 SSE)

## 비호환 환경

- macOS / 비-NVIDIA Linux: GPU 컨테이너 기동 불가 → CPU 폴백 코드는 들어있으나 (`UQI_EMBED_DEVICE=cpu`) 성능 매우 느림
- aarch64 외 (x86_64) 에서 NGC PyTorch 25.06 이미지는 동일 태그로 받아짐 (Docker 가 자동 선택)
