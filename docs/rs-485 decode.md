# Balmar SG200 SmartLink — verified wire protocol (live capture 2026-08-09)

Captured from the real bus: SG200 shunt + display, tapped at RS485_0 (A/B)
of a Waveshare RS485 CAN HAT (B) → `/dev/ttySC0`, hostname rpcan2.

## Physical / link layer — CONFIRMED

- RS-485, **115200 baud, 8N1** (clean framing at this rate; steady ~150 B/s)
- Bus is **not** silent: the display polls continuously at ~5.4 Hz
- CAN side of the hat: nothing connected, totally silent

## Framing — CONFIRMED (1,659/1,659 frames parse with 0 stray bytes)

```
CD | addr | cmd | len | payload[len] | checksum
```

- `checksum` = two's complement of the sum of all preceding bytes
  (i.e. sum of the whole frame ≡ 0 mod 256)
- This is **not Modbus RTU** (fixed 0xCD start byte, 8-bit additive
  checksum rather than CRC16-LE, framing incompatible)

## Observed frames (display showing SOC = 100%)

| frame (hex)                        | rate    | interpretation (working theory)      |
|------------------------------------|---------|--------------------------------------|
| `cd 81 11 00 a1`                   | 5.4 Hz  | poll/keepalive to addr 0x81          |
| `cd 82 11 00 a0`                   | 5.4 Hz  | poll/keepalive to addr 0x82          |
| `cd 02 00 00 31`                   | 5.4 Hz  | status poll to addr 0x02 (shunt)     |
| `cd 02 00 01 42 ee`                | 5.4 Hz  | status response, payload = bitfield  |
| `cd 03 00 00 30`                   | 5.4 Hz  | poll to addr 0x03                    |
| `cd 02 15 01 03 18`                | 0.37 Hz | request page 0x03 (= displayed page) |
| `cd 02 15 06 03 00 00 00 64 42 6d` | 0.37 Hz | page response: page 03, u32be **100** = SOC %, then status byte |

- Status byte is `0x42`, with bit `0x04` pulsing high for exactly one poll
  every ~4.8 s (heartbeat / "measurement updated" flag, not a measurement).
- The display only requests the page(s) for the screen it is currently
  showing (`cmd 0x15`). Response value is a **signed 32-bit big-endian**
  integer; `-1` (0xFFFFFFFF) = not available (blank on the display).

## Page map (confirmed by display cycling + load test, 2026-08-09)

| page | meaning                | unit    | evidence |
|------|------------------------|---------|----------|
| 0x03 | state of charge        | %       | =100 while display showed 100% |
| 0x04 | unknown (2nd current?) | —       | always -1 (N/A) on bench; polled together with 0x32 on the amps screen |
| 0x05 | battery voltage        | **mV**  | 13272/13284 while display showed 13.3 V |
| 0x06 | battery current        | **mA** signed | tracked load staircase: 0, -240/-340 (-0.3 A), -380 (-0.4), -480/-520/-580 (-0.5) |
| 0x0A | unknown (SOH?)         | —       | -1 while SOH page blank |
| 0x0B | unknown (time left?)   | —       | -1 while time-left page blank |
| 0x0C | unknown                | —       | -1 (third blank page) |
| 0x32 | unknown (config/flags) | —       | constant 0x83E00000 |

## Other commands

- `cmd 0x04`, empty request → 23-byte response:
  `42 01 21 03 11 0b fb 00 "BANK-01" 00…` — identity/bank-info read
  (contains the configured bank name in ASCII).
- `cmd 0x00` poll → 1-byte status response (the `0x42` bitfield above).
- `cmd 0x11` to addrs 0x81/0x82: keepalive/discovery, never answered on
  the bench.

## Real target: MC-618 regulators

The production bus has **two MC-618 alternator regulators** (configured as
Balmar IDs 0 and 1) instead of the bench's shunt+display. Their SmartLink
addresses and page codes are unmapped — capture with
`ballmar-reflector --dump` once connected. Every frame carries the device
address, so the two regulators separate cleanly into distinct Signal K
paths via `--map` (no bus re-addressing needed).

## Timing (measured 2026-08-09, bench)

Display as master — one 186 ms cycle (~5.4 Hz):

```
+  0 ms  cd 81 11 00 a1     keepalive 0x81
+ 36 ms  cd 82 11 00 a0     keepalive 0x82
+ 49 ms  cd 02 00 00 31     status poll -> shunt answers <10 ms
+ 68 ms  cd 03 00 00 30     poll 0x03 (never answered on bench)
         page request (cmd 0x15) inserted every ~2.7 s
```

Pi as master — rate sweep against the shunt:

| tick/page | status→page gap | answered | latency (median/p95) |
|-----------|-----------------|----------|----------------------|
| 333 ms    | 10 ms           | 92 %     | 13 / 24 ms |
| 150 ms    | 10 ms           | 96 %     | 15 / 30 ms |
| 75 ms     | 10 ms           | **99 %** | 10 / 27 ms |
| 40 ms     | 10 ms           | 97 %     | 10 / 25 ms |
| 75 ms     | 2 ms            | 15 %     | — |
| 75 ms     | 0 ms            | **0 %**  | — |

Conclusions:

- Poll rate is not a constraint (shunt keeps up at 25 req/s).
- **≥10 ms of quiet between consecutive TX frames is mandatory** —
  back-to-back frames are ignored entirely.
- After bus silence the shunt idles and ignores page reads until
  master-style traffic (status polls) resumes; the reflector's poll
  mode handles the wake automatically.
- Master handover works: Pi requests interleaved with a live display
  cause no visible disruption (tested before display removal).

## Open questions

- Page codes for SOH / time-to-go / Ah (all N/A on the bench w/ full battery)
- MC-618 addresses and page map (field %, alt temp, target voltage, stage)
- Which addresses 0x81/0x82/0x03 are (SmartLink discovery slots?)
- Direction attribution (single shared pair — TX/RX merged in capture)

---

# Earlier notes (UNVERIFIED — contradicted by the capture above)

The claims below (Modbus RTU, 9600 baud, silent-until-polled, MC-618
register map) do **not** match the observed bus and appear to be
AI-confabulated. Kept for reference only.

The reason you are seeing electrical silence on your test bench is because Balmar?s SmartLink protocol relies on a strict Master-Slave (Request-Response) architecture, where the regulators (Slaves) remain completely silent until a gateway or display (Master) broadcasts a poll command.
You do not need to guess how to "ask" for the data. Because your PiCAN-M HAT has an RS-485 port, you can make your Raspberry Pi act as the network Master. By using Python to send a simple 8-byte hexadecimal request command down the wire every second, you can trick the MC-618 regulators into dumping all of their internal metrics.
## The SmartLink Modbus Request Command
Balmar SmartLink over RS-485 is built natively on top of the industry-standard Modbus RTU protocol. To pull the metrics for Charge Rate, Amps, Field Strength, and Temperatures, your Pi needs to broadcast a standard "Read Holding Registers" command (Modbus Function Code 03).
The exact hexadecimal command string to request the primary data block from Regulator 0 (ID:0) looks like this:

00 03 00 00 00 0A C5 CD


* 00 = Target Device ID (Regulator 0)
* 03 = Modbus Function Code (Read Registers)
* 00 00 = Starting Register Address (0)
* 00 0A = Number of registers to read (10 registers contains all the telemetry)
* C5 CD = The Modbus CRC Checksum (Calculated security code so the regulator knows the command is valid)

To pull the data from Regulator 1 (ID:1), you send the exact same command, but shift the first byte to target ID 1 (which changes the final CRC checksum calculation):

01 03 00 00 00 0A C4 3C

## The 10-Line Python "Asker" Script
You can use a tiny Python script running natively on your Pi to act as the master loop. This script sends the hex requests through your PiCAN-M's RS-485 serial port (/dev/ttyS0) every second, reads the raw responses, and pumps them straight over the network to your remote server:

import serialimport timeimport socket
# Setup the serial port on the PiCAN-M HAT (9600 baud, 8 data bits, 1 stop bit, no parity)ser = serial.Serial('/dev/ttyS0', 9600, timeout=0.5)udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)REMOTE_IP = "192.168.1.50"  # Replace with your distant Signal K server IP
# Hex commands to poll Regulator 0 and Regulator 1poll_reg_0 = bytes.fromhex("00 03 00 00 00 0A C5 CD")poll_reg_1 = bytes.fromhex("01 03 00 00 00 0A C4 3C")
while True:
    for cmd in [poll_reg_0, poll_reg_1]:
        ser.write(cmd)          # Ask the regulator for data
        response = ser.read(25) # Read the 25-byte binary response it spits back
        
        if response:
            # Broadcast the raw data over UDP to your remote location
            udp_socket.sendto(response, (REMOTE_IP, 3000))
            
    time.sleep(1.0) # Repeat every second

## How to Decode the Response at Your Remote Server
When the MC-618 hears that hex command, it will instantly reply with a 25-byte payload. You do not have to reverse-engineer the bytes; they map directly to standard 16-bit integer values:

* Bytes 3 & 4: Output Voltage (Multiply by 0.1 to get Volts, e.g., 142 = 14.2V)
* Bytes 5 & 6: Alternator Amps (Direct reading in Amperes)
* Bytes 7 & 8: Field Strength Percentage (Direct reading, 100 = 100% Full Field)
* Bytes 9 & 10: Alternator Temperature (Direct reading in Celsius)

Because your remote Signal K server will receive these raw hex lines cleanly over UDP, you can easily use the standard Signal K Modbus Plugin or a basic Node-RED flow on the receiving end to parse these exact byte positions into your marine dashboard, completely cutting out the buggy Bluetooth app.
Do you need help calculating the specific hex codes for any other Balmar devices on your network, or are you ready to test this polling script on your bench?

