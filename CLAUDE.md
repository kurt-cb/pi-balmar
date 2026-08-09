# pi-ballmar — Balmar SmartLink → Signal K reflector

## What this is

A Raspberry Pi reads Balmar SmartLink (RS-485) battery/charging data and
reflects it over WiFi as Signal K delta JSON via UDP. No Signal K plugin
exists for SmartLink — the protocol was reverse-engineered here (see
`docs/rs-485 decode.md`) and decoding happens on the Pi.

## Hardware

- **Pi:** "rpcan2", Alpine Linux (OpenRC, `apk`, PEP 668 → use venv,
  `lbu commit -d` if diskless). SSH: `kgodwin@` or `root@` (192.168.4.70
  on the bench network).
- **Hat:** Waveshare RS485 CAN HAT (B). SmartLink is wired to **RS485_0
  A/B** → `/dev/ttySC0` @ 115200 8N1 (`sc16is752-spi1` overlay). The CAN
  side is unused — SmartLink is NOT NMEA 2000.
- **Bench devices:** SG200 shunt (addr `0x02`, bank "BANK-01") + display.
- **Production target:** two **MC-618 regulators** (Balmar IDs 0 and 1)
  on one SmartLink bus, on the boat's development system, with the
  Signal K server there too.

## Code layout

- `src/ballmar_reflector/smartlink.py` — frame parser, checksum, page
  table (`PAGES`), page-request builder. Extend `PAGES` as new pages are
  decoded.
- `src/ballmar_reflector/app.py` — daemon: passive listen or `--poll`
  (bus master) mode; `--map ADDR=signalk.path.prefix` per device;
  `--dump` for protocol discovery.
- `src/ballmar_reflector/signalk.py` — delta JSON + UDP.
- `openrc/` (Alpine service), `systemd/` (non-Alpine), `docs/setup.md`.
- Stdlib only — deployable by copying `src/` if pip is unavailable.

## Protocol in one line

`CD | addr | cmd | len | payload | cksum` (sum≡0 mod 256); cmd `0x15`
page reads, values s32 BE, −1 = N/A; confirmed pages: `0x03` SOC %,
`0x05` mV, `0x06` mA. Full details: `docs/rs-485 decode.md`.

## Plan / next session (on the boat)

1. Hook the Pi + display to the bus with both MC-618s powered and
   charging; `--dump` (or the capture scripts) while cycling the
   display through regulator screens.
2. Map MC-618 addresses + page codes (field %, alternator temp, target
   voltage, charge stage); extend `PAGES` and `docs/rs-485 decode.md`.
3. Configure Signal K server: data connection, Input Type **Signal K**,
   **UDP**, port **4123**.
4. Run reflector with `--map` for both regulators (e.g.
   `electrical.alternators.0/1`), verify data in Signal K.
5. Test `--poll` mode (Pi as bus master), then remove the display and
   confirm data still flows; install the OpenRC service.

## Useful commands

```
PYTHONPATH=src python3 -m ballmar_reflector.app --dump   # no install needed
ballmar-reflector --host <signalk> --port 4123 -v
```
