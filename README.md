# Agentic Google Workspace Orchestrator

Natural-language orchestration layer over Gmail, Google Calendar, and Google Drive: an LLM
classifies intent, a planner builds a dependency DAG, a parallel orchestrator executes it across
service agents, a pgvector-backed hybrid search layer retrieves relevant context, and a
synthesizer turns the results into a coherent natural-language response.

Full documentation:

- [`DESIGN.md`](DESIGN.md) - architecture, scaling to 1M users, embedding strategy
- [`API.md`](API.md) - endpoint reference
- [`docs/sample_queries.md`](docs/sample_queries.md) - 10+ worked queries incl. edge cases
- [`docs/postman_collection.json`](docs/postman_collection.json) - importable Postman collection
- [`docs/er_diagram.md`](docs/er_diagram.md) - entity-relationship diagram

## Quickstart

```bash
cp .env.example .env
docker compose up --build
```

The API defaults to `MOCK_GOOGLE_API=true` and a mock LLM/embedding provider, so the entire stack
(orchestration, hybrid search, sync) runs end-to-end against seeded fixture data with **zero
external API keys**. Set `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` and flip `MOCK_GOOGLE_API=false`
with real Google OAuth credentials to go live.

Once running:

```bash
curl -X POST http://localhost:8000/api/v1/query \
  -H "Content-Type: application/json" \
  -H "X-User-Id: 00000000-0000-0000-0000-000000000001" \
  -d '{"query": "What is on my calendar next week?"}'
```

Interactive API docs: http://localhost:8000/docs · More worked examples: [`docs/sample_queries.md`](docs/sample_queries.md)

## Running the tests

```bash
docker compose exec api pytest              # unit + integration (83 tests)
docker compose exec api pytest tests/unit   # pure-Python, no services needed
```

`tests/integration/test_hybrid_search_precision.py` measures Precision@5 (target >0.8) and query
latency (target <500ms) against the seeded fixture data.

## Project layout

```
app/
  intent/        LLM-backed intent classifier (structured output)
  planner/       Execution DAG builder (per-intent templates + generic fallback)
  orchestrator/  Parallel DAG execution engine, conversation context, compute nodes
  agents/        Gmail/Calendar/Drive agents (search/execute/get_context), real + mock clients
  search/        pgvector hybrid search (vector + full-text, RRF fusion, recency decay)
  synthesizer/   Aggregates orchestration results into a natural-language response
  llm/           Provider-agnostic LLM/embedding interfaces (Anthropic/OpenAI + mock)
  sync/          Celery background sync tasks + beat schedule
  cache/         Redis caching, rate limiting, Google API quota limiter
  api/v1/        FastAPI routers (query, auth, sync, health, websocket)
  db/            SQLAlchemy models + session plumbing
alembic/         Database migrations
tests/           Unit + integration test suite
docs/            Sample queries, ER diagram, Postman/OpenAPI specs
```
