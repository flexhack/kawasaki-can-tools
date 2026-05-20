# Kawasaki / Denso 2x36 donor ECU bench pinout

Confirmed by successful bench OBD/CAN response and safe dump.

## Temporary connector numbering

Black connector:

Top row:    01 02 03 04 05 06 07 08 09 10 11 12

Middle row: 13 14 15 16 17 18 19 20 21 22 23 24

Bottom row: 25 26 27 28 29 30 31 32 33 34 35 36

Grey connector:

Top row:    01 02 03 04 05 06 07 08 09 10 11 12

Middle row: 13 14 15 16 17 18 19 20 21 22 23 24

Bottom row: 25 26 27 28 29 30 31 32 33 34 35 36

## Confirmed bench wiring

Power:

- Black 1  = +12V

- Black 2  = +12V

Ground:

- Black 25 = GND

- Black 29 = GND

- Grey 27  = GND

CAN:

- Black 7 = CAN-H

- Black 6 = CAN-L

Bench supply:

- 12.00V

- observed current about 0.178A

- CV mode

SocketCAN:

- can0

- 500000 bitrate

- request IDs: 0x7DF / 0x7E0

- response ID: 0x7E8

Confirmed response:

TX 7DF 02 01 00 00 00 00 00 00

RX 7E8 06 41 00 BE 3E C0 11 55

Donor ECU:

- VIN: ML5EXER17SDA*****

- Calibration ID: 49245-2210

- CVN: 000096AA

Do not connect for normal CAN diagnostics:

- Black 10

- Black 34

- Grey 21

- Grey 24

- Black 11 RXD

- Black 18 TXD

- Black 22 CNF

RXD/TXD/CNF are likely direct boot/programming lines, not CAN.
