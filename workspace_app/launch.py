"""Install or refresh local dependencies, then start Scraper Workspace."""

from hashlib import sha256
from importlib.util import find_spec
from os import environ
from pathlib import Path
import subprocess
import sys

from workspace_app import setup_runtime


def main():
    if sys.version_info[:2] != (3, 11) or sys.prefix == sys.base_prefix:
        raise SystemExit("Gunakan Python 3.11 di .venv. Lihat bagian Pemecahan masalah di README.")

    root = Path(__file__).resolve().parents[1]
    requirements = root / "requirements.txt"
    digest = sha256(requirements.read_bytes()).hexdigest()
    stamp = Path(sys.prefix) / ".workspace-requirements"
    installed = stamp.read_text(encoding="ascii") if stamp.is_file() else ""
    if installed != digest or find_spec("streamlit") is None:
        print("Memasang atau memperbarui dependensi Python...", flush=True)
        if find_spec("pip") is None:
            subprocess.run([sys.executable, "-m", "ensurepip", "--upgrade"], check=True)
        subprocess.run([sys.executable, "-m", "pip", "install", "-r", str(requirements)], check=True)
        stamp.write_text(digest, encoding="ascii")

    setup_runtime.main()
    port = environ.get("STREAMLIT_SERVER_PORT", "8510")
    print(f"Buka http://127.0.0.1:{port} setelah Streamlit siap. Tekan Ctrl+C untuk berhenti.", flush=True)
    return subprocess.call(
        [sys.executable, "-m", "streamlit", "run", "workspace_app.py",
         "--server.address", "127.0.0.1", "--server.port", port],
        cwd=root,
    )


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(0)
    except subprocess.CalledProcessError:
        raise SystemExit("Pemasangan gagal. Periksa pesan di atas dan coba buka launcher lagi.")
