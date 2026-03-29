```markdown
# UQI — Universal Quantum Infrastructure

> A quantum computing management platform that abstracts multi-vendor QPU access, circuit optimization, noise simulation, and calibration monitoring into a unified interface.

---

## 🖥️ Screenshots

> *(Add screenshots of the webapp dashboard here)*

---

## ✨ Key Features

- **Multi-framework support**: PennyLane, Qiskit, Qrisp, CUDAQ, Perceval
- **Multi-vendor QPU access**: IBM, IQM, Quandela, AWS Braket (IonQ Forte, Rigetti Ankaa, QuEra Aquila)
- **QPU dashboard**: Real-time calibration data with fidelity scoring (1Q error, readout error, T2 decoherence)
- **3-phase submission pipeline**: Calibration analysis → QPU recommendation → Noise simulation
- **RAG knowledge base**: SQLite + ChromaDB semantic search over quantum computing docs
- **Security sandbox**: Static analysis + execution limits (CPU, memory, max gates)
- **MCP server**: FastMCP SSE transport for Claude Desktop / AI agent integration
- **Pipeline cache**: Per-step invalidation with MD5-keyed cache entries

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────┐
│               uqi_webapp.html               │  ← Single-file HTML webapp
│         (Dashboard / Circuit Runner)        │
└────────────────────┬────────────────────────┘
                     │ SSE / HTTP
┌────────────────────▼────────────────────────┐
│              mcp_server.py                  │  ← FastMCP SSE server
│   (Tool routing, pipeline orchestration)    │
└──┬──────────────┬──────────────┬────────────┘
   │              │              │
┌──▼───┐   ┌─────▼─────┐  ┌────▼──────────┐
│uqi_  │   │uqi_        │  │uqi_rag.py     │
│calib │   │extractor   │  │(SQLite +      │
│ration│   │.py         │  │ ChromaDB)     │
│.py   │   │            │  │               │
└──────┘   └────────────┘  └───────────────┘
```

---

## ⚙️ Prerequisites

- Python 3.10+
- CUDA-capable GPU (DGX recommended for simulation)
- Quantum framework SDKs: `pennylane`, `qiskit`, `cudaq`, `perceval-quandela`
- `chromadb`, `fastmcp`, `sentence-transformers`

---

## 🚀 Installation

```bash
git clone https://github.com/joygoLive/uqi.git
cd uqi

python -m venv .venv_transpile
source .venv_transpile/bin/activate

pip install -r requirements.txt
```

### Environment setup

Copy `.env` and fill in your credentials:

```bash
cp .env .env.local
```

Required variables:
```
IBM_QUANTUM_TOKEN=
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=
IQM_SERVER_URL=
```

---

## ▶️ Usage

### Start MCP server

```bash
source .venv_transpile/bin/activate
python src/mcp_server.py
```

Or via systemd:

```bash
sudo systemctl start uqi-mcp
sudo systemctl status uqi-mcp
```

### Open webapp

Open `webapp/uqi_webapp.html` in your browser and connect to the MCP server endpoint.

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

---

## 📁 Project Structure

```
uqi/
├── src/
│   ├── mcp_server.py        # FastMCP SSE server + tool definitions
│   ├── uqi_calibration.py   # QPU calibration fetching & fidelity scoring
│   ├── uqi_extractor.py     # Circuit extraction & analysis
│   └── uqi_rag.py           # RAG system (SQLite + ChromaDB)
├── webapp/
│   └── uqi_webapp.html      # Single-file web dashboard
├── alg-files/               # Sample quantum algorithm files
├── data/                    # Calibration cache & DB
├── tests/
└── .env
```

---

## 📄 License

MIT
```

---

저장소 루트에 `README.md` 파일로 저장하면 됩니다.