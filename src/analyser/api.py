"""HTTP service: Jenkins calls /webhook/jenkins on failure; humans/dashboards read /reports."""
import hmac
import logging

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel

from .config import get_settings
from .pipeline import TriagePipeline

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("analyser.api")

app = FastAPI(title="Jenkins AI Analyser")
_pipeline: TriagePipeline | None = None


def pipeline() -> TriagePipeline:
    global _pipeline
    if _pipeline is None:
        _pipeline = TriagePipeline.from_settings(get_settings())
    return _pipeline


def check_token(x_jaa_token: str = Header(default="")) -> None:
    expected = get_settings().webhook_token
    if not expected or not hmac.compare_digest(x_jaa_token, expected):
        raise HTTPException(status_code=401, detail="invalid token")


class JenkinsEvent(BaseModel):
    job: str  # full job path, e.g. "platform/main-build" (env.JOB_NAME in a pipeline)
    build: int  # env.BUILD_NUMBER
    result: str | None = None


def _run(job: str, number: int) -> None:
    try:
        pipeline().analyze_build(job, number)
    except Exception:
        log.exception("Analysis failed for %s #%s", job, number)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/webhook/jenkins", status_code=202, dependencies=[Depends(check_token)])
def jenkins_webhook(ev: JenkinsEvent, bg: BackgroundTasks):
    if ev.result and ev.result.upper() not in ("FAILURE", "UNSTABLE"):
        return {"queued": False, "reason": f"result {ev.result} not analysed"}
    bg.add_task(_run, ev.job, ev.build)
    return {"queued": True, "job": ev.job, "build": ev.build}


@app.post("/analyze/{job:path}/{number}", dependencies=[Depends(check_token)])
def analyze_now(job: str, number: int, force: bool = False, notify: bool = True):
    return pipeline().analyze_build(job, number, notify=notify, force=force)


@app.get("/reports")
def reports(limit: int = 50, job: str | None = None, category: str | None = None):
    return pipeline().store.recent(limit, job, category)


@app.get("/reports/{job:path}/{number}")
def report(job: str, number: int):
    rec = pipeline().store.get(job, number)
    if not rec:
        raise HTTPException(404, "not analysed")
    return rec


@app.get("/stats")
def stats():
    return pipeline().store.stats()
