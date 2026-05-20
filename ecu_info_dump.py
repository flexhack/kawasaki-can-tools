import argparse
import time
import can
from datetime import datetime

CHANNEL = "can0"
REQ_ID = 0x7DF
RESP_ID = 0x7E8
FLOW_CONTROL_ID = 0x7E0

MODE09_PIDS = {
    0x00: "supported_mode09_pids_01_20",
    0x02: "vin",
    0x04: "calibration_id",
    0x06: "calibration_verification_number",
    0x0A: "ecu_name",
}

def hex_bytes(data):
    return " ".join(f"{b:02X}" for b in data)

def send_single_frame(bus, service, pid):
    msg = can.Message(
        arbitration_id=REQ_ID,
        data=[0x02, service, pid, 0x00, 0x00, 0x00, 0x00, 0x00],
        is_extended_id=False,
    )
    bus.send(msg)

def send_flow_control(bus):
    # ISO-TP Flow Control: Continue To Send, block size 0, separation time 0
    msg = can.Message(
        arbitration_id=FLOW_CONTROL_ID,
        data=[0x30, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00],
        is_extended_id=False,
    )
    bus.send(msg)

def read_isotp_response(bus, expected_service, expected_pid, timeout=1.5):
    end = time.time() + timeout
    payload = bytearray()
    expected_len = None
    next_cf_sn = 1
    raw_frames = []

    while time.time() < end:
        msg = bus.recv(timeout=0.05)
        if msg is None:
            continue
        if msg.arbitration_id != RESP_ID:
            continue

        data = list(msg.data)
        raw_frames.append(data)

        pci = data[0]

        # Single Frame: 0x0L
        if (pci & 0xF0) == 0x00:
            length = pci & 0x0F
            sf_payload = data[1:1 + length]
            return bytes(sf_payload), raw_frames

        # First Frame: 0x1L LL
        if (pci & 0xF0) == 0x10:
            expected_len = ((pci & 0x0F) << 8) | data[1]
            payload.extend(data[2:8])
            send_flow_control(bus)
            continue

        # Consecutive Frame: 0x2N
        if (pci & 0xF0) == 0x20:
            sn = pci & 0x0F
            # Do not fail hard on SN mismatch; just collect for now
            next_cf_sn = (next_cf_sn + 1) & 0x0F
            payload.extend(data[1:8])
            if expected_len is not None and len(payload) >= expected_len:
                return bytes(payload[:expected_len]), raw_frames

    return None, raw_frames

def ascii_clean(payload):
    printable = []
    for b in payload:
        if 32 <= b <= 126:
            printable.append(chr(b))
        elif b in (0x00, 0x55):
            continue
        else:
            printable.append(".")
    return "".join(printable).strip()

def strip_mode09_header(pid, payload):
    # Real Kawasaki Mode 09 text/CVN replies include an item count byte:
    # 49 <pid> 01 <data...>
    if len(payload) >= 3 and payload[0] == 0x49 and payload[1] == pid:
        return payload[3:]
    if len(payload) >= 2 and payload[0] == 0x49 and payload[1] == pid:
        return payload[2:]
    return payload

def mask_vin(vin):
    if len(vin) <= 11:
        return vin
    return vin[:11] + "****"

def decode_mode09(pid, payload, show_vin=False):
    if not payload:
        return None

    body = strip_mode09_header(pid, payload)

    if pid == 0x00:
        return hex_bytes(body)

    if pid == 0x02:
        vin = ascii_clean(body)
        return vin if show_vin else mask_vin(vin)

    if pid == 0x04:
        return ascii_clean(body.rstrip(b"\x00"))

    if pid == 0x0A:
        return ascii_clean(body)

    if pid == 0x06:
        return "".join(f"{b:02X}" for b in body[:4])

    return hex_bytes(body)

def main():
    parser = argparse.ArgumentParser(description="Read-only Kawasaki Mode 09 ECU info dump")
    parser.add_argument("--show-vin", action="store_true", help="print full VIN instead of masking it")
    args = parser.parse_args()

    bus = can.interface.Bus(channel=CHANNEL, interface="socketcan")

    print(f"ECU info dump started at {datetime.now().isoformat(timespec='seconds')}")
    print("Read-only Mode 09 requests only.\n")

    try:
        for pid, name in MODE09_PIDS.items():
            print(f"Requesting 09 {pid:02X} - {name}")
            send_single_frame(bus, 0x09, pid)
            payload, raw = read_isotp_response(bus, 0x49, pid)

            print("  raw frames:")
            for frame in raw:
                print(f"    {hex_bytes(frame)}")

            if payload is None:
                print("  result: timeout/no response\n")
                continue

            print(f"  payload: {hex_bytes(payload)}")
            print(f"  decoded: {decode_mode09(pid, payload, show_vin=args.show_vin)}\n")
            time.sleep(0.5)
    finally:
        bus.shutdown()

if __name__ == "__main__":
    main()
