# Implementation plan — boat installation

## Hardware

**Waveshare RS485 CAN HAT (B)** ([wiki](https://www.waveshare.com/wiki/RS485_CAN_HAT_(B)),
[schematic](https://files.waveshare.com/upload/c/cb/RS485-CAN-HAT-B-schematic.pdf))

| item | spec |
|------|------|
| Power input | screw terminal P2, **DC 8–28 V**, reverse-polarity diode (SS34) |
| 5 V converter | **MP1584** buck → powers the Pi via GPIO header; 3 A peak, ~1.5–2 A practical continuous |
| Power mux | P-MOSFET ideal-diode between buck and Pi 5 V rail — USB and 12 V may be connected simultaneously, no back-feed (safe migration) |
| Logic rails | RT9193-33 LDOs (3.3 V, 300 mA) — hat logic only, not the Pi |
| Isolation | B0505LS-1W isolated supply + Π163M31 digital isolator for RS-485/CAN side |
| Bus protection | SM712 TVS on both RS-485 channels, SMAJ12CA/SMAJ6.5CA clamps, polyfuses on all bus lines, SM24CANB on CAN, 1 nF/1 kV Y-cap to **Earth pad** (bond in engine bay) |
| RS-485 | SC16IS752 (SPI1) + 2× SP3485 → `/dev/ttySC0` (RS485_0, SmartLink), `/dev/ttySC1` spare |
| CAN | MCP2515 + SN65HVD230 → `can0` — unused; available for future NMEA 2000 reporting |
| Termination | onboard 120 Ω via jumpers (RS-485 and CAN) |

## Compute

**Pi 4** (already configured, Alpine "rpcan2"). Zero 2 W would be
right-sized (~1–2 Ah/day vs ~6 Ah/day at 12 V) but is unobtainable;
Pi 4 draw is acceptable. Engine bay ambient is only ~100 °F — no
thermal concern for either the Pi or the MP1584 at headless load.

## Power wiring

- Fused (1–2 A) feed from a switched 12 V circuit → hat screw terminal.
- Bond the hat's Earth pad to bonding ground.
- Engine cranking can sag below the 8 V input floor → Pi may reboot;
  harmless (diskless Alpine, reflector auto-restarts via OpenRC).
- Migration: bench-test on 110 V USB with the 12 V terminal connected —
  the power mux makes the handover seamless; pull USB once verified.

## Deployment sequence (boat)

1. Move Pi + hat to the boat's dev system; SmartLink (MC-618 ×2,
   Balmar IDs 0/1) on RS485_0 A/B, display attached initially.
2. Capture with `--dump` while regulators are powered and charging;
   cycle the display through regulator screens.
3. Map MC-618 SmartLink addresses + page codes (field %, alt temp,
   target voltage, stage); extend `PAGES` in `smartlink.py` and
   `docs/rs-485 decode.md`.
4. Signal K server: add data connection — Input Type **Signal K**,
   protocol **UDP**, port **4123**.
5. Run reflector with `--map` per regulator (e.g.
   `electrical.alternators.0/1`); verify paths in Signal K.
6. Test `--poll` (Pi as bus master), remove display, confirm data.
7. Install OpenRC service (`openrc/ballmar-reflector.initd`),
   `lbu commit -d`.

## Future option

NMEA 2000 reporting via the hat's unused CAN port (drop cable to the
backbone): standard PGNs cover volts/amps/temp (127508), SOC/SOH/
alternator DC-type (127506), charge stage (127507). Needs an N2K
sender implementation (ISO address claim + fast-packet). Field % /
target voltage stay Signal K-only. MFD visibility is the payoff.
