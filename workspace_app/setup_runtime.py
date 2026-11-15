"""Install Chromium for the pinned Scrapling runtime once per environment."""
from importlib.metadata import version
from pathlib import Path
import subprocess
import sys


def main():
    stamp = Path(sys.prefix) / f".scrapling-browser-{version('scrapling')}-{version('patchright')}"
    if stamp.exists():
        return
    # Scrapling stealth launches Patchright Chromium, whose browser revision
    # can differ from Playwright's Chromium.
    subprocess.run([sys.executable, "-m", "patchright", "install", "chromium"], check=True)
    stamp.touch()


if __name__ == "__main__":
    main()
