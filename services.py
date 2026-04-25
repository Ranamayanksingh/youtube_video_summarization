"""
Manage the Telegram bot and web app as macOS launchd services.

launchd keeps them running in the background at all times — survives terminal
close, lid close (while on power), and reboots. Each service auto-restarts
if it crashes.

Usage:
    python services.py start bot          # start bot now + register with launchd
    python services.py start web          # start web app now + register with launchd
    python services.py start all          # start both

    python services.py stop bot
    python services.py stop web
    python services.py stop all

    python services.py restart bot
    python services.py restart all

    python services.py status             # show status of both services
    python services.py logs bot           # tail the bot log
    python services.py logs web           # tail the web log
    python services.py uninstall all      # remove launchd plists entirely
"""
import argparse
import os
import plistlib
import subprocess
import sys
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────
PROJECT_DIR = Path(__file__).resolve().parent
VENV_PYTHON = PROJECT_DIR / ".venv" / "bin" / "python3"
LAUNCH_AGENTS = Path.home() / "Library" / "LaunchAgents"

BOT_LABEL   = "com.yt-summarizer.bot"
WEB_LABEL   = "com.yt-summarizer.web"

BOT_PLIST   = LAUNCH_AGENTS / f"{BOT_LABEL}.plist"
WEB_PLIST   = LAUNCH_AGENTS / f"{WEB_LABEL}.plist"

BOT_LOG     = PROJECT_DIR / "telegram_bot.log"
WEB_LOG     = PROJECT_DIR / "web_app.log"

WEB_HOST    = "0.0.0.0"
WEB_PORT    = "8000"


def _check_venv():
    if not VENV_PYTHON.exists():
        sys.exit(
            f"❌ Virtual environment not found at {VENV_PYTHON}\n"
            "   Run: bash setup.sh"
        )


def _launchctl(*args) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True)


def _is_loaded(label: str) -> bool:
    r = _launchctl("list", label)
    return r.returncode == 0


def _make_plist(label: str, program_args: list[str], log_path: Path) -> dict:
    return {
        "Label": label,
        "ProgramArguments": [str(VENV_PYTHON)] + program_args,
        "WorkingDirectory": str(PROJECT_DIR),
        "EnvironmentVariables": {
            # Ensure the venv's site-packages are found
            "PATH": str(VENV_PYTHON.parent) + ":/usr/local/bin:/usr/bin:/bin",
        },
        "StandardOutPath": str(log_path),
        "StandardErrorPath": str(log_path),
        # Restart automatically if the process exits
        "KeepAlive": True,
        # Start immediately when loaded
        "RunAtLoad": True,
        # Throttle rapid restart loops (wait 10s before restarting)
        "ThrottleInterval": 10,
    }


def _write_plist(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        plistlib.dump(data, f)


def _load(plist_path: Path, label: str):
    # Unload first in case a stale entry exists
    _launchctl("unload", str(plist_path))
    r = _launchctl("load", str(plist_path))
    if r.returncode != 0:
        print(f"❌ Failed to load {label}:\n{r.stderr.strip()}")
        sys.exit(1)


def _unload(plist_path: Path):
    _launchctl("unload", str(plist_path))


# ── Service definitions ────────────────────────────────────────────────────

def _bot_plist_data() -> dict:
    return _make_plist(
        BOT_LABEL,
        ["telegram_bot.py"],
        BOT_LOG,
    )


def _web_plist_data() -> dict:
    return _make_plist(
        WEB_LABEL,
        ["-m", "uvicorn", "web_app:app",
         "--host", WEB_HOST, "--port", WEB_PORT],
        WEB_LOG,
    )


# ── Commands ───────────────────────────────────────────────────────────────

def start(service: str):
    _check_venv()
    services = _resolve(service)
    for svc in services:
        if svc == "bot":
            _write_plist(BOT_PLIST, _bot_plist_data())
            _load(BOT_PLIST, BOT_LABEL)
            print(f"✅ Bot started and registered with launchd")
            print(f"   Log  : tail -f {BOT_LOG}")
            print(f"   PList: {BOT_PLIST}")
        elif svc == "web":
            _write_plist(WEB_PLIST, _web_plist_data())
            _load(WEB_PLIST, WEB_LABEL)
            print(f"✅ Web app started and registered with launchd")
            print(f"   URL  : http://localhost:{WEB_PORT}")
            print(f"   Log  : tail -f {WEB_LOG}")
            print(f"   PList: {WEB_PLIST}")
    print()
    print("ℹ️  These services will keep running after you close the terminal,")
    print("   close the lid, and will auto-start on reboot.")
    print("   Run 'python services.py status' to verify.")


def stop(service: str):
    services = _resolve(service)
    for svc in services:
        if svc == "bot":
            _unload(BOT_PLIST)
            print(f"⏹  Bot stopped.")
        elif svc == "web":
            _unload(WEB_PLIST)
            print(f"⏹  Web app stopped.")


def restart(service: str):
    stop(service)
    import time; time.sleep(1)
    start(service)


def status():
    _check_venv()
    print("── YT Summarizer Services ──────────────────────────────────")

    for label, name, plist, log in [
        (BOT_LABEL,  "Telegram Bot", BOT_PLIST, BOT_LOG),
        (WEB_LABEL,  "Web App",      WEB_PLIST, WEB_LOG),
    ]:
        loaded = _is_loaded(label)
        icon = "🟢" if loaded else "🔴"
        print(f"\n{icon} {name}")

        if loaded:
            r = _launchctl("list", label)
            # Parse PID and last exit code from launchctl output
            pid, exit_code = None, None
            for line in r.stdout.splitlines():
                line = line.strip()
                if line.startswith('"PID"'):
                    pid = line.split("=")[-1].strip().rstrip(";").strip('"')
                if line.startswith('"LastExitStatus"'):
                    exit_code = line.split("=")[-1].strip().rstrip(";").strip('"')
            if pid and pid != "0":
                print(f"   Status    : running  (PID {pid})")
            else:
                status_str = f"stopped (last exit: {exit_code})" if exit_code else "stopped"
                print(f"   Status    : {status_str}")
        else:
            plist_note = "(plist not installed)" if not plist.exists() else "(plist installed but not loaded)"
            print(f"   Status    : not running {plist_note}")

        print(f"   Log       : {log}")
        if plist.exists():
            print(f"   PList     : {plist}")

    print("\n────────────────────────────────────────────────────────────")
    print("Commands: start | stop | restart | logs <bot|web> | uninstall")


def logs(service: str):
    if service == "web":
        log_path = WEB_LOG
    else:
        log_path = BOT_LOG

    if not log_path.exists():
        print(f"No log file yet at {log_path}")
        return

    print(f"Tailing {log_path} — Ctrl+C to stop\n")
    try:
        subprocess.run(["tail", "-f", "-n", "50", str(log_path)])
    except KeyboardInterrupt:
        pass


def uninstall(service: str):
    services = _resolve(service)
    for svc in services:
        if svc == "bot":
            _unload(BOT_PLIST)
            if BOT_PLIST.exists():
                BOT_PLIST.unlink()
            print(f"🗑  Bot plist removed.")
        elif svc == "web":
            _unload(WEB_PLIST)
            if WEB_PLIST.exists():
                WEB_PLIST.unlink()
            print(f"🗑  Web app plist removed.")


def _resolve(service: str) -> list[str]:
    if service == "all":
        return ["bot", "web"]
    if service not in ("bot", "web"):
        sys.exit(f"❌ Unknown service '{service}'. Use: bot | web | all")
    return [service]


# ── CLI ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Manage YT Summarizer services via macOS launchd.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python services.py start all        # start bot + web, keep running forever
  python services.py status           # check what's running
  python services.py logs bot         # live log stream for the bot
  python services.py restart bot      # restart after a code change
  python services.py stop all         # stop both
  python services.py uninstall all    # remove from launchd entirely
        """,
    )

    subparsers = parser.add_subparsers(dest="cmd", required=True)

    for cmd in ("start", "stop", "restart", "uninstall"):
        p = subparsers.add_parser(cmd)
        p.add_argument("service", choices=["bot", "web", "all"])

    subparsers.add_parser("status")

    logs_p = subparsers.add_parser("logs")
    logs_p.add_argument("service", choices=["bot", "web"])

    args = parser.parse_args()

    if args.cmd == "start":      start(args.service)
    elif args.cmd == "stop":     stop(args.service)
    elif args.cmd == "restart":  restart(args.service)
    elif args.cmd == "status":   status()
    elif args.cmd == "logs":     logs(args.service)
    elif args.cmd == "uninstall": uninstall(args.service)
