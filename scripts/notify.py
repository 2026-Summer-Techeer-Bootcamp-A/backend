#!/usr/bin/env python3
"""CI/CD 및 배포 결과 알림을 Discord 및 Slack으로 전송합니다.

GitHub Actions가 기본적으로 주입하는 환경 변수와 추가로 설정된 STATUS/JOB/DETAILS 환경 변수에서 
컨텍스트를 읽어 Discord 및 Slack 웹훅으로 서식 있는 메시지를 발송합니다. 
알림 전송의 실패나 설정 누락이 전체 빌드/배포 파이프라인의 성공 여부에 영향을 주지 않습니다.

로컬 테스트:
    STATUS=success JOB=Deploy \
    DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/... \
    SLACK_WEBHOOK_URL=https://hooks.slack.com/services/... \
    python3 scripts/notify.py
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

# Discord 임베드 색상 (10진수).
_COLOR_SUCCESS = 0x2ECC71  # 초록
_COLOR_FAILURE = 0xE74C3C  # 빨강
_COLOR_NEUTRAL = 0x95A5A6  # 회색 (취소됨 / 알 수 없음)

# 상태 -> (아이콘, 색상, 한국어 상태어)
_STATUS_META = {
    "success": ("✅", _COLOR_SUCCESS, "성공"),
    "failure": ("❌", _COLOR_FAILURE, "실패"),
    "cancelled": ("⚪", _COLOR_NEUTRAL, "취소됨"),
}

# 잡 이름 -> 한국어 표시
_JOB_KO = {"CI": "CI", "Deploy": "배포"}


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def _build_embed() -> dict:
    status = _env("STATUS", "success").lower()
    job = _env("JOB", "CI")
    icon, color, verb = _STATUS_META.get(status, ("⚪", _COLOR_NEUTRAL, status or "완료"))
    job_ko = _JOB_KO.get(job, job)

    # 실패하면 범인 찾기.
    is_ci_fail = job == "CI" and status == "failure"

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
        {"name": "저장소", "value": repo, "inline": True},
        {"name": "브랜치", "value": branch, "inline": True},
        {"name": "커밋", "value": f"[`{short_sha}`]({commit_url})", "inline": True},
        {"name": "🚨 범인" if is_ci_fail else "보낸 사람", "value": actor, "inline": True},
    ]

    # 배포 잡이 넘겨준 컨테이너 상태(docker compose ps)를 코드블록으로 첨부.
    details = _env("DETAILS").strip()
    if details:
        # Discord 필드값은 1024자 제한 -> 코드펜스 여유를 두고 자름.
        clipped = details[-960:]
        fields.append({"name": "컨테이너", "value": f"```\n{clipped}\n```", "inline": False})

    embed = {
        "title": f"{icon} {job_ko} {verb}",
        "url": run_url,
        "color": color,
        "fields": fields,
        "footer": {"text": "GitHub Actions"},
    }
    if is_ci_fail:
        embed["description"] = "빌드가 깨졌습니다 (`exit 1`). `git blame` 이 지목한 유력 용의자 👇"
        embed["footer"] = {"text": "CI 경찰청 · 강력계"}
    return embed


def notify_discord(webhook_url: str) -> None:
    payload = {"embeds": [_build_embed()]}
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=data,
        headers={
            "Content-Type": "application/json",
            # Discord의 에지(Cloudflare) 장비에서 기본 urllib User-Agent를 차단(403)하므로 별도 헤더 설정
            "User-Agent": "career-backend-notifier/1.0 (+github-actions)",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        # Discord는 정상 작동 시 204 No Content 응답을 반환함
        if resp.status not in (200, 204):
            raise RuntimeError(f"Discord webhook returned HTTP {resp.status}")


def _build_slack_payload() -> dict:
    status = _env("STATUS", "success").lower()
    job = _env("JOB", "CI")
    icon, color, verb = _STATUS_META.get(status, ("⚪", _COLOR_NEUTRAL, status or "완료"))
    job_ko = _JOB_KO.get(job, job)

    # 실패하면 범인 찾기.
    is_ci_fail = job == "CI" and status == "failure"

    repo = _env("GITHUB_REPOSITORY", "local/repo")
    branch = _env("GITHUB_REF_NAME", "-")
    sha = _env("GITHUB_SHA", "")
    short_sha = sha[:7] if sha else "-"
    actor = _env("GITHUB_ACTOR", "-")
    server = _env("GITHUB_SERVER_URL", "https://github.com")
    run_id = _env("GITHUB_RUN_ID", "")

    commit_url = f"{server}/{repo}/commit/{sha}" if sha else server
    run_url = f"{server}/{repo}/actions/runs/{run_id}" if run_id else server

    # Slack 첨부(attachment) 필드 구성
    slack_fields = [
        {"title": "저장소", "value": repo, "short": True},
        {"title": "브랜치", "value": branch, "short": True},
        {"title": "커밋", "value": f"<{commit_url}|`{short_sha}`>", "short": True},
        {"title": "🚨 범인" if is_ci_fail else "보낸 사람", "value": actor, "short": True},
    ]

    # 배포 잡이 넘겨준 컨테이너 상태(docker compose ps)를 코드블록으로 첨부.
    details = _env("DETAILS").strip()
    if details:
        # Slack 필드값은 1024자 제한 -> 코드펜스 여유를 두고 자름.
        clipped = details[-960:]
        slack_fields.append({"title": "컨테이너", "value": f"```\n{clipped}\n```", "short": False})

    color_hex = f"#{color:06x}"
    title = f"{icon} {job_ko} {verb}"

    attachment = {
        "color": color_hex,
        "title": title,
        "title_link": run_url,
        "fields": slack_fields,
        "footer": "GitHub Actions",
    }

    if is_ci_fail:
        attachment["text"] = "빌드가 깨졌습니다 (`exit 1`). `git blame` 이 지목한 유력 용의자 👇"
        attachment["footer"] = "진실은 단 하나."

    return {
        "text": f"{title} - {repo} ({branch})",
        "attachments": [attachment],
    }


def notify_slack(webhook_url: str) -> None:
    payload = _build_slack_payload()
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "career-backend-notifier/1.0 (+github-actions)",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        if resp.status not in (200, 204):
            raise RuntimeError(f"Slack webhook returned HTTP {resp.status}")


def main() -> int:
    discord_webhook = _env("DISCORD_WEBHOOK_URL")
    slack_webhook = _env("SLACK_WEBHOOK_URL")

    if not discord_webhook and not slack_webhook:
        print("Neither DISCORD_WEBHOOK_URL nor SLACK_WEBHOOK_URL is set; skipping notifications.")
        return 0

    if discord_webhook:
        try:
            notify_discord(discord_webhook)
            print("Discord notification sent.")
        except (urllib.error.URLError, RuntimeError, TimeoutError) as exc:
            print(f"Discord notification failed: {exc}", file=sys.stderr)

    if slack_webhook:
        try:
            notify_slack(slack_webhook)
            print("Slack notification sent.")
        except (urllib.error.URLError, RuntimeError, TimeoutError) as exc:
            print(f"Slack notification failed: {exc}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
