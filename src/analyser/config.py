from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="JAA_", env_file=".env", extra="ignore")

    # LLM
    llm_model: str = "claude-opus-5-5"
    llm_effort: str = "high"

    # Jenkins
    jenkins_url: str = ""
    jenkins_user: str = ""
    jenkins_api_token: str = ""
    jenkins_jobs: str = ""
    jenkins_verify_ssl: bool = True

    # Gerrit
    gerrit_enabled: bool = False
    gerrit_url: str = ""
    gerrit_user: str = ""
    gerrit_http_password: str = ""

    # Jira
    jira_enabled: bool = False
    jira_url: str = ""
    jira_email: str = ""
    jira_token: str = ""
    jira_project_key: str = "CI"
    jira_issue_type: str = "Bug"
    jira_min_confidence: float = Field(0.6, ge=0, le=1)

    # Service
    webhook_token: str = ""
    db_path: str = "analyser.db"
    max_log_chars: int = 60_000

    @property
    def job_list(self) -> list[str]:
        return [j.strip() for j in self.jenkins_jobs.split(",") if j.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
