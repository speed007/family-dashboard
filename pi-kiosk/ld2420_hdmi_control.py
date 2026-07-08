"""
LD2420 mmWave Presence Sensor → HDMI Power Control for Pi Zero 2W Kiosk

Monitors an LD2420 over UART and toggles the HDMI display:
  - Person within DISTANCE_THRESHOLD cm → display ON
  - No presence for DISPLAY_TIMEOUT seconds → display OFF

Wiring (LD2420 → Pi Zero 2W GPIO):
  LD2420 5V  → Pi Pin 2  (5V)
  LD2420 GND → Pi Pin 6  (GND)
  LD2420 OT1 → Pi Pin 10 (RX / GPIO15)  — via voltage divider (5V→3.3V)
  LD2420 RX  → Pi Pin 8  (TX / GPIO14)

  Voltage divider (LD2420 OT1 → Pi RX):
    LD2420 OT1 ──┬─ 2kΩ ── Pi RX (GPIO15)
                 └─ 1kΩ ── GND
    (Roughly halves 5V to ~3.3V)

Pi setup (do once):
  sudo raspi-config -> Interface Options -> Serial Port ->
    "Login shell over serial"  → NO
    "Serial port hardware"     → YES
  # This enables UART on GPIO14/15 without using it for console login.
  sudo reboot

Install dependencies:
  pip install pyserial
"""

import serial
import subprocess
import time
import logging
import sys

# ── Configuration ──────────────────────────────────────────────────
SERIAL_PORT = "/dev/serial0"
BAUD_RATE = 115200
DISPLAY_TIMEOUT = 60          # seconds after last presence before turning off
DISTANCE_THRESHOLD = 50       # cm — trigger distance
POLL_INTERVAL = 0.1           # seconds between serial reads

# ── Logging ────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s %(levelname)s: %(message)s",
    level=logging.INFO,
    handlers=[logging.StreamHandler(), logging.FileHandler("/var/log/ld2420_hdmi.log")],
)
log = logging.getLogger("ld2420_hdmi")


def display_power(on: bool):
    """Set HDMI display power state via vcgencmd."""
    state = "1" if on else "0"
    try:
        subprocess.run(
            ["vcgencmd", "display_power", state],
            capture_output=True,
            timeout=5,
            check=True,
        )
    except Exception as e:
        log.error("Failed to set display power %s: %s", state, e)


def parse_frame(data: bytes):
    """
    Try to parse an LD2420 basic data frame.

    Frame format (confirmed from ESPHome LD2420 component):
      [0xAA, 0xAA]  — header (2 bytes)
      [length]       — bytes remaining after this byte (1 byte)
      [cmd_lo, cmd_hi] — command word (2 bytes, 0x00 0x00 = data)
      [data...]      — frame payload
      [checksum]     — sum of all preceding bytes & 0xFF

    Data payload (cmd=0x0000):
      offset 0-1: moving_distance  (uint16 LE, cm)
      offset 2-3: still_distance   (uint16 LE, cm)
      offset 4:   moving_energy    (uint8)
      offset 5:   still_energy     (uint8)
      offset 6:   has_moving_target (uint8, 0/1)
      offset 7:   has_still_target  (uint8, 0/1)
      offset 8:   reserved
    """
    if len(data) < 5:
        return None
    length = data[2]
    # Total frame = 2 (header) + 1 (length) + length + 1 (checksum)
    if len(data) < length + 4:
        return None

    cmd = data[3] | (data[4] << 8)
    if cmd != 0x0000:
        return None

    payload = data[5 : 3 + length]
    if len(payload) < 9:
        return None

    moving_distance = payload[0] | (payload[1] << 8)
    still_distance = payload[2] | (payload[3] << 8)
    has_moving = payload[6]
    has_still = payload[7]

    return {
        "moving_distance": moving_distance,
        "still_distance": still_distance,
        "present": bool(has_moving or has_still),
        "moving_energy": payload[4],
        "still_energy": payload[5],
    }


def main():
    log.info("Starting LD2420 HDMI control")

    # Ensure display starts on
    display_power(True)
    display_on = True
    last_presence = time.time()

    try:
        ser = serial.Serial(
            SERIAL_PORT,
            BAUD_RATE,
            timeout=POLL_INTERVAL,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
        )
    except Exception as e:
        log.critical("Cannot open %s: %s", SERIAL_PORT, e)
        sys.exit(1)

    buffer = bytearray()
    presence_log_throttle = 0  # avoid spamming logs

    log.info("Listening on %s @ %d baud", SERIAL_PORT, BAUD_RATE)

    try:
        while True:
            # Read available bytes
            chunk = ser.read(256)
            if chunk:
                buffer.extend(chunk)

            now = time.time()

            # Search for frame headers (0xAA 0xAA)
            processed = 0
            while processed < len(buffer) - 1:
                if buffer[processed] == 0xAA and buffer[processed + 1] == 0xAA:
                    # Need at least header (2) + length (1) to determine frame size
                    if processed + 2 >= len(buffer):
                        break
                    length = buffer[processed + 2]
                    total_len = 2 + 1 + length + 1  # header + len_byte + payload + checksum
                    if processed + total_len > len(buffer):
                        break

                    frame = bytes(buffer[processed : processed + total_len])
                    info = parse_frame(frame)
                    if info:
                        dist = info["moving_distance"]
                        present = info["present"]

                        if present and dist <= DISTANCE_THRESHOLD:
                            if not display_on:
                                log.info("Presence detected (%.0f cm) — turning display ON", dist)
                                display_power(True)
                                display_on = True
                            last_presence = now
                            if presence_log_throttle % 50 == 0:
                                log.debug("Presence: %.0f cm, energy=%d", dist, info["moving_energy"])
                            presence_log_throttle += 1

                        elif display_on and (now - last_presence) > DISPLAY_TIMEOUT:
                            log.info("No presence for %ds — turning display OFF", DISPLAY_TIMEOUT)
                            display_power(False)
                            display_on = False
                            presence_log_throttle = 0

                    processed += total_len
                else:
                    processed += 1

            # Trim processed bytes
            if processed > 0:
                buffer = buffer[processed:]

            # Rate-limit loop
            time.sleep(POLL_INTERVAL)

    except KeyboardInterrupt:
        log.info("Shutting down — turning display back on")
        display_power(True)
    except Exception as e:
        log.exception("Unhandled error: %s", e)
        display_power(True)
    finally:
        ser.close()


if __name__ == "__main__":
    main()
