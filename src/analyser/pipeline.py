"""Orchestration: fetch build -> extract log -> LLM analysis -> store -> Jira + Gerrit."""
import logging

from .analyzer import Analyzer, build_prompt
from .config import Settings
from .gerrit_client import GerritClient
from .jenkins_client import JenkinsClient
from .jira_client import JiraClient
from .log_extractor import extract_relevant, fingerprint
from .models import AnalysisRecord, BuildAnalysis, BuildInfo, FailureCategory, Severity
from .store import Store

log = logging.getLogger(__name__)

JIRA_PRIORITY = {Severity.BLOCKER: "Highest", Severity.HIGH: "High",
                 Severity.MEDIUM: "Medium", Severity.LOW: "Low"}


def format_report(build: BuildInfo, a: BuildAnalysis, jira_key: str | None = None, *, jira_url: str = "") -> str:
    lines = [
        f"🤖 Jenkins AI Analyser — {build.job} #{build.number}",
        f"Summary: {a.summary}",
        f"Category: {a.category.value} | Severity: {a.severity.value} | Confidence: {a.confidence:.0%}",
        f"Caused by this change: {'yes' if a.caused_by_change else 'no / unclear'}",
        "",
        "Root cause:",
        a.root_cause,
        "",
        "Evidence:",
        *[f"  > {e}" for e in a.evidence[:10]],
    ]
    if a.suspected_files:
        lines += ["", "Suspected files: " + ", ".join(a.suspected_files)]
    lines += ["", "Suggested fix:", a.suggested_fix, "", f"Build: {build.url}"]
    if jira_key:
        lines.append(f"Jira: {jira_url.rstrip('/')}/browse/{jira_key}")
    return "\n".join(lines)


class TriagePipeline:
    def __init__(self, settings: Settings, store: Store, jenkins: JenkinsClient, analyzer: Analyzer,
                 gerrit: GerritClient | None = None, jira: JiraClient | None = None):
        self.cfg = settings
        self.store = store
        self.jenkins = jenkins
        self.analyzer = analyzer
        self.gerrit = gerrit
        self.jira = jira

    @classmethod
    def from_settings(cls, s: Settings) -> "TriagePipeline":
        return cls(
            settings=s,
            store=Store(s.db_path),
            jenkins=JenkinsClient(s.jenkins_url, s.jenkins_user, s.jenkins_api_token, s.jenkins_verify_ssl),
            analyzer=Analyzer(s.llm_model, s.llm_effort),
            gerrit=GerritClient(s.gerrit_url, s.gerrit_user, s.gerrit_http_password) if s.gerrit_enabled else None,
            jira=JiraClient(s.jira_url, s.jira_token, s.jira_email) if s.jira_enabled else None,
        )

    def analyze_build(self, job: str, number: int, *, notify: bool = True, force: bool = False) -> AnalysisRecord:
        if not force and (existing := self.store.get(job, number)):
            log.info("Already analysed %s #%s", job, number)
            return existing

        build = self.jenkins.get_build(job, number)
        console = self.jenkins.console_text(job, number)
        failed_tests = self.jenkins.test_report(job, number)
        fp = fingerprint(console)

        diff = None
        if self.gerrit and build.gerrit_change:
            change, ps = build.gerrit_change
            try:
                diff = self.gerrit.get_diff_summary(change, ps)
            except Exception as e:  # diff is context only; never block triage on it
                log.warning("Could not fetch Gerrit diff for %s/%s: %s", change, ps, e)

        similar = [f"- {r.job} #{r.build_number}: {r.analysis.summary} (jira: {r.jira_key or 'none'})"
                   for r in self.store.by_fingerprint(fp)]

        prompt = build_prompt(build, extract_relevant(console, self.cfg.max_log_chars), failed_tests, diff, similar)
        result = self.analyzer.analyze(prompt)
        rec = AnalysisRecord(
            job=job, build_number=number, build_url=build.url, fingerprint=fp, analysis=result.analysis,
            llm_input_tokens=result.input_tokens, llm_output_tokens=result.output_tokens,
        )

        if notify:
            rec.jira_key = self._file_jira(build, rec)
            rec.gerrit_commented = self._comment_gerrit(build, rec)

        rec.id = self.store.save(rec)
        log.info("Analysed %s #%s -> %s (%s, %.0f%%)", job, number, result.analysis.category.value,
                 rec.jira_key or "no ticket", result.analysis.confidence * 100)
        return rec

    def _file_jira(self, build: BuildInfo, rec: AnalysisRecord) -> str | None:
        a = rec.analysis
        if not self.jira or a.confidence < self.cfg.jira_min_confidence:
            return None
        # Failures caused by a patch under review belong on the Gerrit change, not in Jira
        if a.caused_by_change and build.gerrit_change:
            return None
        label = f"jaa-fp-{rec.fingerprint}"
        body = format_report(build, a).replace("🤖 ", "")
        try:
            if key := self.jira.find_open_by_label(self.cfg.jira_project_key, label):
                self.jira.add_comment(key, f"Recurred in {build.job} #{build.number}\n\n{body}")
                return key
            return self.jira.create_issue(
                project=self.cfg.jira_project_key,
                issue_type=self.cfg.jira_issue_type,
                summary=f"[CI][{a.category.value}] {build.job}: {a.summary}",
                description=body,
                labels=["jenkins-ai-analyser", label, f"jaa-{a.category.value}"],
                priority=JIRA_PRIORITY.get(a.severity),
            )
        except Exception as e:
            log.error("Jira update failed for %s #%s: %s", build.job, build.number, e)
            return None

    def _comment_gerrit(self, build: BuildInfo, rec: AnalysisRecord) -> bool:
        if not self.gerrit or not build.gerrit_change:
            return False
        if rec.analysis.category == FailureCategory.UNKNOWN and rec.analysis.confidence < 0.3:
            return False
        change, ps = build.gerrit_change
        try:
            self.gerrit.post_review(change, ps, format_report(build, rec.analysis, rec.jira_key,
                                                               jira_url=self.cfg.jira_url))
            return True
        except Exception as e:
            log.error("Gerrit comment failed for %s/%s: %s", change, ps, e)
            return False

    def backfill(self, job: str, limit: int = 100, notify: bool = False) -> list[AnalysisRecord]:
        """Analyse historical failed builds (no Jira/Gerrit noise by default)."""
        out = []
        for b in self.jenkins.failed_builds(job, limit):
            if self.store.exists(job, b["number"]):
                continue
            try:
                out.append(self.analyze_build(job, b["number"], notify=notify))
            except Exception as e:
                log.error("Backfill failed for %s #%s: %s", job, b["number"], e)
        return out
