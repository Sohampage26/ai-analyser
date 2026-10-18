"""LLM root-cause analysis using Claude structured outputs."""
import logging
from dataclasses import dataclass

import anthropic

from .models import BuildAnalysis, BuildInfo

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a senior build/release engineer triaging failed Jenkins builds.
You receive build metadata, the failing pipeline stages, failed tests, the code change under test
(if any), similar past failures, and an extract of the console log.

Determine the real root cause, not the last line that printed "error". Distinguish between:
- failures introduced by the change under test (compile errors, broken tests in touched code),
- environmental/infrastructure problems (agent offline, disk full, OOM, network, registry down),
- flaky tests (timing-dependent, passed on retry, unrelated to the diff),
- pipeline/configuration mistakes.

Quote evidence verbatim from the log. If the log is insufficient, say so and lower confidence."""


@dataclass
class AnalysisResult:
    analysis: BuildAnalysis
    input_tokens: int
    output_tokens: int


def build_prompt(build: BuildInfo, log_extract: str, failed_tests: list[str],
                 diff: str | None, similar: list[str]) -> str:
    parts = [
        f"## Build\nJob: {build.job}\nNumber: {build.number}\nResult: {build.result}\nURL: {build.url}",
        f"Duration: {build.duration_ms / 1000:.0f}s",
    ]
    if build.stage_failures:
        parts.append("## Failed stages\n" + "\n".join(build.stage_failures))
    if build.parameters:
        safe = {k: v for k, v in build.parameters.items() if not any(
            s in k.upper() for s in ("TOKEN", "PASSWORD", "SECRET", "KEY"))}
        parts.append("## Parameters\n" + "\n".join(f"{k}={v}" for k, v in safe.items()))
    if build.changes:
        parts.append("## Commits in this build\n" + "\n".join(build.changes[:30]))
    if failed_tests:
        parts.append("## Failed tests\n" + "\n".join(failed_tests[:50]))
    if diff:
        parts.append("## Change under test (Gerrit patch, truncated)\n```diff\n" + diff + "\n```")
    if similar:
        parts.append("## Previously diagnosed failures with the same signature\n" + "\n".join(similar))
    parts.append("## Console log extract\n```\n" + log_extract + "\n```")
    return "\n\n".join(parts)


class Analyzer:
    def __init__(self, model: str, effort: str = "high", client: anthropic.Anthropic | None = None):
        self.client = client or anthropic.Anthropic()
        self.model = model
        self.effort = effort

    def analyze(self, prompt: str) -> AnalysisResult:
        response = self.client.messages.parse(
            model=self.model,
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
            output_format=BuildAnalysis,
            output_config={"effort": self.effort},
            # Server-side refusal fallback: if a safety classifier declines (e.g. a log that
            # looks like exploit output), the API retries on a fallback model automatically.
            extra_headers={"anthropic-beta": "server-side-fallback-2026-07-01"},
            extra_body={"fallbacks": "default"},
        )
        if response.stop_reason == "refusal":
            raise RuntimeError(f"Model refused to analyse this build: {response.stop_details}")
        if response.stop_reason == "max_tokens" or response.parsed_output is None:
            raise RuntimeError(f"Incomplete analysis (stop_reason={response.stop_reason})")
        return AnalysisResult(
            analysis=response.parsed_output,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )
