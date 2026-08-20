"""In-memory mock Google API clients, seeded from the JSON fixtures under mock_data/.

Each is keyed by user_id and lazily seeded on first access, so write operations (send, draft,
create, update, delete, share) mutate a real per-user in-memory mailbox/calendar/drive for the
lifetime of the process — enough to demo and test full read+write orchestration flows with zero
Google Cloud credentials.
"""
import asyncio
import uuid
from datetime import UTC, datetime

from app.agents.mock_data.loader import load_emails, load_events, load_files

_STORE: dict[str, dict[str, list[dict]]] = {}


def _mailbox(user_id: str) -> dict[str, list[dict]]:
    if user_id not in _STORE:
        now = datetime.now(UTC)
        _STORE[user_id] = {
            "emails": load_emails(now),
            "events": load_events(now),
            "files": load_files(now),
        }
    return _STORE[user_id]


def reset_mock_store(user_id: str | None = None) -> None:
    """Used by tests to get a clean, deterministic fixture state between cases."""
    if user_id is None:
        _STORE.clear()
    else:
        _STORE.pop(user_id, None)


class MockGmailClient:
    def __init__(self, user_id: str):
        self.user_id = user_id

    async def list_messages(self, query: str = "", max_results: int = 50) -> list[dict]:
        await asyncio.sleep(0)
        return list(_mailbox(self.user_id)["emails"])[:max_results]

    async def get_message(self, message_id: str) -> dict:
        for m in _mailbox(self.user_id)["emails"]:
            if m["id"] == message_id:
                return m
        raise KeyError(f"message {message_id} not found")

    async def send_message(self, to: str, subject: str, body: str, thread_id: str | None = None) -> dict:
        msg = {
            "id": f"msg_sent_{uuid.uuid4().hex[:8]}",
            "thread_id": thread_id or f"thread_{uuid.uuid4().hex[:8]}",
            "subject": subject,
            "from": self.user_id,
            "to": [to],
            "body": body,
            "labels": ["SENT"],
            "received_at": datetime.now(UTC),
        }
        _mailbox(self.user_id)["emails"].append(msg)
        return msg

    async def create_draft(self, to: str, subject: str, body: str) -> dict:
        draft = {
            "id": f"draft_{uuid.uuid4().hex[:8]}",
            "thread_id": f"thread_{uuid.uuid4().hex[:8]}",
            "subject": subject,
            "from": self.user_id,
            "to": [to],
            "body": body,
            "labels": ["DRAFT"],
            "received_at": datetime.now(UTC),
        }
        _mailbox(self.user_id)["emails"].append(draft)
        return draft

    async def modify_labels(self, message_id: str, add: list[str] | None = None, remove: list[str] | None = None) -> dict:
        msg = await self.get_message(message_id)
        labels = set(msg.get("labels", []))
        labels |= set(add or [])
        labels -= set(remove or [])
        msg["labels"] = sorted(labels)
        return msg


class MockCalendarClient:
    def __init__(self, user_id: str):
        self.user_id = user_id

    async def list_events(
        self, time_min: datetime | None = None, time_max: datetime | None = None,
        q: str | None = None, max_results: int = 50,
    ) -> list[dict]:
        await asyncio.sleep(0)
        events = _mailbox(self.user_id)["events"]
        if time_min:
            events = [e for e in events if e["end_time"] >= time_min]
        if time_max:
            events = [e for e in events if e["start_time"] <= time_max]
        return events[:max_results]

    async def get_event(self, event_id: str) -> dict:
        for e in _mailbox(self.user_id)["events"]:
            if e["id"] == event_id:
                return e
        raise KeyError(f"event {event_id} not found")

    async def create_event(
        self, summary: str, description: str, start: datetime, end: datetime,
        attendees: list[str] | None = None, location: str | None = None,
    ) -> dict:
        event = {
            "id": f"evt_{uuid.uuid4().hex[:8]}",
            "calendar_id": "primary",
            "title": summary,
            "description": description,
            "location": location,
            "organizer": self.user_id,
            "attendees": attendees or [],
            "status": "confirmed",
            "start_time": start,
            "end_time": end,
        }
        _mailbox(self.user_id)["events"].append(event)
        return event

    async def update_event(self, event_id: str, patch: dict) -> dict:
        event = await self.get_event(event_id)
        event.update(patch)
        return event

    async def delete_event(self, event_id: str) -> dict:
        events = _mailbox(self.user_id)["events"]
        for i, e in enumerate(events):
            if e["id"] == event_id:
                return events.pop(i)
        raise KeyError(f"event {event_id} not found")


class MockDriveClient:
    def __init__(self, user_id: str):
        self.user_id = user_id

    async def list_files(self, q: str | None = None, max_results: int = 50) -> list[dict]:
        await asyncio.sleep(0)
        return list(_mailbox(self.user_id)["files"])[:max_results]

    async def get_file(self, file_id: str) -> dict:
        for f in _mailbox(self.user_id)["files"]:
            if f["id"] == file_id:
                return f
        raise KeyError(f"file {file_id} not found")

    async def share_file(self, file_id: str, email: str, role: str = "reader") -> dict:
        file = await self.get_file(file_id)
        shares = file.setdefault("shared_with", [])
        shares.append({"email": email, "role": role})
        return file

    async def create_folder(self, name: str, parent_id: str | None = None) -> dict:
        folder = {
            "id": f"folder_{uuid.uuid4().hex[:8]}",
            "name": name,
            "mime_type": "application/vnd.google-apps.folder",
            "content_preview": "",
            "owners": [self.user_id],
            "web_view_link": f"https://drive.google.com/drive/folders/folder_{uuid.uuid4().hex[:8]}",
            "parent_folder_id": parent_id,
            "modified_at": datetime.now(UTC),
        }
        _mailbox(self.user_id)["files"].append(folder)
        return folder

    async def move_file(self, file_id: str, new_parent_id: str) -> dict:
        file = await self.get_file(file_id)
        file["parent_folder_id"] = new_parent_id
        file["modified_at"] = datetime.now(UTC)
        return file
