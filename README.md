# The Last Commit

**An AI archaeologist for abandoned open-source repositories.**

Give it a dead repo. It excavates the git history, the final source tree, and the
documentation; reconstructs what the original developers were trying to build; and
produces a **forensic report** — unfinished features, suspicious regressions,
undocumented design decisions — plus a ranked roadmap for reviving the project.

Then it does the part you actually came for: **you interrogate the repository as if
interviewing its former maintainer.** The persona answers in first person with a
fallible human memory, but every claim is grounded in real commits, files, and
branches pulled live from the history — and every answer ends with a receipts block.

## Install

```bash
uv sync          # or: pip install -e .
```

## Configure (optional)

The chat model is any OpenAI-compatible endpoint; the default is Groq's
`qwen/qwen3.8-27b` (bring your own open-weight model via vLLM, Ollama, llama.cpp…).

```bash
export GROQ_API_KEY=...            # or OPENAI_API_KEY / LASTCOMMIT_API_KEY
```

Or write `~/.config/lastcommit/config.toml`:

```toml
[llm]
base_url = "https://api.groq.com/openai/v1"
model = "qwen/qwen3.8-27b"
# api_key = "..."

[embed]
provider = "fastembed"                  # local ONNX embeddings, no server needed
model = "BAAI/bge-small-en-v1.5"        # or provider = "ollama", model = "nomic-embed-text"
```

## Use

```bash
lastcommit analyze /path/to/abandoned-repo      # forensic report → ~/Downloads/lastcommit-<repo>-report.md
lastcommit interview /path/to/abandoned-repo    # interrogate the former maintainer
lastcommit ingest /path/to/abandoned-repo       # debugging: findings summary only
```

The report is written to your Downloads folder by default (override with `--out`). The analyzed
repo only ever gains a `.lastcommit/` folder with the disposable retrieval index and any
interview transcripts — delete it freely, it rebuilds on the next run.

Analyzing a remote repository: clone it **with full history** (never `--depth 1` — the
forensics read the commit graph), then point `lastcommit` at the clone. Remote-tracking
branches are picked up automatically, so stranded work on unmerged branches is still found.

`analyze` works fully **without** an LLM (`--no-llm`, or when no key is configured):
every finding is computed deterministically from git + tree-sitter + docs, and the
model only ever adds narrative and the roadmap, constrained to cite finding IDs.
A machine verifier strips any claim referencing evidence that doesn't exist.

## Web app

The same engine ships as a self-contained web app (FastAPI backend + a no-build
vanilla frontend, dark and lightly haunted):

```bash
lastcommit web                       # → http://127.0.0.1:8321
```

Paste a GitHub repo link, watch the exhumation progress, read the rendered
markdown report in the page, download it with an explicit button. No sign-in.

**Summon the Maintainer** — under every finished report there's a séance panel:
ask the former maintainer questions and they answer in character, grounded in
the report and live git lookups (search, commits + diffs, file revisions,
branch list), every answer ending with a receipts block. Chat is deliberately
slimmed for free-tier token budgets (5 tools, 3 tool rounds, capped context):
expect ~30–90s per answer on Groq's on-demand tier; instant on Dev Tier.
Separate quotas: 20 questions per IP per day and 100 globally
(`LASTCOMMIT_DAILY_CHAT_PER_IP`, `LASTCOMMIT_DAILY_CHAT_GLOBAL`).

- **Secrets stay server-side.** The frontend only talks to `/api/*` and never
  sees the Groq key, the git client, or the filesystem.
- **Rate limits** (persisted server-side in SQLite): 5 reports per IP per day
  and 100 reports per day globally — configurable via `LASTCOMMIT_DAILY_PER_IP`
  and `LASTCOMMIT_DAILY_GLOBAL`.
- **Deployment knobs**: `LASTCOMMIT_DATA_DIR` (usage db location),
  `LASTCOMMIT_WORKERS` (parallel analyses), `LASTCOMMIT_CLONE_TIMEOUT`,
  `LASTCOMMIT_TRUST_PROXY=1` when behind a reverse proxy that sets
  `X-Forwarded-For`, `GROQ_API_KEY` as usual. Without a key the app still
  produces deterministic reports.
- Only `github.com` URLs are accepted; clones run in a temp dir with a hard
  timeout and full history (the forensics read the commit graph).

## How it works

```
repo ──▶ ingest ──▶ forensic detectors ──▶ findings (typed evidence)
              │            │                      │
              │            └─ pygit2 · tree-sitter · doc parsing
              ▼                                   ▼
        chunk index (SQLite + FTS5 + local vectors) ──▶ report (verified citations)
              │
              ▼
     interview: maintainer persona + agentic git tools (show_commit, blame, file_at_rev, search…)
```

The design rule: **deterministic first, LLM second.** Facts come from code;
the model writes the story — and doesn't get to invent the facts.

## Layout

```
src/lastcommit/
├── ingest/       git_history (pygit2), code_parser (tree-sitter), docs, pipeline
├── analysis/     signals (deterministic detectors), narrative, verify
├── index/        store (SQLite/FTS5), embeddings (fastembed), search (hybrid RRF)
├── report/       markdown renderer
├── interview/    tools, persona, repl
└── llm/          OpenAI-compatible client + config
```
