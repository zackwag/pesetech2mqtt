import asyncio
import json
import logging
import os
import time

from uuid import UUID

from . import Module


class ScannerModule(Module):
    """
    Handle all scan related tasks
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self._unprovisioned = set()
        self.report_path = ""
        self.text_path = ""
        self.started_at = None

    def initialize(self, app, store, config):
        super().initialize(app, store, config)
        self.report_path = os.environ.get("PESETECH_MESH_SCAN_REPORT", "")
        self.text_path = os.environ.get("PESETECH_MESH_SCAN_TEXT", "")

    def setup_cli(self, parser):
        parser.add_argument("--seconds", type=int, default=10)
        parser.add_argument("--repeat", type=int, default=1)
        parser.add_argument("--stop-on-found", action="store_true")

    def _scan_result(self, rssi, data, options):
        """
        The method is called from the bluetooth-meshd daemon when a
        unique UUID has been seen during UnprovisionedScan() for
        unprovsioned devices.
        """

        try:
            uuid = UUID(bytes=data[:16])
            self._unprovisioned.add(uuid)
            logging.info(f"Found unprovisioned node: {uuid}")
        except:
            logging.exception("Failed to retrieve UUID")

    async def handle_cli(self, args):
        seconds = max(1, int(args.seconds))
        repeat = max(1, int(args.repeat))
        self.started_at = time.time()
        try:
            await self.scan(seconds=seconds, repeat=repeat, stop_on_found=args.stop_on_found)
        except Exception as exc:
            self.write_reports("failed", str(exc), seconds=seconds, repeat=repeat)
            raise

        # print user friendly results
        self.print_results()
        self.write_reports("passed", seconds=seconds, repeat=repeat)
        print(f"Mesh scan completed: found {len(self._unprovisioned)} unprovisioned node(s).", flush=True)

    def print_results(self):
        print(f"\nFound {len(self._unprovisioned)} nodes:")
        for uuid in sorted(self._unprovisioned, key=str):
            print(f"\t{uuid}")

    def report_payload(self, status, error="", *, seconds=10, repeat=1):
        return {
            "operation": "scan",
            "status": status,
            "error": error,
            "started_at": self.started_at,
            "finished_at": time.time(),
            "scan_seconds": seconds,
            "scan_repeat": repeat,
            "found_count": len(self._unprovisioned),
            "unprovisioned_uuids": [str(uuid) for uuid in sorted(self._unprovisioned, key=str)],
            "provisioned": False,
            "imported": False,
            "published_mqtt": False,
            "sent_light_commands": False,
        }

    def write_reports(self, status, error="", *, seconds=10, repeat=1):
        payload = self.report_payload(status, error, seconds=seconds, repeat=repeat)
        if self.report_path:
            with open(self.report_path, "w", encoding="utf-8") as report:
                json.dump(payload, report, indent=2, sort_keys=True)
                report.write("\n")
        if self.text_path:
            with open(self.text_path, "w", encoding="utf-8") as report:
                report.write(f"operation: scan\n")
                report.write(f"status: {status}\n")
                report.write(f"Mesh scan completed: found {payload['found_count']} unprovisioned node(s).\n")
                report.write(f"scan_seconds: {payload['scan_seconds']}\n")
                report.write(f"scan_repeat: {payload['scan_repeat']}\n")
                report.write(f"found_count: {payload['found_count']}\n")
                if error:
                    report.write(f"error: {error}\n")
                report.write("unprovisioned_uuids:\n")
                for uuid in payload["unprovisioned_uuids"]:
                    report.write(f"  - {uuid}\n")

    async def scan(self, *, seconds=10, repeat=1, stop_on_found=False):
        logging.info(
            "Scanning for unprovisioned devices: %s second window(s), repeat=%s",
            seconds,
            repeat,
        )

        for attempt in range(1, repeat + 1):
            logging.info("Starting unprovisioned scan attempt %s/%s", attempt, repeat)
            await self.app.management_interface.unprovisioned_scan(seconds=seconds)
            await asyncio.sleep(float(seconds))
            if stop_on_found and self._unprovisioned:
                logging.info("Stopping scan loop after finding %s node(s)", len(self._unprovisioned))
                break
