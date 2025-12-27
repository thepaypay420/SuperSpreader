from __future__ import annotations

import asyncio
import base64
import time
from typing import Any

import requests

from monitoring.github_publisher import _build_report_md, _read_tail_lines
from utils.logging import get_logger


def _github_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "superspreader-local-repo-publisher",
    }


def _get_current_file_sha(token: str, repo: str, path: str, branch: str) -> str | None:
    r = requests.get(
        f"https://api.github.com/repos/{repo}/contents/{path}",
        headers=_github_headers(token),
        params={"ref": branch},
        timeout=20,
    )
    if r.status_code == 404:
        return None
    r.raise_for_status()
    data = r.json()
    return data.get("sha")


def _put_file(token: str, repo: str, path: str, branch: str, *, content: str, sha: str | None, message: str) -> None:
    b64 = base64.b64encode(content.encode("utf-8")).decode("ascii")
    payload: dict[str, Any] = {
        "message": message,
        "content": b64,
        "branch": branch,
    }
    if sha:
        payload["sha"] = sha
    r = requests.put(
        f"https://api.github.com/repos/{repo}/contents/{path}",
        headers=_github_headers(token),
        json=payload,
        timeout=30,
    )
    r.raise_for_status()


async def run_repo_publisher_task(settings: Any, store: Any) -> None:
    """
    Publish a periodic snapshot into a repo file (creates a commit each update).

    Enable with:
    - GITHUB_REPO_PUBLISH_ENABLED=1
    - GITHUB_REPO=owner/name (e.g. thepaypay420/SuperSpreader)
    - GH_TOKEN or GITHUB_TOKEN with permission to write contents
    - optional: GITHUB_REPO_BRANCH, GITHUB_REPO_PATH
    """
    log = get_logger(__name__)

    if not bool(getattr(settings, "github_repo_publish_enabled", False)):
        while True:
            await asyncio.sleep(3600)

    token = getattr(settings, "github_token", None)
    if not token:
        log.warning("github_repo_publish.disabled", reason="missing_token")
        while True:
            await asyncio.sleep(3600)

    repo = getattr(settings, "github_repo", None)
    if not repo:
        log.warning("github_repo_publish.disabled", reason="missing_repo")
        while True:
            await asyncio.sleep(3600)

    branch = str(getattr(settings, "github_repo_branch", "main"))
    path = str(getattr(settings, "github_repo_path", "ops/telemetry/latest.md")).lstrip("/")
    prefix = str(getattr(settings, "github_repo_commit_prefix", "telemetry"))
    interval = int(getattr(settings, "github_publish_interval_secs", 60))
    tail_lines = int(getattr(settings, "github_publish_log_tail_lines", 200))
    log_file = getattr(settings, "log_file", None)

    last_sha: str | None = None

    while True:
        try:
            tail = None
            if log_file:
                tail = await asyncio.to_thread(_read_tail_lines, str(log_file), tail_lines)
            content = await asyncio.to_thread(_build_report_md, store, log_tail=tail)

            # Refresh SHA occasionally (or first time) in case it changed.
            if last_sha is None:
                last_sha = await asyncio.to_thread(_get_current_file_sha, token, repo, path, branch)

            msg = f"{prefix}: update {path} @ {time.strftime('%Y-%m-%d %H:%M:%S')}"
            await asyncio.to_thread(_put_file, token, repo, path, branch, content=content, sha=last_sha, message=msg)

            # Fetch the new SHA so next update is consistent.
            last_sha = await asyncio.to_thread(_get_current_file_sha, token, repo, path, branch)
            log.info("github_repo_publish.updated", repo=str(repo), branch=branch, path=path)
        except Exception:
            # Force re-fetch SHA next loop (handles manual edits / force pushes).
            last_sha = None
            log.exception("github_repo_publish.error", repo=str(repo), branch=branch, path=path)

        await asyncio.sleep(max(15, interval))

