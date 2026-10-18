"""Pipeline wiring tests with fake Jenkins/Jira/Gerrit/LLM — no network, no API key."""
from analyser.analyzer import AnalysisResult
from analyser.config import Settings
from analyser.models import BuildAnalysis, BuildInfo
from analyser.pipeline import TriagePipeline
from analyser.store import Store


class FakeJenkins:
    def __init__(self, params=None):
        self.params = params or {}

    def get_build(self, job, n):
        return BuildInfo(job=job, number=n, url=f"http://j/{job}/{n}/", result="FAILURE",
                         timestamp_ms=0, duration_ms=1000, parameters=self.params)

    def console_text(self, job, n):
        return "compiling...\nERROR: No space left on device\nBuild step failed"

    def test_report(self, job, n):
        return []

    def failed_builds(self, job, limit):
        return [{"number": 1}, {"number": 2}]


class FakeAnalyzer:
    def __init__(self, caused_by_change=False):
        self.prompts = []
        self.caused = caused_by_change

    def analyze(self, prompt):
        self.prompts.append(prompt)
        return AnalysisResult(BuildAnalysis(
            summary="Agent disk full", category="infrastructure", severity="high",
            root_cause="disk", evidence=["ERROR: No space left on device"], suspected_files=[],
            caused_by_change=self.caused, suggested_fix="clean workspace", confidence=0.9), 100, 50)


class FakeJira:
    def __init__(self):
        self.created, self.comments, self.open = [], [], {}

    def find_open_by_label(self, project, label):
        return self.open.get(label)

    def create_issue(self, project, issue_type, summary, description, labels, priority=None):
        key = f"CI-{len(self.created) + 1}"
        self.created.append(summary)
        self.open[labels[1]] = key
        return key

    def add_comment(self, key, body):
        self.comments.append(key)


class FakeGerrit:
    def __init__(self):
        self.reviews = []

    def get_diff_summary(self, c, p):
        return "diff --git a/x b/x"

    def post_review(self, c, p, msg):
        self.reviews.append((c, p, msg))


def make(tmp_path, **kw):
    s = Settings(db_path=str(tmp_path / "t.db"), jira_url="http://jira")
    return TriagePipeline(s, Store(s.db_path), kw.get("jenkins", FakeJenkins()),
                          kw.get("analyzer", FakeAnalyzer()), kw.get("gerrit"), kw.get("jira"))


def test_creates_then_dedups_jira(tmp_path):
    jira = FakeJira()
    p = make(tmp_path, jira=jira)
    r1 = p.analyze_build("app", 1)
    r2 = p.analyze_build("app", 2)
    assert r1.jira_key == r2.jira_key == "CI-1"
    assert len(jira.created) == 1 and jira.comments == ["CI-1"]


def test_cached_result_skips_llm(tmp_path):
    an = FakeAnalyzer()
    p = make(tmp_path, analyzer=an)
    p.analyze_build("app", 1)
    p.analyze_build("app", 1)
    assert len(an.prompts) == 1


def test_gerrit_change_gets_comment_not_jira(tmp_path):
    jira, gerrit = FakeJira(), FakeGerrit()
    jenkins = FakeJenkins({"GERRIT_CHANGE_NUMBER": "123", "GERRIT_PATCHSET_NUMBER": "4"})
    an = FakeAnalyzer(caused_by_change=True)
    p = make(tmp_path, jira=jira, gerrit=gerrit, jenkins=jenkins, analyzer=an)
    rec = p.analyze_build("verify", 7)
    assert rec.gerrit_commented and rec.jira_key is None
    assert gerrit.reviews[0][:2] == ("123", "4")
    assert "diff --git" in an.prompts[0]


def test_backfill_and_stats(tmp_path):
    p = make(tmp_path)
    assert len(p.backfill("app")) == 2
    assert p.backfill("app") == []
    st = p.store.stats()
    assert st["total_analyses"] == 2 and st["by_category"] == {"infrastructure": 2}
