# Xanbus wire protocol — decoded from live capture (2026-08-12, on the boat)

Captured on rpcan2 via the Waveshare RS485 CAN HAT (B) `can0`, tapped to
the SW3000. Every field below was confirmed by correlating against panel
readings taken at the same moment, at two or more different operating
points — not inferred from byte patterns alone.

## Link layer

- **250 kbit/s** (NOT 500 k — see "Gotchas" below), 29-bit extended IDs.
- J1939/NMEA-2000 ID layout: `prio(3) | EDP | DP | PF(8) | PS(8) | SA(8)`.
  PF ≥ 240 → broadcast, PGN includes PS; PF < 240 → PS is the destination.
- Multi-frame messages use **NMEA 2000 fast-packet**: byte0 =
  `(sequence << 5) | frame_index`; on frame 0 byte1 = total payload
  length and payload starts at byte 2; later frames carry 7 bytes each.
- Two nodes observed: `0x01` (SW3000, most traffic) and `0x00` (SCP).
- Standard ISO PGNs present: 60928 address claim, 59904 ISO request.

### It is Xanbus, not RV-C

RV-C is also J1939-based at 250 k, so it is a reasonable guess, but the
capture rules it out: **RV-C DGNs live at 0x1FF00–0x1FFFF (PGN
130816–131071) and not a single frame appeared in that range.** RV-C also
does not use NMEA 2000 fast-packet, which every multi-frame message here
does. Xanbus is NMEA 2000-derived framing carrying Xantrex-proprietary
PGNs.

Consequence: canboat/Signal K parse the *framing* fine but have no field
definitions for these PGNs, so nothing becomes `electrical.*` on its own.
The SW3000 never emits standard 127506/127508/127509, so decoding has to
happen here — same situation as SmartLink.

## Field map — CONFIRMED

All multi-byte values are **little-endian** (note: N2K standard PGNs are
big-endian; these proprietary ones are not).

### PGN 126979 — AC input status (src 0x01)

| off | type | unit | meaning | observed | panel |
|-----|------|------|---------|----------|-------|
| 4  | u32 | mV      | AC input voltage | 119210 | 119 V |
| 8  | u32 | mA      | AC input current | 7060 | 7 A |
| 12 | u16 | 0.01 Hz | line frequency | 6001 = 60.01 Hz | — |
| 16 | u32 | W       | apparent power | 841 | 119.21 × 7.06 = 841.6 |
| 14 | u16 | 0.01 A  | mirrors the charge-rate setpoint | 500 | 5 A |

### PGN 126982 — AC output / load status (src 0x01)

Same shape as 126979 but shifted one byte later.

| off | type | unit | meaning | observed | panel |
|-----|------|------|---------|----------|-------|
| 5  | u32 | mV      | AC output voltage | 117638 | 118 V |
| 9  | u32 | mA      | AC output current | 5780 | 6 A |
| 14 | u16 | 0.01 Hz | frequency | 6001 = 60.01 Hz | — |
| 18 | u32 | W       | power | 686 | 117.6 × 5.78 = 679.8 |

### PGN 127172 / 127173 — DC status

Identical layout. **127173 reports the same current with the opposite
sign** — two measurement points (battery terminal vs inverter DC side).

| off | type | unit | meaning |
|-----|------|------|---------|
| 2  | u32 | mV      | DC voltage |
| 6  | s32 | mA      | DC current (charge positive on 127172) |
| 10 | u32 | W       | DC power |
| 19 | u16 | 0.01 K  | temperature |

Confirmed at two operating points:

```
charging:   13920 mV  +19610 mA  272 W  31500 = 315.00 K = 107.3 F
throttled:  12510 mV   +3650 mA   45 W  31700 = 317.00 K = 110.9 F
```

Power self-checks: 13.92 × 19.61 = 273 W ✓, 12.51 × 3.65 = 45.7 W ✓.
Temperature matched panel readings of 107 °F and 111 °F.

### PGN 75264 — settings (addressed, PF 0x26)

45-byte payload, mostly `0xFF` padding, with a value/min/max triple at
the tail:

| off | type | unit | meaning |
|-----|------|------|---------|
| 39 | u16 | 0.01 A | **PowerShare / AC input current limit** ("PS" on the panel) |
| 41 | u16 | 0.01 A | minimum settable (500 = 5.00 A) |
| 43 | u16 | 0.01 A | maximum settable (3000 = 30.00 A) |

Confirmed by changing the setting on the panel and watching the field:
`3000 → 1500 → 500 → 1000` for 30 A → 15 A → 5 A → 10 A.

**It limits AC input, not DC charge current.** PowerShare caps what the
unit draws from shore; the charger then converts whatever AC headroom is
left after the AC loads. So "PS15" does not cap DC at 15 A — it only
becomes visible when it is tight enough to starve the charger:

- Set to 15.00 A, measured AC input was 11.99 A (below the limit, so not
  binding) while DC charging ran at 37 A — far above "15 A".
- Set to 5.00 A, DC collapsed to ~3.5 A and voltage sagged 13.9 → 12.5 V.
  5 A × 117 V ≈ 585 W, while AC loads alone drew ~686 W, so nothing was
  left for the charger.

Corroborating: the 5–30 A range matches shore-cord sizing, and the value
is mirrored in **126979 off 14 — the AC *input* status PGN**.

### Energy balance check

One capture validates the whole map at once:

```
AC in    117.108 V x 11.990 A = 1409 W
AC loads 116.952 V x  6.620 A =  771 W
DC out    13.340 V x 36.980 A =  493 W
                       losses ~ 145 W  (~90% efficiency)
```

## Not yet identified

- PGN 126990 — static (`14300 mV` + `150000`); the 150000 is NOT the
  charge limit (that range is 5–30 A). Left unidentified rather than
  guessed.
- Charge stage (bulk/absorption/float) — candidate enum bytes in 126991
  (`03 04 03 03 02 00` vs `...03 00`) and 126979 off 17/21, which moved
  4 → 3 when the charger throttled. Needs a deliberate stage transition
  to confirm.
- 127004, 127165, 127167, 129033, 129038.
- Inverter on/off state: switching the inverter on with no AC load
  produced no isolable change; retest under load.

## Gotchas

- **`/etc/network/interfaces` had `bitrate 500000`.** At the wrong bit
  rate the interface sits in `ERROR-PASSIVE` and receives *nothing* —
  which looks exactly like "the gateway is broken". Now set to 250000
  with `listen-only on`; original saved as
  `/etc/network/interfaces.bak-500k`.
- Use **`listen-only`** on a live inverter bus: the Pi then never
  transmits or even ACKs, so it cannot disturb the Xantrex network.
  Remove it only if you intend to send commands.
- Single-frame PGNs are easy to miss: a fast-packet reassembler that
  assumes byte0 is a sequence header will silently drop them, and small
  enum/state fields (inverter on/off, charge stage) live there.

## Tools

`tools/candump.py` (PGN tally), `tools/xanbus.py` (fast-packet
reassembly + ground-truth correlation), `tools/xanmon.py` (live V/I),
`tools/xanwatch.py` (record a setting change and its effect),
`tools/xansnap.py` / `tools/xansingle.py` (snapshot + diff to isolate a
field). All stdlib-only, using Python's `AF_CAN` socket support.

---

# Reference: Xanbus RJ45 pinout

On a Xantrex Freedom SW 3000 inverter/charger, the CAN_H and CAN_L communication pins are located inside the standard RJ45 (8-pin) Xanbus ports. Xantrex utilizes a CAN-based network protocol called Xanbus to link devices like the System Control Panel (SCP) or automatic generator starters. [1, 2, 3, 4, 5] 
## Xanbus RJ45 CAN Bus Pinout
The ports adhere to the standard T568A wiring profile. When looking directly at the front of an RJ45 modular plug with the release tab facing away from you (or looking into the female port), the pins are numbered 1 to 8 from left to right: [6, 7] 

* Pin 4: CAN_L (Conductor Name: CAN_L, typically the Solid Blue wire)
* Pin 5: CAN_H (Conductor Name: CAN_H, typically the White/Blue striped wire) [6, 7] 

## Full Port Reference
If you are building custom communication cables or integrating a third-party battery management system (BMS), it is critical to know what the surrounding pins do so you do not accidentally short out the system:

| Pin Number | Conductor Name | Description | Standard CAT5 Color (T568A) |
|---|---|---|---|
| 1 | NET_S | +15 VDC Network Power | White/Green |
| 2 | NET_S | +15 VDC Network Power | Green |
| 3 | NET_C | Network Common / Ground | White/Orange |
| 4 | CAN_L | CAN Bus Low Signal | Blue |
| 5 | CAN_H | CAN Bus High Signal | White/Blue |
| 6 | NET_C | Network Common / Ground | Orange |
| 7 | NET_S | +15 VDC Network Power | White/Brown |
| 8 | NET_C | Network Common / Ground | Brown |

## Important Installation Warnings

* Do Not Use Crossover Cables: Standard Xanbus networking requires straight-through CAT5 or CAT5e patch cables. A crossover cable can map the 15V power pins directly into the CAN communication lines, instantly destroying the networking board inside the inverter. [6, 8] 
* Termination Resistance: The network operates at a specific baud rate and requires a network terminator (a male or female RJ45 plug with a 120-ohm resistor across Pins 4 and 5) inserted into any empty Xanbus port at both ends of the physical network chain. [6, 9] 

Are you trying to connect a third-party lithium battery (BMS) to the Xantrex, or are you trying to read data using an Arduino/Raspberry Pi? If you tell me what you're connecting, I can help you with the protocol or wiring requirements.

[1] [https://xantrex.com](https://xantrex.com/products/accessories/freedom-sw-xanbus-automatic-generator-start/)
[2] [https://www.scribd.com](https://www.scribd.com/document/855740648/Book-Davide-Andrea-Lithium-Ion-Batteries-and-Applications-A-Practical-and-Comprehensive-Guide-to-Lithium-Ion-Batteries-Vol2)
[3] [https://xantrex.com](https://xantrex.com/products/accessories/freedom-sw-xanbus-automatic-generator-start/)
[4] [https://inverterservicecenter.com](https://inverterservicecenter.com/xanbus-system-control-panel-xantrex-809-0921)
[5] [https://www.donrowe.com](https://www.donrowe.com/Xantrex-808-9010-Freedom-SW-Remote-Adapter-p/808-9010.htm)
[6] [https://xantrex.com](https://xantrex.com/wp-content/uploads/2023/03/Xanbus_System_975-0136-01-01_IM.pdf)
[7] [https://studylib.net](https://studylib.net/doc/18301942/technical-note-powering-xanbus-network-through)
[8] [https://xantrex.com](https://xantrex.com/wp-content/uploads/2021/12/975-0731-01-01_Rev-C.pdf)
[9] [https://www.pilz.com](https://www.pilz.com/en-ZA/eshop/Connection-technology-and-education-systems/Connection-technology-and-education-systems/Cables-and-plug-in-connectors/Adapter/c/0011100248716980UK)
