# UQI Deploy — 인프라 / 서비스 설정

이 디렉토리는 UQI 가 DGX (또는 동등한 NVIDIA aarch64 Linux) 머신에서 어떻게 배포·운영되는지를 코드로 박제한다. 코드 (src/) 가 아닌, **서비스 레이어** 의 ground truth.

## 구성

```
deploy/
├── Dockerfile           # uqi-rag 컨테이너 이미지 (bge-m3 + reranker 추론 환경)
├── embed_server.py      # 임베딩 서버 (FastAPI, OpenAI-compatible /embeddings)
├── rerank_server.py     # 크로스인코더 reranker 서버 (FastAPI /rerank)
└── systemd/
    ├── uqi-mcp.service     # MCP 메인 서버 (호스트 venv 직접 실행, :8765 SSE)
    ├── uqi-embed.service   # bge-m3 컨테이너 (loopback :7997)
    └── uqi-rerank.service  # bge-reranker-v2-m3 컨테이너 (loopback :7998)
```

## 아키텍처

```
   Claude Desktop / 외부 클라이언트
              │ SSE :8765
              ▼
      uqi-mcp.service ──► sqlite-vec (RAG storage)
        (host venv)
              │
        ┌─────┴──────┐
        ▼            ▼
  uqi-embed     uqi-rerank
   :7997 (Docker, GPU)
              :7998 (Docker, GPU)
              │
        bge-m3 / reranker
        (/home/sean/models/hf)
```

- **uqi-mcp** 는 호스트 venv 에서 실행 (CUDA quantum sim, qiskit-aer 등 무거운 의존성 때문)
- **uqi-embed / uqi-rerank** 는 격리된 컨테이너 (PyTorch + sentence-transformers 만 필요)
- MCP → embed/rerank 는 로컬 HTTP loopback (외부 노출 X)

## 사전 요건

- Linux (aarch64 검증, x86_64 도 가능)
- NVIDIA GPU + 드라이버 + `nvidia-container-toolkit`
- Docker (`docker.service` enabled)
- Python 3.12 venv: `/home/sean/work/orientom/QUWA/.venv_transpile`
  - `requirements.txt` 기준 의존성 설치 완료
- 모델 가중치: `/home/sean/models/hf/` (bge-m3 + bge-reranker-v2-m3, 약 8.6 GB)
  - 미리 받지 않아도 첫 기동 시 HuggingFace 에서 자동 다운로드

## 설치 절차

### 1. Docker 이미지 빌드

```bash
cd /home/sean/work/orientom/uqi/deploy
docker build -t uqi-rag:0.1 .
```

이미지는 NGC PyTorch 25.06 (arm64, CUDA 13) 기반이라 첫 빌드 시 시간이 걸린다 (대략 24 GB). NVIDIA GPU 가 있는 호스트에서만 빌드/실행 가능.

### 2. systemd 유닛 설치

```bash
sudo cp deploy/systemd/uqi-embed.service   /etc/systemd/system/
sudo cp deploy/systemd/uqi-rerank.service  /etc/systemd/system/
sudo cp deploy/systemd/uqi-mcp.service     /etc/systemd/system/
sudo systemctl daemon-reload
```

### 3. 서비스 enable + 기동

순서가 중요하다. embed/rerank 를 먼저 띄우고 그 위에 mcp 를 띄운다 (`uqi-mcp.service` 의 `After=` 가 이미 보장하지만, 처음 한 번은 명시적으로):

```bash
sudo systemctl enable --now uqi-embed.service
sudo systemctl enable --now uqi-rerank.service
sudo systemctl enable --now uqi-mcp.service
```

### 4. 헬스체크

```bash
curl -s http://127.0.0.1:7997/health  # → {"status":"ok","model":"BAAI/bge-m3",...}
curl -s http://127.0.0.1:7998/health  # → {"status":"ok","model":"BAAI/bge-reranker-v2-m3",...}
ss -ltn 'sport = :8765'               # uqi-mcp SSE listener
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
