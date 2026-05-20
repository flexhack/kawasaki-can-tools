import argparse
import json
import time
from datetime import datetime
from pathlib import Path

import can

CHANNEL = "can0"
REQ_FUNC = 0x7DF
REQ_PHYS = 0x7E0
RESP = 0x7E8
DEFAULT_REQUEST_DELAY = 0.15

OUT_DIR = Path("dumps")
OUT_DIR.mkdir(exist_ok=True)

MODE01_PIDS = [
    0x00, 0x01, 0x03, 0x04, 0x05, 0x06, 0x07, 0x0B, 0x0C, 0x0D,
    0x0E, 0x0F, 0x11, 0x12, 0x14, 0x1C, 0x20, 0x21, 0x40, 0x41,
    0x45, 0x47, 0x4C, 0x60,
]

MODE09_PIDS = [0x00, 0x02, 0x04, 0x06, 0x0A]

COMMON_DIDS = [
    0xF180,  # boot software identification
    0xF181,  # application software identification
    0xF182,  # application data identification
    0xF183,  # boot software fingerprint
    0xF184,  # application software fingerprint
    0xF185,  # application data fingerprint
    0xF186,  # active diagnostic session
    0xF187,  # vehicle manufacturer spare part number
    0xF188,  # vehicle manufacturer ECU software number
    0xF189,  # vehicle manufacturer ECU software version
    0xF18A,  # system supplier identifier
    0xF18B,  # ECU manufacturing date
    0xF18C,  # ECU serial number
    0xF190,  # VIN
    0xF191,  # vehicle manufacturer ECU hardware number
    0xF192,  # system supplier ECU hardware number
    0xF193,  # system supplier ECU hardware version
    0xF194,  # system supplier ECU software number
    0xF195,  # system supplier ECU software version
    0xF197,  # system name/engine type
    0xF198,  # repair shop code / tester serial, often unsupported
    0xF19E,
    0xF1A0,
]

def hx(data):
    return " ".join(f"{b:02X}" for b in data)

def ascii_clean(data):
    chars = []
    for b in data:
        if 32 <= b <= 126:
            chars.append(chr(b))
        elif b in (0x00, 0x55):
            continue
        else:
            chars.append(".")
    return "".join(chars).strip()

def send(bus, arb_id, data):
    bus.send(can.Message(arbitration_id=arb_id, data=data, is_extended_id=False))

def drain_rx(bus, timeout=0.2):
    drained = []
    end = time.time() + timeout
    while time.time() < end:
        msg = bus.recv(timeout=0.02)
        if msg is None:
            continue
        drained.append({"id": f"0x{msg.arbitration_id:X}", "data": hx(list(msg.data))})
    return drained

def read_frames(bus, timeout=0.75):
    frames = []
    end = time.time() + timeout
    while time.time() < end:
        msg = bus.recv(timeout=0.05)
        if msg and msg.arbitration_id == RESP:
            frames.append(list(msg.data))
    return frames

def expected_response(payload):
    service = payload[0]
    if service == 0x01 and len(payload) >= 2:
        return bytes([0x41, payload[1]]), False
    if service == 0x09 and len(payload) >= 2:
        return bytes([0x49, payload[1]]), False
    if service == 0x03:
        return bytes([0x43]), False
    if service == 0x07:
        return bytes([0x47]), False
    if service == 0x22 and len(payload) >= 3:
        return bytes([0x62, payload[1], payload[2]]), True
    raise ValueError(f"Unsupported read-only request payload: {hx(payload)}")

def payload_matches(payload, expected_prefix, allow_negative=False):
    if allow_negative and payload and payload[0] == 0x7F:
        return True
    return payload.startswith(expected_prefix)

def base_result(req_id, request, drained, frames, ignored):
    return {
        "request_id": f"0x{req_id:X}",
        "request": hx(request),
        "drained": drained,
        "frames": [hx(f) for f in frames],
        "ignored_frames": [hx(f) for f in ignored],
    }

def isotp_request(bus, req_id, payload, timeout=1.5):
    # payload should fit single-frame request for this script
    if len(payload) > 7:
        raise ValueError("Only single-frame requests are supported here")

    request = [len(payload)] + payload + [0x00] * (7 - len(payload))
    expected_prefix, allow_negative = expected_response(payload)
    drained = drain_rx(bus)
    send(bus, req_id, request)

    frames = []
    ignored = []
    end = time.time() + timeout
    full_payload = bytearray()
    expected_len = None
    collecting = False

    while time.time() < end:
        msg = bus.recv(timeout=0.05)
        if not msg:
            continue
        if msg.arbitration_id in (req_id, REQ_FUNC, REQ_PHYS):
            continue
        if msg.arbitration_id != RESP:
            continue

        data = list(msg.data)
        pci = data[0]

        # Single frame
        if (pci & 0xF0) == 0x00:
            length = pci & 0x0F
            response_payload = bytes(data[1:1 + length])
            if not payload_matches(response_payload, expected_prefix, allow_negative):
                ignored.append(data)
                continue
            frames.append(data)
            result = base_result(req_id, request, drained, frames, ignored)
            result["payload"] = hx(response_payload)
            if allow_negative and response_payload and response_payload[0] == 0x7F:
                result["negative_response"] = True
            return result

        # First frame
        if (pci & 0xF0) == 0x10:
            expected_len = ((pci & 0x0F) << 8) | data[1]
            first_payload = bytes(data[2:8])
            if not payload_matches(first_payload, expected_prefix, allow_negative):
                ignored.append(data)
                full_payload.clear()
                expected_len = None
                collecting = False
                continue
            frames.append(data)
            full_payload = bytearray(first_payload)
            collecting = True
            # Flow Control to physical request ID
            send(bus, REQ_PHYS, [0x30, 0x00, 0x00, 0, 0, 0, 0, 0])
            continue

        # Consecutive frame
        if (pci & 0xF0) == 0x20:
            if not collecting:
                ignored.append(data)
                continue
            frames.append(data)
            full_payload.extend(data[1:8])
            if expected_len is not None and len(full_payload) >= expected_len:
                response_payload = bytes(full_payload[:expected_len])
                result = base_result(req_id, request, drained, frames, ignored)
                if payload_matches(response_payload, expected_prefix, allow_negative):
                    result["payload"] = hx(response_payload)
                    if allow_negative and response_payload and response_payload[0] == 0x7F:
                        result["negative_response"] = True
                    return result
                result["payload"] = None
                result["mismatch"] = True
                result["mismatch_payload"] = hx(response_payload)
                return result

    result = base_result(req_id, request, drained, frames, ignored)
    result["payload"] = None
    result["timeout"] = True
    if ignored:
        result["mismatch"] = True
    return result

def decode_mode09(pid, payload_hex):
    if not payload_hex:
        return None
    data = bytes.fromhex(payload_hex)
    if len(data) >= 3 and data[0] == 0x49 and data[1] == pid:
        body = data[3:]  # strip 49 PID count
    elif len(data) >= 2 and data[0] == 0x49 and data[1] == pid:
        body = data[2:]
    else:
        body = data

    if pid in (0x02, 0x04, 0x0A):
        return ascii_clean(body)
    if pid == 0x06 and len(body) >= 4:
        return "".join(f"{b:02X}" for b in body[-4:])
    return hx(body)

def decode_ascii_payload(payload_hex):
    if not payload_hex:
        return None
    data = bytes.fromhex(payload_hex)
    return ascii_clean(data)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--uds22", action="store_true", help="Also try common UDS 0x22 DID read-only requests")
    parser.add_argument("--request-delay", type=float, default=DEFAULT_REQUEST_DELAY)
    args = parser.parse_args()

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    outfile = OUT_DIR / f"ecu_safe_dump_{stamp}.json"

    dump = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "notes": [
            "Read-only dump only",
            "No Mode 04, no UDS security, no erase/write/programming",
        ],
        "mode01": {},
        "mode03": None,
        "mode07": None,
        "mode09": {},
        "uds22": {},
    }

    bus = can.interface.Bus(channel=CHANNEL, interface="socketcan")
    try:
        print("Mode 01 dump...")
        for pid in MODE01_PIDS:
            result = isotp_request(bus, REQ_FUNC, [0x01, pid], timeout=0.7)
            dump["mode01"][f"01{pid:02X}"] = result
            print(f"  01 {pid:02X}: {result.get('payload') or 'timeout'}")
            time.sleep(args.request_delay)

        print("Mode 03 stored DTC...")
        dump["mode03"] = isotp_request(bus, REQ_FUNC, [0x03], timeout=0.7)
        print(f"  03: {dump['mode03'].get('payload') or 'timeout'}")
        time.sleep(args.request_delay)

        print("Mode 07 pending DTC...")
        dump["mode07"] = isotp_request(bus, REQ_FUNC, [0x07], timeout=0.7)
        print(f"  07: {dump['mode07'].get('payload') or 'timeout'}")
        time.sleep(args.request_delay)

        print("Mode 09 ECU info...")
        for pid in MODE09_PIDS:
            result = isotp_request(bus, REQ_FUNC, [0x09, pid], timeout=1.5)
            result["decoded"] = decode_mode09(pid, result.get("payload"))
            dump["mode09"][f"09{pid:02X}"] = result
            print(f"  09 {pid:02X}: {result.get('decoded') or result.get('payload') or 'timeout'}")
            time.sleep(args.request_delay)

        if args.uds22:
            print("UDS 0x22 common DID read-only probe...")
            for did in COMMON_DIDS:
                result = isotp_request(bus, REQ_PHYS, [0x22, (did >> 8) & 0xFF, did & 0xFF], timeout=0.7)
                result["decoded_ascii_guess"] = decode_ascii_payload(result.get("payload"))
                dump["uds22"][f"0x{did:04X}"] = result
                status = result.get("payload") or "timeout"
                print(f"  22 {did:04X}: {status}")
                time.sleep(args.request_delay)

    finally:
        bus.shutdown()

    outfile.write_text(json.dumps(dump, indent=2))
    print(f"\nSaved: {outfile}")

if __name__ == "__main__":
    main()
