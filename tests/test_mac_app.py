"""Exercise the actual compiled Mac transport against a loopback HTTP server."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
BINARY = os.environ.get("MAC_APP_BIN", "")


@unittest.skipUnless(BINARY, "Native binary is built in the mac-app CI job")
class NativeTransportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.payload = Path(self.temp.name) / "payload.json"
        self.data = {"name": "Grüße 🎵", "tracks": [{"name": "Hello", "artist": "Björk"},
                    {"name": "Again", "artist": "Band"}, {"name": "Again", "artist": "Band"}], "create": True}
        self.payload.write_text(json.dumps(self.data), encoding="utf-8")
        self.key = "fixture_key_" + "x" * 32
        self.requests = []
        self.status = 200
        outer = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                outer.requests.append((self.path, None, None))
                self.send_response(500)
                self.end_headers()
            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                outer.requests.append((self.path, self.headers.get("Authorization"), json.loads(body)))
                self.send_response(outer.status)
                if outer.status == 302:
                    self.send_header("Location", outer.url + "/leaked-key")
                self.send_header("Content-Type", "application/json")
                content = json.dumps({"ok": True, "changed": True, "created": True, "tracks": 3} if outer.status == 200
                                     else {"error": "Do not display untrusted response " + outer.key}).encode()
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = "http://127.0.0.1:" + str(self.server.server_port)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)

    def run_transport(self):
        return subprocess.run([BINARY, "--transport-test", self.url, self.key, str(self.payload)],
                              capture_output=True, text=True, timeout=45)

    def test_unicode_order_duplicates_and_scoped_payload(self):
        result = self.run_transport()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Confirmed 3 tracks", result.stdout)
        self.assertEqual(self.requests, [("/api/playlist-sync", "Bearer " + self.key, self.data)])

    def test_redirect_is_rejected_without_forwarding_key(self):
        self.status = 302
        result = self.run_transport()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(self.requests), 1)
        self.assertIn("um", result.stderr)
        self.assertNotIn(self.key, result.stdout + result.stderr)

    def test_authorization_error_never_claims_success_or_echoes_response(self):
        self.status = 403
        result = self.run_transport()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Schlüssel", result.stderr)
        self.assertNotIn("Confirmed", result.stdout)
        self.assertNotIn(self.key, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
