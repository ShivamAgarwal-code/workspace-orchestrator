# Sample Queries

All of these were run live against the seeded mock fixture data (`docker compose up`, zero API
keys) via `POST /api/v1/query`. Dates are relative to "today" at seed time (the fixtures use
day-offsets, resolved fresh on every `docker compose up` — see `app/agents/mock_data/loader.py`),
so exact dates below will differ on a different run date, but the *behavior* is stable.

Run any of these yourself:
```bash
curl -X POST http://localhost:8000/api/v1/query -H "Content-Type: application/json" \
  -d '{"query": "<QUERY>"}'
```

## Single service

### 1. "What is on my calendar next week?"
- **Intent**: `search_calendar` · **Services**: `gcal`
- **Behavior**: resolves "next week" to next Mon-Sun in the user's timezone, lists matching events.
- **Expected**: `needs_clarification: false`, `results.gcal` non-empty, `results.gmail`/`gdrive` empty.

### 2. "Find emails from sarah@company.com about the budget"
- **Intent**: `search_email` · **Services**: `gmail`
- **Behavior**: extracts `sarah@company.com` as a sender filter (pushed into the hybrid-search
  WHERE clause, not just a ranking signal) combined with vector+keyword relevance for "budget".
- **Expected**: only emails from `sarah@company.com` are returned.

### 3. "Show me PDFs in Drive from last month"
- **Intent**: `search_drive` · **Services**: `gdrive`
- **Behavior**: `file_type=pdf` -> `mime_type=application/pdf` filter; `"last month"` resolved to
  the full previous calendar month via `app/utils/temporal.py`.
- **Expected**: only `application/pdf` files modified last calendar month.

## Multi-service

### 4. "Cancel my Turkish Airlines flight"
- **Intent**: `cancel_flight` · **Services**: `gmail`, `gcal`, `gdrive`
- **Behavior**: Gmail (booking email) + Calendar (flight event) searched **in parallel**; a
  cancellation email is **drafted, not sent**, using the booking reference extracted from the
  email and the flight description from the event.
- **Expected**: `actions_taken` contains a draft action; response ends by asking the user to
  confirm before actually sending; `needs_clarification: false`.

### 5. "Prepare for tomorrow's client meeting with Acme Corp"
- **Intent**: `prepare_for_meeting` · **Services**: `gmail`, `gcal`, `gdrive`
- **Behavior**: Calendar search (tomorrow + "Acme Corp") and Drive search run in parallel; Gmail
  search runs **after** the calendar search, since it uses the resolved attendee list.
- **Expected**: all three `results.*` non-empty; includes the Acme Corp meeting, related emails
  (including the proposal thread), and Drive docs (`Acme Corp - Proposal v2.pdf`, roadmap deck).

### 6. "Find events next week that conflict with my out-of-office doc"
- **Intent**: `find_conflicts` · **Services**: `gcal`, `gdrive`
- **Behavior**: Calendar + Drive searched in parallel; a `compute` node (no Google API call)
  cross-references event start times against the OOO doc's date range (extracted at ingestion
  time into `gdrive_cache.extra_metadata`, not re-parsed from free text on every query).
- **Expected**: `response` includes a "Conflicts found" section naming the overlapping event and
  the document; `results.gcal`/`gdrive` populated even if zero conflicts are found.

### 7. "What is on my calendar next week where john@company.com is invited?"
- **Intent**: `search_calendar` · **Services**: `gcal`
- **Behavior**: attendee filter pushed into the SQL WHERE clause (`:attendee = ANY(attendees)`),
  combined with the "next week" date range.
- **Expected**: only events where `john@company.com` is an attendee.

## Hard cases (ambiguity, context, temporal reasoning)

### 8. "Move the meeting with John" — ambiguous, which John?
- **Intent**: `reschedule_event` · `needs_clarification: true`
- **Behavior**: the classifier detects a person reference with no disambiguating date/company;
  the calendar is still searched (read-only) so candidates are surfaced, but the `update_event`
  write node is **blocked** by the orchestrator's global write-block policy.
- **Expected**: `results.gcal` lists multiple "John" meetings (e.g. "1:1 with John (Smith)" and
  "Partnership check-in with John"); `actions_taken: []`; `clarification_question` asks which
  meeting and new time.

### 9. "That email about the proposal" — requires conversation context
- **Intent**: `reference_lookup`
- **Behavior (no prior context)**: `resolve_reference` finds nothing in the last 5 turns ->
  `needs_clarification: true`, asks "Which email are you referring to?".
- **Behavior (after a prior turn like "Find emails about the proposal")**: `resolve_reference`
  matches the previously-surfaced email by keyword against conversation history, fetches its full
  content via `get_context`, and the response quotes the actual email (subject, sender, body).
- **Try it**:
  ```bash
  curl -X POST http://localhost:8000/api/v1/query -H "Content-Type: application/json" \
    -d '{"query": "Find emails about the proposal"}'
  curl -X POST http://localhost:8000/api/v1/query -H "Content-Type: application/json" \
    -d '{"query": "That email about the proposal"}'
  ```

### 10. "What do I have next Tuesday?" — temporal reasoning + timezone handling
- **Intent**: `search_calendar` (a bare date question with no other service keyword defaults to
  calendar-only, see `MockLLMProvider._classify_intent`)
- **Behavior**: "next Tuesday" resolves via `app/utils/temporal.py:resolve_date_phrase` — the
  *closest future* Tuesday, non-inclusive of today if today is itself Tuesday (documented
  convention), computed in the user's IANA timezone (`User.timezone`) then converted to UTC for
  the SQL range filter. See `tests/unit/test_temporal.py` for exhaustive coverage including a
  timezone-crossing-midnight case and year-boundary month arithmetic.
- **Expected**: `results.gcal` contains only events whose `start_time` falls on that specific
  date. (On days where no fixture event happens to land exactly on "next Tuesday" — the fixture
  uses day-offsets from whatever day the demo is started — this correctly returns an empty list;
  the temporal *resolution* is verified deterministically by the unit tests with a frozen clock.)

### 11. "Prepare for tomorrow's client meeting with Acme Corp" then later "Cancel my Turkish Airlines flight"
- **Regression coverage**: earlier conversation turns are included in the classifier prompt as
  context (for reference resolution), but must **never** leak keywords into an unrelated later
  query — e.g. a prior "cancel my flight" turn must not make a later "prepare for a meeting" query
  get misclassified as `cancel_flight` just because the word "cancel" appears in the history
  block. See `tests/unit/test_mock_classifier.py::test_conversation_history_does_not_leak_keywords_into_current_query`
  for the regression test (this was a real bug caught during manual verification).

## Edge cases worth trying

- **Partial failure**: stop the `postgres`/point a bad `mime_type` at the DB, etc. — any single
  node failure surfaces in `errors.<service>` while the rest of the response stays intact
  (`app/orchestrator/orchestrator.py` never lets one node's exception crash the whole DAG).
- **Rate limit**: send >100 queries for the same `X-User-Id` within an hour -> `429` with a
  `Retry-After` header.
- **WebSocket streaming**: connect to `ws://localhost:8000/api/v1/ws/query` and send
  `{"query": "Cancel my Turkish Airlines flight"}` to watch `node_complete` events arrive as
  Gmail and Calendar are searched in parallel, before the final synthesized response.
