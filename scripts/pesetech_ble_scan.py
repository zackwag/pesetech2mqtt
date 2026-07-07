#!/usr/bin/env python3
import argparse
import json
import os
import pty
import re
import select
import signal
import subprocess
import sys
import time
from pathlib import Path


ADDRESS_RE = re.compile(r"\b([0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5})\b")
NAME_RE = re.compile(r"\bname\s+(.+)$", re.IGNORECASE)
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
HEX32_RE = re.compile(r"\b([0-9a-fA-F]{32})\b")
ONLINE_STATUS_SERVICE_UUID = "00010203-0405-0607-0809-0a0b0c0d1a10"
ONLINE_STATUS_CHARACTERISTIC_UUID = "00010203-0405-0607-0809-0a0b0c0d1a11"


def discover_hci_indexes():
    sysfs = Path("/sys/class/bluetooth")
    if not sysfs.exists():
        return []
    indexes = []
    for path in sysfs.glob("hci*"):
        match = re.match(r"hci(\d+)$", path.name)
        if match:
            indexes.append(int(match.group(1)))
    return sorted(indexes)


def mesh_io_indexes(mesh_io):
    mesh_io = mesh_io or ""
    match = re.search(r"hci(\d+)", mesh_io)
    if match:
        return [int(match.group(1))]
    match = re.search(r":(\d+)$", mesh_io)
    if match:
        return [int(match.group(1))]
    return discover_hci_indexes()


def read_available(master_fd, timeout):
    chunks = []
    deadline = time.monotonic() + timeout
    while time.monotonic() <= deadline:
        remaining = max(0.0, deadline - time.monotonic())
        readable, _, _ = select.select([master_fd], [], [], min(0.2, remaining))
        if not readable:
            continue
        try:
            data = os.read(master_fd, 4096)
        except OSError:
            break
        if not data:
            break
        chunks.append(data.decode("utf-8", errors="replace"))
    return "".join(chunks)


def write_command(master_fd, command):
    os.write(master_fd, (command + "\n").encode("utf-8"))


def run_interactive_btmgmt(btmgmt, adapter_index, seconds, mode):
    master_fd, slave_fd = pty.openpty()
    command = [btmgmt, "--index", str(adapter_index)]
    process = subprocess.Popen(
        command,
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        close_fds=True,
        start_new_session=True,
    )
    os.close(slave_fd)
    transcript = [f"$ {' '.join(command)}\n"]
    try:
        transcript.append(read_available(master_fd, 1.0))
        for setup_command in ("info", "power on", "le on"):
            transcript.append(f"\n> {setup_command}\n")
            write_command(master_fd, setup_command)
            transcript.append(read_available(master_fd, 1.0))

        scan_command = f"find {mode}".rstrip()
        transcript.append(f"\n> {scan_command}\n")
        write_command(master_fd, scan_command)
        transcript.append(read_available(master_fd, max(1, seconds)))

        transcript.append("\n> stop-find\n")
        write_command(master_fd, "stop-find")
        transcript.append(read_available(master_fd, 1.5))

        transcript.append("\n> quit\n")
        write_command(master_fd, "quit")
        transcript.append(read_available(master_fd, 1.0))
    finally:
        try:
            os.close(master_fd)
        except OSError:
            pass
        try:
            return_code = process.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except OSError:
                process.terminate()
            try:
                return_code = process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                process.kill()
                return_code = process.wait(timeout=2.0)

    text = "".join(transcript)
    return {"command": command, "scan_command": scan_command, "return_code": return_code, "output": text}


def parse_scan_output(text):
    lines = text.splitlines()
    event_lines = [
        line.strip()
        for line in lines
        if "dev_found:" in line or "Device found:" in line or "hci event" in line.lower()
    ]
    address_matches = [match.group(1).upper() for line in lines for match in ADDRESS_RE.finditer(line)]
    names = sorted(
        {
            match.group(1).strip()
            for line in lines
            for match in [NAME_RE.search(line)]
            if match and match.group(1).strip()
        }
    )
    return {
        "event_lines": event_lines,
        "addresses": sorted(set(address_matches)),
        "names": names,
    }


def strip_ansi(text):
    return ANSI_RE.sub("", text)


def command_exists(path):
    return path and Path(path).exists() and os.access(path, os.X_OK)


def find_first_executable(paths):
    for path in paths:
        if command_exists(path):
            return path
    return ""


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def clean_hex_key(value):
    text = str(value or "").strip().replace(":", "").replace("-", "")
    if re.fullmatch(r"[0-9a-fA-F]{32}", text):
        return text.lower()
    return ""


def load_network_key():
    mesh_path = Path("/share/pesetech_mesh.json")
    mesh = read_json(mesh_path)
    if isinstance(mesh, dict):
        for key in mesh.get("netKeys") or []:
            net_key = clean_hex_key(key.get("key") if isinstance(key, dict) else "")
            if net_key:
                return bytes.fromhex(net_key), str(mesh_path)

    store_path = Path("/data/store.yaml")
    try:
        text = store_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    if text:
        for line in text.splitlines():
            if "network_key" not in line:
                continue
            match = HEX32_RE.search(line)
            if match:
                return bytes.fromhex(match.group(1)), str(store_path)
        match = HEX32_RE.search(text)
        if match:
            return bytes.fromhex(match.group(1)), str(store_path)

    return None, ""


def uuid_to_public_address(uuid_text):
    compact = re.sub(r"[^0-9a-fA-F]", "", str(uuid_text or ""))
    if len(compact) < 12:
        return ""
    raw = bytes.fromhex(compact[:12])
    return ":".join(f"{byte:02X}" for byte in reversed(raw))


def parse_unicast(value):
    text = str(value or "").strip()
    try:
        return int(text, 16)
    except ValueError:
        return None


def online_status_targets():
    mesh_path = Path("/share/pesetech_mesh.json")
    mesh = read_json(mesh_path)
    targets = []
    seen = set()
    if isinstance(mesh, dict):
        for node in mesh.get("nodes") or []:
            if not isinstance(node, dict):
                continue
            uuid_text = node.get("UUID") or node.get("uuid") or ""
            address = uuid_to_public_address(uuid_text)
            unicast_text = str(node.get("unicastAddress") or "").strip()
            unicast = parse_unicast(unicast_text)
            if not address or unicast is None:
                continue
            if node.get("name") == "Provisioner Node" or (node.get("pid") or "").lower() == "0100":
                continue
            key = (address, unicast_text.upper())
            if key in seen:
                continue
            seen.add(key)
            targets.append(
                {
                    "address": address,
                    "address_type": "public",
                    "name": node.get("name") or f"node_{unicast:04x}",
                    "pid": node.get("pid") or "",
                    "unicast": f"{unicast:04x}",
                    "uuid": str(uuid_text),
                    "source": str(mesh_path),
                }
            )

    if targets:
        return targets

    return [
        {
            "address": "A4:C1:38:E7:67:AB",
            "address_type": "public",
            "name": "Skylight B",
            "pid": "0001",
            "unicast": "0005",
            "uuid": "ab67e738-c1a4-1118-5065-736554656368",
            "source": "fallback",
        },
        {
            "address": "A4:C1:38:A1:5A:35",
            "address_type": "public",
            "name": "Skylight A",
            "pid": "0001",
            "unicast": "0802",
            "uuid": "355aa138-c1a4-1118-5065-736554656368",
            "source": "fallback",
        },
    ]


def aes_block(block, key):
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    encryptor = Cipher(algorithms.AES(key), modes.ECB(), backend=default_backend()).encryptor()
    return encryptor.update(bytes(block)) + encryptor.finalize()


def aes_cmac(message, key):
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives.cmac import CMAC
    from cryptography.hazmat.primitives.ciphers import algorithms

    c = CMAC(algorithms.AES(key), backend=default_backend())
    c.update(bytes(message))
    return c.finalize()


def generate_beacon_key(network_key):
    salt = aes_cmac(b"nkbk", bytes(16))
    t = aes_cmac(network_key, salt)
    return aes_cmac(b"id128\x01", t)


def generate_identity_key(network_key):
    salt = aes_cmac(b"nkik", bytes(16))
    t = aes_cmac(network_key, salt)
    return aes_cmac(b"id128\x01", t)


def generate_network_id(network_key):
    salt = aes_cmac(b"smk3", bytes(16))
    t = aes_cmac(network_key, salt)
    return aes_cmac(b"id64\x01", t)[-8:]


def generate_node_identity_hash(identity_key, random_bytes, unicast):
    block = bytes(6) + random_bytes + int(unicast).to_bytes(2, "big")
    return aes_block(block, identity_key)[8:16]


def decrypt_online_status_packet(packet, beacon_key):
    if len(packet) < 7:
        return None, "packet too short"

    iv = packet[:4]
    expected_mic = packet[-2:]
    body_len = len(packet) - 6
    plain = bytearray(packet[4:-2])

    stream_block = bytes(16)
    counter = bytearray(16)
    counter[1:5] = iv
    for index in range(body_len):
        offset = index & 15
        if offset == 0:
            stream_block = aes_block(counter, beacon_key)
            counter[0] = (counter[0] + 1) & 0xFF
        plain[index] ^= stream_block[offset]

    mic_block = bytearray(16)
    mic_block[:4] = iv
    mic_block[4] = body_len & 0xFF
    digest = bytearray(aes_block(mic_block, beacon_key))
    for index, byte in enumerate(plain):
        offset = index & 15
        digest[offset] ^= byte
        if offset == 15 or index == body_len - 1:
            digest = bytearray(aes_block(digest, beacon_key))

    if bytes(digest[:2]) != expected_mic:
        return None, "mic mismatch"

    decrypted = bytearray(packet)
    decrypted[4:-2] = plain
    return bytes(decrypted), ""


def parse_online_status_records(decrypted_packet, target_by_unicast=None):
    if not decrypted_packet or len(decrypted_packet) < 4:
        return []
    if decrypted_packet[0] != 0x62:
        return []

    record_len = decrypted_packet[1] & 0x0F
    status_len = record_len - 3
    if status_len <= 0:
        return []

    target_by_unicast = target_by_unicast or {}
    records = []
    offset = 4
    while offset + record_len <= len(decrypted_packet):
        address = decrypted_packet[offset] | ((decrypted_packet[offset + 1] & 0x7F) << 8)
        sn = decrypted_packet[offset + 2]
        status = bytes(decrypted_packet[offset + 3 : offset + record_len])
        offset += record_len
        if address == 0:
            break
        target = target_by_unicast.get(f"{address:04x}", {})
        record = {
            "unicast": f"{address:04x}",
            "name": target.get("name") or "",
            "sn": sn,
            "status_hex": status.hex(),
            "status": list(status),
        }
        if status:
            record["brightness_raw"] = status[0]
            record["on"] = status[0] != 0 and sn != 0
        if len(status) > 1:
            record["color_temp_raw"] = status[1]
        records.append(record)
    return records


def parse_btgatt_value_handle(transcript):
    clean = strip_ansi(transcript).lower()
    for line in clean.splitlines():
        if ONLINE_STATUS_CHARACTERISTIC_UUID not in line or "charac" not in line:
            continue
        match = re.search(r"\bvalue:\s*(0x[0-9a-f]+)", line)
        if match:
            return match.group(1)
    return ""


def extract_btgatt_notification_packets(transcript):
    clean = strip_ansi(transcript)
    packets = []
    pattern = re.compile(
        r"Handle Value Not/Ind:\s*0x[0-9a-fA-F]+\s*-\s*\((\d+)\s+bytes\):\s*([0-9a-fA-F ]+)"
    )
    for match in pattern.finditer(clean):
        expected_len = int(match.group(1))
        payload_hex = re.sub(r"\s+", "", match.group(2))
        if len(payload_hex) != expected_len * 2:
            continue
        try:
            packets.append(bytes.fromhex(payload_hex))
        except ValueError:
            continue
    return packets


def decode_online_packets(packets, beacon_key, target_by_unicast):
    decoded = []
    failed = []
    for index, packet in enumerate(packets):
        decrypted, error = decrypt_online_status_packet(packet, beacon_key)
        if error:
            failed.append({"index": index, "encrypted_hex": packet.hex(), "error": error})
            continue
        records = parse_online_status_records(decrypted, target_by_unicast)
        decoded.append(
            {
                "index": index,
                "encrypted_hex": packet.hex(),
                "decrypted_hex": decrypted.hex(),
                "records": records,
            }
        )
    return decoded, failed


def format_online_status_summary(report):
    lines = [
        "Online-status summary: "
        f"status={report.get('status')} "
        f"message={report.get('message')} "
        f"records={len(report.get('records') or [])} "
        f"decoded_packets={len(report.get('decoded_packets') or [])} "
        f"failed_packets={len(report.get('failed_packets') or [])} "
        f"attempts={len(report.get('attempts') or [])}"
    ]
    for attempt in report.get("attempts") or []:
        target = attempt.get("target") or {}
        lines.append(
            "  online-status attempt: "
            f"{attempt.get('adapter')} round={attempt.get('round')} "
            f"target={target.get('name') or target.get('unicast')} "
            f"addr={target.get('address')} "
            f"rc={attempt.get('return_code')} "
            f"value_handle={attempt.get('value_handle') or '-'} "
            f"packets={attempt.get('packet_count')} "
            f"decoded={attempt.get('decoded_packet_count')} "
            f"failed={attempt.get('failed_packet_count')}"
        )
    if report.get("records"):
        lines.append(f"  online-status records: {json.dumps(report['records'], sort_keys=True)}")
    return "\n".join(lines) + "\n"


def run_noninteractive(command, timeout=8):
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"command": command, "return_code": None, "output": str(exc)}
    output = (result.stdout or "") + (result.stderr or "")
    return {"command": command, "return_code": result.returncode, "output": output}


def find_btmon():
    return find_first_executable(
        [
            "/usr/bin/btmon",
            "/usr/local/bin/btmon",
            "/opt/build/bluez-5.66/monitor/btmon",
        ]
    )


def parse_ad_structures(data):
    records = []
    index = 0
    while index < len(data):
        length = data[index]
        if length == 0:
            break
        end = index + 1 + length
        if end > len(data) or length < 1:
            break
        ad_type = data[index + 1]
        payload = data[index + 2 : end]
        records.append((ad_type, payload))
        index = end
    return records


def mesh_proxy_service_data(data):
    values = []
    for ad_type, payload in parse_ad_structures(data):
        if ad_type != 0x16 or len(payload) < 2:
            continue
        uuid16 = payload[0] | (payload[1] << 8)
        if uuid16 == 0x1828:
            values.append(payload[2:])
    return values


def parse_btmon_advertisements(text):
    entries = []
    current = None
    collecting_data = False

    def flush():
        nonlocal current, collecting_data
        if current and current.get("address") and current.get("data_hex"):
            try:
                current["data"] = bytes.fromhex("".join(current["data_hex"]))
                entries.append(current)
            except ValueError:
                pass
        current = None
        collecting_data = False

    for line in text.splitlines():
        stripped = line.strip()
        if re.match(r"Entry\s+\d+", stripped):
            flush()
            current = {"data_hex": []}
            continue
        if current is None:
            continue
        if stripped.startswith("Address type:"):
            current["address_type_label"] = stripped.split(":", 1)[1].strip()
            label = current["address_type_label"].lower()
            current["address_type"] = "random" if "random" in label else "public"
            continue
        if stripped.startswith("Address:"):
            match = ADDRESS_RE.search(stripped)
            if match:
                current["address"] = match.group(1).upper()
            continue
        if stripped.startswith("Data length:"):
            collecting_data = True
            continue
        if collecting_data:
            tokens = []
            for token in stripped.split():
                if re.fullmatch(r"[0-9a-fA-F]{2}", token):
                    tokens.append(token)
                else:
                    break
            if tokens:
                current["data_hex"].extend(tokens)
            else:
                collecting_data = False

    flush()
    return entries


def run_btmon_proxy_scan(btmgmt, adapter_index, seconds):
    btmon = find_btmon()
    if not btmon:
        return {"btmon": "", "btmgmt": None, "output": "btmon was not found\n", "advertisements": []}

    process = subprocess.Popen(
        [btmon, "-i", str(adapter_index)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        close_fds=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    try:
        time.sleep(0.5)
        scan = run_interactive_btmgmt(btmgmt, adapter_index, max(4, seconds), "-l")
    finally:
        process.terminate()
        try:
            output, _ = process.communicate(timeout=3.0)
        except subprocess.TimeoutExpired:
            process.kill()
            output, _ = process.communicate(timeout=2.0)

    return {
        "btmon": btmon,
        "btmgmt": scan,
        "output": output,
        "advertisements": parse_btmon_advertisements(output),
    }


def resolve_mesh_proxy_targets(btmgmt, indexes, seconds, network_key, targets):
    network_id = generate_network_id(network_key)
    identity_key = generate_identity_key(network_key)
    unicast_targets = {target["unicast"].lower(): target for target in targets}
    unicast_ints = [int(unicast, 16) for unicast in unicast_targets]
    resolved = []
    seen = set()
    raw_parts = ["\n===== mesh proxy advertisement resolver =====\n"]
    capture_summaries = []

    for adapter_index in indexes:
        capture = run_btmon_proxy_scan(btmgmt, adapter_index, max(6, min(12, seconds)))
        raw_parts.append(f"\n----- hci{adapter_index} btmon proxy scan -----\n")
        raw_parts.append(f"btmon: {capture['btmon'] or 'missing'}\n")
        if capture.get("btmgmt"):
            raw_parts.append(capture["btmgmt"]["output"])
        raw_parts.append(capture["output"])
        advertisements = capture.get("advertisements") or []
        capture_summaries.append(
            {
                "adapter": f"hci{adapter_index}",
                "btmon": capture["btmon"],
                "advertisement_count": len(advertisements),
            }
        )
        for adv in advertisements:
            for service_data in mesh_proxy_service_data(adv.get("data") or b""):
                resolved_item = None
                if len(service_data) >= 9 and service_data[0] == 0 and service_data[1:9] == network_id:
                    resolved_item = {
                        "identity_type": "network_id",
                        "identity_unicast": "",
                        "name": "mesh_proxy_network_id",
                    }
                elif len(service_data) >= 17 and service_data[0] == 1:
                    advertised_hash = service_data[1:9]
                    random_bytes = service_data[9:17]
                    for unicast in unicast_ints:
                        if advertised_hash == generate_node_identity_hash(identity_key, random_bytes, unicast):
                            base = unicast_targets[f"{unicast:04x}"]
                            resolved_item = {
                                "identity_type": "node_identity",
                                "identity_unicast": f"{unicast:04x}",
                                "name": base.get("name") or f"node_{unicast:04x}",
                                "uuid": base.get("uuid") or "",
                                "pid": base.get("pid") or "",
                            }
                            break
                if not resolved_item:
                    continue
                key = (adv["address"], adv.get("address_type") or "public", resolved_item.get("identity_unicast") or "")
                if key in seen:
                    continue
                seen.add(key)
                resolved.append(
                    {
                        **resolved_item,
                        "address": adv["address"],
                        "address_type": adv.get("address_type") or "public",
                        "source": "btmon_mesh_proxy_advertisement",
                        "service_data_hex": service_data.hex(),
                    }
                )

    resolved.sort(key=lambda item: (item.get("identity_type") != "node_identity", item.get("identity_unicast") or "ffff"))
    raw_parts.append(f"\nResolved mesh proxy targets: {json.dumps(resolved, sort_keys=True)}\n")
    return resolved, capture_summaries, "".join(raw_parts)


def prepare_online_adapter(btmgmt, adapter_index):
    transcript = []
    for action in (("power", "on"), ("le", "on"), ("connectable", "on")):
        command = [btmgmt, "--index", str(adapter_index), *action]
        result = run_noninteractive(command, timeout=8)
        transcript.append(f"$ {' '.join(command)}\n{result['output']}\n")
    return "".join(transcript)


def run_btgatt_online_status_probe(btgatt, adapter_index, target, listen_seconds, connect_timeout=10.0):
    master_fd, slave_fd = pty.openpty()
    command = [
        btgatt,
        "-v",
        "-i",
        f"hci{adapter_index}",
        "-d",
        target["address"],
        "-t",
        target.get("address_type") or "public",
        "-m",
        "247",
    ]
    process = subprocess.Popen(
        command,
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        close_fds=True,
        start_new_session=True,
    )
    os.close(slave_fd)
    transcript = [f"$ {' '.join(command)}\n"]
    value_handle = ""
    try:
        # Wait for the raw L2CAP connection and automatic GATT discovery. The
        # earlier probe sent commands while btgatt-client was still connecting.
        initial = read_available(master_fd, connect_timeout)
        transcript.append(initial)
        text = "".join(transcript)
        if "Failed to connect" not in text and "GATT discovery procedures complete" not in text:
            transcript.append(read_available(master_fd, max(2.0, connect_timeout / 2)))

        text = "".join(transcript)
        value_handle = parse_btgatt_value_handle(text)
        for service_command in (
            "services",
            f"services -u {ONLINE_STATUS_SERVICE_UUID}",
        ):
            if value_handle:
                break
            transcript.append(f"\n> {service_command}\n")
            write_command(master_fd, service_command)
            transcript.append(read_available(master_fd, 2.0))
            value_handle = parse_btgatt_value_handle("".join(transcript))

        if value_handle:
            transcript.append(f"\n> write-value -w {value_handle} 01\n")
            write_command(master_fd, f"write-value -w {value_handle} 01")
            transcript.append(read_available(master_fd, 2.0))
            transcript.append("\n# listening after online-status enable write, before CCC notify registration\n")
            transcript.append(read_available(master_fd, min(3.0, listen_seconds)))
            for setup_command in (
                f"register-notify {value_handle}",
                f"write-value -w {value_handle} 01",
            ):
                transcript.append(f"\n> {setup_command}\n")
                write_command(master_fd, setup_command)
                transcript.append(read_available(master_fd, 2.0))
            transcript.append(f"\n# listening for online-status notifications for {listen_seconds}s\n")
            transcript.append(read_available(master_fd, listen_seconds))
            transcript.append("\n> unregister-notify 1\n")
            write_command(master_fd, "unregister-notify 1")
            transcript.append(read_available(master_fd, 1.0))
        else:
            transcript.append("\n# online-status characteristic value handle was not found\n")
    finally:
        try:
            os.close(master_fd)
        except OSError:
            pass
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except OSError:
            process.terminate()
        try:
            return_code = process.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            process.kill()
            return_code = process.wait(timeout=2.0)

    output = "".join(transcript)
    return {
        "command": command,
        "return_code": return_code,
        "value_handle": value_handle,
        "packets": extract_btgatt_notification_packets(output),
        "output": output,
    }


def run_online_status_probe(btmgmt, indexes, seconds):
    started_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    raw_parts = [
        "\n\n===== Telink online-status GATT probe =====\n",
        f"started_at: {started_at}\n",
    ]
    net_key, net_key_source = load_network_key()
    targets = online_status_targets()
    target_by_unicast = {target["unicast"].lower(): target for target in targets}
    btgatt = find_first_executable(
        [
            "/opt/build/bluez-5.66/tools/btgatt-client",
            "/usr/bin/btgatt-client",
            "/usr/local/bin/btgatt-client",
        ]
    )
    raw_parts.append(f"net_key_source: {net_key_source or 'missing'}\n")
    raw_parts.append(f"targets: {json.dumps(targets, sort_keys=True)}\n")
    raw_parts.append(f"btgatt-client: {btgatt or 'missing'}\n")

    report = {
        "started_at": started_at,
        "net_key_source": net_key_source,
        "targets": targets,
        "resolved_targets": [],
        "proxy_scan_captures": [],
        "btgatt_client": btgatt,
        "attempts": [],
        "decoded_packets": [],
        "failed_packets": [],
        "records": [],
        "status": "skipped",
        "message": "",
    }

    if not net_key:
        report["message"] = "network key was not found"
        raw_parts.append("# network key was not found; cannot decrypt online-status packets\n")
        return report, "".join(raw_parts)
    if not btgatt:
        report["message"] = "btgatt-client was not found"
        raw_parts.append("# btgatt-client was not found; cannot subscribe to online-status characteristic\n")
        return report, "".join(raw_parts)
    if not targets:
        report["message"] = "no target BLE addresses were found"
        raw_parts.append("# no target BLE addresses were found\n")
        return report, "".join(raw_parts)

    try:
        beacon_key = generate_beacon_key(net_key)
    except Exception as exc:
        report["status"] = "failed"
        report["message"] = f"beacon key generation failed: {exc}"
        raw_parts.append(f"# beacon key generation failed: {exc}\n")
        return report, "".join(raw_parts)

    proxy_scan_rounds = max(1, min(4, seconds // 8))
    resolved_targets = []
    for proxy_round in range(1, proxy_scan_rounds + 1):
        try:
            round_targets, proxy_captures, proxy_raw = resolve_mesh_proxy_targets(btmgmt, indexes, seconds, net_key, targets)
            report["proxy_scan_captures"].extend(
                {"round": proxy_round, **capture} for capture in proxy_captures
            )
            raw_parts.append(f"\n===== mesh proxy resolver round {proxy_round}/{proxy_scan_rounds} =====\n")
            raw_parts.append(proxy_raw)
            for target in round_targets:
                key = (target.get("address"), target.get("address_type"), target.get("identity_unicast"))
                if key not in {(item.get("address"), item.get("address_type"), item.get("identity_unicast")) for item in resolved_targets}:
                    resolved_targets.append(target)
            if resolved_targets:
                break
        except Exception as exc:
            raw_parts.append(f"\n# mesh proxy advertisement resolver round {proxy_round} failed: {exc}\n")
            report["proxy_scan_captures"].append({"round": proxy_round, "error": str(exc)})
    report["resolved_targets"] = resolved_targets
    if resolved_targets:
        targets = resolved_targets[:4] + targets

    probe_rounds = max(2, min(6, seconds // 6))
    listen_seconds = max(4, min(10, seconds // 2))
    connect_timeout = max(6.0, min(12.0, seconds / 2))
    for adapter_index in indexes:
        raw_parts.append(f"\n===== online-status hci{adapter_index} prepare =====\n")
        raw_parts.append(prepare_online_adapter(btmgmt, adapter_index))
        for probe_round in range(1, probe_rounds + 1):
            for target in targets:
                raw_parts.append(
                    f"\n===== online-status hci{adapter_index} round {probe_round}/{probe_rounds} "
                    f"{target['name']} {target['address']} =====\n"
                )
                try:
                    attempt = run_btgatt_online_status_probe(
                        btgatt,
                        adapter_index,
                        target,
                        listen_seconds,
                        connect_timeout=connect_timeout,
                    )
                except Exception as exc:
                    attempt = {
                        "command": [btgatt, "-i", f"hci{adapter_index}", "-d", target["address"]],
                        "return_code": None,
                        "value_handle": "",
                        "packets": [],
                        "output": f"probe exception: {exc}\n",
                    }
                raw_parts.append(attempt["output"])
                decoded, failed = decode_online_packets(attempt["packets"], beacon_key, target_by_unicast)
                attempt_report = {
                    "adapter": f"hci{adapter_index}",
                    "round": probe_round,
                    "target": {key: target[key] for key in ("address", "name", "unicast", "uuid", "pid") if key in target},
                    "return_code": attempt["return_code"],
                    "value_handle": attempt["value_handle"],
                    "packet_count": len(attempt["packets"]),
                    "decoded_packet_count": len(decoded),
                    "failed_packet_count": len(failed),
                }
                report["attempts"].append(attempt_report)
                report["decoded_packets"].extend(decoded)
                report["failed_packets"].extend(failed)
                for packet in decoded:
                    report["records"].extend(packet["records"])
                if report["records"]:
                    break
            if report["records"]:
                break
        if report["records"]:
            break

    if report["records"]:
        report["status"] = "passed"
        report["message"] = f"decrypted {len(report['decoded_packets'])} online-status packet(s)"
    elif report["failed_packets"]:
        report["status"] = "failed"
        report["message"] = "captured online-status notification(s), but decrypt failed"
    else:
        report["status"] = "failed"
        report["message"] = "no online-status notifications were captured"

    raw_parts.append(
        "\nOnline-status decrypt result: "
        f"{report['status']} - {report['message']} - records={json.dumps(report['records'], sort_keys=True)}\n"
    )
    return report, "".join(raw_parts)


def main():
    parser = argparse.ArgumentParser(description="Run a no-motion BLE advertisement scan through an interactive btmgmt PTY.")
    parser.add_argument("--btmgmt", required=True)
    parser.add_argument("--mesh-io", default="")
    parser.add_argument("--seconds", type=int, default=20)
    parser.add_argument("--raw-output", required=True)
    parser.add_argument("--report-output", required=True)
    parser.add_argument("--mode", action="append", default=[], help="btmgmt find mode suffix, for example -l or empty string.")
    args = parser.parse_args()

    indexes = mesh_io_indexes(args.mesh_io)
    modes = args.mode or ["-l", ""]
    started_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    raw_parts = [
        f"BLE advertisement scan for {args.seconds}s per mode.\n",
        "This operation drives btmgmt through a pseudo-terminal so asynchronous scan events are visible.\n",
        f"btmgmt: {args.btmgmt}\n",
        f"mesh_io: {args.mesh_io}\n",
        f"hci indexes: {indexes}\n",
        f"modes: {modes}\n",
    ]
    adapter_reports = []
    all_event_lines = []
    all_addresses = set()
    all_names = set()
    failed = False

    if not indexes:
        raw_parts.append("No /sys/class/bluetooth/hci* adapter was visible to the add-on.\n")
        failed = True

    for adapter_index in indexes:
        adapter_report = {"hci": f"hci{adapter_index}", "modes": []}
        raw_parts.append(f"\n===== hci{adapter_index} =====\n")
        for mode in modes:
            mode_label = mode or "default"
            raw_parts.append(f"\n----- find mode: {mode_label} -----\n")
            result = run_interactive_btmgmt(args.btmgmt, adapter_index, args.seconds, mode)
            parsed = parse_scan_output(result["output"])
            raw_parts.append(result["output"])
            mode_report = {
                "mode": mode_label,
                "return_code": result["return_code"],
                "event_count": len(parsed["event_lines"]),
                "address_count": len(parsed["addresses"]),
                "addresses": parsed["addresses"][:50],
                "names": parsed["names"][:50],
            }
            adapter_report["modes"].append(mode_report)
            all_event_lines.extend(parsed["event_lines"])
            all_addresses.update(parsed["addresses"])
            all_names.update(parsed["names"])
            if result["return_code"] not in (0, None):
                failed = True
        adapter_reports.append(adapter_report)

    online_status_report, online_status_raw = run_online_status_probe(args.btmgmt, indexes, args.seconds)
    raw_parts.append(online_status_raw)

    report = {
        "created_at": started_at,
        "operation": "ble-scan",
        "status": "failed" if failed else "passed",
        "exit_code": 1 if failed else 0,
        "message": "BLE advertisement scan completed" if not failed else "BLE advertisement scan failed",
        "seconds": args.seconds,
        "mesh_io": args.mesh_io,
        "hci_indexes": indexes,
        "scan_modes": [mode or "default" for mode in modes],
        "raw_log": args.raw_output,
        "dev_found_count": len(all_event_lines),
        "unique_address_count": len(all_addresses),
        "addresses": sorted(all_addresses)[:50],
        "names": sorted(all_names)[:50],
        "adapter_reports": adapter_reports,
        "online_status_probe": online_status_report,
        "sent_light_commands": False,
        "published_mqtt": False,
        "started_bluetooth_meshd": False,
        "provisioned": False,
        "imported": False,
    }

    online_status_summary = format_online_status_summary(online_status_report)
    Path(args.raw_output).write_text(online_status_summary + "\n" + "".join(raw_parts), encoding="utf-8")
    Path(args.report_output).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        "BLE scan completed: "
        f"{report['dev_found_count']} dev_found event(s), "
        f"{report['unique_address_count']} unique address(es), "
        f"names={report['names']}; "
        f"online_status={online_status_report['status']} "
        f"records={len(online_status_report.get('records') or [])}"
    )
    print(online_status_summary, end="")
    return report["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
