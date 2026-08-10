# User manual — bench rig & reflector operation

## The rig

- **Pi:** "rpcan2", Raspberry Pi 3B+, Alpine Linux. Bench access:
  `ssh kgodwin@<ip>` or `root@<ip>` — eth0 gets DHCP (last seen
  `192.168.4.89`), wlan0 auto-joins `kghome-e24` (bench), `kghome-lte`
  (boat), or `totaldns-uplink`.
- **Bus:** Balmar SG200 shunt on SmartLink → hat port **RS485_0 (A/B)**
  → `/dev/ttySC0` @ 115200. Protocol: `docs/rs-485 decode.md`.
- **Code:** `/root/pi-ballmar` (copy of this repo; `src/` is what runs).

## The service

Runs as OpenRC service `ballmar-reflector`, started at boot.

```
rc-service ballmar-reflector status|start|stop|restart
tail -f /var/log/ballmar-reflector.log        # startup lines + stats every 5 min
```

Configuration lives in **`/etc/conf.d/ballmar-reflector`**:

```
REFLECTOR_BIN=/usr/local/bin/ballmar-reflector
REFLECTOR_ARGS="--device /dev/ttySC0 --host kgboat-nmea2000.lan --port 4123 \
                --map 0x02=electrical.batteries.house --poll 0x03,0x05,0x06"
```

Edit args, then `rc-service ballmar-reflector restart`.

## Passive mode vs master mode

**Passive** (no `--poll` flag): the reflector only listens. Use when a
Balmar display (or other master) is on the bus. You only get the pages
the display itself requests — i.e. whatever screen it is showing.

**Master mode** (`--poll <pages>`): the Pi drives the bus like a
display — status polls plus round-robin page requests to every address
in `--map`. Use when the display is removed (SmartLink slaves never
speak unprompted; a quiet bus also puts the shunt into an idle state,
and the poll traffic wakes it). To turn it on, add to the args:

```
--poll 0x03,0x05,0x06        # SOC, voltage, current
```

`--poll-interval N` sets seconds per full round (default 1.0).
Both masters coexist surprisingly well, but don't leave master mode on
permanently while a display is attached.

## Hands-on / debugging (stop the service first — it owns the port)

```
rc-service ballmar-reflector stop
PYTHONPATH=/root/pi-ballmar/src python3 -m ballmar_reflector.app --console --poll 0x03,0x05,0x06   # live values on screen, no UDP
PYTHONPATH=/root/pi-ballmar/src python3 -m ballmar_reflector.app --dump              # protocol-level frames (passive)
PYTHONPATH=/root/pi-ballmar/src python3 -m ballmar_reflector.app --dump --poll 0x03,0x05,0x06   # protocol-level, as master
python3 /root/pi-ballmar/tools/uartcap.py /dev/ttySC0 115200 60 /tmp/cap.json        # raw timestamped capture (for decoding new pages/devices)
rc-service ballmar-reflector start
```

`--console` is the debugging mode: same decode pipeline as the daemon,
but values print to the screen (`electrical.… = 13.224`); add `--host`
too and it prints *and* relays.

`--dump` prints unknown pages too (`page=0x?? (?)`) — that plus
`tools/uartcap.py` is the protocol-discovery workflow for the MC-618s.

## Signal K side (server: kgboat-nmea2000.lan)

Server → Data Connections → Add: Input Type **Signal K**, protocol
**UDP**, port **4123**. Values appear under the `--map` prefix, e.g.
`electrical.batteries.house.voltage` (V), `.current` (A),
`.capacity.stateOfCharge` (0–1).

## WiFi

Add another network:

```
wpa_passphrase "SSID" "password" | grep -vE "^\s*#" >> /etc/wpa_supplicant/wpa_supplicant.conf
rc-service wpa_supplicant restart && ifup wlan0
iw dev wlan0 link      # verify association
```

## Gotchas learned the hard way

- The shunt **ignores page reads after bus silence** — master mode's
  status-poll cadence handles the wake automatically.
- RS-485 termination: hat has onboard 120 Ω per channel via jumper.
  Measure A–B resistance (bus powered down): ~60 Ω = two terminators
  already, ~120 Ω = one, open = none. At 115200 over short runs it is
  forgiving either way.
- This is a **sys-mode** Alpine install: changes persist, **no
  `lbu commit` needed**.
- `-1` raw values = "not available" (blank on a display), skipped
  automatically.
