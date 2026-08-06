from __future__ import annotations

import http.client
import tempfile
import threading
import unittest
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path

from zzzdown.library_server import LibraryHandler, parse_byte_range


class QuietLibraryHandler(LibraryHandler):
    def log_message(self, _format, *_args):
        pass


class LibraryServerTests(unittest.TestCase):
    def test_parse_byte_range_supports_regular_open_and_suffix_ranges(self):
        self.assertEqual(parse_byte_range("bytes=2-5", 10), (2, 5))
        self.assertEqual(parse_byte_range("bytes=7-", 10), (7, 9))
        self.assertEqual(parse_byte_range("bytes=-3", 10), (7, 9))
        with self.assertRaises(ValueError):
            parse_byte_range("bytes=10-12", 10)

    def test_video_response_supports_seeking_with_http_range(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "video.mp4").write_bytes(b"0123456789")
            handler = partial(QuietLibraryHandler, directory=str(root))
            server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
            server.library = root
            server.workspace = root
            server.token = "test"
            server.state_lock = threading.Lock()
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port)
            try:
                connection.request("GET", "/video.mp4", headers={"Range": "bytes=2-5"})
                response = connection.getresponse()
                self.assertEqual(response.status, 206)
                self.assertEqual(response.getheader("Accept-Ranges"), "bytes")
                self.assertEqual(response.getheader("Content-Range"), "bytes 2-5/10")
                self.assertEqual(response.read(), b"2345")
            finally:
                connection.close()
                server.shutdown()
                server.server_close()
                worker.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
