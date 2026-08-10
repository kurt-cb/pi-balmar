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

## Future options

### Long-term host: Victron Cerbo GX MK2

The Cerbo (already aboard) may replace the Pi entirely:

- **Venus OS Large** runs a Signal K server on the Cerbo → reflector
  sends deltas to `127.0.0.1`, eliminating the WiFi hop.
- No native RS-485: use an **isolated USB→RS-485 adapter**
  (`/dev/ttyUSB0`); SmartLink's RS-485 tolerates the cable run from the
  engine bay to the Cerbo's mounting spot.
- Reflector is stdlib-only by design: copy `src/` to `/data` (survives
  firmware updates), run with `--device /dev/ttyUSB0`, hook via
  `/data/rc.local`.
- Later possibility: a Victron D-Bus service (dbus-serialbattery style)
  to surface Balmar data on the GX touchscreen and VRM.
- Sequencing: finish the MC-618 decode on the Pi rig first; migrate
  once the page map is stable.

### RV-C input on the hat's CAN port (planned)

The full production system is bigger than the two MC-618s:
**4 shunts** on the house batteries, the alternator charger, and a
**Xantrex SW3000 inverter/charger**. The SW3000 speaks **RV-C** —
250 kbit/s CAN, J1939 framing, publicly documented DGNs
(DC_SOURCE_STATUS_1/2/3, CHARGER_STATUS, INVERTER_STATUS, …) — and
will be wired to the hat's currently unused CAN port.

Plan: add an `rvc.py` input module (stdlib SocketCAN, like the
original CAN prototype) decoding the relevant DGNs table-driven from
the RV-C spec, feeding the same delta pipeline/config as SmartLink
(devices keyed by RV-C source address or instance, `values` names,
`prefix`). No reverse engineering required.

Wiring notes (SW3000 tap):
- The hat's CAN port is **galvanically isolated** (B0505LS-1W +
  digital isolator, SM24CANB TVS, polyfuses) — no ground-loop risk.
- The SW3000's two RJ-45 jacks are daisy-chain ports (same bus). If
  the empty one holds a **terminator plug**, displacing it needs the
  hat's 120 Ω CAN jumper enabled to re-terminate.
- **Check the manual's RJ-45 pinout before crimping** — Xanbus-style
  ports carry ~15 V network power on some pins. Connect CAN-H/CAN-L
  only.
- First contact in **listen-only mode** (cannot disturb the display's
  session, not even ACK bits):
  `ip link set can0 up type can bitrate 250000 listen-only on`
- Verify on the wire whether frames are RV-C or Xantrex Xanbus —
  Freedom SW gear is historically Xanbus; both are 250 kbit/s CAN,
  the DGN/PGN layout decides what rvc.py must decode.

Also to verify: Balmar SG200 documentation suggests SmartLink is
RV-C-aligned, and an **RV-C mode** on the SG200 may expose more data
than the display protocol — check the manual / test on the bench.

### NMEA 2000 reporting

Via the Pi hat's unused CAN port (shared with RV-C? bitrates match at
250k, but RV-C and N2K on one physical bus is nonstandard — likely
pick one per port) (drop cable to the
backbone): standard PGNs cover volts/amps/temp (127508), SOC/SOH/
alternator DC-type (127506), charge stage (127507). Needs an N2K
sender implementation (ISO address claim + fast-packet). Field % /
target voltage stay Signal K-only. MFD visibility is the payoff.
