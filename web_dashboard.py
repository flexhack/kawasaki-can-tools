#!/usr/bin/env python3
"""Local read-only Kawasaki OBD web dashboard."""

from __future__ import annotations

import argparse
import csv
import shutil
import subprocess
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, render_template, send_file

import kawasaki_obd_logger as obd


HOST = "0.0.0.0"
PORT = 8080
BITRATE = 500000
STALE_AFTER_SECONDS = 3.0
LOG_DIR = Path("logs")
POLL_PIDS = [0x0C, 0x0D, 0x05, 0x0F, 0x04, 0x0B, 0x11, 0x45, 0x0E, 0x06, 0x07, 0x14]
SUPPORTED_QUERY_PIDS = [0x00, 0x20, 0x40, 0x60]

FIELD_ORDER = [
    "rpm",
    "speed_kph",
    "speed_mph",
    "coolant_temp",
    "intake_air_temp",
    "engine_load",
    "intake_manifold_pressure",
    "throttle_position",
    "relative_throttle_position",
    "timing_advance",
    "short_term_fuel_trim_b1",
    "long_term_fuel_trim_b1",
    "o2_b1s1_voltage",
    "o2_b1s1_trim",
]

FIELD_META = {
    "rpm": {"label": "RPM", "unit": "rpm", "digits": 0},
    "speed_kph": {"label": "Speed", "unit": "km/h", "digits": 0},
    "speed_mph": {"label": "Speed", "unit": "mph", "digits": 0},
    "coolant_temp": {"label": "Coolant", "unit": "degC", "digits": 0},
    "intake_air_temp": {"label": "Intake Air", "unit": "degC", "digits": 0},
    "engine_load": {"label": "Engine Load", "unit": "%", "digits": 1},
    "intake_manifold_pressure": {"label": "MAP", "unit": "kPa", "digits": 0},
    "throttle_position": {"label": "Throttle", "unit": "%", "digits": 1},
    "relative_throttle_position": {"label": "Rel Throttle", "unit": "%", "digits": 1},
    "timing_advance": {"label": "Timing", "unit": "deg", "digits": 1},
    "short_term_fuel_trim_b1": {"label": "STFT B1", "unit": "%", "digits": 1},
    "long_term_fuel_trim_b1": {"label": "LTFT B1", "unit": "%", "digits": 1},
    "o2_b1s1_voltage": {"label": "O2 B1S1", "unit": "V", "digits": 3},
    "o2_b1s1_trim": {"label": "O2 Trim", "unit": "%", "digits": 1},
}


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


def hx(data: bytes | list[int]) -> str:
    return " ".join(f"{byte:02X}" for byte in data)


def ascii_clean(data: bytes) -> str:
    chars = []
    for byte in data:
        if 32 <= byte <= 126:
            chars.append(chr(byte))
        elif byte in (0x00, 0x55):
            continue
        else:
            chars.append(".")
    return "".join(chars).strip()


def mask_vin(vin: str) -> str:
    return vin if len(vin) <= 11 else vin[:11] + "****"


class DashboardState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.bus_lock = threading.Lock()
        self.values = {
            key: {"value": None, "unit": FIELD_META[key]["unit"], "status": "stale", "updated_at": None}
            for key in FIELD_ORDER
        }
        self.status: dict[str, Any] = {
            "can_status": "starting",
            "last_update": None,
            "polling_active": False,
            "message": "Starting ECU polling",
            "pid_timeout_count": 0,
        }
        self.ecu_info: dict[str, Any] = {
            "vin": None,
            "vin_masked": None,
            "calibration_id": "49245-2210",
            "cvn": "000096AA",
            "can_bitrate": BITRATE,
            "request_id": "0x7DF",
            "response_id": "0x7E8",
        }
        self.supported_pids: dict[str, Any] = {"raw": {}, "decoded": []}
        self.dtc: dict[str, Any] = {"stored": None, "pending": None, "updated_at": None}
        self.raw_frames: deque[dict[str, Any]] = deque(maxlen=120)
        self.logging: dict[str, Any] = {"active": False, "file": None}
        self.csv_file = None
        self.csv_writer = None

    def set_status(self, can_status: str, polling_active: bool, message: str) -> None:
        with self.lock:
            self.status.update({"can_status": can_status, "polling_active": polling_active, "message": message})

    def set_value(self, key: str, value: float, status: str = "fresh") -> None:
        timestamp = now_iso()
        with self.lock:
            self.values[key].update({"value": value, "status": status, "updated_at": timestamp})
            self.status["last_update"] = timestamp

    def mark_timeout(self, keys: list[str]) -> None:
        with self.lock:
            self.status["pid_timeout_count"] += 1
            for key in keys:
                self.values[key]["status"] = "timeout" if self.values[key]["value"] is None else "stale"

    def add_frame(self, direction: str, arbitration_id: int, data: bytes | list[int]) -> None:
        if arbitration_id not in (obd.REQUEST_ID, obd.RESPONSE_ID):
            return
        with self.lock:
            self.raw_frames.append(
                {"time": now_iso(), "direction": direction, "id": f"0x{arbitration_id:03X}", "data": hx(data)}
            )

    def clear_frames(self) -> None:
        with self.lock:
            self.raw_frames.clear()

    def snapshot_values(self) -> dict[str, Any]:
        with self.lock:
            values = {key: dict(value) for key, value in self.values.items()}
        now = time.time()
        for value in values.values():
            updated_at = value["updated_at"]
            if value["status"] == "fresh" and updated_at is not None:
                try:
                    age = now - datetime.fromisoformat(updated_at).timestamp()
                except ValueError:
                    age = 999.0
                if age > STALE_AFTER_SECONDS:
                    value["status"] = "stale"
        return {"fields": values, "order": FIELD_ORDER, "meta": FIELD_META}

    def snapshot_status(self) -> dict[str, Any]:
        with self.lock:
            return dict(self.status)

    def snapshot_logging(self) -> dict[str, Any]:
        with self.lock:
            return dict(self.logging)

    def log_row(self) -> None:
        with self.lock:
            if not self.csv_writer or not self.csv_file:
                return
            row = {"timestamp": now_iso()}
            for key in FIELD_ORDER:
                row[key] = self.values[key]["value"]
            self.csv_writer.writerow(row)
            self.csv_file.flush()

    def start_log(self) -> dict[str, Any]:
        LOG_DIR.mkdir(exist_ok=True)
        with self.lock:
            if self.csv_writer:
                return dict(self.logging)
            path = LOG_DIR / f"web_dashboard_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
            self.csv_file = path.open("w", newline="")
            self.csv_writer = csv.DictWriter(self.csv_file, fieldnames=["timestamp", *FIELD_ORDER])
            self.csv_writer.writeheader()
            self.logging = {"active": True, "file": str(path)}
            return dict(self.logging)

    def stop_log(self) -> dict[str, Any]:
        with self.lock:
            if self.csv_file:
                self.csv_file.close()
            self.csv_file = None
            self.csv_writer = None
            self.logging["active"] = False
            return dict(self.logging)


state = DashboardState()
app = Flask(__name__)


def send_request(bus: Any, arb_id: int, payload: list[int]) -> list[int]:
    request = [len(payload), *payload, *([0x00] * (7 - len(payload)))]
    bus.send(obd.can.Message(arbitration_id=arb_id, data=request, is_extended_id=False))
    state.add_frame("tx", arb_id, request)
    return request


def drain_rx(bus: Any) -> None:
    deadline = time.time() + 0.12
    while time.time() < deadline:
        msg = bus.recv(timeout=0.01)
        if msg and msg.arbitration_id in (obd.REQUEST_ID, obd.RESPONSE_ID):
            state.add_frame("drain", msg.arbitration_id, bytes(msg.data))


def request_mode01(bus: Any, pid: int, timeout: float) -> bytes | None:
    drain_rx(bus)
    send_request(bus, obd.REQUEST_ID, [0x01, pid])
    deadline = time.time() + timeout
    while time.time() < deadline:
        msg = bus.recv(timeout=0.05)
        if msg is None:
            continue
        if msg.arbitration_id in (obd.REQUEST_ID, obd.RESPONSE_ID):
            state.add_frame("rx", msg.arbitration_id, bytes(msg.data))
        if msg.arbitration_id == obd.REQUEST_ID:
            continue
        parsed = obd.parse_response(msg, pid)
        if parsed is not None:
            return parsed
    return None


def read_obd_service(bus: Any, service: int, positive_service: int, timeout: float) -> bytes | None:
    drain_rx(bus)
    send_request(bus, obd.REQUEST_ID, [service])
    deadline = time.time() + timeout
    while time.time() < deadline:
        msg = bus.recv(timeout=0.05)
        if msg is None:
            continue
        if msg.arbitration_id in (obd.REQUEST_ID, obd.RESPONSE_ID):
            state.add_frame("rx", msg.arbitration_id, bytes(msg.data))
        if msg.arbitration_id != obd.RESPONSE_ID:
            continue
        data = bytes(msg.data)
        if len(data) >= 2 and (data[0] & 0xF0) == 0x00:
            payload = data[1 : 1 + (data[0] & 0x0F)]
            if payload and payload[0] == positive_service:
                return payload
    return None


def read_mode09_pid(bus: Any, pid: int, timeout: float) -> bytes | None:
    drain_rx(bus)
    send_request(bus, obd.REQUEST_ID, [0x09, pid])
    deadline = time.time() + timeout
    payload = bytearray()
    expected_len = None
    while time.time() < deadline:
        msg = bus.recv(timeout=0.05)
        if msg is None:
            continue
        if msg.arbitration_id in (obd.REQUEST_ID, obd.RESPONSE_ID):
            state.add_frame("rx", msg.arbitration_id, bytes(msg.data))
        if msg.arbitration_id != obd.RESPONSE_ID:
            continue
        data = bytes(msg.data)
        pci = data[0]
        if (pci & 0xF0) == 0x00:
            response = data[1 : 1 + (pci & 0x0F)]
            if len(response) >= 2 and response[0] == 0x49 and response[1] == pid:
                return response
        if (pci & 0xF0) == 0x10:
            expected_len = ((pci & 0x0F) << 8) | data[1]
            first_payload = data[2:8]
            if len(first_payload) >= 2 and first_payload[0] == 0x49 and first_payload[1] == pid:
                payload = bytearray(first_payload)
                bus.send(obd.can.Message(arbitration_id=0x7E0, data=[0x30, 0, 0, 0, 0, 0, 0, 0], is_extended_id=False))
        elif (pci & 0xF0) == 0x20 and expected_len is not None:
            payload.extend(data[1:8])
            if len(payload) >= expected_len:
                return bytes(payload[:expected_len])
    return None


def decode_mode09(pid: int, payload: bytes | None) -> str | None:
    if not payload:
        return None
    body = payload[3:] if len(payload) >= 3 and payload[:2] == bytes([0x49, pid]) else payload
    if pid in (0x02, 0x04, 0x0A):
        return ascii_clean(body.rstrip(b"\x00"))
    if pid == 0x06 and len(body) >= 4:
        return "".join(f"{byte:02X}" for byte in body[:4])
    return hx(body)


def set_pid_value(pid: int, data: bytes) -> None:
    if pid == 0x0C:
        state.set_value("rpm", obd.rpm(data))
    elif pid == 0x0D:
        kph = obd.speed_kph(data)
        state.set_value("speed_kph", kph)
        state.set_value("speed_mph", kph * 0.621371)
    elif pid == 0x05:
        state.set_value("coolant_temp", obd.temp_c(data))
    elif pid == 0x0F:
        state.set_value("intake_air_temp", obd.temp_c(data))
    elif pid == 0x04:
        state.set_value("engine_load", obd.pct(data))
    elif pid == 0x0B:
        state.set_value("intake_manifold_pressure", obd.pressure_kpa(data))
    elif pid == 0x11:
        state.set_value("throttle_position", obd.pct(data))
    elif pid == 0x45:
        state.set_value("relative_throttle_position", obd.pct(data))
    elif pid == 0x0E:
        state.set_value("timing_advance", obd.timing_deg(data))
    elif pid == 0x06:
        state.set_value("short_term_fuel_trim_b1", obd.fuel_trim(data))
    elif pid == 0x07:
        state.set_value("long_term_fuel_trim_b1", obd.fuel_trim(data))
    elif pid == 0x14 and len(data) >= 2:
        status = "maybe_not_ready/open_loop" if data[0] == 0x00 and data[1] == 0x80 else "fresh"
        state.set_value("o2_b1s1_voltage", data[0] / 200.0, status)
        state.set_value("o2_b1s1_trim", obd.fuel_trim(data[1:2]), status)


def timeout_fields(pid: int) -> list[str]:
    return {
        0x0C: ["rpm"],
        0x0D: ["speed_kph", "speed_mph"],
        0x05: ["coolant_temp"],
        0x0F: ["intake_air_temp"],
        0x04: ["engine_load"],
        0x0B: ["intake_manifold_pressure"],
        0x11: ["throttle_position"],
        0x45: ["relative_throttle_position"],
        0x0E: ["timing_advance"],
        0x06: ["short_term_fuel_trim_b1"],
        0x07: ["long_term_fuel_trim_b1"],
        0x14: ["o2_b1s1_voltage", "o2_b1s1_trim"],
    }[pid]


def refresh_supported(bus: Any, timeout: float, request_delay: float) -> None:
    raw = {}
    decoded = []
    for pid in SUPPORTED_QUERY_PIDS:
        data = request_mode01(bus, pid, timeout)
        obd.wait_between_requests(request_delay)
        if data is None:
            raw[f"01{pid:02X}"] = "timeout"
            continue
        raw[f"01{pid:02X}"] = hx(data[:4])
        decoded.extend(obd.decode_supported(pid, data))
    with state.lock:
        state.supported_pids = {
            "raw": raw,
            "decoded": [f"01 {pid:02X} {obd.PID_NAMES.get(pid, 'unknown')}" for pid in sorted(set(decoded))],
        }


def refresh_ecu_info(bus: Any, timeout: float, request_delay: float) -> None:
    vin = decode_mode09(0x02, read_mode09_pid(bus, 0x02, timeout))
    obd.wait_between_requests(request_delay)
    calibration_id = decode_mode09(0x04, read_mode09_pid(bus, 0x04, timeout))
    obd.wait_between_requests(request_delay)
    cvn = decode_mode09(0x06, read_mode09_pid(bus, 0x06, timeout))
    with state.lock:
        if vin:
            state.ecu_info["vin"] = vin
            state.ecu_info["vin_masked"] = mask_vin(vin)
        if calibration_id:
            state.ecu_info["calibration_id"] = calibration_id
        if cvn:
            state.ecu_info["cvn"] = cvn


def refresh_dtc(bus: Any, timeout: float, request_delay: float) -> dict[str, Any]:
    stored = read_obd_service(bus, 0x03, 0x43, timeout)
    obd.wait_between_requests(request_delay)
    pending = read_obd_service(bus, 0x07, 0x47, timeout)
    result = {
        "stored": hx(stored) if stored else "timeout/no response",
        "pending": hx(pending) if pending else "timeout/no response",
        "updated_at": now_iso(),
    }
    with state.lock:
        state.dtc = result
    return result


def poll_ecu(interface: str, timeout: float, request_delay: float) -> None:
    while True:
        try:
            with obd.open_bus(interface) as bus:
                state.set_status("connected", True, f"Polling {len(POLL_PIDS)} Mode 01 PIDs")
                with state.bus_lock:
                    refresh_supported(bus, timeout, request_delay)
                    refresh_ecu_info(bus, timeout, request_delay)
                while True:
                    with state.bus_lock:
                        for pid in POLL_PIDS:
                            data = request_mode01(bus, pid, timeout)
                            obd.wait_between_requests(request_delay)
                            if data is None:
                                state.mark_timeout(timeout_fields(pid))
                                continue
                            set_pid_value(pid, data)
                    state.log_row()
        except Exception as exc:
            state.set_status("disconnected", False, str(exc))
            time.sleep(1.0)


def pi_health() -> dict[str, Any]:
    try:
        uptime = Path("/proc/uptime").read_text().split()[0]
    except OSError:
        uptime = None
    try:
        cpu_temp = round(int(Path("/sys/class/thermal/thermal_zone0/temp").read_text()) / 1000.0, 1)
    except OSError:
        cpu_temp = None
    try:
        throttled = subprocess.check_output(["vcgencmd", "get_throttled"], text=True, timeout=1).strip()
    except (OSError, subprocess.SubprocessError):
        throttled = "unavailable"
    disk = shutil.disk_usage(".")
    return {
        "uptime_seconds": float(uptime) if uptime else None,
        "cpu_temp_c": cpu_temp,
        "throttled": throttled,
        "disk_free_gb": round(disk.free / (1024**3), 2),
    }


@app.get("/")
def index() -> str:
    return render_template("index.html")


@app.get("/api/current")
def api_current() -> Any:
    return jsonify(state.snapshot_values())


@app.get("/api/status")
def api_status() -> Any:
    return jsonify(state.snapshot_status())


@app.get("/api/dtc")
def api_dtc() -> Any:
    with state.lock:
        return jsonify(dict(state.dtc))


@app.post("/api/dtc/refresh")
def api_dtc_refresh() -> Any:
    with state.bus_lock, obd.open_bus(obd.CAN_INTERFACE) as bus:
        return jsonify(refresh_dtc(bus, 0.8, obd.DEFAULT_REQUEST_DELAY))


@app.get("/api/ecu-info")
def api_ecu_info() -> Any:
    with state.lock:
        info = dict(state.ecu_info)
    info.pop("vin", None)
    return jsonify(info)


@app.get("/api/supported-pids")
def api_supported_pids() -> Any:
    with state.lock:
        return jsonify(dict(state.supported_pids))


@app.get("/api/raw")
def api_raw() -> Any:
    with state.lock:
        return jsonify({"frames": list(state.raw_frames)})


@app.post("/api/raw/clear")
def api_raw_clear() -> Any:
    state.clear_frames()
    return jsonify({"ok": True})


@app.get("/api/log/status")
def api_log_status() -> Any:
    return jsonify(state.snapshot_logging())


@app.post("/api/log/start")
def api_log_start() -> Any:
    return jsonify(state.start_log())


@app.post("/api/log/stop")
def api_log_stop() -> Any:
    return jsonify(state.stop_log())


@app.get("/api/log/download")
def api_log_download() -> Any:
    log_state = state.snapshot_logging()
    path = log_state.get("file")
    if not path or not Path(path).exists():
        return jsonify({"error": "no log file"}), 404
    return send_file(path, as_attachment=True)


@app.get("/api/health")
def api_health() -> Any:
    return jsonify(pi_health())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local Kawasaki OBD web dashboard")
    parser.add_argument("--interface", default=obd.CAN_INTERFACE)
    parser.add_argument("--timeout", type=float, default=0.5)
    parser.add_argument("--request-delay", type=float, default=obd.DEFAULT_REQUEST_DELAY)
    parser.add_argument("--host", default=HOST)
    parser.add_argument("--port", type=int, default=PORT)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    thread = threading.Thread(target=poll_ecu, args=(args.interface, args.timeout, args.request_delay), daemon=True)
    thread.start()
    app.run(host=args.host, port=args.port, debug=False, use_reloader=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
