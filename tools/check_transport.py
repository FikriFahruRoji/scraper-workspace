"""Check the shared stealth transport (Chromium GET and POST) against a local fixture server."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
from threading import Thread

ROOT = Path(__file__).resolve().parents[1]


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(b"<title>Fixture</title><p>Local verification</p>")

    def do_POST(self):
        data = self.rfile.read(int(self.headers["Content-Length"]))
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"path": self.path, "data": json.loads(data)}).encode())

    def log_message(self, *_args):
        pass


def main():
    sys.path.insert(0, str(ROOT / "scrapers" / "_shared"))
    from stealth_transport import StealthTransport

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}"
            with StealthTransport(timeout=10) as client:
                assert b"Local verification" in client.get(url).body
                response = client.post(url + "/api", params={"page": 2}, json={"token": "fixture"})
                assert response.status == 200
                assert response.json() == {"path": "/api?page=2", "data": {"token": "fixture"}}
            print("Scrapling Chrome GET/POST OK", flush=True)
        finally:
            server.shutdown()
            worker.join()


if __name__ == "__main__":
    main()
