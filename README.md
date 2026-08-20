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

Interactive API docs: http://localhost:8000/docs
