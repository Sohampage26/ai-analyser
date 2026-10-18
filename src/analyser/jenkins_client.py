import logging
from urllib.parse import quote

import requests

from .models import BuildInfo

log = logging.getLogger(__name__)


def job_path(job: str) -> str:
    """'folder/sub/job' -> '/job/folder/job/sub/job/job'"""
    return "".join(f"/job/{quote(part, safe='')}" for part in job.strip("/").split("/"))


class JenkinsClient:
    def __init__(self, url: str, user: str, token: str, verify_ssl: bool = True, timeout: int = 60):
        self.base = url.rstrip("/")
        self.s = requests.Session()
        self.s.auth = (user, token)
        self.s.verify = verify_ssl
        self.timeout = timeout

    def _get(self, path: str, **params) -> requests.Response:
        r = self.s.get(f"{self.base}{path}", params=params or None, timeout=self.timeout)
        r.raise_for_status()
        return r

    def list_builds(self, job: str, limit: int = 100) -> list[dict]:
        tree = f"builds[number,result,timestamp,duration,url]{{0,{limit}}}"
        return self._get(f"{job_path(job)}/api/json", tree=tree).json().get("builds", [])

    def failed_builds(self, job: str, limit: int = 100) -> list[dict]:
        return [b for b in self.list_builds(job, limit) if b.get("result") in ("FAILURE", "UNSTABLE")]

    def get_build(self, job: str, number: int) -> BuildInfo:
        data = self._get(f"{job_path(job)}/{number}/api/json").json()
        params: dict[str, str] = {}
        for action in data.get("actions", []) or []:
            for p in (action or {}).get("parameters", []) or []:
                if "name" in p and p.get("value") is not None:
                    params[p["name"]] = str(p["value"])
        changes = []
        for cs in data.get("changeSets", []) or []:
            for item in cs.get("items", []):
                author = (item.get("author") or {}).get("fullName", "?")
                changes.append(f"{item.get('commitId', '')[:10]} {author}: {item.get('msg', '')}")
        return BuildInfo(
            job=job,
            number=number,
            url=data.get("url", ""),
            result=data.get("result"),
            timestamp_ms=data.get("timestamp", 0),
            duration_ms=data.get("duration", 0),
            parameters=params,
            changes=changes,
            stage_failures=self._failed_stages(job, number),
        )

    def _failed_stages(self, job: str, number: int) -> list[str]:
        """Uses the Pipeline Stage View plugin API (wfapi). Silently skipped if not installed."""
        try:
            stages = self._get(f"{job_path(job)}/{number}/wfapi/describe").json().get("stages", [])
        except requests.RequestException:
            return []
        return [s["name"] for s in stages if s.get("status") in ("FAILED", "UNSTABLE")]

    def console_text(self, job: str, number: int) -> str:
        return self._get(f"{job_path(job)}/{number}/consoleText").text

    def test_report(self, job: str, number: int) -> list[str]:
        """Failed JUnit test cases, if the job publishes test results."""
        try:
            data = self._get(
                f"{job_path(job)}/{number}/testReport/api/json",
                tree="suites[cases[className,name,status,errorDetails]]",
            ).json()
        except requests.RequestException:
            return []
        failed = []
        for suite in data.get("suites", []):
            for c in suite.get("cases", []):
                if c.get("status") in ("FAILED", "REGRESSION"):
                    detail = (c.get("errorDetails") or "").strip().splitlines()[:3]
                    failed.append(f"{c['className']}.{c['name']}: {' | '.join(detail)}")
        return failed
