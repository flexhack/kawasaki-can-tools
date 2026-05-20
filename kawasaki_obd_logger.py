#!/usr/bin/env python3
"""Small read-only Kawasaki/KDS OBD Mode 01 logger for SocketCAN."""

from __future__ import annotations

import argparse
import csv
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

try:
    import can
except ImportError:  # Help/setup commands should still work before dependencies are installed.
    can = None  # type: ignore[assignment]


CAN_INTERFACE = "can0"
BITRATE = 500000
REQUEST_ID = 0x7DF
RESPONSE_ID = 0x7E8
MODE_CURRENT_DATA = 0x01
POSITIVE_RESPONSE_MODE_01 = 0x41
DEFAULT_REQUEST_DELAY = 0.10


@dataclass(frozen=True)
class PidDef:
    pid: int
    name: str
    unit: str
    decode: Callable[[bytes], float | str]


def pct(data: bytes) -> float:
    return data[0] * 100.0 / 255.0


def temp_c(data: bytes) -> float:
    return float(data[0] - 40)


def fuel_trim(data: bytes) -> float:
    return (data[0] - 128.0) * 100.0 / 128.0


def pressure_kpa(data: bytes) -> float:
    return float(data[0])


def rpm(data: bytes) -> float:
    return ((data[0] * 256) + data[1]) / 4.0


def speed_kph(data: bytes) -> float:
    return float(data[0])


def timing_deg(data: bytes) -> float:
    return (data[0] / 2.0) - 64.0


def o2_b1s1(data: bytes) -> str:
    voltage = data[0] / 200.0
    trim = fuel_trim(data[1:2])
    status = " maybe_not_ready/open_loop" if len(data) >= 2 and data[0] == 0x00 and data[1] == 0x80 else ""
    return f"voltage={voltage:.3f}V trim={trim:.1f}%{status}"


PIDS: dict[int, PidDef] = {
    0x04: PidDef(0x04, "engine_load", "%", pct),
    0x05: PidDef(0x05, "coolant_temp", "degC", temp_c),
    0x06: PidDef(0x06, "short_term_fuel_trim_b1", "%", fuel_trim),
    0x07: PidDef(0x07, "long_term_fuel_trim_b1", "%", fuel_trim),
    0x0B: PidDef(0x0B, "intake_manifold_pressure", "kPa", pressure_kpa),
    0x0C: PidDef(0x0C, "rpm", "rpm", rpm),
    0x0D: PidDef(0x0D, "vehicle_speed", "km/h", speed_kph),
    0x0E: PidDef(0x0E, "timing_advance", "deg", timing_deg),
    0x0F: PidDef(0x0F, "intake_air_temp", "degC", temp_c),
    0x11: PidDef(0x11, "throttle_position", "%", pct),
    0x14: PidDef(0x14, "o2_b1s1_voltage_trim", "", o2_b1s1),
    0x45: PidDef(0x45, "relative_throttle_position", "%", pct),
    0x4B: PidDef(0x4B, "commanded_throttle_actuator", "%", pct),
}


DEFAULT_LOG_PIDS = [0x04, 0x05, 0x06, 0x07, 0x0B, 0x0C, 0x0D, 0x0E, 0x0F, 0x11, 0x14, 0x45]


PID_NAMES: dict[int, str] = {
    **{pid: definition.name for pid, definition in PIDS.items()},
    0x00: "supported_pids_01_20",
    0x20: "supported_pids_21_40",
    0x40: "supported_pids_41_60",
    0x60: "supported_pids_61_80",
}


def iso_timestamp() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


def bring_up_can(interface: str, bitrate: int) -> None:
    commands = [
        ["sudo", "ip", "link", "set", interface, "down"],
        ["sudo", "ip", "link", "set", interface, "type", "can", "bitrate", str(bitrate)],
        ["sudo", "ip", "link", "set", interface, "up"],
        ["ip", "-details", "link", "show", interface],
    ]
    subprocess.run(commands[0], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for command in commands[1:]:
        subprocess.run(command, check=True)


def require_python_can() -> None:
    if can is None:
        raise SystemExit("Missing dependency: install python-can with `pip install -r requirements.txt`")


def make_request(pid: int) -> can.Message:
    require_python_can()
    data = [0x02, MODE_CURRENT_DATA, pid, 0x00, 0x00, 0x00, 0x00, 0x00]
    return can.Message(arbitration_id=REQUEST_ID, data=data, is_extended_id=False)


def format_bytes(data: bytes) -> str:
    return " ".join(f"{byte:02X}" for byte in data)


def parse_response(message: can.Message, expected_pid: int) -> bytes | None:
    if message.arbitration_id != RESPONSE_ID:
        return None
    data = bytes(message.data)
    if len(data) < 4:
        return None
    payload_len = data[0]
    if payload_len < 3 or len(data) < payload_len + 1:
        return None
    payload = data[1 : payload_len + 1]
    if payload[0] != POSITIVE_RESPONSE_MODE_01 or payload[1] != expected_pid:
        return None
    return payload[2:]


def request_pid(bus: can.BusABC, pid: int, timeout: float) -> bytes | None:
    bus.send(make_request(pid), timeout=timeout)
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        message = bus.recv(timeout=remaining)
        if message is None:
            return None
        if message.arbitration_id == REQUEST_ID:
            continue
        parsed = parse_response(message, pid)
        if parsed is not None:
            return parsed


def request_pid_raw(bus: can.BusABC, pid: int, timeout: float) -> can.Message | None:
    bus.send(make_request(pid), timeout=timeout)
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        message = bus.recv(timeout=remaining)
        if message is None:
            return None
        if message.arbitration_id == REQUEST_ID:
            continue
        if parse_response(message, pid) is not None:
            return message


def decode_pid(pid: int, data: bytes) -> str:
    definition = PIDS[pid]
    value = definition.decode(data)
    if isinstance(value, str):
        return value
    if definition.unit == "rpm":
        return f"{value:.0f}"
    return f"{value:.1f}"


def decode_supported(base_pid: int, data: bytes) -> list[int]:
    if len(data) < 4:
        return []
    supported: list[int] = []
    mask = int.from_bytes(data[:4], byteorder="big")
    for bit in range(32):
        if mask & (1 << (31 - bit)):
            supported.append(base_pid + bit + 1)
    return supported


def query_supported_pids(bus: can.BusABC, timeout: float, request_delay: float) -> set[int]:
    supported: set[int] = set()
    for base_pid in (0x00, 0x20, 0x40, 0x60):
        data = request_pid(bus, base_pid, timeout)
        wait_between_requests(request_delay)
        if data is not None:
            supported.update(decode_supported(base_pid, data))
    return supported


def parse_pid_args(values: list[str]) -> list[int]:
    pids: list[int] = []
    for value in values:
        for part in value.split(","):
            part = part.strip()
            if part:
                pids.append(int(part, 16))
    return pids


def wait_between_requests(delay: float) -> None:
    if delay > 0:
        time.sleep(delay)


def timeout_text(pid: int) -> str:
    if pid == 0x14:
        return "timeout/not_ready"
    return "timeout"


def open_bus(interface: str) -> can.BusABC:
    require_python_can()
    return can.interface.Bus(
        channel=interface,
        interface="socketcan",
        receive_own_messages=False,
    )


def cmd_up(args: argparse.Namespace) -> int:
    bring_up_can(args.interface, args.bitrate)
    return 0


def cmd_supported(args: argparse.Namespace) -> int:
    all_supported: list[int] = []
    with open_bus(args.interface) as bus:
        for base_pid in (0x00, 0x20, 0x40, 0x60):
            data = request_pid(bus, base_pid, args.timeout)
            wait_between_requests(args.request_delay)
            if data is None:
                print(f"01 {base_pid:02X}: timeout")
                continue
            supported = decode_supported(base_pid, data)
            all_supported.extend(supported)
            print(f"01 {base_pid:02X}: {format_bytes(data[:4])}")

    print("\nSupported PIDs:")
    for pid in sorted(set(all_supported)):
        name = PID_NAMES.get(pid, "unknown")
        marker = "*" if pid in PIDS else " "
        print(f"{marker} 01 {pid:02X} {name}")
    print("\n* decoded by this logger")
    return 0


def cmd_log(args: argparse.Namespace) -> int:
    requested_pids = parse_pid_args(args.pids)
    unknown = [pid for pid in requested_pids if pid not in PIDS]
    if unknown:
        names = ", ".join(f"01 {pid:02X}" for pid in unknown)
        raise SystemExit(f"Unsupported logger PID(s): {names}")

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open_bus(args.interface) as bus, output_path.open("a", newline="") as csv_file:
        if not args.include_unsupported:
            ecu_supported = query_supported_pids(bus, args.timeout, args.request_delay)
            requested_pids = [pid for pid in requested_pids if pid in ecu_supported]
            if not requested_pids:
                raise SystemExit("No requested PIDs are reported supported by the ECU.")

        fieldnames = ["timestamp", *[PIDS[pid].name for pid in requested_pids]]
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        if csv_file.tell() == 0:
            writer.writeheader()

        print(f"Logging to {output_path}. Press Ctrl-C to stop.")
        try:
            while True:
                row: dict[str, str] = {"timestamp": iso_timestamp()}
                display: list[str] = [row["timestamp"]]
                for pid in requested_pids:
                    definition = PIDS[pid]
                    data = request_pid(bus, pid, args.timeout)
                    wait_between_requests(args.request_delay)
                    if data is None:
                        text = timeout_text(pid)
                        row[definition.name] = text
                        display.append(f"{definition.name}={text}")
                        continue
                    text = decode_pid(pid, data)
                    row[definition.name] = text
                    suffix = f" {definition.unit}" if definition.unit else ""
                    display.append(f"{definition.name}={text}{suffix}")
                writer.writerow(row)
                csv_file.flush()
                print(" | ".join(display))
                time.sleep(args.interval)
        except KeyboardInterrupt:
            print("\nStopped.")
    return 0


def cmd_raw(args: argparse.Namespace) -> int:
    pids = parse_pid_args(args.pids)
    with open_bus(args.interface) as bus:
        for pid in pids:
            request = make_request(pid)
            response = request_pid_raw(bus, pid, args.timeout)
            wait_between_requests(args.request_delay)
            tx = format_bytes(bytes(request.data))
            if response is None:
                print(f"01 {pid:02X} tx 7DF [{tx}] rx timeout")
                continue
            rx = format_bytes(bytes(response.data))
            print(f"01 {pid:02X} tx 7DF [{tx}] rx {response.arbitration_id:03X} [{rx}]")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only Kawasaki CAN/OBD Mode 01 logger")
    parser.add_argument("--interface", default=CAN_INTERFACE, help="SocketCAN interface")
    parser.add_argument("--timeout", type=float, default=0.5, help="response timeout in seconds")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_request_delay(subparser: argparse.ArgumentParser) -> None:
        subparser.add_argument(
            "--request-delay",
            type=float,
            default=DEFAULT_REQUEST_DELAY,
            help="delay between PID requests in seconds",
        )

    up = subparsers.add_parser("up", help="bring up can0 at 500000 bitrate")
    up.add_argument("--bitrate", type=int, default=BITRATE)
    up.set_defaults(func=cmd_up)

    supported = subparsers.add_parser("supported", help="query and decode supported Mode 01 PIDs")
    add_request_delay(supported)
    supported.set_defaults(func=cmd_supported)

    raw = subparsers.add_parser("raw", help="print raw read-only request/response bytes")
    add_request_delay(raw)
    raw.add_argument("--pids", nargs="+", required=True, help="hex PIDs, comma or space separated")
    raw.set_defaults(func=cmd_raw)

    log = subparsers.add_parser("log", help="print live values and write CSV")
    add_request_delay(log)
    log.add_argument("--output", default="logs/kawasaki_obd.csv", help="CSV output path")
    log.add_argument("--interval", type=float, default=1.0, help="seconds between poll cycles")
    log.add_argument(
        "--include-unsupported",
        action="store_true",
        help="poll requested decoded PIDs even if the ECU support query does not list them",
    )
    log.add_argument(
        "--pids",
        nargs="+",
        default=[f"{pid:02X}" for pid in DEFAULT_LOG_PIDS],
        help="hex Mode 01 PIDs to poll",
    )
    log.set_defaults(func=cmd_log)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return args.func(args)
    except subprocess.CalledProcessError as exc:
        print(f"Command failed: {' '.join(exc.cmd)}", file=sys.stderr)
        return exc.returncode
    except Exception as exc:
        if can is not None and isinstance(exc, can.CanError):
            print(f"CAN error: {exc}", file=sys.stderr)
            return 1
        raise


if __name__ == "__main__":
    raise SystemExit(main())
