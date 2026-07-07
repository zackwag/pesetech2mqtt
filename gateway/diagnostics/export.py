import asyncio
import logging
import os
from contextlib import suppress
from urllib.parse import parse_qs, urlsplit

from .monitor import raw_payload_has_marker


class DiagnosticExportServer:
    DEFAULT_HOST = "0.0.0.0"
    DEFAULT_PORT = 8766
    DEFAULT_TAIL_BYTES = 1024 * 1024
    DEFAULT_COMMAND_PATH = "/share/pesetech-command-monitor.jsonl"
    DEFAULT_BTMON_EVENTS_PATH = "/share/pesetech-btmon-events.jsonl"
    DEFAULT_BTMON_SUMMARY_PATH = "/share/pesetech-btmon-summary.jsonl"
    DEFAULT_BTMON_RAW_PATH = "/share/pesetech-btmon.log"

    def __init__(
        self,
        *,
        enabled=False,
        host=DEFAULT_HOST,
        port=DEFAULT_PORT,
        tail_bytes=DEFAULT_TAIL_BYTES,
        paths=None,
    ):
        self.enabled = bool(enabled)
        self.host = host or self.DEFAULT_HOST
        self.port = int(self.DEFAULT_PORT if port is None else port)
        self.tail_bytes = max(1, int(self.DEFAULT_TAIL_BYTES if tail_bytes is None else tail_bytes))
        self.paths = paths or {}

    @classmethod
    def from_config(cls, config):
        def optional(path, fallback):
            try:
                return config.optional(path, fallback)
            except Exception:
                return fallback

        enabled = bool(optional("diagnostic_export.enabled", False))
        port = int(optional("diagnostic_export.port", cls.DEFAULT_PORT))
        tail_bytes = int(optional("diagnostic_export.tail_bytes", cls.DEFAULT_TAIL_BYTES))

        env_enabled = os.environ.get("PESETECH_DIAGNOSTIC_EXPORT_ENABLED")
        if env_enabled is not None:
            enabled = env_enabled.strip().lower() == "true"
        if raw_payload_has_marker("diagnostic-export-enabled"):
            enabled = True

        env_port = os.environ.get("PESETECH_DIAGNOSTIC_EXPORT_PORT")
        if env_port:
            try:
                port = int(env_port)
            except ValueError:
                logging.warning(f"Ignoring invalid diagnostic export port {env_port!r}")

        env_tail_bytes = os.environ.get("PESETECH_DIAGNOSTIC_EXPORT_TAIL_BYTES")
        if env_tail_bytes:
            try:
                tail_bytes = int(env_tail_bytes)
            except ValueError:
                logging.warning(f"Ignoring invalid diagnostic export tail size {env_tail_bytes!r}")

        paths = {
            "/command-monitor.jsonl": os.environ.get("PESETECH_DIAGNOSTIC_MONITOR_PATH")
            or optional("diagnostic_monitor.path", cls.DEFAULT_COMMAND_PATH),
            "/btmon-events.jsonl": os.environ.get("PESETECH_BTMON_MONITOR_EVENTS_PATH")
            or optional("btmon_monitor.events_path", cls.DEFAULT_BTMON_EVENTS_PATH),
            "/btmon-summary.jsonl": os.environ.get("PESETECH_BTMON_MONITOR_SUMMARY_PATH")
            or optional("btmon_monitor.summary_path", cls.DEFAULT_BTMON_SUMMARY_PATH),
            "/btmon-raw.log": os.environ.get("PESETECH_BTMON_MONITOR_RAW_PATH")
            or optional("btmon_monitor.raw_path", cls.DEFAULT_BTMON_RAW_PATH),
        }
        return cls(enabled=enabled, port=port, tail_bytes=tail_bytes, paths=paths)

    async def run(self):
        if not self.enabled:
            return

        server = await asyncio.start_server(self._handle_client, self.host, self.port)
        sockets = ", ".join(str(sock.getsockname()) for sock in (server.sockets or []))
        logging.info("Diagnostic export server enabled on %s", sockets)
        async with server:
            await server.serve_forever()

    async def _handle_client(self, reader, writer):
        try:
            request_line = await asyncio.wait_for(reader.readline(), timeout=5)
            if not request_line:
                return
            try:
                method, target, _version = request_line.decode("ascii", errors="replace").strip().split(" ", 2)
            except ValueError:
                await self._send_response(writer, 400, b"Bad Request\n", content_type="text/plain")
                return

            while True:
                line = await reader.readline()
                if not line or line in (b"\r\n", b"\n"):
                    break

            if method != "GET":
                await self._send_response(writer, 405, b"Method Not Allowed\n", content_type="text/plain")
                return

            parsed = urlsplit(target)
            path = parsed.path
            if path not in self.paths:
                await self._send_response(writer, 404, b"Not Found\n", content_type="text/plain")
                return

            file_path = self.paths[path]
            if not file_path or not os.path.isfile(file_path):
                await self._send_response(writer, 404, b"Not Found\n", content_type="text/plain")
                return

            tail_bytes = None
            if path == "/btmon-raw.log":
                tail_bytes = self._requested_tail_bytes(parsed.query)

            body = await asyncio.to_thread(self._read_file, file_path, tail_bytes)
            content_type = "application/jsonl" if path.endswith(".jsonl") else "text/plain"
            await self._send_response(writer, 200, body, content_type=content_type)
        except Exception as exc:
            logging.warning("Diagnostic export request failed: %s: %s", type(exc).__name__, exc)
            with suppress(Exception):
                await self._send_response(writer, 500, b"Internal Server Error\n", content_type="text/plain")
        finally:
            writer.close()
            with suppress(Exception):
                await writer.wait_closed()

    def _requested_tail_bytes(self, query):
        values = parse_qs(query).get("tail_bytes", [])
        if not values:
            return self.tail_bytes
        try:
            value = int(values[0])
        except (TypeError, ValueError):
            return self.tail_bytes
        return max(1, min(value, self.tail_bytes))

    def _read_file(self, path, tail_bytes=None):
        if tail_bytes is None:
            with open(path, "rb") as handle:
                return handle.read()
        size = os.path.getsize(path)
        with open(path, "rb") as handle:
            handle.seek(max(0, size - tail_bytes))
            return handle.read()

    async def _send_response(self, writer, status, body, *, content_type):
        reason = {
            200: "OK",
            400: "Bad Request",
            404: "Not Found",
            405: "Method Not Allowed",
            500: "Internal Server Error",
        }.get(status, "OK")
        headers = (
            f"HTTP/1.1 {status} {reason}\r\n"
            f"Content-Length: {len(body)}\r\n"
            f"Content-Type: {content_type}; charset=utf-8\r\n"
            "Connection: close\r\n"
            "\r\n"
        ).encode("ascii")
        writer.write(headers + body)
        await writer.drain()
