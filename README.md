# ballmar-reflector

Reads Balmar SG200 battery-monitor data from its SmartLink RS-485 bus and
reflects it to a Signal K server over WiFi/UDP as Signal K delta JSON.

```
SG200 shunt ──SmartLink/RS-485──> /dev/ttySC0 ──ballmar-reflector──UDP/WiFi──> Signal K
                                  (Waveshare RS485 CAN HAT (B), port RS485_0)
```

The SmartLink protocol was reverse-engineered from live captures — frame
format, checksum, and page map are documented in
[docs/rs-485 decode.md](docs/rs-485%20decode.md). There is no Signal K
plugin for SmartLink; decoding happens here on the Pi and the server just
receives standard deltas (`electrical.batteries.<id>.*`).

## Modes

- **Passive (default):** listen to the traffic the SG200 display already
  generates. Only the page currently shown on the display is on the bus.
- **Active (`--poll`):** the reflector acts as bus master and requests
  pages itself, byte-identical to the display's own requests. Required
  once the display is removed (SmartLink devices only speak when polled).

## Install (Alpine Pi, rpcan2)

```
apk add python3
python3 -m venv /opt/ballmar
/opt/ballmar/bin/pip install /path/to/pi-ballmar
```

## Usage

```
# watch decoded frames, no UDP needed
ballmar-reflector --dump

# passive reflect to Signal K
ballmar-reflector --host 192.168.4.1 --port 4123

# active polling (display removed): request SOC page every second
ballmar-reflector --host 192.168.4.1 --poll 0x03
```

Options: `-d/--device` (default `/dev/ttySC0`), `--baud` (115200),
`--min-period` (per-path rate limit), `--poll-interval`, `-v`,
`--stats-interval`.

Multiple devices on one bus map to separate Signal K paths by their
SmartLink address (default: `0x02=electrical.batteries.house`):

```
ballmar-reflector --host ... \
    --map 0x00=electrical.alternators.0 \
    --map 0x01=electrical.alternators.1
```

Survives serial errors and WiFi dropouts by retrying.

## Signal K server side

Add a data connection (**Server → Data Connections → Add**):

- Input Type: **Signal K**
- Protocol: **UDP**
- Port: **4123** (match `--port`)

Data appears under `electrical.batteries.house.*`.

## Run as a service (OpenRC / Alpine)

```
cp openrc/ballmar-reflector.initd /etc/init.d/ballmar-reflector
chmod +x /etc/init.d/ballmar-reflector
# edit REFLECTOR_ARGS in the init script if needed (host, --poll pages)
rc-update add ballmar-reflector default
rc-service ballmar-reflector start
```

(`systemd/ballmar-reflector.service` is provided for systemd-based Pis.)
