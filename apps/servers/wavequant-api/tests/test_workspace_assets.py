"""Integration checks for the API-to-Web workspace boundary."""

from http.client import HTTPConnection
from threading import Thread
import unittest

from pytdx.reader.gbbq_reader import GbbqReader

from wavequant_api.server import make_server


class WorkspaceAssetTests(unittest.TestCase):
    def test_runtime_includes_the_tdx_corporate_action_reader(self) -> None:
        self.assertIsNotNone(GbbqReader)

    def test_default_web_workspace_serves_page_and_vendored_chart_sdk(self) -> None:
        server = make_server(object(), port=0)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)

        for path, expected_content_type in (
            ("/", "text/html"),
            ("/research", "text/html"),
            ("/market", "text/html"),
            ("/vendor/lightweight-charts.js", "text/javascript"),
            ("/vendor/NOTICE", "text/plain"),
        ):
            with self.subTest(path=path):
                connection = HTTPConnection("127.0.0.1", server.server_port, timeout=2)
                try:
                    connection.request("GET", path)
                    response = connection.getresponse()
                    response.read()
                finally:
                    connection.close()
                self.assertEqual(response.status, 200)
                self.assertIn(expected_content_type, response.headers["Content-Type"])


if __name__ == "__main__":
    unittest.main()
