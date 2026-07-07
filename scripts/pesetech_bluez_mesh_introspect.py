#!/usr/bin/env python3
import argparse
import asyncio
import json
import time

from dbus_next import BusType
from dbus_next.aio import MessageBus


DEFAULT_BUS_NAME = "org.bluez.mesh"
DEFAULT_OBJECT_PATH = "/org/bluez/mesh"


async def inspect_mesh(bus_name, object_path):
    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    introspection = await bus.introspect(bus_name, object_path)
    interfaces = sorted(interface.name for interface in introspection.interfaces)
    nodes = sorted(node.name for node in introspection.nodes)
    bus.disconnect()
    return interfaces, nodes


def write_report(path, report):
    if not path:
        return
    with open(path, "w", encoding="utf-8") as output:
        json.dump(report, output, indent=2, sort_keys=True)
        output.write("\n")


def main():
    parser = argparse.ArgumentParser(description="Inspect bluetooth-meshd's D-Bus object for required BlueZ Mesh interfaces.")
    parser.add_argument("--bus-name", default=DEFAULT_BUS_NAME)
    parser.add_argument("--object-path", default=DEFAULT_OBJECT_PATH)
    parser.add_argument("--require", action="append", default=["org.bluez.mesh.Network1"])
    parser.add_argument("--output-json", default="")
    args = parser.parse_args()

    report = {
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "operation": "mesh-dbus-introspection",
        "bus_name": args.bus_name,
        "object_path": args.object_path,
        "required_interfaces": args.require,
        "sent_light_commands": False,
        "published_mqtt": False,
        "provisioned": False,
        "imported": False,
    }

    try:
        interfaces, nodes = asyncio.run(inspect_mesh(args.bus_name, args.object_path))
        report["interfaces"] = interfaces
        report["nodes"] = nodes
        missing = [interface for interface in args.require if interface not in interfaces]
        report["missing_interfaces"] = missing
        report["status"] = "failed" if missing else "passed"
        print(f"bluetooth-meshd D-Bus object {args.object_path} interfaces: {', '.join(interfaces) or '<none>'}")
        if nodes:
            print(f"bluetooth-meshd D-Bus child nodes: {', '.join(nodes)}")
        if missing:
            print(f"Missing required BlueZ Mesh interface(s): {', '.join(missing)}")
            write_report(args.output_json, report)
            return 1
        print("BlueZ Mesh D-Bus interface check passed.")
        write_report(args.output_json, report)
        return 0
    except Exception as exc:
        report["status"] = "failed"
        report["error"] = str(exc)
        print(f"BlueZ Mesh D-Bus interface check failed: {exc}")
        write_report(args.output_json, report)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
