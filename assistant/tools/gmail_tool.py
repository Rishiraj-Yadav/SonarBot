"""Gmail tool definitions backed by Google OAuth."""

from __future__ import annotations

import base64
from email.message import EmailMessage
from typing import Any

import httpx

from assistant.tools.registry import ToolDefinition

GMAIL_API_ROOT = "https://gmail.googleapis.com/gmail/v1/users/me"


def build_gmail_tools(oauth_token_manager) -> list[ToolDefinition]:
    async def gmail_search(payload: dict[str, Any]) -> dict[str, Any]:
        query = str(payload.get("query", "")).strip()
        limit = max(1, min(int(payload.get("limit", 10)), 25))
        token = await _require_google_token(oauth_token_manager)

        threads_response = await _gmail_request(
            token,
            "GET",
            f"{GMAIL_API_ROOT}/threads",
            params={"q": query, "maxResults": limit},
        )
        thread_refs = threads_response.get("threads", []) or []

        results: list[dict[str, Any]] = []
        for ref in thread_refs[:limit]:
            thread_id = str(ref.get("id", ""))
            if not thread_id:
                continue
            thread = await _gmail_request(
                token,
                "GET",
                f"{GMAIL_API_ROOT}/threads/{thread_id}",
                params={
                    "format": "metadata",
                    "metadataHeaders": ["Subject", "From", "Date"],
                },
            )
            latest_message = (thread.get("messages", []) or [{}])[-1]
            headers = _headers_to_dict(latest_message.get("payload", {}).get("headers", []))
            results.append(
                {
                    "thread_id": thread_id,
                    "history_id": thread.get("historyId"),
                    "snippet": thread.get("snippet", ""),
                    "subject": headers.get("Subject", ""),
                    "from": headers.get("From", ""),
                    "date": headers.get("Date", ""),
                }
            )

        return {"query": query, "count": len(results), "threads": results}

    async def gmail_read_thread(payload: dict[str, Any]) -> dict[str, Any]:
        thread_id = str(payload["thread_id"]).strip()
        token = await _require_google_token(oauth_token_manager)
        thread = await _gmail_request(token, "GET", f"{GMAIL_API_ROOT}/threads/{thread_id}", params={"format": "full"})

        messages = []
        for message in thread.get("messages", []) or []:
            payload_block = message.get("payload", {}) or {}
            headers = _headers_to_dict(payload_block.get("headers", []))
            messages.append(
                {
                    "id": message.get("id"),
                    "thread_id": message.get("threadId"),
                    "subject": headers.get("Subject", ""),
                    "from": headers.get("From", ""),
                    "to": headers.get("To", ""),
                    "date": headers.get("Date", ""),
                    "body": _extract_gmail_body(payload_block),
                    "snippet": message.get("snippet", ""),
                }
            )

        return {"thread_id": thread_id, "messages": messages}

    async def gmail_send(payload: dict[str, Any]) -> dict[str, Any]:
        to = str(payload["to"]).strip()
        subject = str(payload["subject"]).strip()
        body = str(payload["body"])
        cc = str(payload.get("cc", "")).strip()
        bcc = str(payload.get("bcc", "")).strip()
        token = await _require_google_token(oauth_token_manager)
        raw_message = _build_email_message(to=to, subject=subject, body=body, cc=cc, bcc=bcc)
        response = await _gmail_request(
            token,
            "POST",
            f"{GMAIL_API_ROOT}/messages/send",
            json={"raw": raw_message},
        )
        return {"id": response.get("id"), "thread_id": response.get("threadId"), "label_ids": response.get("labelIds", [])}

    async def gmail_create_draft(payload: dict[str, Any]) -> dict[str, Any]:
        to = str(payload["to"]).strip()
        subject = str(payload["subject"]).strip()
        body = str(payload["body"])
        cc = str(payload.get("cc", "")).strip()
        bcc = str(payload.get("bcc", "")).strip()
        token = await _require_google_token(oauth_token_manager)
        raw_message = _build_email_message(to=to, subject=subject, body=body, cc=cc, bcc=bcc)
        response = await _gmail_request(
            token,
            "POST",
            f"{GMAIL_API_ROOT}/drafts",
            json={"message": {"raw": raw_message}},
        )
        draft = response.get("message", {}) or {}
        return {"draft_id": response.get("id"), "message_id": draft.get("id"), "thread_id": draft.get("threadId")}

    return [
        ToolDefinition(
            name="gmail_search",
            description="Search Gmail threads with a Gmail query string and return matching thread summaries.",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "default": 10},
                },
                "required": ["query"],
            },
            handler=gmail_search,
        ),
        ToolDefinition(
            name="gmail_read_thread",
            description="Read a full Gmail thread including headers, snippets, and decoded message bodies.",
            parameters={
                "type": "object",
                "properties": {"thread_id": {"type": "string"}},
                "required": ["thread_id"],
            },
            handler=gmail_read_thread,
        ),
        ToolDefinition(
            name="gmail_send",
            description="Send an email through Gmail using the connected Google account.",
            parameters={
                "type": "object",
                "properties": {
                    "to": {"type": "string"},
                    "subject": {"type": "string"},
                    "body": {"type": "string"},
                    "cc": {"type": "string"},
                    "bcc": {"type": "string"},
                },
                "required": ["to", "subject", "body"],
            },
            handler=gmail_send,
        ),
        ToolDefinition(
            name="gmail_create_draft",
            description="Create a Gmail draft using the connected Google account.",
            parameters={
                "type": "object",
                "properties": {
                    "to": {"type": "string"},
                    "subject": {"type": "string"},
                    "body": {"type": "string"},
                    "cc": {"type": "string"},
                    "bcc": {"type": "string"},
                },
                "required": ["to", "subject", "body"],
            },
            handler=gmail_create_draft,
        ),
    ]


async def _require_google_token(oauth_token_manager) -> str:
    token_payload = await oauth_token_manager.get_token("google")
    if token_payload is None or not str(token_payload.get("access_token", "")).strip():
        raise RuntimeError("Google OAuth is not connected. Run oauth_connect for provider 'google' first.")
    return str(token_payload["access_token"])


async def _gmail_request(
    access_token: str,
    method: str,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    json: dict[str, Any] | None = None,
) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {access_token}"}
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.request(method, url, headers=headers, params=params, json=json)
        response.raise_for_status()
    return response.json()


def _headers_to_dict(headers: list[dict[str, Any]]) -> dict[str, str]:
    return {str(item.get("name", "")): str(item.get("value", "")) for item in headers}


def _extract_gmail_body(payload: dict[str, Any]) -> str:
    body = payload.get("body", {}) or {}
    data = body.get("data")
    if data:
        return _decode_gmail_data(str(data))

    for part in payload.get("parts", []) or []:
        mime_type = str(part.get("mimeType", ""))
        if mime_type == "text/plain":
            return _decode_gmail_data(str((part.get("body", {}) or {}).get("data", "")))

    for part in payload.get("parts", []) or []:
        nested = _extract_gmail_body(part)
        if nested:
            return nested
    return ""


def _decode_gmail_data(data: str) -> str:
    if not data:
        return ""
    padding = "=" * (-len(data) % 4)
    try:
        return base64.urlsafe_b64decode(data + padding).decode("utf-8", errors="replace")
    except Exception:
        return ""


def _build_email_message(*, to: str, subject: str, body: str, cc: str = "", bcc: str = "") -> str:
    message = EmailMessage()
    message["To"] = to
    message["Subject"] = subject
    if cc:
        message["Cc"] = cc
    if bcc:
        message["Bcc"] = bcc
    message.set_content(body)
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")
    return raw.rstrip("=")
