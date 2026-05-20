#!/usr/bin/env bash
set -euo pipefail

sudo ip link set can0 down 2>/dev/null || true
sudo ip link set can0 type can bitrate 500000
sudo ip link set can0 up
ip -details link show can0
