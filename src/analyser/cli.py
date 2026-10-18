import json
import logging

import typer

from .config import get_settings
from .pipeline import TriagePipeline, format_report

app = typer.Typer(help="Jenkins AI Analyser CLI")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@app.command()
def analyze(job: str, build: int, notify: bool = False, force: bool = False):
    """Analyse one build and print the report."""
    p = TriagePipeline.from_settings(get_settings())
    rec = p.analyze_build(job, build, notify=notify, force=force)
    typer.echo(format_report(p.jenkins.get_build(job, build), rec.analysis, rec.jira_key,
                             jira_url=get_settings().jira_url))


@app.command()
def backfill(job: str = typer.Option(None, help="Job path; defaults to all JAA_JENKINS_JOBS"),
             limit: int = 100, notify: bool = False):
    """Analyse historical failed builds to seed the knowledge base."""
    s = get_settings()
    p = TriagePipeline.from_settings(s)
    jobs = [job] if job else s.job_list
    total = 0
    for j in jobs:
        recs = p.backfill(j, limit, notify)
        total += len(recs)
        typer.echo(f"{j}: analysed {len(recs)} new failed builds")
    typer.echo(f"Done. {total} builds analysed.")


@app.command()
def stats():
    """Print aggregate failure statistics."""
    typer.echo(json.dumps(TriagePipeline.from_settings(get_settings()).store.stats(), indent=2))


@app.command()
def serve(host: str = "0.0.0.0", port: int = 8000):
    """Run the webhook/report HTTP service."""
    import uvicorn

    uvicorn.run("analyser.api:app", host=host, port=port)


if __name__ == "__main__":
    app()
