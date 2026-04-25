"""Thin shim — delegates to app.interfaces.bot. Kept for launchd compatibility."""
from app.interfaces.bot import main  # noqa: F401

if __name__ == "__main__":
    main()
