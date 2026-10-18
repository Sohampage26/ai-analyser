"""Shrink multi-MB Jenkins console logs to the parts that matter before they hit the LLM.

Strategy: keep the head (env/setup), the tail (where most failures surface), and a context
window around every line matching a known error signature. Overlapping windows are merged.
"""
import hashlib
import re

ERROR_PATTERNS = [
    r"\berror\b[:\[]", r"\bERROR\b", r"\bFAILED\b", r"\bFAILURE\b", r"BUILD FAILED",
    r"Exception\b", r"Traceback \(most recent call last\)", r"^\s+at [\w.$]+\(",
    r"fatal:", r"Segmentation fault", r"OutOfMemoryError", r"No space left on device",
    r"Could not resolve", r"Connection (refused|timed out|reset)", r"\bTimeout\b",
    r"undefined reference", r"cannot find symbol", r"No such file or directory",
    r"npm ERR!", r"AssertionError", r"\bpanic:", r"exit code [1-9]", r"returned non-zero",
    r"script returned exit code", r"Killed", r"permission denied",
]
_ERR_RE = re.compile("|".join(ERROR_PATTERNS), re.IGNORECASE)
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]|\[8mha:[^\[]*\[0m")
_TS_RE = re.compile(r"^\[?\d{4}-\d{2}-\d{2}[T ][\d:.]+Z?\]?\s*")


def clean(text: str) -> str:
    return _ANSI_RE.sub("", text)


def error_lines(text: str) -> list[str]:
    return [ln for ln in clean(text).splitlines() if _ERR_RE.search(ln)]


def extract_relevant(text: str, max_chars: int = 60_000, head: int = 40, tail: int = 300,
                     context: int = 15) -> str:
    lines = clean(text).splitlines()
    n = len(lines)
    if len("\n".join(lines)) <= max_chars:
        return "\n".join(lines)

    keep: list[tuple[int, int]] = [(0, min(head, n)), (max(0, n - tail), n)]
    for i, ln in enumerate(lines):
        if _ERR_RE.search(ln):
            keep.append((max(0, i - context), min(n, i + context + 1)))
    keep.sort()
    merged: list[list[int]] = []
    for s, e in keep:
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])

    chunks = []
    for s, e in merged:
        chunks.append(f"----- lines {s + 1}-{e} -----\n" + "\n".join(lines[s:e]))
    out = "\n".join(chunks)
    if len(out) > max_chars:
        # Errors near the end are usually the real ones; keep the tail of the extraction.
        out = "[...earlier output truncated...]\n" + out[-max_chars:]
    return out


def fingerprint(text: str, top: int = 5) -> str:
    """Stable hash of the first error lines, with volatile tokens (numbers, hex, paths, timestamps)
    normalised, so the same failure on different builds dedups into one Jira ticket."""
    sig = []
    for ln in error_lines(text)[:top]:
        ln = _TS_RE.sub("", ln.strip())
        ln = re.sub(r"0x[0-9a-fA-F]+|[0-9a-f]{7,40}", "<hex>", ln)
        ln = re.sub(r"\d+", "<n>", ln)
        ln = re.sub(r"(/[\w.\-]+)+/", "<path>/", ln)
        sig.append(ln)
    return hashlib.sha1("\n".join(sig).encode()).hexdigest()[:12]
