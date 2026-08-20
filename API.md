# API Reference

Interactive Swagger UI: `http://localhost:8000/docs` · OpenAPI JSON: `http://localhost:8000/openapi.json`

## Authentication

Every endpoint (except `/health` and `GET /api/v1/auth/google`) resolves the current user from an
`X-User-Id: <uuid>` header. If omitted, requests fall back to the seeded demo user
(`00000000-0000-0000-0000-000000000001`, `michel@resilt.com`) — this is a documented
simplification in place of full session/JWT auth (see DESIGN.md §4); every downstream component
already only takes a `user_id`, so swapping in real auth only touches `app/dependencies.py`.

---

## `POST /api/v1/query`

Runs the full pipeline: intent classification → query planning → parallel orchestration → hybrid
search / write execution → response synthesis.

**Request**

```json
{
  "query": "Cancel my Turkish Airlines flight",
  "conversation_id": null
}
```

| Field | Type | Required | Notes |
|---|---|---|---|
| `query` | string | yes | 1-2000 chars |
| `conversation_id` | UUID \| null | no | accepted for future multi-turn correlation; conversation *context* is currently derived automatically from the user's last 5 turns regardless of this field |

**Response `200`**

```json
{
  "response": "I found your Turkish Airlines booking (TK1234)...\n\n✓ Draft (not send) the cancellation email for user review.\n\nWould you like me to send this email?",
  "conversation_id": "b2e1...",
  "intent": "cancel_flight",
  "services_used": ["gmail", "gcal", "gdrive"],
  "actions_taken": ["Draft (not send) the cancellation email for user review."],
  "needs_clarification": false,
  "clarification_question": null,
  "results": {
    "gmail": [ { "id": "...", "service": "gmail", "title": "...", "snippet": "...", "score": 0.21, "metadata": {}, "timestamp": "..." } ],
    "gcal": [ ... ],
    "gdrive": [ ... ]
  },
  "errors": {},
  "timing_ms": { "classify_ms": 1.2, "execute_ms": 33.6, "synthesize_ms": 0.7, "total_ms": 46.2 }
}
```

| Field | Notes |
|---|---|
| `needs_clarification` / `clarification_question` | when true, no write operation ran — see DESIGN.md §1.3 |
| `errors` | keyed by service; a non-empty entry means that service's node failed but the rest of the response is still complete |
| `results` | every search hit surfaced this turn, normalized across services (`app/agents/base.py:SearchResult`) |

**Errors**

| Status | Cause |
|---|---|
| `429` | rate limit exceeded (100/user/hour); `Retry-After` header set |
| `422` | invalid request body (e.g. empty `query`) |
| `500` | unhandled server error (logged with a request id; generic message returned to the client) |

---

## `GET /api/v1/ws/query` (WebSocket, bonus)

Same pipeline as `POST /api/v1/query`, but streams per-node progress before the final result.
Optional `?user_id=<uuid>` query param (defaults to the demo user, since browser WebSocket clients
can't easily set custom headers).

Send: `{"query": "What is on my calendar next week?"}` (repeatable over the same connection).

Receive, zero or more per query:
```json
{"type": "node_complete", "node_id": "search_gcal", "agent": "gcal", "description": "...", "status": "ok", "duration_ms": 12.3, "error": null}
```
then exactly one:
```json
{"type": "final", "response": "...", "conversation_id": "...", "intent": "...", "needs_clarification": false, "clarification_question": null, "actions_taken": [...], "results": {...}, "errors": {}, "timing_ms": {...}}
```
or, on rate limit: `{"type": "error", "message": "...", "retry_after_seconds": 1800}`.

---

## `GET /api/v1/auth/google`

Starts the Google OAuth2 authorization-code flow.

- With `MOCK_GOOGLE_API=true` (default): returns `{"authorization_url": null, "mock_mode": true, "message": "..."}` immediately — no Google Cloud project needed.
- With `MOCK_GOOGLE_API=false` and `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET` configured: returns `{"authorization_url": "https://accounts.google.com/o/oauth2/auth?...", "mock_mode": false}` to redirect the user to.

## `GET /api/v1/auth/google/callback`

OAuth2 redirect target (`code`, `state` query params from Google; optional `user_id` to attach the
tokens to a specific user, defaults to the demo user). Exchanges the code, persists
access/refresh token + expiry on the `User` row, redirects to `/docs`. 400 if hit while
`MOCK_GOOGLE_API=true`.

---

## `POST /api/v1/sync/trigger`

Triggers a Gmail/Calendar/Drive sync for the current user.

| Query param | Default | Effect |
|---|---|---|
| `wait` | `false` | `true` runs the sync inline and returns its summary; `false` enqueues a Celery task and returns immediately with a `task_id` |

```json
// wait=true
{"triggered": true, "mode": "sync", "summary": {"gmail": 10, "gcal": 7, "gdrive": 7}, "task_id": null}
// wait=false (default)
{"triggered": true, "mode": "async", "summary": null, "task_id": "078b2018-..."}
```

## `GET /api/v1/sync/status`

```json
{
  "services": [
    {"service": "gmail", "state": "idle", "last_synced_at": "2026-08-20T20:24:49Z", "items_synced": 10, "last_error": null},
    {"service": "gcal", "state": "idle", "last_synced_at": "2026-08-20T20:24:49Z", "items_synced": 7, "last_error": null},
    {"service": "gdrive", "state": "idle", "last_synced_at": "2026-08-20T20:24:49Z", "items_synced": 7, "last_error": null}
  ]
}
```

`state` is one of `idle | running | error`; `last_error` is set when a service's most recent sync
attempt failed (e.g. a revoked Google token) — importantly, a failing service never blocks the
other two (see DESIGN.md §1.3).

---

## `GET /health`

Liveness/readiness check — pings Postgres and Redis.

```json
{"status": "ok", "checks": {"database": "ok", "redis": "ok"}}
```

`status` is `"degraded"` if either check fails (their individual error is included in `checks`).

---

## Postman collection

[`docs/postman_collection.json`](docs/postman_collection.json) — import into Postman; set the
`base_url` collection variable (defaults to `http://localhost:8000`).
