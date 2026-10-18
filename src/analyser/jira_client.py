import logging

import requests

log = logging.getLogger(__name__)


class JiraClient:
    """Jira REST v2. Cloud: email + API token (basic). Server/DC: Personal Access Token (bearer)."""

    def __init__(self, url: str, token: str, email: str = "", timeout: int = 30):
        self.base = url.rstrip("/")
        self.s = requests.Session()
        if email:
            self.s.auth = (email, token)
        else:
            self.s.headers["Authorization"] = f"Bearer {token}"
        self.s.headers["Accept"] = "application/json"
        self.timeout = timeout

    def find_open_by_label(self, project: str, label: str) -> str | None:
        jql = f'project = "{project}" AND labels = "{label}" AND statusCategory != Done ORDER BY created DESC'
        r = self.s.get(
            f"{self.base}/rest/api/2/search",
            params={"jql": jql, "maxResults": 1, "fields": "key"},
            timeout=self.timeout,
        )
        r.raise_for_status()
        issues = r.json().get("issues", [])
        return issues[0]["key"] if issues else None

    def create_issue(self, project: str, issue_type: str, summary: str, description: str,
                     labels: list[str], priority: str | None = None) -> str:
        fields = {
            "project": {"key": project},
            "issuetype": {"name": issue_type},
            "summary": summary[:250],
            "description": description,
            "labels": labels,
        }
        if priority:
            fields["priority"] = {"name": priority}
        r = self.s.post(f"{self.base}/rest/api/2/issue", json={"fields": fields}, timeout=self.timeout)
        if r.status_code == 400 and priority:
            # Priority scheme differs per instance; retry without it rather than fail the triage
            log.warning("Jira rejected priority %s, retrying without: %s", priority, r.text)
            fields.pop("priority")
            r = self.s.post(f"{self.base}/rest/api/2/issue", json={"fields": fields}, timeout=self.timeout)
        r.raise_for_status()
        return r.json()["key"]

    def add_comment(self, key: str, body: str) -> None:
        r = self.s.post(f"{self.base}/rest/api/2/issue/{key}/comment", json={"body": body}, timeout=self.timeout)
        r.raise_for_status()
