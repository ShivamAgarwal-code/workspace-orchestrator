"""Unit tests for agent execute()/get_context() against the mock Google clients directly (no DB
needed — search() is covered separately in tests/integration since it hits pgvector)."""
import pytest

from app.agents.base import AgentError
from app.agents.drive_agent import DriveAgent
from app.agents.gcal_agent import GCalAgent
from app.agents.gmail_agent import GmailAgent
from app.agents.mock_clients import (
    MockCalendarClient,
    MockDriveClient,
    MockGmailClient,
    reset_mock_store,
)


@pytest.fixture
def user_id():
    uid = "unit-test-user"
    yield uid
    reset_mock_store(uid)


async def test_gmail_draft_email_creates_draft_not_send(user_id):
    agent = GmailAgent(session=None, user=None, embeddings=None, client=MockGmailClient(user_id))
    result = await agent.execute("draft_email", {"to": "support@airline.com", "subject": "Cancel", "body": "Please cancel."})
    assert "DRAFT" in result["labels"]
    fetched = await agent.get_context(result["id"])
    assert fetched["subject"] == "Cancel"


async def test_gmail_send_email(user_id):
    agent = GmailAgent(session=None, user=None, embeddings=None, client=MockGmailClient(user_id))
    result = await agent.execute("send_email", {"to": "a@b.com", "subject": "Hi", "body": "Hello"})
    assert "SENT" in result["labels"]


async def test_gmail_unknown_action_raises_agent_error(user_id):
    agent = GmailAgent(session=None, user=None, embeddings=None, client=MockGmailClient(user_id))
    with pytest.raises(AgentError):
        await agent.execute("delete_everything", {})


async def test_gmail_get_context_missing_message_raises(user_id):
    agent = GmailAgent(session=None, user=None, embeddings=None, client=MockGmailClient(user_id))
    with pytest.raises(AgentError):
        await agent.get_context("does-not-exist")


async def test_gcal_update_event_resolves_from_search_result_object(user_id):
    from app.agents.base import SearchResult

    client = MockCalendarClient(user_id)
    events = await client.list_events()
    target = events[0]
    agent = GCalAgent(session=None, user=None, embeddings=None, client=client)
    event_ref = SearchResult(id=target["id"], service="gcal", title=target["title"], snippet="", score=1.0)

    result = await agent.execute("update_event", {"event_ref": event_ref, "patch": {"location": "New Room"}})

    assert result["location"] == "New Room"


async def test_gcal_update_event_without_resolved_target_raises(user_id):
    agent = GCalAgent(session=None, user=None, embeddings=None, client=MockCalendarClient(user_id))
    with pytest.raises(AgentError, match="no target event"):
        await agent.execute("update_event", {"event_ref": None})


async def test_gcal_create_and_delete_event(user_id):
    from datetime import UTC, datetime, timedelta

    agent = GCalAgent(session=None, user=None, embeddings=None, client=MockCalendarClient(user_id))
    start = datetime.now(UTC)
    created = await agent.execute("create_event", {
        "summary": "New meeting", "start": start, "end": start + timedelta(hours=1),
    })
    assert created["title"] == "New meeting"

    deleted = await agent.execute("delete_event", {"event_id": created["id"]})
    assert deleted["status"] == "deleted"


async def test_drive_share_file(user_id):
    client = MockDriveClient(user_id)
    files = await client.list_files()
    agent = DriveAgent(session=None, user=None, embeddings=None, client=client)

    result = await agent.execute("share_file", {"file_id": files[0]["id"], "email": "colleague@company.com"})

    assert any(s["email"] == "colleague@company.com" for s in result["shared_with"])


async def test_drive_move_file_updates_parent(user_id):
    client = MockDriveClient(user_id)
    files = await client.list_files()
    agent = DriveAgent(session=None, user=None, embeddings=None, client=client)

    result = await agent.execute("move_file", {"file_id": files[0]["id"], "new_parent_id": "folder_new"})

    assert result["parent_folder_id"] == "folder_new"


async def test_drive_infer_mime_type_from_query_keyword():
    from app.agents.drive_agent import _infer_mime_type

    assert _infer_mime_type(None, "Show me PDFs in Drive") == "application/pdf"
    assert _infer_mime_type("pdf", "irrelevant") == "application/pdf"
    assert _infer_mime_type(None, "no file type mentioned here") is None
