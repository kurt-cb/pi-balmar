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

The service runs `--config /etc/ballmar-reflector.json` (set in
`/etc/conf.d/ballmar-reflector`). **Devices are configured by the name
stored in the device itself** (shown by `--discover`), so the same
config works no matter what bus address a device uses:

```json
{
  "device": "/dev/ttySC0",
  "host": "kgboat-nmea2000.lan",
  "port": 4123,
  "devices": {
    "BANK-01": { "report": true,
                 "prefix": "electrical.batteries.0",
                 "pages": ["0x03", "0x05", "0x06"] }
  }
}
```

At startup the reflector scans the bus, matches names to the config,
reports configured devices on their `prefix`, and logs (but does not
report) unknown devices. Configured-but-absent devices are rescanned
every 60 s until they appear. `"report": false` silences a device.
Edit the file, then `rc-service ballmar-reflector restart`.

## Discovering devices

```
rc-service ballmar-reflector stop
PYTHONPATH=/root/pi-ballmar/src python3 -m ballmar_reflector.app --discover
rc-service ballmar-reflector start
```

Prints every responding SmartLink address with its status byte and
configured name — this is how the MC-618s' names will be learned on
the boat. Copy the names into the config's `devices` section.

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

## Signal K side — transports & settings

Server: `kgboat-nmea2000` — boat LAN `192.168.12.72` /
`kgboat-nmea2000.lan` (DHCP; the name is the stable handle), VPN
overlay `fd00:108::6010`. Data connections live under
**Server → Data Connections**; restart the server after changes.
Form settings for either transport: Data Type **SignalK**,
'self' handling **"No 'self' mapping"** (our deltas carry no context,
so they map to the own vessel automatically), Override timestamps off.
The connection ID becomes the `$source` label shown next to values.

**UDP — production (Pi on the boat LAN).** signalk-server's UDP input
binds **IPv4 only**, so it works when Pi and server share the boat
LAN (192.168.12.0/24), not across the routed-IPv6 home link.

- server connection: ID `balmar-reflector`, SignalK Source **UDP**,
  Port **4123**
- reflector config: `"host": "kgboat-nmea2000.lan", "port": 4123`
  (the sender prefers the IPv4 address when a name resolves to both)

**TCP — reflector serves, Signal K dials in.** Direction is inverted:
the reflector listens (`"listen": 4123` in the config, instead of or
alongside `host`), and the server connects out to the Pi — node's TCP
client is dual-stack, so this is the transport that works across
routed IPv6 (proven bench→boat 2026-08-09, then retired).

- server connection: ID `balmar-reflector-tcp`, SignalK Source **TCP**,
  Host = the Pi's address (bench: `fd00:1::151`), Port **4123**

Keep exactly one transport enabled per environment: UDP on the boat;
TCP only for bench-to-boat demos (disable/delete otherwise).

Values appear under each device's configured `prefix`, e.g.
`electrical.batteries.0.voltage` (V), `.current` (A),
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
