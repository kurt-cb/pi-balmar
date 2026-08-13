# pi-ballmar — Balmar SmartLink → Signal K reflector

## What this is

A Raspberry Pi reads Balmar SmartLink (RS-485) battery/charging data and
reflects it over WiFi as Signal K delta JSON via UDP. No Signal K plugin
exists for SmartLink — the protocol was reverse-engineered here (see
`docs/rs-485 decode.md`) and decoding happens on the Pi.

## Hardware

- **Pi:** "rpcan2" — **Pi 3B+** (was a CM4 with no WiFi chip; SD + hat
  moved over 2026-08-09). Alpine Linux, sys-mode install (OpenRC, `apk`,
  PEP 668 → venv; **no lbu needed**). SSH: `kgodwin@` or `root@`; bench
  eth0 DHCP (last seen 192.168.4.89), wlan0 configured for kghome-e24 /
  kghome-lte (boat) / totaldns-uplink. Reflector runs as OpenRC service
  → `udp://kgboat-nmea2000.lan:4123` (boat Signal K box, Raspbian 13).
- **Hat:** Waveshare RS485 CAN HAT (B). SmartLink is wired to **RS485_0
  A/B** → `/dev/ttySC0` @ 115200 8N1 (`sc16is752-spi1` overlay).
  The **CAN side is now in use** for the Xantrex SW3000 (Xanbus) —
  `can0` @ **250 kbit/s listen-only** (see `docs/xantrex.md`). Xanbus is
  NMEA-2000-derived but SmartLink is not; they are separate buses.
- **Bench devices:** SG200 shunt (addr `0x02`, bank "BANK-01") + display.
- **Boat bus (confirmed 2026-08-12):**
  - `0x02` "GATEWAY" — Balmar BLE gateway, a slave. Status bit `0x08` =
    BLE app active.
  - `0x81` `MC-618-STBD` → **house** bank; `0x82` `MC-618--POR` →
    **engine start**. Powered from an oil-pressure switch, so they are
    absent until an engine runs.
  - An unidentified **bus master** (presumed SG230) polls continuously
    at ~5 Hz and never answers an address probe. **Run passive — do NOT
    use `--poll` here**, it would be a second master.

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

## Plan / next session (bring the Balmar display!)

The display is the blocker: it is the only reliable way to put labels on
the ~40 MC-618 pages already dumped (the BLE app is too flaky, and there
is no second instrument for the regulators the way the Xantrex was for
the house bank).

1. **First engine start: check `0x81`.** It hung after sustained polling
   last session and stayed mute until shutdown. If it answers again, the
   hang is recoverable — see the CAUTION in `docs/rs-485 decode.md`.
2. Hook up the display; read each labelled value and match it against a
   page sweep (`tools/regprobe.py`) taken at the same moment. That is
   the correlation method that decoded Xanbus in minutes.
3. **RPM hunt:** with `tools/regmon.py` running, change engine speed —
   RPM should be the one page that swings hard. (The MC-618 senses
   stator frequency, so it likely knows RPM even though the app hides
   it.) This test was set up but never completed.
4. **Fix `decode_page_response()` first** — it requires a 6-byte payload
   and so rejects every MC-618 reply (they are 5 bytes: page + value,
   no status). Nothing works until this changes.
5. Test **gentle** polling on an expendable regulator: ~1 page/s in the
   master's ~124 ms idle gap, aborting on the first missed reply. If
   sustained polling reliably hangs regulators, reflecting MC-618 data
   is not viable at all — the master never requests it.
6. Fix `/etc/ballmar-reflector.json`: it still names `BANK-01`, which is
   absent here, so the service rescans all 256 addresses every 60 s
   forever. **Service is currently stopped and should stay stopped**
   until this is fixed.
7. Then: Signal K data connection (type **Signal K**, **UDP**, port
   **4123**), map both regulators by name, verify, install the service.

Separately, `electrical.batteries` is absent from **both** Signal K
servers and the Balmar gateway is not publishing to N2K — unresolved,
and independent of the CAN bitrate fix.

## Useful commands

```
PYTHONPATH=src python3 -m ballmar_reflector.app --dump   # no install needed
ballmar-reflector --host <signalk> --port 4123 -v
```
