"""End-to-end API tests: the full classify -> plan -> orchestrate -> synthesize pipeline through
the actual FastAPI app, against the real Postgres/Redis (via docker-compose) and the mock
Google/LLM providers (zero external API keys needed)."""
import httpx
import pytest

from app.main import app
from app.sync.tasks import sync_user_now


@pytest.fixture
async def client(test_user):
    await sync_user_now(test_user)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", headers={"X-User-Id": str(test_user)}) as c:
        yield c


async def test_health_endpoint_no_auth_needed():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


async def test_single_service_calendar_query(client):
    resp = await client.post("/api/v1/query", json={"query": "What is on my calendar next week?"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["intent"] == "search_calendar"
    assert body["services_used"] == ["gcal"]
    assert body["needs_clarification"] is False
    assert isinstance(body["results"]["gcal"], list)


async def test_multi_service_prepare_for_meeting(client):
    resp = await client.post("/api/v1/query", json={"query": "Prepare for tomorrow's client meeting with Acme Corp"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["intent"] == "prepare_for_meeting"
    assert len(body["results"]["gcal"]) >= 1
    assert len(body["results"]["gmail"]) >= 1
    assert len(body["results"]["gdrive"]) >= 1


async def test_cancel_flight_drafts_but_does_not_send(client):
    resp = await client.post("/api/v1/query", json={"query": "Cancel my Turkish Airlines flight"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["intent"] == "cancel_flight"
    assert any("Draft" in a for a in body["actions_taken"])
    assert "send" in body["response"].lower()  # asks for confirmation before sending


async def test_ambiguous_query_asks_for_clarification_and_performs_no_write(client):
    resp = await client.post("/api/v1/query", json={"query": "Move the meeting with John"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["needs_clarification"] is True
    assert body["clarification_question"]
    assert body["actions_taken"] == []  # update_event must never have run


async def test_reference_resolution_across_two_turns(client):
    await client.post("/api/v1/query", json={"query": "Find emails about the proposal"})
    resp = await client.post("/api/v1/query", json={"query": "That email about the proposal"})
    body = resp.json()
    assert body["needs_clarification"] is False
    assert "proposal" in body["response"].lower()


async def test_reference_resolution_without_context_asks_for_clarification(client):
    resp = await client.post("/api/v1/query", json={"query": "That email about the proposal"})
    body = resp.json()
    assert body["needs_clarification"] is True


async def test_each_query_gets_a_distinct_conversation_id(client):
    resp1 = await client.post("/api/v1/query", json={"query": "What is on my calendar next week?"})
    resp2 = await client.post("/api/v1/query", json={"query": "Find emails about the budget"})
    assert resp1.json()["conversation_id"] != resp2.json()["conversation_id"]


async def test_sync_status_reflects_seeded_data(client):
    resp = await client.get("/api/v1/sync/status")
    assert resp.status_code == 200
    services = {s["service"] for s in resp.json()["services"]}
    assert services == {"gmail", "gcal", "gdrive"}


async def test_auth_google_reports_mock_mode(client):
    resp = await client.get("/api/v1/auth/google")
    assert resp.status_code == 200
    body = resp.json()
    assert body["mock_mode"] is True
    assert body["authorization_url"] is None
