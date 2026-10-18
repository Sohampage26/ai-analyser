from enum import Enum

from pydantic import BaseModel, Field


class FailureCategory(str, Enum):
    COMPILATION = "compilation"
    TEST_FAILURE = "test_failure"
    FLAKY_TEST = "flaky_test"
    DEPENDENCY = "dependency"
    INFRASTRUCTURE = "infrastructure"  # agent offline, disk full, OOM, network
    CONFIGURATION = "configuration"  # pipeline/Jenkinsfile/env misconfig
    TIMEOUT = "timeout"
    STATIC_ANALYSIS = "static_analysis"
    UNKNOWN = "unknown"


class Severity(str, Enum):
    BLOCKER = "blocker"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class BuildAnalysis(BaseModel):
    """Structured output the LLM must return for every failed build."""

    summary: str = Field(description="One-sentence description of why the build failed")
    category: FailureCategory
    severity: Severity
    root_cause: str = Field(description="Detailed explanation of the root cause, citing log lines")
    evidence: list[str] = Field(description="Verbatim log lines that prove the root cause (max 10)")
    suspected_files: list[str] = Field(description="Source files / modules likely responsible")
    caused_by_change: bool = Field(
        description="True if the failure is most likely introduced by the code change under test, "
        "False if it is environmental, flaky, or pre-existing"
    )
    suggested_fix: str = Field(description="Concrete steps a developer should take")
    confidence: float = Field(ge=0, le=1, description="0-1 confidence in this diagnosis")


class BuildInfo(BaseModel):
    job: str
    number: int
    url: str
    result: str | None
    timestamp_ms: int
    duration_ms: int
    parameters: dict[str, str] = {}
    changes: list[str] = []  # commit messages / authors from changeSets
    stage_failures: list[str] = []

    @property
    def gerrit_change(self) -> tuple[str, str] | None:
        """(change number, patchset) if the build was triggered by Gerrit Trigger."""
        c = self.parameters.get("GERRIT_CHANGE_NUMBER")
        p = self.parameters.get("GERRIT_PATCHSET_NUMBER")
        return (c, p) if c and p else None


class AnalysisRecord(BaseModel):
    id: int | None = None
    job: str
    build_number: int
    build_url: str
    fingerprint: str
    analysis: BuildAnalysis
    jira_key: str | None = None
    gerrit_commented: bool = False
    created_at: str | None = None
    llm_input_tokens: int = 0
    llm_output_tokens: int = 0
