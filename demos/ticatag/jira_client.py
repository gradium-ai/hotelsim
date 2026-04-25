"""Jira REST client for creating tickets in the gradium.atlassian.net KAN project."""

from __future__ import annotations

import logging
import os

import httpx

logger = logging.getLogger(__name__)

JIRA_BASE_URL = "https://gradium.atlassian.net"
JIRA_PROJECT_KEY = "KAN"


class JiraClient:
    def __init__(self) -> None:
        self.email = os.environ["JIRA_EMAIL"]
        self.token = os.environ["JIRA_API_TOKEN"]
        self._client = httpx.AsyncClient(
            base_url=JIRA_BASE_URL,
            auth=(self.email, self.token),
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            timeout=15.0,
        )

    async def create_ticket(
        self,
        *,
        caller_name: str,
        caller_email: str,
        caller_phone: str,
        issue_summary: str,
        issue_description: str,
    ) -> dict:
        summary = f"[Voice IT Support] {issue_summary[:120]}" if issue_summary else "[Voice IT Support] Demande sans titre"
        description_text = (
            f"Caller: {caller_name or 'N/A'}\n"
            f"Email: {caller_email or 'N/A'}\n"
            f"Phone: {caller_phone or 'N/A'}\n\n"
            f"Issue:\n{issue_description or issue_summary or 'No description provided.'}\n\n"
            f"_Created automatically by the Ticatag voice agent._"
        )

        payload = {
            "fields": {
                "project": {"key": JIRA_PROJECT_KEY},
                "summary": summary,
                "issuetype": {"name": "Task"},
                "description": {
                    "type": "doc",
                    "version": 1,
                    "content": [
                        {
                            "type": "paragraph",
                            "content": [{"type": "text", "text": description_text}],
                        }
                    ],
                },
            }
        }

        resp = await self._client.post("/rest/api/3/issue", json=payload)
        if resp.status_code >= 400:
            logger.error("Jira create failed %s: %s", resp.status_code, resp.text)
            resp.raise_for_status()

        data = resp.json()
        key = data["key"]
        return {
            "key": key,
            "url": f"{JIRA_BASE_URL}/browse/{key}",
            "summary": summary,
            "caller_name": caller_name,
            "caller_email": caller_email,
            "caller_phone": caller_phone,
            "issue": issue_description or issue_summary,
        }

    async def list_recent_tickets(self, limit: int = 20) -> list[dict]:
        jql = f'project = {JIRA_PROJECT_KEY} AND summary ~ "Voice IT Support" ORDER BY created DESC'
        resp = await self._client.post(
            "/rest/api/3/search/jql",
            json={"jql": jql, "maxResults": limit, "fields": ["summary", "status", "created"]},
        )
        if resp.status_code >= 400:
            logger.warning("Jira list failed %s: %s", resp.status_code, resp.text)
            return []

        issues = resp.json().get("issues", [])
        return [
            {
                "key": i["key"],
                "url": f"{JIRA_BASE_URL}/browse/{i['key']}",
                "summary": i["fields"]["summary"],
                "status": i["fields"]["status"]["name"],
                "created": i["fields"]["created"],
            }
            for i in issues
        ]

    async def aclose(self) -> None:
        await self._client.aclose()
