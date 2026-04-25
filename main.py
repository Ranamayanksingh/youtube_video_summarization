"""Thin shim — kept for backward compatibility (launchd, scripts)."""
from app.interfaces.cli import process_video, run_video_mode, run_channel_mode  # noqa: F401


def main_cli():
    """Entry point for pyproject.toml scripts."""
    from app.interfaces.cli import main
    main()


if __name__ == "__main__":
    from app.interfaces.cli import main
    main()
