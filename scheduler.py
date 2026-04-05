"""
Installs / uninstalls a macOS launchd job that runs the channel pipeline
daily at 7:00 AM IST (01:30 UTC) without any manual intervention.

Usage:
  python scheduler.py install --channel https://www.youtube.com/@ChannelName/videos
  python scheduler.py uninstall
  python scheduler.py status
"""
import os
import sys
import argparse
import subprocess
import plistlib
from pathlib import Path

LABEL = "com.youtube-summarizer.daily"
PLIST_PATH = Path.home() / "Library/LaunchAgents" / f"{LABEL}.plist"

# 7:00 AM IST = 01:30 UTC
SCHEDULE_HOUR_UTC = 1
SCHEDULE_MINUTE_UTC = 30


def get_paths() -> tuple[str, str]:
    """Returns (python_executable, project_dir)."""
    project_dir = os.path.dirname(os.path.abspath(__file__))
    venv_python = os.path.join(project_dir, "venv", "bin", "python")
    if not os.path.exists(venv_python):
        sys.exit(f"❌ venv python not found at {venv_python}. Run: python -m venv venv && pip install -r requirements.txt")
    return venv_python, project_dir


def install(channel_url: str):
    python, project_dir = get_paths()

    plist = {
        "Label": LABEL,
        "ProgramArguments": [python, os.path.join(project_dir, "main.py"), "channel", channel_url],
        "WorkingDirectory": project_dir,
        "StartCalendarInterval": {
            "Hour": SCHEDULE_HOUR_UTC,
            "Minute": SCHEDULE_MINUTE_UTC,
        },
        "StandardOutPath": os.path.join(project_dir, "scheduler.log"),
        "StandardErrorPath": os.path.join(project_dir, "scheduler.log"),
        "RunAtLoad": False,
    }

    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(PLIST_PATH, "wb") as f:
        plistlib.dump(plist, f)

    # Unload first in case it was previously loaded
    subprocess.run(["launchctl", "unload", str(PLIST_PATH)], capture_output=True)
    result = subprocess.run(["launchctl", "load", str(PLIST_PATH)], capture_output=True, text=True)

    if result.returncode != 0:
        print(f"❌ Failed to load launchd job:\n{result.stderr}")
        sys.exit(1)

    print(f"✅ Scheduled daily at 7:00 AM IST (01:30 UTC)")
    print(f"   Channel  : {channel_url}")
    print(f"   Plist    : {PLIST_PATH}")
    print(f"   Log      : {os.path.join(project_dir, 'scheduler.log')}")
    print(f"\nTip: run 'python scheduler.py status' to confirm it's loaded.")


def uninstall():
    if not PLIST_PATH.exists():
        print("No scheduled job found.")
        return

    subprocess.run(["launchctl", "unload", str(PLIST_PATH)], capture_output=True)
    PLIST_PATH.unlink()
    print(f"✅ Uninstalled scheduled job and removed {PLIST_PATH}")


def status():
    result = subprocess.run(
        ["launchctl", "list", LABEL],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print("❌ Job is NOT loaded.")
        if PLIST_PATH.exists():
            print(f"   Plist exists at {PLIST_PATH} but is not active. Run: python scheduler.py install --channel <url>")
    else:
        print("✅ Job is loaded and active.")
        print(result.stdout)
        print(f"   Plist : {PLIST_PATH}")
        project_dir = os.path.dirname(os.path.abspath(__file__))
        print(f"   Log   : {os.path.join(project_dir, 'scheduler.log')}")

        # Parse channel URL from plist for display
        if PLIST_PATH.exists():
            with open(PLIST_PATH, "rb") as f:
                plist = plistlib.load(f)
            args = plist.get("ProgramArguments", [])
            if len(args) >= 4:
                print(f"   Channel: {args[-1]}")
            print(f"   Runs at: 7:00 AM IST daily (01:30 UTC)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Manage the daily YouTube summarizer schedule.")
    subparsers = parser.add_subparsers(dest="action", required=True)

    install_parser = subparsers.add_parser("install", help="Install and activate the daily schedule")
    install_parser.add_argument("--channel", required=True, help="YouTube channel URL to monitor daily")

    subparsers.add_parser("uninstall", help="Remove the daily schedule")
    subparsers.add_parser("status", help="Check if the schedule is active")

    args = parser.parse_args()

    if args.action == "install":
        install(args.channel)
    elif args.action == "uninstall":
        uninstall()
    elif args.action == "status":
        status()
