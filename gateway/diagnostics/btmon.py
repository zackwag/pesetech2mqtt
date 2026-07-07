import asyncio
import json
import logging
import os
import shutil
import time

from collections import Counter, deque
from datetime import datetime, timezone

from .monitor import raw_payload_has_marker
from .rotation import prune_extra_rotations, rotate_if_needed


class BtmonMonitor:
    """
    Passive controller-level Bluetooth monitor.

    This runs BlueZ btmon and records what the Bluetooth controller sees. It is
    separate from the gateway's parsed Bluetooth Mesh message monitor.
    """

    DEFAULT_RAW_PATH = "/share/pesetech-btmon.log"
    DEFAULT_EVENTS_PATH = "/share/pesetech-btmon-events.jsonl"
    DEFAULT_SUMMARY_PATH = "/share/pesetech-btmon-summary.jsonl"
    DEFAULT_SUMMARY_INTERVAL = 60
    DEFAULT_MAX_BYTES = 25 * 1024 * 1024
    DEFAULT_MAX_FILES = 3
    DEFAULT_EVENTS_MAX_BYTES = 5 * 1024 * 1024
    DEFAULT_EVENTS_MAX_FILES = 3
    DEFAULT_SUMMARY_MAX_BYTES = 5 * 1024 * 1024
    DEFAULT_SUMMARY_MAX_FILES = 3

    def __init__(
        self,
        *,
        enabled=False,
        adapter="hci0",
        raw_path=DEFAULT_RAW_PATH,
        events_path=DEFAULT_EVENTS_PATH,
        summary_path=DEFAULT_SUMMARY_PATH,
        summary_interval_seconds=DEFAULT_SUMMARY_INTERVAL,
        max_bytes=DEFAULT_MAX_BYTES,
        max_files=DEFAULT_MAX_FILES,
        events_max_bytes=DEFAULT_EVENTS_MAX_BYTES,
        events_max_files=DEFAULT_EVENTS_MAX_FILES,
        summary_max_bytes=DEFAULT_SUMMARY_MAX_BYTES,
        summary_max_files=DEFAULT_SUMMARY_MAX_FILES,
    ):
        self.enabled = bool(enabled)
        self.adapter = adapter or "hci0"
        self.raw_path = raw_path or self.DEFAULT_RAW_PATH
        self.events_path = events_path or self.DEFAULT_EVENTS_PATH
        self.summary_path = summary_path or self.DEFAULT_SUMMARY_PATH
        self.summary_interval_seconds = max(5, int(summary_interval_seconds or self.DEFAULT_SUMMARY_INTERVAL))
        self.max_bytes = max(1024 * 1024, int(max_bytes or self.DEFAULT_MAX_BYTES))
        self.max_files = max(1, int(max_files or self.DEFAULT_MAX_FILES))
        self.events_max_bytes = max(1024 * 1024, int(events_max_bytes or self.DEFAULT_EVENTS_MAX_BYTES))
        self.events_max_files = max(1, int(events_max_files or self.DEFAULT_EVENTS_MAX_FILES))
        self.summary_max_bytes = max(1024 * 1024, int(summary_max_bytes or self.DEFAULT_SUMMARY_MAX_BYTES))
        self.summary_max_files = max(1, int(summary_max_files or self.DEFAULT_SUMMARY_MAX_FILES))
        self._counts = Counter()
        self._selected_counts = Counter()
        self._important = deque(maxlen=25)
        self._raw_handle = None
        self._raw_size = 0
        self._line_count = 0

    @classmethod
    def from_config(cls, config):
        def optional(path, fallback):
            try:
                return config.optional(path, fallback)
            except Exception:
                return fallback

        enabled = bool(optional("btmon_monitor.enabled", False))
        adapter = optional("btmon_monitor.adapter", "")
        raw_path = optional("btmon_monitor.raw_path", cls.DEFAULT_RAW_PATH)
        events_path = optional("btmon_monitor.events_path", cls.DEFAULT_EVENTS_PATH)
        summary_path = optional("btmon_monitor.summary_path", cls.DEFAULT_SUMMARY_PATH)
        interval = int(optional("btmon_monitor.summary_interval_seconds", cls.DEFAULT_SUMMARY_INTERVAL))
        max_bytes = int(optional("btmon_monitor.max_bytes", cls.DEFAULT_MAX_BYTES))
        max_files = int(optional("btmon_monitor.max_files", cls.DEFAULT_MAX_FILES))
        events_max_bytes = int(optional("btmon_monitor.events_max_bytes", cls.DEFAULT_EVENTS_MAX_BYTES))
        events_max_files = int(optional("btmon_monitor.events_max_files", cls.DEFAULT_EVENTS_MAX_FILES))
        summary_max_bytes = int(optional("btmon_monitor.summary_max_bytes", cls.DEFAULT_SUMMARY_MAX_BYTES))
        summary_max_files = int(optional("btmon_monitor.summary_max_files", cls.DEFAULT_SUMMARY_MAX_FILES))

        env_enabled = os.environ.get("PESETECH_BTMON_MONITOR_ENABLED")
        if env_enabled is not None:
            enabled = env_enabled.strip().lower() == "true"
        if raw_payload_has_marker("btmon-monitor-enabled"):
            enabled = True

        env_adapter = os.environ.get("PESETECH_BTMON_MONITOR_ADAPTER")
        if env_adapter:
            adapter = env_adapter
        if not adapter:
            adapter = adapter_from_mesh_io(os.environ.get("PESETECH_MESH_IO", "")) or "hci0"

        raw_path = os.environ.get("PESETECH_BTMON_MONITOR_RAW_PATH") or raw_path
        events_path = os.environ.get("PESETECH_BTMON_MONITOR_EVENTS_PATH") or events_path
        summary_path = os.environ.get("PESETECH_BTMON_MONITOR_SUMMARY_PATH") or summary_path

        env_interval = os.environ.get("PESETECH_BTMON_MONITOR_SUMMARY_INTERVAL_SECONDS")
        if env_interval:
            try:
                interval = int(env_interval)
            except ValueError:
                logging.warning(f"Ignoring invalid btmon monitor interval {env_interval!r}")

        return cls(
            enabled=enabled,
            adapter=adapter,
            raw_path=raw_path,
            events_path=events_path,
            summary_path=summary_path,
            summary_interval_seconds=interval,
            max_bytes=max_bytes,
            max_files=max_files,
            events_max_bytes=events_max_bytes,
            events_max_files=events_max_files,
            summary_max_bytes=summary_max_bytes,
            summary_max_files=summary_max_files,
        )

    def _now(self):
        return datetime.now(timezone.utc).isoformat()

    def _event(self, event, **fields):
        data = {
            "ts": self._now(),
            "mono": round(time.monotonic(), 6),
            "event": event,
            "adapter": self.adapter,
        }
        data.update(fields)
        return data

    def _write_jsonl(self, event, **fields):
        try:
            line = json.dumps(self._event(event, **fields), sort_keys=True) + "\n"
            directory = os.path.dirname(self.summary_path)
            if directory:
                os.makedirs(directory, exist_ok=True)
            prune_extra_rotations(self.summary_path, self.summary_max_files)
            rotate_if_needed(
                self.summary_path,
                len(line.encode("utf-8")),
                self.summary_max_bytes,
                self.summary_max_files,
            )
            with open(self.summary_path, "a", encoding="utf-8") as handle:
                handle.write(line)
        except Exception as exc:
            logging.warning(f"btmon monitor failed to write summary: {type(exc).__name__}: {exc}")

    def _write_event_jsonl(self, kind, **fields):
        try:
            line = json.dumps(self._event(kind, **fields), sort_keys=True) + "\n"
            directory = os.path.dirname(self.events_path)
            if directory:
                os.makedirs(directory, exist_ok=True)
            prune_extra_rotations(self.events_path, self.events_max_files)
            rotate_if_needed(
                self.events_path,
                len(line.encode("utf-8")),
                self.events_max_bytes,
                self.events_max_files,
            )
            with open(self.events_path, "a", encoding="utf-8") as handle:
                handle.write(line)
        except Exception as exc:
            logging.warning(f"btmon monitor failed to write selected event: {type(exc).__name__}: {exc}")

    def _open_raw(self):
        directory = os.path.dirname(self.raw_path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        prune_extra_rotations(self.raw_path, self.max_files)
        self._raw_size = os.path.getsize(self.raw_path) if os.path.exists(self.raw_path) else 0
        self._raw_handle = open(self.raw_path, "a", encoding="utf-8", buffering=1)

    def _close_raw(self):
        if self._raw_handle is not None:
            try:
                self._raw_handle.close()
            finally:
                self._raw_handle = None

    def _rotate_raw_if_needed(self, next_bytes):
        if self._raw_size + next_bytes <= self.max_bytes:
            return
        self._close_raw()
        rotate_if_needed(self.raw_path, next_bytes, self.max_bytes, self.max_files)
        self._open_raw()

    def _write_raw_line(self, line):
        encoded_len = len(line.encode("utf-8", errors="replace"))
        if self._raw_handle is None:
            self._open_raw()
        self._rotate_raw_if_needed(encoded_len)
        self._raw_handle.write(line)
        self._raw_size += encoded_len

    def record_line(self, line):
        self._line_count += 1
        category = classify_btmon_line(line)
        self._counts[category] += 1
        if is_important_btmon_line(line, category):
            self._important.append(line.strip())
        if is_selected_btmon_event(line, category):
            self._selected_counts[category] += 1
            self._write_event_jsonl("btmon_event", category=category, line=line.strip())

    def flush_summary(self):
        if not self._line_count and not self._counts:
            return
        counts = dict(sorted(self._counts.items()))
        important = list(self._important)
        bearer_counts = {
            "advertising": counts.get("advertising_report", 0),
            "acl_gatt": counts.get("acl_rx", 0) + counts.get("acl_tx", 0),
            "connection": counts.get("connection", 0),
            "disconnect": counts.get("disconnect", 0),
            "non_success_status": self._selected_counts.get("status_or_error", 0),
        }
        self._write_jsonl(
            "btmon_summary",
            interval_seconds=self.summary_interval_seconds,
            line_count=self._line_count,
            counts=counts,
            bearer_counts=bearer_counts,
            important=important,
        )
        logging.info(
            "btmon summary adapter=%s lines=%s counts=%s important_tail=%s",
            self.adapter,
            self._line_count,
            counts,
            important[-5:],
        )
        self._counts.clear()
        self._selected_counts.clear()
        self._important.clear()
        self._line_count = 0

    async def _read_output(self, process):
        while True:
            raw = await process.stdout.readline()
            if not raw:
                break
            line = raw.decode("utf-8", errors="replace")
            self._write_raw_line(line)
            self.record_line(line)

    async def _summarize_loop(self):
        while True:
            await asyncio.sleep(self.summary_interval_seconds)
            self.flush_summary()

    async def run(self):
        if not self.enabled:
            return

        btmon = find_btmon()
        if not btmon:
            logging.warning("btmon monitor enabled, but btmon was not found in the add-on container.")
            self._write_jsonl("btmon_missing")
            return

        self._open_raw()
        self._write_jsonl("btmon_start", raw_path=self.raw_path, summary_path=self.summary_path, btmon=btmon)
        self._write_event_jsonl(
            "btmon_start",
            raw_path=self.raw_path,
            events_path=self.events_path,
            summary_path=self.summary_path,
            btmon=btmon,
        )
        logging.info(
            "btmon monitor enabled; adapter=%s raw=%s events=%s summary=%s",
            self.adapter,
            self.raw_path,
            self.events_path,
            self.summary_path,
        )

        process = None
        summary_task = None
        try:
            process = await asyncio.create_subprocess_exec(
                btmon,
                "-i",
                self.adapter,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            summary_task = asyncio.create_task(self._summarize_loop())
            await self._read_output(process)
            return_code = await process.wait()
            self.flush_summary()
            self._write_jsonl("btmon_exit", return_code=return_code)
            self._write_event_jsonl("btmon_exit", return_code=return_code)
            logging.warning("btmon monitor exited with code %s", return_code)
        except asyncio.CancelledError:
            self.flush_summary()
            self._write_jsonl("btmon_stop")
            self._write_event_jsonl("btmon_stop")
            if process is not None and process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=5)
                except asyncio.TimeoutError:
                    process.kill()
            raise
        except Exception as exc:
            self.flush_summary()
            self._write_jsonl("btmon_error", exception={"type": type(exc).__name__, "message": str(exc)})
            self._write_event_jsonl("btmon_error", exception={"type": type(exc).__name__, "message": str(exc)})
            logging.warning(f"btmon monitor failed: {type(exc).__name__}: {exc}")
        finally:
            if summary_task is not None:
                summary_task.cancel()
            self._close_raw()


def find_btmon():
    for candidate in (
        "/usr/bin/btmon",
        "/usr/local/bin/btmon",
        "/opt/build/bluez-5.66/monitor/btmon",
    ):
        if os.path.exists(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return shutil.which("btmon")


def adapter_from_mesh_io(mesh_io):
    for token in str(mesh_io or "").replace(":", " ").split():
        if token.startswith("hci") and token[3:].isdigit():
            return token
    return ""


def classify_btmon_line(line):
    stripped = line.strip()
    if not stripped:
        return "blank"
    if stripped.startswith("< HCI Command"):
        return "hci_command"
    if stripped.startswith("> HCI Event"):
        return "hci_event"
    if stripped.startswith("< ACL Data"):
        return "acl_tx"
    if stripped.startswith("> ACL Data"):
        return "acl_rx"
    if stripped.startswith("@ MGMT"):
        return "mgmt"
    lowered = stripped.lower()
    if "advertising report" in lowered or (
        "event type:" in lowered and ("adv_" in lowered or "connectable" in lowered)
    ):
        return "advertising_report"
    if "connection complete" in lowered or "connection request" in lowered:
        return "connection"
    if "disconnect" in lowered:
        return "disconnect"
    if "error" in lowered or "fail" in lowered or "status: " in lowered:
        return "status_or_error"
    if stripped.startswith("#") or stripped.startswith("="):
        return "header"
    return "detail"


def is_important_btmon_line(line, category):
    lowered = line.lower()
    if category in {"connection", "disconnect", "status_or_error", "mgmt"}:
        return True
    return any(
        marker in lowered
        for marker in (
            "advertising report",
            "le set scan enable",
            "le set advertising enable",
            "connection complete",
            "disconnect complete",
            "command status",
            "command complete",
        )
    )


def is_selected_btmon_event(line, category):
    lowered = line.lower()
    if "mesh device found" in lowered:
        return False
    if category in {"acl_rx", "acl_tx", "connection", "disconnect"}:
        return True
    if category == "status_or_error":
        return is_non_success_status_line(line)
    return "error" in lowered or "fail" in lowered


def is_non_success_status_line(line):
    lowered = line.lower()
    if "error" in lowered or "fail" in lowered:
        return True
    if "status:" not in lowered:
        return False
    return "success" not in lowered and "0x00" not in lowered
