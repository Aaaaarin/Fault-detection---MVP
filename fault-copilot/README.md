# fault-copilot

## Quick Demo

```bash
# 1. Copy env file and add your Anthropic API key
cp .env.example .env
# edit .env → set ANTHROPIC_API_KEY=sk-ant-...

# 2. Install dependencies
pip install -r requirements.txt

# 3. Launch everything with one command
python start_demo.py
```

The launcher will:
- Verify your API key
- Ingest the service manual automatically (text-only, ~15 seconds)
- Start the backend on **localhost:8000**
- Start the frontend on **localhost:3000**
- Open the browser

Try the sample faults on the landing page, or type your own.

A fault resolution copilot for industrial manufacturing plants. Operators submit a
fault code or description; the system retrieves the relevant section from
maintenance manuals and returns step-by-step resolution guidance in plain language.

## Project layout

```
fault-copilot/
├── backend/
│   ├── main.py              # FastAPI app
│   ├── ingestion/           # PDF parsing, image description, embedding
│   ├── retrieval/           # Hybrid exact + semantic search
│   ├── resolution/          # Claude-generated step-by-step guidance
│   ├── logging/             # SQLite auto-logging
│   └── config.py            # API keys, model names, paths
├── frontend/                # (empty for now)
├── manuals/                 # Drop PDF manuals here
├── data/                    # ChromaDB persists here
├── logs/                    # SQLite DB here
├── requirements.txt
└── .env.example
```

## Setup

1. Create a virtual environment and install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

2. Copy `.env.example` to `.env` and fill in your `ANTHROPIC_API_KEY`.

3. Drop maintenance manual PDFs into `manuals/`.

## Run CNC manual self-test

After setup, verify the full pipeline against the bundled Allen-Bradley 9/Series
CNC manual (`manuals/8520-um511_-en-p.pdf`):

```bash
# From fault-copilot/ directory
python backend/test_cnc_manual.py
```

The script runs six stages end-to-end without needing the web server:

| Stage | What it checks |
|-------|---------------|
| Environment | `.env` present, `ANTHROPIC_API_KEY` set, PDF found |
| Ingestion | Parse PDF → describe images → embed → store in ChromaDB |
| Retrieval | 8 test queries against the ingested manual |
| Resolution | 5 realistic fault inputs → Claude step-by-step guidance |
| Logging | SQLite write + read-back round-trip |
| Report | Writes `logs/cnc_self_test_report.md` |

Options:

```bash
python backend/test_cnc_manual.py --force           # delete + re-ingest
python backend/test_cnc_manual.py --skip-resolution  # skip Claude API calls
```

> **Note:** First-time ingestion calls Claude Vision for every diagram in the
> manual and may take several minutes.  Subsequent runs skip ingestion
> automatically unless `--force` is passed.

## Configuration

All runtime configuration lives in `backend/config.py` and is sourced from
environment variables (or `.env`):

| Variable             | Default                |
| -------------------- | ---------------------- |
| `ANTHROPIC_API_KEY`  | _required_             |
| `VISION_MODEL`       | `claude-opus-4-5`      |
| `RESOLVER_MODEL`     | `claude-sonnet-4-6`    |
| `CHROMA_PATH`        | `./data/chroma`        |
| `MANUALS_PATH`       | `./manuals`            |
| `LOG_DB_PATH`        | `./logs/faults.db`     |
