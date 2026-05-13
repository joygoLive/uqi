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

- **Linux** (arm64 or x86_64). DGX Spark (Blackwell SM12.1, 121 GB unified
  memory) is the reference host; any CUDA-capable Linux box also works.
- **Python 3.10+**, virtualenv
- **Docker + nvidia-container-toolkit** (for embed/rerank GPU services)
- **SQLite** with extension-load support (sqlite-vec)
- Optional: ngrok for remote webapp access

Core Python packages (see `requirements.txt` for the full pin set):

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

```bash
git clone https://github.com/joygoLive/uqi.git
cd uqi

python -m venv .venv_transpile
source .venv_transpile/bin/activate

pip install -r requirements.txt
```

### Build the embed/rerank container

```bash
cd /etc/uqi
docker build -t uqi-rag:0.1 .
```

(Image is based on `nvcr.io/nvidia/pytorch:25.06-py3` and bundles
sentence-transformers + FastAPI / uvicorn for OpenAI-compatible endpoints.)

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

## ▶️ Usage

### Start services (systemd)

```bash
sudo systemctl start uqi-embed uqi-rerank uqi-mcp
sudo systemctl is-active uqi-embed uqi-rerank uqi-mcp
# All three should report "active"
```

To restart MCP after `.env` or code changes:

```bash
sudo systemctl restart uqi-mcp
```

### Open the webapp

Open `webapp/uqi_webapp.html` in your browser. The webapp talks to the MCP
server over SSE; configure the endpoint in the header connection dialog
(local `http://localhost:8765/sse` or remote ngrok URL).

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
