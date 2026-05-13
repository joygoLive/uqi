# UQI — Universal Quantum Infrastructure

> A multi-vendor quantum computing management platform unifying QPU access,
> circuit optimization, noise simulation, calibration monitoring, and a
> knowledge base with hybrid retrieval + LLM synthesis.

---

## ✨ Key Features

- **Multi-framework support**: Qiskit, PennyLane, Qrisp, CUDAQ, Perceval (photonic),
  Pulser (analog AHS), Braket-AHS
- **Multi-vendor QPU access**: IBM Quantum, IQM Resonance, AWS Braket
  (IonQ Forte, Rigetti, QuEra Aquila), Azure Quantum (Pasqal Fresnel),
  Pasqal Cloud Services (PCS — direct submit + emulator), Quandela
- **3-phase submission pipeline**: Circuit analyze → QPU recommendation →
  Noise simulation → Real submit (with cost safeguard + emulator dry-run)
- **Hybrid Knowledge Base (RAG v2)**: sqlite-vec dense + FTS5 BM25 →
  Reciprocal Rank Fusion → bge-reranker-v2-m3 → Claude Opus 4.7 synthesis,
  with PII scrubbing before external API
- **Algorithm file selector UX**: framework auto-detection, grouped optgroups
  with search filter, hover-compatible QPU list, favorites + recents,
  inline circuit meta preview
- **Emulator-aware UX**: Pasqal `pasqal_emu_fresnel` / `pasqal_emu_free`
  with dedicated warn/button text (no QPU queue / no cost)
- **3-locale i18n**: English / Korean / French. All UI labels and dynamic
  alert/confirm/error messages use i18n keys with parameter interpolation;
  backend response includes `error_key` + `error_params` for consistent
  rendering across locales
- **Security sandbox**: Static analysis + execution limits (CPU, memory,
  max gates) + cost safeguard (threshold $50, emulator/sim auto-passthrough)
- **MCP server**: FastMCP SSE transport for Claude Desktop / AI agent
  integration
- **Pipeline cache**: Per-step invalidation with MD5-keyed cache entries

---

## 🏗️ Architecture

Single DGX Spark host runs three coordinated systemd services. The webapp
connects via SSE (optionally tunneled through ngrok), and the MCP server
fans out to the appropriate cloud vendor or local GPU executor.

```
┌────────────────────────────────────────────────────────────────────┐
│  systemd (single DGX Spark host)                                    │
│  ┌──────────────────────┐  ┌──────────────────────┐                 │
│  │ uqi-embed.service    │  │ uqi-rerank.service   │                 │
│  │ Docker uqi-rag:0.1   │  │ Docker uqi-rag:0.1   │                 │
│  │ → embed_server.py    │  │ → rerank_server.py   │                 │
│  │ bge-m3 / 1024-dim    │  │ bge-reranker-v2-m3   │                 │
│  │ 127.0.0.1:7997       │  │ 127.0.0.1:7998       │                 │
│  └──────────────────────┘  └──────────────────────┘                 │
│                ▲                       ▲                            │
│                │ HTTP loopback         │ HTTP loopback               │
│  ┌─────────────┴───────────────────────┴──────────────────────┐     │
│  │ uqi-mcp.service  —  Starlette SSE :8765                     │     │
│  │   ├─ Tools: analyze / noise / qec / qpu_submit / kb_ask     │     │
│  │   │         job_status / job_cancel / file_meta ...         │     │
│  │   ├─ Vendor executors: IBM / IQM / Braket / Azure / Pasqal  │     │
│  │   │                     (PCS direct + Azure fallback)       │     │
│  │   └─ Anthropic API (Claude Opus 4.7) for kb_ask / kb_explain│     │
│  │ SQLite uqi_rag.db: records + record_vec (vec0) + record_fts │     │
│  └─────────────────────────────────────────────────────────────┘     │
│                ▲                                                    │
└────────────────┼────────────────────────────────────────────────────┘
                 │ ngrok / direct
        Browser (uqi_webapp.html)
```

See [`docs/rag.md`](docs/rag.md) for the knowledge-base data flow in
detail (embed → BM25 → RRF → rerank → scrub → Claude synthesis).

---

## ⚙️ Prerequisites

### 호스트 OS / 하드웨어

- **Linux** (arm64 권장 — DGX Spark 가 reference). x86_64 도 동작.
- **NVIDIA GPU** + 드라이버 (embed/rerank GPU 컨테이너 + 선택적 qiskit-aer-GPU /
  cudaq 가속)
- **Python 3.12** (가상환경, DGX 와 동일 버전 권장)
- **Docker + nvidia-container-toolkit**
  ```bash
  # NVIDIA container toolkit 설치 (Ubuntu/Debian)
  curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
    | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
  curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
    | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
    | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
  sudo apt update && sudo apt install -y nvidia-container-toolkit
  sudo nvidia-ctk runtime configure --runtime=docker
  sudo systemctl restart docker
  ```
- **SQLite** with extension-load support (sqlite-vec)
- **Rust toolchain** (rustc/cargo) — `quizx` ZX-calculus optimizer 의 maturin 빌드용
  ```bash
  curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
  ```
- **snap** (for ngrok — public webapp URL 노출 시)
- **Node.js / npm** (notion-backup 정적 사이트 빌드 시 — `sudo apt install nodejs npm` 또는 brew/nodesource)

### Sibling 프로젝트 (DGX 전체 layout 재현 시 모두 필요)

`$HOME/work/orientom/` 가 `joygoLive/orientom` working tree 가 되고, 그 안에
nested 별도 repo 들이 sibling 으로 들어갑니다 (현재 DGX 와 동일 구조).

| 프로젝트 | repo | 의무? | 용도 |
|---|---|---|---|
| **orientom** (부모 working tree) | `joygoLive/orientom` | ✅ | QUWA/alg-files/azure 등 subfolder + venv 위치 |
| **uqi** (nested) | `joygoLive/uqi` | ✅ | 본 프로젝트 |
| **quizx** (nested) | `zxcalc/quizx` (upstream) | ✅ | `uqi_optimizer.py` 가 import 하는 ZX-calculus (Rust+Python bindings, maturin 빌드) |
| **qiskit-aer fork** | `joygoLive/qiskit-aer` (`jetson-patch`) | ⚠️ aarch64+NVIDIA 만 | Jetson 패치된 qiskit-aer GPU 빌드 |
| **quartz-site** (nested) | `joygoLive/quartz-site` | ⚪ notion-backup 서빙 시 | Quartz fork + Orientom 커스터마이징 |
| **obsidian-vault** (nested) | `joygoLive/orientom-notion-backup` | ⚪ notion-backup 서빙 시 | Notion 원본 markdown |
| **orientom-notion-pipeline** (nested) | `joygoLive/orientom-notion-pipeline` | ⚪ Notion sync 자동화 시 | weekly_notion_sync.sh 등 |

`deploy/setup.sh` 가 위 layout 전체를 자동으로 clone + 빌드합니다.

### Core Python packages

(see `requirements.txt` for the full pin set):

```
fastmcp                      anthropic >= 0.100
sqlite-vec                   pasqal-cloud, pulser, pulser-pasqal
sentence-transformers >= 3   amazon-braket-sdk
qiskit, qiskit-ibm-runtime   iqm-client
pennylane, perceval-quandela cudaq
azure-quantum, azure-identity
```

Note: **`chromadb` is no longer required** — Phase 8 of the RAG
reconstruction removed it (2026-05-12). The vector backend is sqlite-vec.

---

## 🚀 Installation

> 빠른 길: `deploy/setup.sh` 가 아래 1~5 단계를 자동화합니다.
> 수동으로 따라가려면 아래 순서대로.

### 1. UQI + sibling 프로젝트 clone

```bash
mkdir -p ~/work/orientom && cd ~/work/orientom

# UQI
git clone git@github.com:joygoLive/uqi.git

# QUWA (venv 공유 — UQI 와 같은 부모 디렉토리에 sibling 으로 둠)
git clone git@github.com:joygoLive/orientom.git QUWA

# (선택) Jetson/GH200 GPU 가속 qiskit-aer fork
git clone -b jetson-patch git@github.com:joygoLive/qiskit-aer.git ~/work/qiskit/qiskit-aer

# (선택) notion-backup 정적 사이트 (Quartz 기반)
git clone https://github.com/jackyzha0/quartz.git quartz-site
```

### 2. 공유 venv 생성 (QUWA/.venv_transpile)

UQI 의 `uqi-mcp.service` 는 **QUWA 의 venv 를 직접 가리킵니다**
(`/home/$USER/work/orientom/QUWA/.venv_transpile/bin/python`). 이는 두
프로젝트가 동일한 quantum SDK 스택을 공유하는 디자인 결정입니다.

```bash
cd ~/work/orientom/QUWA
python3.12 -m venv .venv_transpile
source .venv_transpile/bin/activate
pip install --upgrade pip
```

### 3. (선택) qiskit-aer GPU fork 빌드 — Jetson/GH200 만

PyPI stock `qiskit-aer` 로도 동작하지만, Jetson/GH200 에서 GPU 가속을
쓰려면 fork 의 빌드 절차를 따릅니다:

```bash
cd ~/work/qiskit/qiskit-aer
# (venv 활성화 상태에서)
# fork README 의 build 절차 참조. 일반적으로:
pip install pybind11 scikit-build cmake
python setup.py bdist_wheel -- -DAER_THRUST_BACKEND=CUDA
pip install dist/qiskit_aer-*-linux_aarch64.whl --force-reinstall
```

> 이 단계를 건너뛰면 다음 `pip install -r requirements.txt` 가 PyPI stock
> `qiskit-aer==0.17.2` 를 설치합니다 (CPU only).

### 4. UQI 의존성 설치

```bash
cd ~/work/orientom/uqi
# venv 는 이미 활성화 상태 (QUWA/.venv_transpile)
pip install -r requirements.txt
```

> ⚠️ Note: `requirements.txt` 의 `cudaq`, `cupy-cuda13x`, `nvidia-*`,
> `jax-cuda12-*` 패키지들은 **CUDA 환경 전용**입니다. macOS / 비-NVIDIA Linux
> 에서는 해당 라인을 제거하거나 fallback 처리 필요.

### 5. embed/rerank Docker 이미지 빌드

```bash
cd ~/work/orientom/uqi/deploy
docker build -t uqi-rag:0.1 .
```

(Image is based on `nvcr.io/nvidia/pytorch:25.06-py3` and bundles
sentence-transformers + FastAPI / uvicorn for OpenAI-compatible endpoints.
약 24GB, 첫 빌드 5~15분.)

### 6. systemd 유닛 설치 + 활성화

```bash
# 4 개 unit 일괄 설치
sudo cp ~/work/orientom/uqi/deploy/systemd/uqi-mcp.service     /etc/systemd/system/
sudo cp ~/work/orientom/uqi/deploy/systemd/uqi-embed.service   /etc/systemd/system/
sudo cp ~/work/orientom/uqi/deploy/systemd/uqi-rerank.service  /etc/systemd/system/
sudo cp ~/work/orientom/uqi/deploy/systemd/ngrok-8765.service  /etc/systemd/system/
sudo systemctl daemon-reload

# enable (부팅 시 자동 시작) — 시작은 .env 채운 뒤 8단계에서
sudo systemctl enable uqi-embed uqi-rerank uqi-mcp ngrok-8765
```

> Unit 파일 안의 경로 (`/home/sean/work/orientom/...`) 가 실제 사용자명과
> 다르면 `sudo sed -i 's|/home/sean/|/home/$USER/|g' /etc/systemd/system/uqi-*.service`
> 같은 식으로 보정.

### 7. ngrok 셋업 (외부에서 webapp 접근 시)

```bash
# snap 으로 ngrok 설치
sudo snap install ngrok

# authtoken 등록 — https://dashboard.ngrok.com/ 에서 발급
ngrok config add-authtoken <YOUR_AUTHTOKEN>

# (선택) 고정 URL 사용 시 https://dashboard.ngrok.com/cloud-edge/domains 에서
# reserved domain 발급 후 ngrok-8765.service 의 --url 인자 교체
sudo sed -i 's|--url=superelegant-terrence-grittiest.ngrok-free.dev|--url=<YOUR_DOMAIN>|' \
  /etc/systemd/system/ngrok-8765.service
sudo systemctl daemon-reload
```

`deploy/systemd/ngrok-8765.service` 의 기본 URL 은 원작자 reserved domain 이므로
새 환경에서는 본인 ngrok 계정의 reserved domain (또는 random URL) 으로 교체 필수.

### 8. 모델 가중치 (선택 — 첫 기동 시 자동 다운로드)

기본 동작은 컨테이너 첫 기동 시 HuggingFace 에서 자동 다운로드
(`/home/$USER/models/hf/` 에 캐시, 약 8.6GB). offline 환경이거나 미리 받아두려면:

```bash
mkdir -p ~/models/hf
HF_HOME=~/models/hf huggingface-cli download BAAI/bge-m3
HF_HOME=~/models/hf huggingface-cli download BAAI/bge-reranker-v2-m3
```

### 9. (선택) notion-backup 정적 사이트 연동

`webapp/notion-backup` 은 별도 프로젝트 (`quartz-site`) 가 빌드한 정적
사이트로의 symlink 입니다. UQI 와는 분리된 repo 이므로 해당 프로젝트의
README 를 따로 참조하세요. `deploy/setup.sh` 를 쓰면 sibling clone + 빌드 +
symlink 까지 한 번에 처리합니다 (인터랙티브 prompt 에서 yes 선택 시).

이 단계를 건너뛰어도 메인 webapp (`/sse` + `/`) 는 정상 동작 —
`/notion-backup/` 경로만 404.

### Environment setup

Copy and fill `.env` at the project root:

```bash
cp .env.example .env   # if provided
```

Key environment variables (grouped by area):

```env
# ── LLM synthesis (Anthropic) ─────────────────────────────────────
ANTHROPIC_API_KEY=sk-ant-...
UQI_SYNTH_MODEL=claude-opus-4-7          # default

# ── Local RAG stack (DGX) ────────────────────────────────────────
UQI_EMBED_URL=http://127.0.0.1:7997
UQI_RERANK_URL=http://127.0.0.1:7998
# Optional hybrid weights (dense,sparse) — default per-intent:
# UQI_HYBRID_W_CONCEPT=0.7,0.3
# UQI_HYBRID_W_DIRECT=0.3,0.7
# UQI_HYBRID_W_MIXED=0.5,0.5
# Optional scrubbing level: off / standard / strict
# UQI_SCRUB_LEVEL=standard

# ── Pasqal Cloud Services (direct submit) ────────────────────────
PASQAL_USERNAME=...
PASQAL_PASSWORD=...
PASQAL_PROJECT_ID=...
# Routing: auto (PCS → Azure fallback, default) / pcs / azure
# UQI_PASQAL_BACKEND=auto

# ── Azure Quantum (fallback for Pasqal, primary for Quantinuum) ──
AZURE_TENANT_ID=...
AZURE_CLIENT_ID=...
AZURE_CLIENT_SECRET=...
AZURE_QUANTUM_SUBSCRIPTION_ID=...
AZURE_QUANTUM_RESOURCE_GROUP=...
AZURE_QUANTUM_WORKSPACE=...
AZURE_QUANTUM_LOCATION=...

# ── IBM Quantum ───────────────────────────────────────────────────
IBM_QUANTUM_TOKEN=...

# ── IQM Resonance ─────────────────────────────────────────────────
IQM_QUANTUM_TOKEN=...

# ── AWS Braket ────────────────────────────────────────────────────
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
IONQ_FORTE_ARN=arn:aws:braket:...
RIGETTI_CEPHEUS_ARN=arn:aws:braket:...
BRAKET_SV1_ARN=arn:aws:braket:...
# Optional direct keys (some Braket devices)
# IONQ_API_KEY=...
# RIGETTI_API_KEY=...

# ── Quandela Cloud (photonic) ────────────────────────────────────
QUANDELA_TOKEN=...
```

---

## 🍎 기타 OS 안내

`deploy/setup.sh --yes` 한 줄로 **현재 OS 환경 (aarch64 Linux + NVIDIA, DGX Spark
기준)** 에서는 풀 셋업이 끝납니다. 그 외 가정하는 환경 2가지에서는 setup.sh 가
환경을 자동 감지해서 적용 가능한 단계만 진행 — 사용자가 수동 처리할 부분만
아래에 정리합니다.

### macOS (M-series / Intel)

setup.sh 가 자동으로:
- clone (UQI / QUWA / quartz / obsidian-vault) ✓
- venv + pip install (CUDA 패키지 자동 제외) ✓
- quartz build + symlinks ✓

자동 skip 되는 단계 + 수동 대응:

| 항목 | 수동 대응 |
|---|---|
| `uqi-mcp` 실행 | systemd 없음 → 직접 실행 `python src/mcp_server.py --host 0.0.0.0 --port 8765 --transport sse` (또는 launchd plist 작성) |
| `uqi-embed` / `uqi-rerank` | docker GPU 컨테이너 빌드 skip. **CPU 모드로 호스트 직접 실행**: `UQI_EMBED_DEVICE=cpu python deploy/embed_server.py` (매우 느림) |
| `qiskit-aer` GPU | fork 빌드 skip → PyPI stock (CPU) 자동 사용 |
| Node.js / npm | notion-backup 빌드 시 `brew install node` |
| ngrok | `brew install ngrok/ngrok/ngrok` (snap 대체) |

### x86_64 Linux + NVIDIA (예: H100)

setup.sh 가 자동으로:
- 동일환경과 사실상 동일하게 풀 셋업 — clone / venv / pip / docker / systemd 모두 진행 ✓

**유일한 차이**: `qiskit-aer` Jetson-patch fork 는 aarch64 전용 (Jetson/GH200 GPU
patch) → setup.sh 가 fork clone/빌드 자동 skip, PyPI stock 사용. H100 GPU 가속 원하면
별도로 upstream `Qiskit/qiskit-aer` 에서 CUDA 빌드 진행 필요.

---

## ▶️ Usage

### Start services (systemd)

```bash
# embed → rerank → mcp → ngrok 순서 (uqi-mcp.service 의 After= 가 의존 보장)
sudo systemctl start uqi-embed uqi-rerank uqi-mcp ngrok-8765
sudo systemctl is-active uqi-embed uqi-rerank uqi-mcp ngrok-8765
# 4 개 모두 "active" 보고해야 정상
```

서비스별 역할:
- `uqi-embed` (Docker, :7997): bge-m3 임베딩 (loopback only)
- `uqi-rerank` (Docker, :7998): bge-reranker-v2-m3 (loopback only)
- `uqi-mcp` (host venv, :8765): MCP SSE + webapp 정적 서빙 + notion-backup mount
- `ngrok-8765` (snap): :8765 를 public URL 로 터널링 (외부 접속 시만 필요)

To restart MCP after `.env` or code changes:

```bash
sudo systemctl restart uqi-mcp
# 초기화 1~2 분 (embed/rerank 모델 로딩 + RAG DB open)
```

### Open the webapp

webapp 은 MCP 서버 (`uqi-mcp.service`, :8765) 가 정적으로 서빙합니다 —
별도 web server 불필요.

- **로컬 접근**: `http://localhost:8765/` → webapp 자동 로드,
  `http://localhost:8765/sse` 가 MCP SSE 엔드포인트
- **외부 접근** (ngrok 활성 시): `https://<your-reserved-domain>.ngrok-free.dev/`
  ngrok dashboard 의 reserved domain 또는 `ngrok-8765.service` 의 `--url` 값 확인
- **헤더의 connection dialog** 에서 SSE endpoint 를 명시적으로 설정 가능
  (local: `http://localhost:8765/sse`, remote: ngrok URL + `/sse`)

### Health check

```bash
curl -s http://127.0.0.1:7997/health   # embed: {"status":"ok","model":"BAAI/bge-m3",...}
curl -s http://127.0.0.1:7998/health   # rerank: {"status":"ok","model":"BAAI/bge-reranker-v2-m3",...}
ss -ltn 'sport = :8765'                # uqi-mcp SSE listener
curl -s http://127.0.0.1:4040/api/tunnels | jq '.tunnels[].public_url'  # ngrok local API
```

### Claude Desktop integration

Add to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "uqi": {
      "url": "http://localhost:8765/sse"
    }
  }
}
```

### Submitting circuits

Two routes, picked automatically by framework detection:

- **Gate-based** (Qiskit / PennyLane / Qrisp / CUDAQ → IBM / IQM / Rigetti /
  IonQ): `analyze → optimize → noise → qec → qpu_submit`
- **AHS** (Pulser → Pasqal; Braket-AHS → QuEra Aquila): direct AHS
  submit path with cost preview and emulator dry-run option

The `pasqal_emu_fresnel` / `pasqal_emu_free` selections route to the Pasqal
Cloud Services emulator (no QPU queue, no cost) — the UI uses a distinct
"Submit to Emulator" button and warning text.

---

## 📦 Multi-vendor QPU Catalog (excerpt)

| QPU id                  | Vendor      | Modality        | Runtime              |
|-------------------------|-------------|-----------------|----------------------|
| `ibm_fez`, `ibm_marrakesh`, `ibm_kingston` | IBM | superconducting | IBM Quantum |
| `iqm_garnet`, `iqm_emerald`, `iqm_sirius`   | IQM | superconducting | IQM Resonance |
| `ionq_forte1`           | IonQ        | ion-trap        | AWS Braket           |
| `rigetti_cepheus`       | Rigetti     | superconducting | AWS Braket           |
| `quera_aquila`          | QuEra       | neutral-atom    | AWS Braket (AHS)     |
| `pasqal_fresnel`        | Pasqal      | neutral-atom    | Azure Quantum / PCS  |
| `pasqal_fresnel_can1`   | Pasqal      | neutral-atom    | Azure Quantum / PCS  |
| `pasqal_emu_fresnel`    | Pasqal      | neutral-atom    | Pasqal Cloud (emu)   |
| `pasqal_emu_free`       | Pasqal      | neutral-atom    | Pasqal Cloud (emu)   |
| `qpu:ascella`, `qpu:belenos` | Quandela | photonic       | Quandela Cloud       |
| `sim:ascella`, `sim:belenos` | Quandela | photonic (sim) | Quandela Cloud       |
| `braket_sv1`, `dm1`, `tn1` | Amazon   | simulator       | AWS Braket           |

Routing: `pasqal_fresnel(_can1)` uses `UQI_PASQAL_BACKEND` to choose PCS
direct submit (primary) with Azure Quantum fallback. `pasqal_emu_*` always
routes through PCS.

---

## 📁 Project Structure

```
uqi/
├── src/
│   ├── mcp_server.py            # FastMCP SSE server + tool definitions
│   ├── uqi_calibration.py       # QPU calibration fetching, fidelity scoring
│   ├── uqi_extractor.py         # Multi-framework circuit extraction
│   ├── uqi_qir_converter.py     # QASM/QIR/native conversion
│   ├── uqi_optimizer.py         # Circuit optimization helpers
│   ├── uqi_noise.py             # Noise simulation (Qiskit / Pulser / Braket)
│   ├── uqi_qec.py               # QEC analyze / apply
│   ├── uqi_pricing.py           # Vendor pricing models + catalog
│   ├── uqi_job_store.py         # Local SQLite job tracking
│   ├── uqi_messages.py          # i18n key registry for backend responses
│   ├── uqi_rag.py               # Hybrid RAG (sqlite-vec + FTS5 + rerank)
│   ├── uqi_rag_scrub.py         # PII / secret scrubbing for external API
│   ├── uqi_executor_ibm.py      # IBM Quantum
│   ├── uqi_executor_iqm.py      # IQM Resonance
│   ├── uqi_executor_braket.py   # AWS Braket (IonQ / Rigetti / QuEra)
│   ├── uqi_executor_azure.py    # Azure Quantum (Pasqal / Quantinuum)
│   ├── uqi_executor_pasqal.py   # Pasqal Cloud Services direct (PCS)
│   ├── uqi_executor_perceval.py # Quandela (photonic)
│   ├── uqi_executor_cudaq.py    # NVIDIA CUDAQ
│   ├── uqi_qpu_live_check.py    # Live availability / queue check
│   └── ...                       # benchmarks, viz, migration helpers
├── webapp/
│   ├── uqi_webapp.html          # Single-file webapp (HTML + JS + CSS)
│   └── locales/{en,ko,fr}.json  # External i18n catalogs
├── alg-files/                   # Sample quantum algorithm files
├── data/                        # SQLite caches + job store + RAG db
├── docs/
│   ├── rag.md                   # RAG v2 operator guide
│   └── qpu_comparison.md        # Cross-vendor QPU spec comparison
├── tests/                       # ~1000 unit + integration tests
├── /etc/uqi/                    # systemd / Docker build files (host-only)
└── .env
```

---

## 📚 Knowledge Base (RAG v2)

The webapp's **Knowledge** tab provides hybrid search over UQI's
quantum-pipeline records (optimization / execution / calibration / noise
simulation / pipeline issues / QEC experiments / etc.):

- **Hybrid search**: dense (bge-m3) + FTS5 BM25 → RRF → cross-encoder
  rerank → top-K
- **🤖 AI Summary**: re-uses the last search query, retrieves top-N
  records, scrubs sensitive fields, and asks Claude Opus 4.7 to synthesize
  an answer with `[id]` citations
- **🤖 Explain (per card)**: single-record human-friendly explanation
- **Type-rich rendering**: each record type renders as a structured card
  (e.g. optimization gate-reduction bars, security_block masked patterns,
  QPU performance metrics)
- **Citation chips**: clickable jump-to-record from AI summary

Full operator details, regression thresholds, and golden-set evaluation
are in [`docs/rag.md`](docs/rag.md).

---

## 🌐 Internationalization

UI is fully localized across **English / Korean / French**:

- Static labels via `data-i18n="key"` attributes
- Dynamic alerts/errors via `t('key', {params})` helper
- Backend tool responses include `error_key` + `error_params` (and
  `message_key` where applicable); the webapp renders via
  `_renderBackendError(d)` which prefers the keyed form and falls back to
  legacy `error` text
- The Knowledge Base AI Summary / Explain prompts the LLM in the current
  UI language

Adding a new key: edit `webapp/locales/{en,ko,fr}.json` **and** the
in-page inline `_LOCALE_*` blocks (used for first-paint). `tests/test_i18n.py`
guards 3-locale consistency.

---

## 🧪 Testing

```bash
cd tests
python run_tests.py           # full suite (~1000 cases)
python -m pytest test_i18n.py # quick i18n consistency
python -m pytest test_uqi_executor_pasqal.py  # individual module
```

Live RAG quality regression (requires uqi-embed / uqi-rerank running):

```bash
python tests/test_rag_quality.py
python tests/golden_set_eval.py --live --k 10
```

---

## 📖 Documentation

- [`docs/rag.md`](docs/rag.md) — RAG v2 architecture, operator guide,
  troubleshooting, golden-set evaluation
- [`docs/qpu_comparison.md`](docs/qpu_comparison.md) — Cross-vendor QPU
  spec reference
- `snapshot-notion.md` — Notion archive sync procedure

---

## 📄 License

MIT
