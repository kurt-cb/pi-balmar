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

## Boat bus survey (2026-08-12, engine OFF — regulators unpowered)

First capture on the production bus. Quality is good: 595 frames /
3,074 bytes in 20 s with **0 bad bytes**, so wiring and termination are
sound.

`--discover` (full 0x00-0xFF scan) — exactly one responder:

```
addr   status  name       header
0x02   0x00    GATEWAY    00 03 23 04 17 00 00 00
```

`0x02` "GATEWAY" is the Balmar **BLE** gateway. The Balmar **NMEA 2000**
gateway is a *separate* device and was **not connected** during any of
this session's captures — do not conflate the two.

**The BLE gateway IS the bus master** — CONFIRMED 2026-08-12 by
unplugging it: the bus went completely silent (no frames at all,
including the regulator polling) and resumed the instant it was
replugged.

It is easy to get this wrong, and earlier revisions of this document did
so twice — first guessing an unseen SG230, then blaming the N2K device.
The BLE gateway also *answers* status polls at `0x02`, which looks like
slave behaviour and implies some other device must be polling it. It
does both. Do not infer the master from "who answers a probe": masters
generally do not answer, so an address scan finds only the slaves.

This also explains the missing Signal K battery data: `electrical.batteries`
is absent from both Signal K servers simply because **the N2K gateway is
unplugged**. Nothing is publishing. It was never a CAN-bitrate or
gateway-fault problem.

One ~200 ms cycle (~5 Hz), identical in shape to the bench display's
cycle:

```
0x02 cmd 0x00  status poll   -> gateway answers 0x00
       ~124 ms idle
0x81 cmd 0x11  (no reply) ─┐  17-18 ms between unanswered polls
0x82 cmd 0x11  (no reply)  │  = the master's response timeout
0x81 cmd 0x11  (no reply)  │
0x82 cmd 0x11  (no reply) ─┘
```

Readings:

- **`0x81` / `0x82` are almost certainly the two MC-618s** (Balmar IDs
  0/1) — two addresses, polled twice per cycle, silent only because the
  engine is off. The bench saw the same two addresses polled with no
  regulators present, so the master polls these slots unconditionally.
- **`cmd 0x11` carries no page byte** (len 0), unlike the SG200's
  per-page `cmd 0x15`. A len-0 request implies the reply is a **bulk
  data block** — many fields in one frame — rather than one value per
  request. UNCONFIRMED until a regulator answers.
- Caution when that first reply lands: `FrameParser.MAX_PAYLOAD` is 32.
  A larger regulator block would be silently discarded as bad bytes, so
  captures must retain **raw** bytes, not just parsed frames
  (`tools/buslog.py`).

### Operational consequence: run PASSIVE on this bus

The boat bus already has a permanent master polling at 5 Hz. The
reflector must therefore run **passive** here — `--poll` would put a
second master on the wire and collide with it. Passive is sufficient
precisely because the master polls continuously, so regulator data will
appear on the wire without us transmitting. (This supersedes the bench
plan of "remove the display, then poll".) `--discover` does transmit;
avoid running it repeatedly while the master is active.

## MC-618 regulators (2026-08-12, engines RUNNING)

The regulators are powered from an oil-pressure switch, so they only
exist on the bus once an engine is running. They boot and wait a few
seconds before enabling the field.

### Identity — both units name themselves

```
0x81  name='MC-618-STBD'  header=00 01 18 05 01 00 07 00   -> HOUSE bank
0x82  name='MC-618--POR'  header=00 01 18 05 01 00 07 00   -> ENGINE START
```

Names come from the cmd 0x04 identity read, so the reflector's
name-keyed config resolves these without depending on bus addresses.

### The master never asks for regulator data

The bus master polls `0x81`/`0x82` with **cmd 0x11 only** — a keepalive,
answered with a 1-byte `00`. Across 130k+ passively captured frames there
was never a single cmd 0x15 page request to a regulator.

**Passive listening can therefore never yield MC-618 data.** Unlike the
SG200 bench setup (where the display requested pages and we could listen
in), regulator data only exists if we request it ourselves.

### Response format differs from the SG200 — code change required

MC-618 page responses are **5 bytes** (`page + 4-byte big-endian value`),
not the SG200's 6 (`page + value + status`):

```
SG200 :  cd 02 15 06 | page v3 v2 v1 v0 status | cksum
MC-618:  cd 81 15 05 | page v3 v2 v1 v0        | cksum
```

`smartlink.decode_page_response()` requires `len(payload) == 6` and so
**rejects every MC-618 reply**. It needs to accept 5-byte payloads with
status = None before the reflector can read regulators at all.

Also note `FrameParser.MAX_PAYLOAD = 32`: fine for what we have seen, but
raise it on the instance when probing unknown pages.

### Page sweep (cmd 0x15, pages 0x00-0xFF, both units)

~40 pages answer. Cross-comparing the two units separates live data from
configuration: pages identical on both are settings, pages that differ
are per-device measurements.

Live (differ between units, and drift over time):

| page | STBD (house) | PORT (start) | behavior |
|------|--------------|--------------|----------|
| 0x06 | 154 | 153 | oscillates ±1 |
| 0x08 | 20  | 17  | oscillates ±1 |
| 0x0B | 642 | 656 | slow drift |
| 0x25 | 8   | 4   | differs |
| 0x37 | 129 | 130 | differs |
| 0x13 | 1765564748 | 1741274483 | static — serial number |

Static on both (configuration):

```
0x02=3  0x04=719  0x0F=42  0x24=4  0x26=90  0x29=1
0x2A=769 0x2B=740 0x2C=719 0x2E=709 0x30=670 0x32=635   <- charge profile
0x2D=3  0x2F=3  0x31=3                                   <- profile flags?
0x33=186 0x34=162 0x35=65 0x36=65 0x39=106
```

### Voltage scaling — CONFIRMED: raw / 50 (0.02 V per count)

Established by capturing the display while it was briefly working, then
matching against what the operator read off its screen:

```
0x81 page 0x03 over the display window: 706 -> 668
     708 / 50 = 14.16 V     <- display showed "14.1 / 14.2", charging
     668 / 50 = 13.36 V     <- after the alternator dropped off
```

Applying /50 to the voltage-shaped pages yields a textbook Balmar 12 V
profile, which is strong corroboration:

| page | raw | volts | reading |
|------|-----|-------|---------|
| 0x03 | 668-708 | 13.36-14.16 | **measured battery voltage** (live) |
| 0x04 | 671 | 13.42 | **active target voltage** |
| 0x2C | 719 | 14.38 | absorption setpoint |
| 0x2E | 709 | 14.18 | intermediate setpoint |
| 0x30 | 670 | 13.40 | float setpoint |

`0x04` tracking `0x30` (13.42 vs 13.40) means the regulator was in
**float**, targeting the float setpoint — internally consistent.

The wider 0x2A-0x32 run (769, 740, 719, 709, 670, 635 = 15.38, 14.80,
14.38, 14.18, 13.40, 12.70 V) is the full programmable setpoint table.

### What the display actually polls

Captured passively while the display was up — 1,160 page-read frames
across both regulators. This is Balmar's own firmware telling us which
pages matter:

```
0x03, 0x04, 0x08, 0x05, 0x06, 0x02, 0x24, 0x2E, 0x30, 0x26, 0x2C
```

### Confirmed MC-618 pages

| page | scale | meaning | how confirmed |
|------|-------|---------|---------------|
| 0x03 | /50 | **battery voltage** | display read 14.36 V; page read 718. Also cross-checked against the Xantrex measuring the same house bank over Xanbus: 715 = 14.30 V vs 14.25-14.27 V — two independent protocols agreeing within 0.05 V |
| 0x08 | raw | **field %** | display read 24 %; page read 24 (osc. 24-26) |
| 0x05 | — | **battery temperature** | display showed `--` with the sensor unplugged; page read 255 (0xFF = not available) |
| 0x06 | **raw - 114** | **alternator temperature, °C** | 154 -> 40 C, 156 -> 42 C, 158 -> 44 C (three labelled points). NOTE: a one-point fit of `/2 - 36` also reproduces 42 C but predicts 43 where the display reads 44 — one point cannot determine both scale and offset |
| 0x04 | /50 | active target voltage | tracks 0x30 in float, 0x2C in absorption |
| 0x2C | /50 | absorption setpoint (14.38 V) | textbook Balmar profile |
| 0x2E | /50 | intermediate setpoint (14.18 V) | " |
| 0x30 | /50 | float setpoint (13.40 V) | " |

`0x02` = **charge stage**, confirmed by catching a transition: it moved
`3 -> 6` on both regulators at the same moment the active target `0x04`
dropped from the absorption setpoint (719 = 14.38 V) to the intermediate
setpoint (709 = 14.18 V), while candidate `0x0C` stayed at 0 throughout.
Enum values so far: **3 = "fixed bulk"** (display-labelled); 6 = the
stage entered when leaving absorption (not yet labelled).

Still unidentified: `0x07` (255), `0x0C` (0), `0x24` (4), `0x25` (4 on
PORT vs 8 on STBD — per-device state, not config), `0x26` (90 on both).

### Max field % — a regulator setting, read once at display startup

The display shows a per-regulator "max field" (60 % STBD, 80 % PORT).
It did not appear in any steady-state capture, but that is **not**
evidence it is absent from the bus:

- The capture began after the display had already booted, so a value
  fetched once at startup would never be seen.
- It is static configuration — it cannot change without a setup change,
  so there is no reason for the display to re-read it.
- The full 0x00-0xFF sweep is not a reliable negative either: it
  demonstrably dropped pages to collisions with the master (0x03 missing
  on STBD, 0x30 missing on PORT, both of which certainly exist).

**To capture it: power-cycle the display while recording.** The boot
config read should show 60 on STBD and 80 on PORT — differing values on
the two units make the identification unambiguous. The same capture
should reveal any other write-once configuration the display reads at
start and never requests again.

### Bus topology — the display bridges TWO segments

```
SG-200 shunt  ->  display port 1        <- NOT visible to the Pi
display port 2  ->  STBD charger  ->  Pi   <- our tap
```

This matters for interpreting captures: **the display mirrors its master
poll cycle out both ports**, so a poll seen on our segment does not mean
the target is on our segment. Address `0x03` (the shunt) is polled on
port 2 and never answered there, because the SG-200 replies on port 1.
Do not conclude "polled here, so it must live here" — an earlier
revision of this document made exactly that error.

Consequence: MC-618 regulator data reaches our tap, but **SG-200 shunt
data cannot be seen from it**. Reading both would need a second tap on
port 1 — the HAT's unused `RS485_1` / `/dev/ttySC1` is available for
that.

Full raw sweep: `/root/captures/2026-08-12/regprobe.out` on rpcan2.

### CAUTION: sustained polling appears to hang a regulator

`0x81` stopped answering the bus entirely — including the master's own
keepalives — after receiving two full page sweeps plus ~420 rapid page
reads (~22/s). It never recovered while the engine kept running.

- It kept **regulating normally** throughout: the Xantrex measured a
  steady 14.27 V on the house bank the whole time. Comms-only fault.
- `0x82` received the same two sweeps but no sustained polling, and
  stayed at a 100% reply rate for the rest of the session.
- The BLE app was **ruled out** as the cause: two scans were recorded
  with our bus fully passive, and `0x82` held 100% through both.

Not proven, but the asymmetry points at polling rate. Before polling
regulators continuously, test on a known-expendable unit: ~1 page/s,
placed in the ~124 ms idle gap of the master's cycle, aborting the
moment a reply is missed.

### Gateway status byte (addr 0x02)

Bit 3 (`0x08`) = **BLE app active** — confirmed by pressing scan and
watching the status flip `0x00` -> `0x08` at that exact second.

## Open questions

- Whether `cmd 0x11` returns a bulk block, and its field layout
  (resolves the moment the engine runs)
- Page codes for SOH / time-to-go / Ah (all N/A on the bench w/ full battery)
- MC-618 field map (field %, alt temp, target voltage, stage)
- Confirm the master is the SG230 (inferred: transmits, never answers)
- Whether the 0x02 gateway already bridges SG230 data to NMEA 2000 — if
  so the reflector only needs to add the MC-618 regulator data
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

