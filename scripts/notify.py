#!/usr/bin/env python3
"""Post CI/CD and deploy result notifications to Discord (Slack: TODO).

Reads context from environment variables (the ones GitHub Actions injects by
default, plus STATUS/JOB/DETAILS we set explicitly) and sends a rich embed to
the Discord webhook. A failed or missing notification never fails the build.

Slack is intentionally stubbed (see notify_slack): fill it in and wire
SLACK_WEBHOOK_URL when we want a second channel.

Local test:
    STATUS=success JOB=Deploy \
    DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/... \
    python3 scripts/notify.py
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

# Discord embed colors (decimal).
_COLOR_SUCCESS = 0x2ECC71  # green
_COLOR_FAILURE = 0xE74C3C  # red
_COLOR_NEUTRAL = 0x95A5A6  # grey (cancelled / unknown)

# status -> (icon, color, past-tense verb)
_STATUS_META = {
    "success": ("✅", _COLOR_SUCCESS, "succeeded"),
    "failure": ("❌", _COLOR_FAILURE, "failed"),
    "cancelled": ("⚪", _COLOR_NEUTRAL, "cancelled"),
}


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def _build_embed() -> dict:
    status = _env("STATUS", "success").lower()
    job = _env("JOB", "CI")
    icon, color, verb = _STATUS_META.get(status, ("⚪", _COLOR_NEUTRAL, status or "ran"))

    repo = _env("GITHUB_REPOSITORY", "local/repo")
    branch = _env("GITHUB_REF_NAME", "-")
    sha = _env("GITHUB_SHA", "")
    short_sha = sha[:7] if sha else "-"
    actor = _env("GITHUB_ACTOR", "-")
    server = _env("GITHUB_SERVER_URL", "https://github.com")
    run_id = _env("GITHUB_RUN_ID", "")

    commit_url = f"{server}/{repo}/commit/{sha}" if sha else server
    run_url = f"{server}/{repo}/actions/runs/{run_id}" if run_id else server

    fields = [
        {"name": "Repo", "value": repo, "inline": True},
        {"name": "Branch", "value": branch, "inline": True},
        {"name": "Commit", "value": f"[`{short_sha}`]({commit_url})", "inline": True},
        {"name": "By", "value": actor, "inline": True},
    ]

    # Optional container health (e.g. `docker compose ps`) captured by the deploy job.
    details = _env("DETAILS").strip()
    if details:
        # Discord field values cap at 1024 chars; leave margin for the code fence.
        clipped = details[-960:]
        fields.append({"name": "Containers", "value": f"```\n{clipped}\n```", "inline": False})

    return {
        "title": f"{icon} {job} {verb}",
        "url": run_url,
        "color": color,
        "fields": fields,
        "footer": {"text": "GitHub Actions"},
    }


def notify_discord(webhook_url: str) -> None:
    payload = {"embeds": [_build_embed()]}
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=data,
        headers={
            "Content-Type": "application/json",
            # Discord's edge (Cloudflare) 403s the default urllib User-Agent.
            "User-Agent": "career-backend-notifier/1.0 (+github-actions)",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        # Discord replies 204 No Content on success.
        if resp.status not in (200, 204):
            raise RuntimeError(f"Discord webhook returned HTTP {resp.status}")


def notify_slack() -> None:
    # TODO(slack): read SLACK_WEBHOOK_URL, build a Slack Block Kit payload that
    # mirrors the Discord embed (status color, repo/branch/commit/actor fields,
    # run link), and POST it. Not wired up yet -- Discord is the only live channel.
    raise NotImplementedError("Slack notifications are not implemented yet.")


def main() -> int:
    webhook = _env("DISCORD_WEBHOOK_URL")
    if not webhook:
        # No channel configured (e.g. secret missing on a fork) -> skip quietly.
        print("DISCORD_WEBHOOK_URL not set; skipping Discord notification.")
        return 0
    try:
        notify_discord(webhook)
        print("Discord notification sent.")
    except (urllib.error.URLError, RuntimeError, TimeoutError) as exc:
        # A broken notification must never fail the pipeline.
        print(f"Discord notification failed: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
