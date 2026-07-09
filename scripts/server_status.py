#!/usr/bin/env python3
"""서버 상태 모니터링 및 Discord 알림 전송 스크립트.

OS 정보, 커널 버전, 업데이트 필요 패키지 수, CPU/RAM 상태 및 크론 잡 실행 상태를
수집하여 Discord 웹훅 임베드로 전송합니다.

사용법:
    DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..." python3 server_status.py
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import time
import urllib.error
import urllib.request

# Discord 임베드 색상 (10진수).
_COLOR_INFO = 0x3498DB  # 하늘색


def get_os_info() -> str:
    """/etc/os-release 에서 PRETTY_NAME을 추출하거나 플랫폼 정보를 반환합니다."""
    try:
        if os.path.exists("/etc/os-release"):
            with open("/etc/os-release", "r") as f:
                for line in f:
                    if line.startswith("PRETTY_NAME="):
                        return line.split("=")[1].strip().strip('"')
    except Exception:
        pass
    return f"{platform.system()} {platform.release()}"


def get_upgradable_packages_count() -> int | str:
    """Debian/Ubuntu(APT) 또는 Fedora/CentOS(DNF) 환경에서 업데이트 대기 중인 패키지 수를 구합니다."""
    import shutil
    # APT 패키지 매니저 감지 (Debian/Ubuntu)
    if shutil.which("apt-get"):
        try:
            res = subprocess.run(["apt-get", "-s", "upgrade"], capture_output=True, text=True)
            if res.returncode == 0:
                count = sum(1 for line in res.stdout.splitlines() if line.startswith("Inst "))
                return count
        except Exception as exc:
            return f"APT 확인 실패 ({exc})"

    # DNF 패키지 매니저 감지 (Fedora/CentOS)
    if shutil.which("dnf"):
        try:
            # dnf check-update --quiet
            # exit code 100: updates available, listed on stdout
            # exit code 0: no updates
            res = subprocess.run(["dnf", "check-update", "--quiet"], capture_output=True, text=True)
            if res.returncode in (0, 100):
                lines = [line.strip() for line in res.stdout.splitlines() if line.strip()]
                packages = []
                for line in lines:
                    if "Obsoleting packages" in line or "Security:" in line:
                        break
                    parts = line.split()
                    if len(parts) >= 3:
                        packages.append(parts[0])
                return len(packages)
            return 0
        except Exception as exc:
            return f"DNF 확인 실패 ({exc})"
            
    return "지원되지 않는 패키지 매니저"


def get_cpu_info() -> dict:
    """/proc/stat 및 os.getloadavg를 사용하여 CPU 사용률 및 부하 정보를 구합니다."""
    try:
        def read_cpu() -> tuple[float, float]:
            with open("/proc/stat", "r") as f:
                line = f.readline()
            parts = line.split()
            if not parts or parts[0] != "cpu":
                return 0, 0
            parts = [float(x) for x in parts[1:]]
            idle = parts[3] + parts[4]  # idle + iowait
            total = sum(parts)
            return idle, total
        
        idle1, total1 = read_cpu()
        time.sleep(1)
        idle2, total2 = read_cpu()
        idle_delta = idle2 - idle1
        total_delta = total2 - total1
        usage_pct = (1.0 - idle_delta / total_delta) * 100.0 if total_delta > 0 else 0.0
        
        cores = os.cpu_count() or 1
        load = os.getloadavg() if hasattr(os, "getloadavg") else (0.0, 0.0, 0.0)
        return {
            "usage_pct": usage_pct,
            "cores": cores,
            "load_1m": load[0],
            "load_5m": load[1],
            "load_15m": load[2]
        }
    except Exception:
        return {
            "usage_pct": 0.0,
            "cores": 1,
            "load_1m": 0.0,
            "load_5m": 0.0,
            "load_15m": 0.0
        }


def get_ram_info() -> dict:
    """free -b 명령어를 사용하여 RAM 상세 상태를 구합니다."""
    try:
        res = subprocess.run(["free", "-b"], capture_output=True, text=True)
        for line in res.stdout.splitlines():
            if line.startswith("Mem:"):
                parts = line.split()
                total = int(parts[1])
                used = int(parts[2])
                free = int(parts[3])
                usage_pct = (used / total) * 100.0 if total > 0 else 0.0
                return {
                    "total_gb": total / (1024**3),
                    "used_gb": used / (1024**3),
                    "free_gb": free / (1024**3),
                    "usage_pct": usage_pct
                }
    except Exception:
        pass
    return {
        "total_gb": 0.0,
        "used_gb": 0.0,
        "free_gb": 0.0,
        "usage_pct": 0.0
    }


def get_cron_info() -> dict:
    """시스템 크론 데몬 상태 및 활성 유저 크론잡 목록을 구합니다."""
    service_status = "Unknown"
    for service in ["crond", "cron"]:
        try:
            res = subprocess.run(["systemctl", "is-active", service], capture_output=True, text=True)
            if res.stdout.strip() == "active":
                service_status = f"{service}가 실행 중입니다 (active)"
                break
        except Exception:
            pass
    if service_status == "Unknown":
        service_status = "cron 서비스가 비활성화 상태이거나 설치되지 않았습니다."
        
    user_jobs = []
    try:
        res = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
        if res.returncode == 0:
            for line in res.stdout.splitlines():
                line_clean = line.strip()
                if line_clean and not line_clean.startswith("#"):
                    user_jobs.append(line_clean)
    except Exception:
        pass
        
    return {
        "service": service_status,
        "jobs_count": len(user_jobs),
        "jobs": user_jobs
    }


def get_next_run_time() -> str:
    """크론 주기(00:00, 08:00, 16:00 KST)에 근거해 다음 발송 예정 시각을 KST 기준으로 구합니다."""
    import datetime
    # KST 타임존 설정 (UTC+9)
    kst_tz = datetime.timezone(datetime.timedelta(hours=9))
    now_kst = datetime.datetime.now(kst_tz)
    
    if now_kst.hour < 8:
        next_run = now_kst.replace(hour=8, minute=0, second=0, microsecond=0)
    elif now_kst.hour < 16:
        next_run = now_kst.replace(hour=16, minute=0, second=0, microsecond=0)
    else:
        next_run = now_kst.replace(hour=0, minute=0, second=0, microsecond=0) + datetime.timedelta(days=1)
    return next_run.strftime("%Y-%m-%d %H:%M:%S KST")


def send_discord_notification(webhook_url: str) -> None:
    """수집된 서버 메트릭을 Discord 임베드 포맷으로 전송합니다."""
    os_name = get_os_info()
    kernel = platform.release()
    updates_count = get_upgradable_packages_count()
    
    cpu = get_cpu_info()
    ram = get_ram_info()
    cron = get_cron_info()
    next_run_str = get_next_run_time()
    
    # 템플릿 메타 빌드
    fields = [
        {
            "name": "🖥️ 시스템 정보 (OS & Kernel)",
            "value": f"• **OS**: {os_name}\n• **Kernel**: {kernel}\n• **업데이트 대기 패키지**: `{updates_count}`개",
            "inline": False
        },
        {
            "name": "⚡ CPU & 부하 (Load Average)",
            "value": f"• **CPU 사용률**: `{cpu['usage_pct']:.1f}%` ({cpu['cores']} Cores)\n• **부하 지수 (1m/5m/15m)**: `{cpu['load_1m']:.2f}`, `{cpu['load_5m']:.2f}`, `{cpu['load_15m']:.2f}`",
            "inline": False
        },
        {
            "name": "💾 메모리 (RAM)",
            "value": f"• **사용률**: `{ram['usage_pct']:.1f}%`\n• **상태**: `{ram['used_gb']:.2f} GB` / `{ram['total_gb']:.1f} GB`\n• **여유 공간**: `{ram['free_gb']:.2f} GB` 여유",
            "inline": False
        },
        {
            "name": "⏱️ 크론 스케줄러 (Cron Status)",
            "value": f"• **서비스 상태**: {cron['service']}\n• **등록된 사용자 크론잡**: `{cron['jobs_count']}`개" + 
                     (f"\n```cron\n" + "\n".join(cron['jobs'][:5]) + ("\n...외 생략" if len(cron['jobs']) > 5 else "") + "\n```" if cron['jobs'] else "") +
                     f"\n• **다음 발송 예정 시각**: `{next_run_str}` (8시간 간격)",
            "inline": False
        }
    ]
    
    payload = {
        "embeds": [{
            "title": "📊 서버 상태 요약",
            "color": _COLOR_INFO,
            "fields": fields,
            "footer": {"text": "서버 모니터링 (8h 주기)"},
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        }]
    }
    
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        webhook_url.strip().strip("'\""),
        data=data,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "server-status-monitor/1.0",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        if resp.status not in (200, 204):
            raise RuntimeError(f"Discord webhook returned HTTP {resp.status}")


def send_slack_notification(webhook_url: str) -> None:
    """수집된 서버 메트릭을 Slack 첨부(attachment) 포맷으로 전송합니다."""
    os_name = get_os_info()
    kernel = platform.release()
    updates_count = get_upgradable_packages_count()
    
    cpu = get_cpu_info()
    ram = get_ram_info()
    cron = get_cron_info()
    next_run_str = get_next_run_time()
    
    # Slack fields
    fields = [
        {
            "title": "🖥️ 시스템 정보 (OS & Kernel)",
            "value": f"• OS: {os_name}\n• Kernel: {kernel}\n• 업데이트 대기 패키지: `{updates_count}`개",
            "short": False
        },
        {
            "title": "⚡ CPU & 부하 (Load Average)",
            "value": f"• CPU 사용률: `{cpu['usage_pct']:.1f}%` ({cpu['cores']} Cores)\n• 부하 지수 (1m/5m/15m): `{cpu['load_1m']:.2f}`, `{cpu['load_5m']:.2f}`, `{cpu['load_15m']:.2f}`",
            "short": False
        },
        {
            "title": "💾 메모리 (RAM)",
            "value": f"• 사용률: `{ram['usage_pct']:.1f}%`\n• 상태: `{ram['used_gb']:.2f} GB` / `{ram['total_gb']:.1f} GB`\n• 여유 공간: `{ram['free_gb']:.2f} GB` 여유",
            "short": False
        },
        {
            "title": "⏱️ 크론 스케줄러 (Cron Status)",
            "value": f"• 서비스 상태: {cron['service']}\n• 등록된 사용자 크론잡: `{cron['jobs_count']}`개" + 
                     (f"\n```cron\n" + "\n".join(cron['jobs'][:5]) + ("\n...외 생략" if len(cron['jobs']) > 5 else "") + "\n```" if cron['jobs'] else "") +
                     f"\n• 다음 발송 예정 시각: `{next_run_str}` (8시간 간격)",
            "short": False
        }
    ]
    
    color_hex = f"#{_COLOR_INFO:06x}"
    payload = {
        "text": "📊 서버 상태 요약",
        "attachments": [{
            "color": color_hex,
            "title": "📊 서버 상태 요약",
            "fields": fields,
            "footer": "서버 모니터링 (8h 주기)"
        }]
    }
    
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        webhook_url.strip().strip("'\""),
        data=data,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        if resp.status not in (200, 204):
            raise RuntimeError(f"Slack webhook returned HTTP {resp.status}")


def main() -> int:
    discord_webhook = os.environ.get("DISCORD_WEBHOOK_URL")
    slack_webhook = os.environ.get("SLACK_WEBHOOK_URL")
    
    if not discord_webhook and not slack_webhook:
        print("에러: DISCORD_WEBHOOK_URL 또는 SLACK_WEBHOOK_URL 환경 변수가 설정되지 않았습니다.", file=sys.stderr)
        return 1
        
    success = False
    if discord_webhook:
        try:
            send_discord_notification(discord_webhook)
            print("서버 상태 리포트가 Discord로 성공적으로 발송되었습니다.")
            success = True
        except Exception as exc:
            print(f"Discord 전송 실패: {exc}", file=sys.stderr)
            
    if slack_webhook:
        try:
            send_slack_notification(slack_webhook)
            print("서버 상태 리포트가 Slack으로 성공적으로 발송되었습니다.")
            success = True
        except Exception as exc:
            print(f"Slack 전송 실패: {exc}", file=sys.stderr)
            
    return 0 if success else 2


if __name__ == "__main__":
    raise SystemExit(main())
