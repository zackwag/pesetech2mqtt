import asyncio
import sys
import tempfile
import unittest

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def load_export_module():
    sys.path.insert(0, str(REPO_ROOT / "gateway"))
    from diagnostics import export

    return export


EXPORT = load_export_module()


class DiagnosticExportTest(unittest.TestCase):
    def test_serves_only_approved_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            command_path = root / "command.jsonl"
            command_path.write_text('{"event":"ok"}\n', encoding="utf-8")
            server = EXPORT.DiagnosticExportServer(
                enabled=True,
                port=0,
                paths={"/command-monitor.jsonl": str(command_path)},
            )

            ok = asyncio.run(request(server, "/command-monitor.jsonl"))
            denied = asyncio.run(request(server, "/../command.jsonl"))

        self.assertIn(b"HTTP/1.1 200 OK", ok)
        self.assertIn(b'{"event":"ok"}\n', ok)
        self.assertIn(b"HTTP/1.1 404 Not Found", denied)

    def test_raw_tail_respects_tail_bytes_cap(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw_path = root / "btmon.log"
            raw_path.write_bytes(b"0123456789")
            server = EXPORT.DiagnosticExportServer(
                enabled=True,
                port=0,
                tail_bytes=4,
                paths={"/btmon-raw.log": str(raw_path)},
            )

            response = asyncio.run(request(server, "/btmon-raw.log?tail_bytes=8"))

        self.assertIn(b"HTTP/1.1 200 OK", response)
        self.assertTrue(response.endswith(b"6789"))

    def test_missing_file_returns_404(self):
        server = EXPORT.DiagnosticExportServer(
            enabled=True,
            port=0,
            paths={"/command-monitor.jsonl": "/tmp/does-not-exist-pesetech.jsonl"},
        )

        response = asyncio.run(request(server, "/command-monitor.jsonl"))

        self.assertIn(b"HTTP/1.1 404 Not Found", response)


async def request(export_server, path):
    server = await asyncio.start_server(export_server._handle_client, "127.0.0.1", 0)
    try:
        host, port = server.sockets[0].getsockname()[:2]
        reader, writer = await asyncio.open_connection(host, port)
        writer.write(f"GET {path} HTTP/1.1\r\nHost: test\r\n\r\n".encode("ascii"))
        await writer.drain()
        data = await reader.read()
        writer.close()
        await writer.wait_closed()
        return data
    finally:
        server.close()
        await server.wait_closed()


if __name__ == "__main__":
    unittest.main()
