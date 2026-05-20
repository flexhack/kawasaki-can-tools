# Kawasaki CAN/OBD Logger

Read-only Kawasaki EURO5/KDS CAN diagnostics, live data logging, ECU identity dumping, and local web dashboard tooling for a 2025 Kawasaki Ninja 650 / EX650 generation ECU.

This project was developed and tested with:

```text
Motorcycle: 2025 Kawasaki Ninja 650 / EX650
Diagnostic connector: EURO5/KDS 6-pin
CAN bitrate: 500000
SocketCAN interface: can0
USB-CAN adapter: DSD TECH SH-C31G / CANable 2.0 / gs_usb
OBD functional request ID: 0x7DF
OBD physical request ID:   0x7E0
ECU response ID:           0x7E8
Live bike Calibration ID:  49245-2210
Live bike CVN:             000096AA
Live bike VIN:             ML5EXEP19SDA*****
```

The tool is intentionally **read-only**. It supports live OBD Mode 01 polling, DTC reading, Mode 09 ECU identity reading, CSV logging, and a local web dashboard. It does **not** implement DTC clearing, ECU flashing, erase/write operations, UDS SecurityAccess, RequestDownload, TransferData, or programming commands.

---

## Project status

Working and confirmed:

- Raspberry Pi + SH-C31G SocketCAN setup.
- Live-bike EURO5/KDS 6-pin CAN diagnostics.
- Donor ECU bench power-up and CAN diagnostics.
- OBD Mode 01 live data.
- OBD Mode 03 stored DTC read.
- OBD Mode 07 pending DTC read.
- OBD Mode 09 VIN / Calibration ID / CVN read.
- CSV logging.
- Local web dashboard.

Not implemented:

- Mode 04 clear DTC.
- ECU write/erase/programming.
- Full firmware dump.
- D-CAN proprietary flashing protocol.
- RXD/TXD/CNF boot-mode tooling.

---

## Repository contents

Typical files in this project:

```text
kawasaki_obd_logger.py     # CLI logger for Mode 01 live data
 ecu_safe_dump.py          # Read-only ECU info / PID / DTC dump
 ecu_info_dump.py          # Mode 09 ECU identity dump helper
 web_dashboard.py          # Local Flask web dashboard
 requirements.txt          # Python dependencies
 scripts/can500.sh         # Bring up SocketCAN at 500 kbit/s
 scripts/run_web.sh        # Start web dashboard
 static/                   # Web dashboard JavaScript/CSS
 templates/                # Web dashboard HTML
 docs/                     # Hardware notes and bench pinout docs
 logs/                     # Local logs, ignored by Git
 dumps/                    # ECU dumps, ignored by Git
```

`logs/`, `dumps/`, `.venv/`, CSV files, JSON dump files, and firmware files should not be committed to Git.

---

## Safety notes

This project is for read-only diagnostics and logging.

Allowed operations:

```text
OBD Mode 01 = live/current data
OBD Mode 03 = read stored DTC
OBD Mode 07 = read pending DTC
OBD Mode 09 = VIN / Calibration ID / CVN / ECU info
Passive CAN logging
```

Not allowed and not implemented:

```text
Mode 04 clear DTC
UDS 0x10 programming/extended-session experiments unless explicitly researched
UDS 0x27 SecurityAccess
UDS 0x31 RoutineControl erase/write
UDS 0x34 RequestDownload
UDS 0x35 RequestUpload
UDS 0x36 TransferData
UDS 0x37 RequestTransferExit
UDS 0x3D WriteMemoryByAddress
ECU flash erase
ECU flash write
ECU programming
```

Do not connect random ECU pins to +12V. Use a current-limited bench power supply for donor ECU work. Never use a motorcycle battery directly during pin discovery.

---

## Tested USB-CAN adapter

Adapter used:

```text
DSD TECH SH-C31G Isolated USB-to-CAN Adapter
Based on CANable 2.0 Pro
Firmware/driver mode: candleLight / gs_usb
Linux interface: can0
```

On Raspberry Pi it appears as:

```text
lsusb:
1d50:606f OpenMoko, Inc. Geschwister Schneider CAN adapter

Product string:
canable2 gs_usb

Linux driver:
gs_usb
```

Useful checks:

```bash
lsusb
dmesg | tail -50
ip link
ip -details link show can0
```

Expected `can0` state after setup:

```text
can state ERROR-ACTIVE
bitrate 500000
parent driver: gs_usb
```

---

## Why use Raspberry Pi instead of macOS directly?

The SH-C31G works cleanly on Linux as a `gs_usb` SocketCAN device. macOS does not natively expose the same SocketCAN interface.

Recommended setup:

```text
Kawasaki ECU / motorcycle
        ↓ CAN
SH-C31G USB-CAN adapter
        ↓ USB
Raspberry Pi
        ↓ SSH / browser
MacBook / desktop / phone
```

Use the Mac as a remote terminal or browser client:

```bash
ssh flex21@klipper.local
```

Or open the dashboard from a browser:

```text
http://<raspberry-pi-ip>:8080
```

---

## Python setup

Create a virtual environment and install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

System packages used on Raspberry Pi:

```bash
sudo apt update
sudo apt install -y can-utils python3-pip python3-venv git usbutils
```

---

## Bring up CAN at 500 kbit/s

Use the helper script:

```bash
./scripts/can500.sh
```

Equivalent shell command:

```bash
sudo ip link set can0 down 2>/dev/null || true
sudo ip link set can0 type can bitrate 500000
sudo ip link set can0 up
ip -details link show can0
```

The logger can also bring the interface up:

```bash
python3 kawasaki_obd_logger.py up
```

---

# Live motorcycle connection

## EURO5/KDS 6-pin diagnostic connector

The live motorcycle uses a EURO5/KDS-style 6-pin diagnostic connector.

Measured values on the motorcycle connector with ignition ON:

```text
Pin 1 = about 5V
Pin 2 = about 2.5V
Pin 3 = GND
Pin 4 = +12V
Pin 5 = about 2.5V
Pin 6 = about 10V
```

Measured with ignition OFF:

```text
Pin 2 ↔ Pin 5 = about 60Ω
Pin 3 ↔ battery negative = continuity / GND
```

Confirmed CAN/GND pins:

```text
EURO5/KDS pin 2 = CAN pair
EURO5/KDS pin 5 = CAN pair
EURO5/KDS pin 3 = GND
```

SH-C31G wiring:

```text
SH-C31G CAN-H → EURO5/KDS pin 2 or pin 5
SH-C31G CAN-L → EURO5/KDS pin 5 or pin 2
SH-C31G GND   → EURO5/KDS pin 3
```

If there is no response, swap CAN-H and CAN-L.

Do not connect SH-C31G to:

```text
EURO5/KDS pin 1 = about 5V
EURO5/KDS pin 4 = +12V
EURO5/KDS pin 6 = about 10V / likely K-Line or another diagnostic line
```

## Live-bike CAN settings

Confirmed:

```text
Bitrate: 500000
Functional request: 0x7DF
Physical request:   0x7E0
ECU response:       0x7E8
```

Smoke test:

```bash
cansend can0 7DF#0201000000000000
```

Expected response:

```text
7E8 [8] 06 41 00 BE 3E C0 11 55
```

Physical request also works:

```bash
cansend can0 7E0#0201000000000000
```

---

# Donor ECU bench setup

A donor Kawasaki/Denso ECU was powered and queried successfully on the bench.

The donor ECU has two 36-pin connectors:

```text
Black connector: 3 rows x 12 pins = 36 pins
Grey connector:  3 rows x 12 pins = 36 pins
```

Temporary project numbering, looking directly at the ECU connector pins:

```text
Top row:    01 02 03 04 05 06 07 08 09 10 11 12
Middle row: 13 14 15 16 17 18 19 20 21 22 23 24
Bottom row: 25 26 27 28 29 30 31 32 33 34 35 36
```

This numbering is project-local. Confirm physical orientation before wiring.

## Confirmed bench pinout

Confirmed by successful OBD/CAN response and safe dump.

```text
Power:
Black 1  = +12V
Black 2  = +12V

Ground:
Black 25 = GND
Black 29 = GND
Grey 27  = GND

CAN:
Black 7  = CAN-H
Black 6  = CAN-L
```

Working bench wiring:

```text
Bench PSU OUT+ → Black 1 + Black 2
Bench PSU OUT- → Black 25 + Black 29 + Grey 27

SH-C31G CAN-H → Black 7
SH-C31G CAN-L → Black 6
SH-C31G GND   → Bench PSU OUT-
```

Bench power used:

```text
Supply: FNIRSI DPS-150
Voltage: 12.00V
Observed current: about 0.178A
Supply mode: CV
Suggested current limit after confirmation: 0.20A to 0.30A
```

Do not use high current limits during discovery. Do not use a battery directly.

## Confirmed donor bench response

```text
TX 7DF [8] 02 01 00 00 00 00 00 00
RX 7E8 [8] 06 41 00 BE 3E C0 11 55

TX 7E0 [8] 02 01 00 00 00 00 00 00
RX 7E8 [8] 06 41 00 BE 3E C0 11 55
```

## Donor ECU identity

Donor ECU read through Mode 09:

```text
VIN: ML5EXER17SDA*****
Calibration ID: 49245-2210
CVN: 000096AA
```

The donor Calibration ID and CVN match the live bike:

```text
Live bike Calibration ID: 49245-2210
Live bike CVN:            000096AA
Donor ECU Calibration ID:  49245-2210
Donor ECU CVN:             000096AA
```

## Expected donor bench DTCs

When the ECU is powered on the bench without the full motorcycle harness, many DTCs are expected.

Example bench DTCs observed:

```text
P0030
P0105
P0110
P0115
P0120
P0220
P0418
P0480
P0914
C0064
```

These are expected because sensors, actuators, fan circuits, and other bike-side components are not connected.

Do not clear DTCs as part of this project.

---

# Pins discovered but not used for normal CAN diagnostics

During pin discovery, several candidate pins were tested and rejected or left unused.

Do not connect these for normal CAN diagnostics:

```text
Black 10 = unknown; not confirmed for bench power
Black 34 = unknown; not confirmed for bench power
Grey 21  = bad wake/power candidate; 100Ω resistor became very hot
Grey 24  = possible input but not required after correct power pins were found
Black 1 through 100Ω = not enough to boot ECU; final confirmed power is direct Black 1 + Black 2
```

The correct confirmed power pins are:

```text
Black 1 + Black 2 = +12V
```

---

# RXD / TXD / CNF notes

Related Kawasaki/Denso SH7058-family information suggests:

```text
Black 11 = RXD
Black 18 = TXD
Black 22 = CNF / CNF1
```

These are **not CAN**.

They are likely direct boot/programming UART/SCI-related lines for Renesas/Denso boot mode.

Do not connect the SH-C31G to RXD/TXD/CNF.

Normal diagnostics and logging only use:

```text
CAN-H
CAN-L
GND
+12V power pins
```

Boot/direct programming research is outside the current read-only scope.

---

# Query supported PIDs

```bash
python3 kawasaki_obd_logger.py supported
```

The command polls:

```text
01 00
01 20
01 40
01 60
```

It then prints supported PID names. PIDs decoded by this logger are marked with `*`.

Observed supported PID bitmaps:

```text
01 00: BE 3E C0 11
01 20: 80 00 00 01
01 40: 8A 10 00 00
01 60: 00 00 00 00
```

---

# Raw debug

Print request and response bytes for selected read-only Mode 01 PIDs:

```bash
python3 kawasaki_obd_logger.py raw --pids 0E,14,45
```

Example raw responses observed:

```text
01 0E → 41 0E 7A
01 14 → 41 14 00 80
01 45 → 41 45 00
```

---

# Live CSV logging

```bash
python3 kawasaki_obd_logger.py log --output logs/ride.csv --interval 1.0
```

Before live logging, the tool queries supported PIDs and only polls PIDs the ECU reports as supported.

To force a decoded but unsupported PID manually:

```bash
python3 kawasaki_obd_logger.py log --include-unsupported --pids 4B
```

The default delay between PID requests is 100 ms. Adjust it if needed:

```bash
python3 kawasaki_obd_logger.py log --request-delay 0.1
```

Poll a smaller set:

```bash
python3 kawasaki_obd_logger.py log --pids 05 0C 11 --output logs/basic.csv
```

## Default decoded PIDs

```text
01 04 = engine load
01 05 = coolant temperature
01 06 = short term fuel trim bank 1
01 07 = long term fuel trim bank 1
01 0B = intake manifold pressure
01 0C = RPM
01 0D = vehicle speed
01 0E = timing advance
01 0F = intake air temperature
01 11 = throttle position
01 14 = O2 sensor B1S1 voltage and trim
01 45 = relative throttle position
```

`01 4B` commanded throttle actuator is decoded if forced manually, but it is not part of default logging because this ECU times out on it.

If `01 14` returns `00 80`, the logger prints:

```text
voltage=0.000V
trim=0.0%
maybe_not_ready/open_loop
```

If `01 14` times out during logging, the CSV and terminal output show `timeout/not_ready` and logging continues.

---

# Read-only ECU safe dump

Run:

```bash
python3 ecu_safe_dump.py
```

The safe dump reads:

```text
Mode 01 supported/live data PIDs
Mode 03 stored DTC
Mode 07 pending DTC
Mode 09 VIN / Calibration ID / CVN
```

Example Mode 09 output:

```text
09 02 = masked VIN
09 04 = 49245-2210
09 06 = 000096AA
09 0A = timeout
```

The dump is read-only.

Do not commit dump files. They may contain VIN and other private data.

---

# Web dashboard

Install requirements, bring up `can0`, then start the local dashboard:

```bash
pip install -r requirements.txt
./scripts/can500.sh
./scripts/run_web.sh
```

The server listens on:

```text
0.0.0.0:8080
```

From the Pi, open:

```text
http://localhost:8080
```

From another device on the same network, open:

```text
http://<pi-ip-address>:8080
```

JSON endpoints:

```bash
curl http://localhost:8080/api/current
curl http://localhost:8080/api/status
```

The dashboard uses the same read-only Mode 01 polling as the CLI. It keeps the last good value when a PID times out and marks that field stale or timeout.

---

# Useful CAN commands

Listen to all traffic:

```bash
candump -tz -x can0
```

Listen only for ECU responses in the standard response range:

```bash
candump -tz can0,7E8:7F8
```

Request supported PIDs:

```bash
cansend can0 7DF#0201000000000000
```

Physical request to ECU:

```bash
cansend can0 7E0#0201000000000000
```

Check CAN statistics:

```bash
ip -details -statistics link show can0
```

Useful fields:

```text
bus-errors
error-warn
error-pass
bus-off
RX packets
TX packets
```

If `read: Network is down` appears, reset the CAN interface:

```bash
sudo ip link set can0 down 2>/dev/null || true
sudo ip link set can0 type can bitrate 500000
sudo ip link set can0 up
```

---

# Troubleshooting

## `can0` does not exist

Check USB:

```bash
lsusb
dmesg | tail -50
ip link
```

Expected USB ID:

```text
1d50:606f OpenMoko, Inc. Geschwister Schneider CAN adapter
```

If the adapter appears as STM DFU mode instead of `1d50:606f`, disconnect it from the bike/ECU and reconnect USB only.

## No ECU response, only local echo

If `candump` only shows your own `7DF` or `7E0` frames, but no `7E8`, check:

- CAN-H/CAN-L swapped.
- Wrong CAN pins.
- ECU not powered.
- Bench PSU current limit too low.
- Missing ground reference.
- Dashboard/logger already running and interfering.

Check for running tools:

```bash
ps aux | egrep 'kawasaki|web_dashboard|ecu_safe|obd_logger' | grep -v grep
```

Stop them if needed:

```bash
sudo pkill -f web_dashboard.py
sudo pkill -f kawasaki_obd_logger.py
sudo pkill -f ecu_safe_dump.py
```

## Bench ECU powered but no CAN idle voltage

If Black 6/7 are 0V without the SH-C31G connected, the ECU is probably not powered correctly. In the confirmed bench setup, when the ECU is correctly powered, CAN lines idle at about 2.5V without the CAN reader connected.

Confirmed donor bench power:

```text
+12V → Black 1 + Black 2
GND  → Black 25 + Black 29 + Grey 27
```

## Raspberry Pi undervoltage

If `dmesg` shows undervoltage warnings, use a better Raspberry Pi power supply. USB-CAN adapters can disconnect or behave oddly if the Pi is underpowered.

Check throttling:

```bash
vcgencmd get_throttled
```

---

# Git / privacy notes

Do not commit:

```text
logs/
dumps/
*.csv
*.json ECU dump files
*.bin
*.hex
*.srec
*.mot
full VINs
```

Recommended `.gitignore` entries:

```text
.venv/
__pycache__/
*.pyc
.DS_Store

logs/
dumps/
*.log
*.csv
*.json

*.bin
*.hex
*.srec
*.mot
```

VINs in public docs should be masked:

```text
ML5EXEP19SDA*****
ML5EXER17SDA*****
```

Check before committing:

```bash
grep -RInE 'ML5[A-Z0-9]{14}' . --exclude-dir=.git --exclude-dir=.venv --exclude-dir=dumps --exclude-dir=logs || true
```

---

# Current confirmed facts

```text
Live bike:
- 2025 Kawasaki Ninja 650 / EX650
- EURO5/KDS CAN 500k
- Calibration ID: 49245-2210
- CVN: 000096AA
- Request IDs: 0x7DF / 0x7E0
- Response ID: 0x7E8

Donor ECU:
- Same Calibration ID: 49245-2210
- Same CVN: 000096AA
- Bench powered successfully
- Bench CAN diagnostics confirmed

Confirmed donor bench pinout:
- Black 1  = +12V
- Black 2  = +12V
- Black 25 = GND
- Black 29 = GND
- Grey 27  = GND
- Black 7  = CAN-H
- Black 6  = CAN-L
```

---

# Next research steps

The next stage is full firmware dump research. This is **not implemented** in this repository yet.

Likely paths:

```text
1. Proprietary Kawasaki/Denso D-CAN read protocol.
2. Direct/boot mode using RXD/TXD/CNF on the donor ECU.
3. Opening the donor ECU and identifying MCU, CAN transceiver, power regulators, and boot pads.
```

Before any write/flash work, the project needs:

```text
- full read/backup path
- recovery path
- checksum understanding
- map definitions
- safe donor-only validation
```

Do not attempt flashing on a live motorcycle ECU.
