"""MCP server exposing the analyser as tools, so engineers can ask an MCP client
(Claude Code, Claude Desktop, IDE assistants) things like
"why did platform/main-build #812 fail?" or "what are the top recurring CI failures?".

Run:  python -m analyser.mcp_server            (stdio, for local clients)
      python -m analyser.mcp_server --http     (streamable HTTP on :8001, for shared use)
"""
import sys

from mcp.server.fastmcp import FastMCP

from .config import get_settings
from .log_extractor import error_lines
from .pipeline import TriagePipeline, format_report

mcp = FastMCP("jenkins-ai-analyser", host="0.0.0.0", port=8001)
_p: TriagePipeline | None = None


def p() -> TriagePipeline:
    global _p
    if _p is None:
        _p = TriagePipeline.from_settings(get_settings())
    return _p


@mcp.tool()
def list_failed_builds(job: str, limit: int = 20) -> list[dict]:
    """List recent FAILURE/UNSTABLE builds for a Jenkins job (folder paths use '/')."""
    return p().jenkins.failed_builds(job, limit)


@mcp.tool()
def analyze_build(job: str, build_number: int, notify: bool = False, force: bool = False) -> str:
    """Run AI root-cause analysis on a Jenkins build. notify=True also files Jira / comments on Gerrit."""
    rec = p().analyze_build(job, build_number, notify=notify, force=force)
    build = p().jenkins.get_build(job, build_number)
    return format_report(build, rec.analysis, rec.jira_key, jira_url=get_settings().jira_url)


@mcp.tool()
def get_build_errors(job: str, build_number: int, max_lines: int = 100) -> list[str]:
    """Return the error-looking lines from a build's console log (no LLM call)."""
    return error_lines(p().jenkins.console_text(job, build_number))[:max_lines]


@mcp.tool()
def get_analysis(job: str, build_number: int) -> dict | None:
    """Fetch a previously stored analysis."""
    rec = p().store.get(job, build_number)
    return rec.model_dump() if rec else None


@mcp.tool()
def search_analyses(job: str | None = None, category: str | None = None, limit: int = 20) -> list[dict]:
    """Search stored analyses. category: compilation, test_failure, flaky_test, dependency,
    infrastructure, configuration, timeout, static_analysis, unknown."""
    return [
        {"job": r.job, "build": r.build_number, "summary": r.analysis.summary,
         "category": r.analysis.category.value, "jira": r.jira_key, "url": r.build_url}
        for r in p().store.recent(limit, job, category)
    ]


@mcp.tool()
def failure_stats() -> dict:
    """Aggregate stats: failures by category, top recurring failure signatures, LLM token usage."""
    return p().store.stats()


if __name__ == "__main__":
    mcp.run(transport="streamable-http" if "--http" in sys.argv else "stdio")
