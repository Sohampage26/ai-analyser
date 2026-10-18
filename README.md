# Jenkins AI Analyser

AI agent that triages failed Jenkins builds: it pulls the console log, test report and (for Gerrit-triggered
builds) the patch under review, asks Claude for a structured root-cause analysis, then:

- **Gerrit-triggered build broken by the patch** → posts the diagnosis as a review comment on the change.
- **Infra / flaky / pre-existing failure** → files a Jira ticket, or comments on the existing open ticket
  with the same failure fingerprint (no duplicate tickets).
- Stores every analysis in SQLite, exposed via a REST API and an **MCP server** so engineers can ask
  "why did build #812 fail?" from Claude Code / Claude Desktop / their IDE.

```
Jenkins post{failure} ──webhook──▶ FastAPI (:8000) ──▶ TriagePipeline
                                                         ├─ JenkinsClient  (build, consoleText, testReport, wfapi)
                                                         ├─ GerritClient   (patch, review comment)
                                                         ├─ log_extractor  (shrink log, fingerprint)
                                                         ├─ Analyzer       (Claude structured output)
                                                         ├─ Store          (SQLite)
                                                         └─ JiraClient     (create / dedup / comment)
MCP clients ───────────────────────▶ MCP server (:8001) ──▶ same pipeline + store
```

## Layout

| Path | Purpose |
|---|---|
| `src/analyser/config.py` | All settings from env vars (`JAA_*`) |
| `src/analyser/jenkins_client.py` | Jenkins REST: builds, console, JUnit report, failed stages |
| `src/analyser/gerrit_client.py` | Gerrit REST: patch fetch, review comment |
| `src/analyser/jira_client.py` | Jira REST v2: search by label, create, comment (Cloud or Server/DC) |
| `src/analyser/log_extractor.py` | Error-window extraction + failure fingerprinting |
| `src/analyser/analyzer.py` | Prompt + Claude call returning a validated `BuildAnalysis` |
| `src/analyser/pipeline.py` | Orchestration and routing rules |
| `src/analyser/api.py` | Webhook + reports REST API |
| `src/analyser/mcp_server.py` | MCP tools |
| `src/analyser/cli.py` | `jaa analyze / backfill / stats / serve` |
| `jenkins/Jenkinsfile.snippet.groovy` | `post` hook to add to pipelines |
| `deploy/` | systemd unit (non-Docker option) |
| `tests/` | Offline tests with fake Jenkins/Jira/Gerrit/LLM |
| `.github/` | CI (pytest on 3.11/3.12 + Docker build), Dependabot, PR template |

## Run locally

```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env    # fill in values
pytest                  # offline tests, no creds needed
jaa analyze platform/main-build 812          # one build, prints report, no Jira/Gerrit
jaa backfill --limit 200                     # seed history for all JAA_JENKINS_JOBS
jaa serve                                    # webhook API on :8000
```

## Deploy

**Where:** a small internal Linux VM (2 vCPU / 4 GB RAM / 20 GB disk is plenty) — or a pod on your
internal Kubernetes — that can reach:

- Jenkins, Gerrit, Jira over HTTPS (inbound to them),
- `api.anthropic.com:443` outbound (via your proxy if needed: set `HTTPS_PROXY`),
- and is reachable **from the Jenkins controller** on port 8000.

Keep it inside the corporate network; don't expose 8000/8001 to the internet.

**Docker (recommended):**
```bash
git clone <this repo> /opt/jaa && cd /opt/jaa
cp .env.example .env && vi .env
docker compose up -d --build
curl localhost:8000/health
docker compose exec api jaa backfill --limit 200   # one-time history seed
```

**Without Docker:** see `deploy/jenkins-ai-analyser.service`.

**Hook Jenkins up:**
1. Create a Jenkins *Secret text* credential `jaa-webhook-token` = `JAA_WEBHOOK_TOKEN`.
2. Add the `post { failure / unstable }` block from `jenkins/Jenkinsfile.snippet.groovy` to pipelines
   (best: put `notifyAnalyser()` in your shared library so every job gets it).
3. Replace `jaa.internal.example.com` with the VM's hostname.

**Hook up MCP clients** (e.g. Claude Code): `claude mcp add --transport http jenkins-analyser http://jaa.internal.example.com:8001/mcp`

## API

| Method | Path | |
|---|---|---|
| POST | `/webhook/jenkins` | `{"job","build","result"}` + `X-JAA-Token` header → 202, analysed in background |
| POST | `/analyze/{job}/{n}?force=&notify=` | synchronous analysis (token required) |
| GET | `/reports?job=&category=&limit=` | recent analyses |
| GET | `/reports/{job}/{n}` | one analysis |
| GET | `/stats` | counts by category, recurring failures, token usage |

## Bot accounts & permissions

| System | Account | Minimum permission |
|---|---|---|
| Jenkins | `ai-analyser-bot` + API token | Overall/Read, Job/Read on watched jobs |
| Gerrit | `ai-analyser-bot` + HTTP password | Read on projects; permission to post review comments (no Code-Review/Verified label needed) |
| Jira | bot user + API token / PAT | Browse + Create Issues + Add Comments in the CI project |
| Anthropic | API key | from console.anthropic.com, ideally in a dedicated workspace with a spend limit |
