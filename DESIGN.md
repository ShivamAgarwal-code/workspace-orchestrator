# DESIGN.md

## 1. Architecture

```
User Query
    |
    v
Intent Classifier (LLM, forced structured output)      app/intent/
    |
    v
Query Planner (builds an execution DAG)                app/planner/
    |
    v
Service Orchestrator (parallel DAG execution)           app/orchestrator/
    |-- Gmail Agent    (search / execute / get_context)  app/agents/gmail_agent.py
    |-- GCal Agent     (search / execute / get_context)  app/agents/gcal_agent.py
    |-- Drive Agent    (search / execute / get_context)  app/agents/drive_agent.py
    |-- compute nodes  (conflict detection, reference resolution, no Google API)
    |
    v
Embedding & Hybrid Search Layer (pgvector, RRF fusion)   app/search/
    |
    v
Response Synthesizer (LLM, grounded in structured JSON)  app/synthesizer/
```

Everything above is built from scratch on top of FastAPI + SQLAlchemy(async) + asyncio — no
agent/workflow framework. The two places that talk to an LLM (`app/llm/anthropic_provider.py`,
`app/llm/openai_embeddings.py`) are thin, direct SDK wrappers; all control flow (classification ->
planning -> parallel execution -> synthesis) is plain Python.

### 1.1 Intent Classifier (`app/intent/`)

Calls the LLM with a forced tool-use call (`classify_intent`) against a JSON Schema
(`app/intent/schemas.py:INTENT_SCHEMA`), so the output is always structured — no free-text
parsing. The prompt explicitly tells the model **not** to resolve relative dates itself (that's
`app/utils/temporal.py`'s job, since it needs the user's real timezone/"now", not the model's
guess) and to set `needs_clarification` + `clarification_question` rather than silently guessing
on an ambiguous write ("Move the meeting with John" with two candidate Johns).

One nuance: for `reference_lookup` ("that email about the proposal"), the classifier can't
actually know in advance whether the reference will resolve — that depends on conversation
history, which is only fully evaluated by the `resolve_reference` compute node *after*
classification. So the classifier intentionally does **not** set `needs_clarification` for this
intent; `app/orchestrator/reporting.py:resolve_final_clarification` overrides it after the DAG
runs, based on whether `resolve_reference` actually found a match. This keeps the "ask before
guessing" safety property while not making the classifier hallucinate an answer it can't have yet.

**Mock mode**: `app/llm/mock_provider.py:MockLLMProvider` implements the same forced-schema
contract with real rule-based NLU (keyword/regex matching, airline/company/person/date-phrase
extraction) rather than a canned stub, so the entire pipeline runs deterministically with zero
API keys — this is what makes the Docker Compose stack demoable offline.

### 1.2 Query Planner (`app/planner/`)

`QueryPlanner.build_plan(intent)` dispatches to a hand-written DAG template per known intent
(`app/planner/query_planner.py`), or falls back to a generic parallel fan-out across
`intent.services` for anything unrecognized. Each template deliberately encodes the right mix of
parallel and sequential structure:

- **`cancel_flight`**: Gmail search (booking email) + Calendar search (flight event) run in
  parallel; a `draft_email` write node depends on *both* (needs the booking reference from the
  email and the flight description from the event).
- **`prepare_for_meeting`**: Calendar search + Drive search run in parallel (independent); Gmail
  search depends on the Calendar result (needs the resolved attendee list) — "Find calendar event
  -> search emails with client -> pull docs" from the assignment is genuinely two edges: one
  parallel, one sequential.
- **`find_conflicts`**: Calendar search + Drive search (for the out-of-office doc) run in
  parallel; a `compute` node (`detect_conflicts`, no Google API call) depends on both and
  cross-references event start times against the doc's date range.

`app/planner/dag.py:ExecutionDAG.topological_layers()` is a from-scratch Kahn's-algorithm
implementation that groups nodes into batches — each batch is exactly what the orchestrator hands
to a single `asyncio.gather()` call.

### 1.3 Service Orchestrator (`app/orchestrator/orchestrator.py`)

Runs each DAG layer concurrently, and per node:

- Enforces a 15s timeout (`asyncio.wait_for`).
- If a node's non-optional dependency failed, the node is marked `skipped` rather than run —
  this is the "failures must be handled gracefully" requirement: a Gmail search failing does not
  crash a parallel Calendar search, and does not silently run downstream nodes on missing data.
  Nodes can opt into `optional=True` to run anyway even if their dependency failed/errored.
- **Every write operation is blocked outright whenever `intent.needs_clarification` is true** —
  this is a single, centralized policy (not per-template logic) that guarantees an ambiguous
  query never silently sends/creates/deletes anything.
- Successful write nodes are appended to `report.actions_taken` and logged to `audit_log`
  (service, operation, JSON-safe payload, success/error) for compliance/security review.

An optional `on_node_complete(node, result)` callback lets the WebSocket endpoint
(`app/api/v1/ws.py`) stream live per-node progress ("searching Gmail... done (120ms)") without
duplicating orchestration logic — both the REST and WS endpoints call the exact same
`app/orchestrator/pipeline.py:run_query_pipeline`.

### 1.4 Embedding & Hybrid Search Layer (`app/search/`)

See `app/search/vector_store.py` module docstring for the full rationale; summary:

- **What's embedded**: `title + body`, prepared by `app/search/chunking.py` — for long email
  bodies, a head-heavy + tail slice (80%/20%) rather than naive truncation, since Gmail threads
  put the newest reply at the top and the original context at the bottom.
- **Metadata filtering before vector search**: every hybrid-search query pushes `user_id` (tenant
  isolation) plus optional sender/attendee/mime_type/date-range filters into the WHERE clause of
  both candidate-generation CTEs, narrowing the row set *before* the `<=>` cosine-distance scan
  runs — this is the "metadata filtering > pure vector search for speed" hint from the
  assignment, and is what keeps queries well under the 500ms target (measured ~5-50ms against the
  demo fixture; see `tests/integration/test_hybrid_search_precision.py`).
- **Fusion**: vector candidates and Postgres full-text (`ts_rank`) keyword candidates are combined
  via Reciprocal Rank Fusion (`k=10`, tuned down from the textbook `k=60` for a per-user corpus of
  thousands rather than millions of rows) plus a small recency boost (30-day half-life decay,
  applied at query time rather than baked into the embedding — see "temporal decay" note below).
- **Precision@5**: measured directly in `tests/integration/test_hybrid_search_precision.py`
  against labeled queries over the seeded fixture data, target `> 0.8` — see that file's docstring
  for why precision@min(5, |relevant|) is the correct metric on a small demo corpus.

**Temporal decay**: baking recency into the embedding vector itself would mean the exact same
email's vector changes every day (since "how old is this" keeps shifting), forcing a full
re-embed on every sync pass just to keep rankings fresh. Instead the embedding is timeless and a
small recency multiplier is added to the fused score at query time — cheap, always current, and
doesn't affect what "similar" means.

### 1.5 Response Synthesizer (`app/synthesizer/response_synthesizer.py`)

Builds one structured JSON context (results per service, per-service errors, actions taken, any
pending confirmation, resolved reference item, computed conflicts, the final clarification
question) and hands it to the LLM as the *only* source of truth — the system prompt explicitly
forbids inventing facts not present in that JSON. This is what lets partial failures ("Gmail
succeeded, Calendar failed") produce a graceful partial answer instead of either a crash or a
confidently wrong one.

## 2. Database schema

See [`docs/er_diagram.md`](docs/er_diagram.md) for the full ER diagram and design notes
(separate per-service cache tables vs. one polymorphic table, `content_hash` for incremental
sync, `ivfflat` + `(user_id, time_col)` composite indexes, `extra_metadata` for content-derived
structured hints, append-only `audit_log`).

## 3. Scaling to 1M users

```
                        Load Balancer (geo-routed)
                              |
        -----------------------------------------------
        |                    |                        |
   US region             EU region                APAC region
   API servers x N       API servers x N          API servers x N
   (FastAPI, stateless)                                |
        |                    |                        |
        -----------------------------------------------
                              |
              -----------------------------------
              |                                   |
       Redis (cache + rate limit +          PostgreSQL (metadata + pgvector)
       Celery broker), regional replicas    primary + read replicas, sharded by user_id
              |                                   |
              -----------------------------------
                              |
                    Celery workers (sync + long orchestrations)
                              |
                 Google Workspace APIs  +  LLM APIs (Anthropic/OpenAI)
```

### Caching (implemented in `app/cache/`)

| What | Where | TTL | Why |
|---|---|---|---|
| Embeddings | Redis, `app/cache/cached_embedding_provider.py` | 1h | identical text -> identical vector forever; TTL is just memory hygiene |
| Intent classifications | Redis, `app/intent/classifier.py` | 5min | conversation context can change what the same raw text means, so kept short |
| Conversation history | Postgres (`conversations`, last 5 via `app/orchestrator/context.py`) | n/a | source of truth; a Redis read-through cache in front of this is the natural next step at scale |
| Rate limit counters | Redis fixed-hour window, `app/cache/rate_limiter.py` | 1h | 100 queries/user/hour, shared across every API process |
| Google API quota | Redis fixed-1s window, `app/cache/google_quota.py` | 2s | approximates the 250 units/sec project-wide cap across all API/worker processes |

At 1M users, embedding cache hit rate is the dominant lever on both LLM spend and P99 latency —
most queries re-search recently-synced, already-embedded content.

### Rate limiting & Google API quotas

- Per-user: 100 queries/hour, enforced at the top of `run_query_pipeline` before any expensive
  work happens (classification, orchestration) — cheap to reject early.
- Google API-wide: `acquire_google_quota()` wraps every real Google API call
  (`app/agents/google_clients.py:_call_google`) with a Redis-backed distributed token bucket, so N
  horizontally-scaled API/worker processes collectively respect one Google Cloud project's quota
  instead of each independently hitting the ceiling.
- Every real Google call already retries with exponential backoff (`tenacity`, 5 attempts) on
  403/429/500/503 — "Google APIs fail often" from the assignment.

### Async processing & pre-computation

- `POST /api/v1/query` runs synchronously for reads/searches (typically <100ms against a synced
  corpus) but the 2-5s slow path (multi-service orchestration with a write, e.g. `cancel_flight`)
  is still request-response today; at higher scale this would move to: enqueue via Celery, return
  a task id immediately, client polls or receives a WebSocket push
  (`app/api/v1/ws.py` already demonstrates the streaming-progress pattern this would build on).
- Background sync (`app/sync/tasks.py`, Celery beat every `SYNC_INTERVAL_MINUTES`) keeps
  Gmail/Calendar/Drive cache tables warm so queries hit pgvector, never live Google APIs, on the
  hot path. `content_hash` skips re-embedding unchanged items — the dominant cost of a sync pass.
- At 1M users, `sync-all-users` as a single task enumerating every user does not scale; the next
  step is fanning out one Celery task per user (or per shard) so sync parallelizes across the
  whole worker fleet and a single user's large mailbox can't head-of-line-block everyone else's
  15-minute sync window.

### Sharding & multi-region

- `gmail_cache` / `gcal_cache` / `gdrive_cache` / `conversations` / `sync_status` all key
  everything off `user_id` first — a natural shard key. At 1M users this becomes Postgres
  partitioning (or Citus/logical sharding) by `user_id` hash, keeping every query's WHERE clause
  (already `user_id = :user_id` first) shard-local.
- Deploy stateless API servers + Celery workers per region (US/EU/APAC); route users to their
  nearest region by geo-DNS/anycast LB. Postgres primary lives in one region per shard with
  read replicas fanned out; cross-region writes (rare — mostly background sync) accept the extra
  latency.
- Redis: regional instances for rate-limiting/quota (per-region caps composed into a global
  budget) and cache (cache misses are cheap, no need for cross-region cache coherency).

### Metrics to monitor (target from the assignment)

| Metric | Target | Where it's measured today |
|---|---|---|
| P99 query latency | <2s | `timing_ms.total_ms` in every `/api/v1/query` response; aggregate via APM/structured logs |
| Cache hit rate | >80% | Redis `INFO stats` (`keyspace_hits`/`keyspace_misses`) on the embedding+intent cache DBs |
| Google API error rate | <0.1% | `tenacity` retry exhaustion + `audit_log.result='error'` rows tagged `service` |
| Embedding freshness | <15min lag | `sync_status.last_synced_at` per user/service, exposed via `GET /api/v1/sync/status` |

Structured JSON logs (`structlog`, `app/middleware/logging_middleware.py`) carry a per-request
`request_id` through classification/orchestration/synthesis, so a slow or failed query can be
traced end-to-end without correlating separate log streams.

## 4. Security

- **Multi-tenant isolation**: every cache-table query and every agent call is scoped by
  `user_id`; there is no code path that reads another user's data. The FastAPI dependency
  resolving "current user" (`app/dependencies.py`) is the single place session/JWT auth would
  plug in — everything downstream already only takes a `user_id`/`User`.
- **OAuth token refresh**: `app/auth/token_manager.py` refreshes the access token ~2 minutes
  before expiry and persists the new token, so an in-flight multi-node orchestration never hits a
  401 mid-DAG.
- **Audit logging**: every write operation (send/draft/create/update/delete/share/move), success
  or failure, is recorded in `audit_log` with the resolved payload (JSON-safe serialized),
  service, operation, and result — see `app/orchestrator/orchestrator.py:_audit`.
- **No secrets in mock mode**: with `MOCK_GOOGLE_API=true` (the default), no real Google
  credentials are requested, stored, or transmitted at all.

## 5. What's real vs. mocked

| Component | Real implementation | Mock/demo implementation |
|---|---|---|
| Gmail/Calendar/Drive API calls | `app/agents/google_clients.py` (googleapiclient + OAuth2, retry/backoff, quota-limited) | `app/agents/mock_clients.py`, seeded from `app/agents/mock_data/*.json` |
| LLM (classification, synthesis) | `app/llm/anthropic_provider.py` (Claude, forced tool-use) | `app/llm/mock_provider.py` (rule-based NLU + templated synthesis) |
| Embeddings | `app/llm/openai_embeddings.py` (`text-embedding-3-small`) | `app/llm/mock_provider.py:MockEmbeddingProvider` (deterministic feature-hashing) |
| OAuth2 flow | `app/auth/google_oauth.py` (real authorization-code flow) | Bypassed entirely; `GET /api/v1/auth/google` reports mock mode |

Switching any of these to "real" is a config change (`.env`: `MOCK_GOOGLE_API=false` +
`GOOGLE_CLIENT_ID`/`SECRET`, `LLM_PROVIDER=anthropic` + `ANTHROPIC_API_KEY`,
`EMBEDDING_PROVIDER=openai` + `OPENAI_API_KEY`) — no code changes, since every provider is
selected behind the same interface (`app/llm/base.py`, `app/agents/base.py`).
