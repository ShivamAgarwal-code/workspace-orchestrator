"""Real Google API wrappers (Gmail/Calendar/Drive), method-signature-compatible with
app.agents.mock_clients so GmailAgent/GCalAgent/DriveAgent can use either interchangeably based
on settings.mock_google_api.

`googleapiclient` is a synchronous/blocking client; every call is dispatched via
`asyncio.to_thread` so agents stay non-blocking. Transient failures (rate limits, 5xx) are
retried with exponential backoff — the assignment explicitly calls out that "Google APIs fail
often."
"""
import asyncio
import base64
from datetime import datetime
from email.mime.text import MIMEText
from typing import Any

from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

_RETRYABLE_STATUSES = {403, 429, 500, 503}


def _is_retryable(exc: BaseException) -> bool:
    from googleapiclient.errors import HttpError

    return isinstance(exc, HttpError) and getattr(exc, "status_code", getattr(exc.resp, "status", None)) in _RETRYABLE_STATUSES


def _retry():
    return retry(
        retry=retry_if_exception(_is_retryable),
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=1, max=20),
        reraise=True,
    )


def _extract_header(headers: list[dict], name: str) -> str | None:
    for h in headers:
        if h["name"].lower() == name.lower():
            return h["value"]
    return None


def _extract_body(payload: dict) -> str:
    if payload.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")
    for part in payload.get("parts", []) or []:
        if part.get("mimeType") == "text/plain":
            return _extract_body(part)
    for part in payload.get("parts", []) or []:
        text = _extract_body(part)
        if text:
            return text
    return ""


class RealGmailClient:
    def __init__(self, credentials):
        from googleapiclient.discovery import build

        self._svc = build("gmail", "v1", credentials=credentials, cache_discovery=False)

    async def list_messages(self, query: str = "", max_results: int = 50) -> list[dict]:
        @_retry()
        def _call():
            resp = self._svc.users().messages().list(userId="me", q=query, maxResults=max_results).execute()
            return resp.get("messages", [])

        refs = await asyncio.to_thread(_call)
        return await asyncio.gather(*(self.get_message(r["id"]) for r in refs))

    async def get_message(self, message_id: str) -> dict:
        @_retry()
        def _call():
            return self._svc.users().messages().get(userId="me", id=message_id, format="full").execute()

        raw = await asyncio.to_thread(_call)
        headers = raw.get("payload", {}).get("headers", [])
        return {
            "id": raw["id"],
            "thread_id": raw.get("threadId"),
            "subject": _extract_header(headers, "Subject") or "",
            "from": _extract_header(headers, "From") or "",
            "to": [_extract_header(headers, "To") or ""],
            "body": _extract_body(raw.get("payload", {})) or raw.get("snippet", ""),
            "labels": raw.get("labelIds", []),
            "received_at": datetime.fromtimestamp(int(raw["internalDate"]) / 1000),
        }

    async def send_message(self, to: str, subject: str, body: str, thread_id: str | None = None) -> dict:
        mime = MIMEText(body)
        mime["to"] = to
        mime["subject"] = subject
        raw = base64.urlsafe_b64encode(mime.as_bytes()).decode()
        payload: dict[str, Any] = {"raw": raw}
        if thread_id:
            payload["threadId"] = thread_id

        @_retry()
        def _call():
            return self._svc.users().messages().send(userId="me", body=payload).execute()

        return await asyncio.to_thread(_call)

    async def create_draft(self, to: str, subject: str, body: str) -> dict:
        mime = MIMEText(body)
        mime["to"] = to
        mime["subject"] = subject
        raw = base64.urlsafe_b64encode(mime.as_bytes()).decode()

        @_retry()
        def _call():
            return self._svc.users().drafts().create(userId="me", body={"message": {"raw": raw}}).execute()

        return await asyncio.to_thread(_call)

    async def modify_labels(self, message_id: str, add: list[str] | None = None, remove: list[str] | None = None) -> dict:
        @_retry()
        def _call():
            body = {"addLabelIds": add or [], "removeLabelIds": remove or []}
            return self._svc.users().messages().modify(userId="me", id=message_id, body=body).execute()

        return await asyncio.to_thread(_call)


class RealCalendarClient:
    def __init__(self, credentials):
        from googleapiclient.discovery import build

        self._svc = build("calendar", "v3", credentials=credentials, cache_discovery=False)

    async def list_events(
        self, time_min: datetime | None = None, time_max: datetime | None = None,
        q: str | None = None, max_results: int = 50,
    ) -> list[dict]:
        @_retry()
        def _call():
            resp = self._svc.events().list(
                calendarId="primary",
                timeMin=time_min.isoformat() if time_min else None,
                timeMax=time_max.isoformat() if time_max else None,
                q=q, maxResults=max_results, singleEvents=True, orderBy="startTime",
            ).execute()
            return resp.get("items", [])

        items = await asyncio.to_thread(_call)
        return [self._normalize(i) for i in items]

    async def get_event(self, event_id: str) -> dict:
        @_retry()
        def _call():
            return self._svc.events().get(calendarId="primary", eventId=event_id).execute()

        return self._normalize(await asyncio.to_thread(_call))

    async def create_event(
        self, summary: str, description: str, start: datetime, end: datetime,
        attendees: list[str] | None = None, location: str | None = None,
    ) -> dict:
        body = {
            "summary": summary, "description": description, "location": location,
            "start": {"dateTime": start.isoformat()}, "end": {"dateTime": end.isoformat()},
            "attendees": [{"email": a} for a in (attendees or [])],
        }

        @_retry()
        def _call():
            return self._svc.events().insert(calendarId="primary", body=body).execute()

        return self._normalize(await asyncio.to_thread(_call))

    async def update_event(self, event_id: str, patch: dict) -> dict:
        @_retry()
        def _call():
            return self._svc.events().patch(calendarId="primary", eventId=event_id, body=patch).execute()

        return self._normalize(await asyncio.to_thread(_call))

    async def delete_event(self, event_id: str) -> dict:
        @_retry()
        def _call():
            self._svc.events().delete(calendarId="primary", eventId=event_id).execute()

        await asyncio.to_thread(_call)
        return {"id": event_id, "status": "deleted"}

    @staticmethod
    def _normalize(item: dict) -> dict:
        start = item.get("start", {}).get("dateTime") or item.get("start", {}).get("date")
        end = item.get("end", {}).get("dateTime") or item.get("end", {}).get("date")
        return {
            "id": item["id"],
            "calendar_id": "primary",
            "title": item.get("summary", ""),
            "description": item.get("description", ""),
            "location": item.get("location", ""),
            "organizer": item.get("organizer", {}).get("email", ""),
            "attendees": [a.get("email") for a in item.get("attendees", []) if a.get("email")],
            "status": item.get("status", "confirmed"),
            "start_time": datetime.fromisoformat(start) if start else None,
            "end_time": datetime.fromisoformat(end) if end else None,
        }


class RealDriveClient:
    def __init__(self, credentials):
        from googleapiclient.discovery import build

        self._svc = build("drive", "v3", credentials=credentials, cache_discovery=False)

    _FIELDS = "files(id,name,mimeType,owners,webViewLink,parents,modifiedTime,description)"

    async def list_files(self, q: str | None = None, max_results: int = 50) -> list[dict]:
        @_retry()
        def _call():
            resp = self._svc.files().list(q=q, pageSize=max_results, fields=self._FIELDS).execute()
            return resp.get("files", [])

        items = await asyncio.to_thread(_call)
        return [self._normalize(i) for i in items]

    async def get_file(self, file_id: str) -> dict:
        @_retry()
        def _call():
            return self._svc.files().get(
                fileId=file_id, fields="id,name,mimeType,owners,webViewLink,parents,modifiedTime,description"
            ).execute()

        return self._normalize(await asyncio.to_thread(_call))

    async def share_file(self, file_id: str, email: str, role: str = "reader") -> dict:
        @_retry()
        def _call():
            self._svc.permissions().create(
                fileId=file_id, body={"type": "user", "role": role, "emailAddress": email}, sendNotificationEmail=False
            ).execute()

        await asyncio.to_thread(_call)
        return await self.get_file(file_id)

    async def create_folder(self, name: str, parent_id: str | None = None) -> dict:
        body = {"name": name, "mimeType": "application/vnd.google-apps.folder"}
        if parent_id:
            body["parents"] = [parent_id]

        @_retry()
        def _call():
            return self._svc.files().create(body=body, fields="id,name,mimeType,parents").execute()

        return self._normalize(await asyncio.to_thread(_call))

    async def move_file(self, file_id: str, new_parent_id: str) -> dict:
        current = await self.get_file(file_id)
        old_parents = ",".join(current.get("parent_folder_id") and [current["parent_folder_id"]] or [])

        @_retry()
        def _call():
            return self._svc.files().update(
                fileId=file_id, addParents=new_parent_id, removeParents=old_parents,
                fields="id,name,mimeType,parents,modifiedTime",
            ).execute()

        return self._normalize(await asyncio.to_thread(_call))

    @staticmethod
    def _normalize(item: dict) -> dict:
        modified = item.get("modifiedTime")
        return {
            "id": item["id"],
            "name": item.get("name", ""),
            "mime_type": item.get("mimeType", ""),
            "content_preview": item.get("description", "") or "",
            "owners": [o.get("emailAddress") for o in item.get("owners", []) if o.get("emailAddress")],
            "web_view_link": item.get("webViewLink", ""),
            "parent_folder_id": (item.get("parents") or [None])[0],
            "modified_at": datetime.fromisoformat(modified.replace("Z", "+00:00")) if modified else None,
        }
