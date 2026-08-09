# Pi setup — Waveshare RS485 CAN HAT (B) on Alpine (rpcan2)

The SG200 SmartLink bus is RS-485 (115200 8N1), wired to the hat's
**RS485_0** A/B screw terminals → `/dev/ttySC0`. No ground wire needed
(both ends share battery negative). The hat's CAN port is unused.

## Boot overlay (already in place on rpcan2)

`/boot/usercfg.txt`:

```
dtparam=spi=on
dtoverlay=sc16is752-spi1,int_pin=24
```

This creates `/dev/ttySC0` (RS485_0) and `/dev/ttySC1` (RS485_1). The
sc16is752 handles RS-485 driver enable automatically, so the reflector's
active poll mode (`--poll`) just writes to the port.

Alpine notes: package manager is `apk`; if the system runs diskless,
`lbu commit -d` after config changes. Python venv required (PEP 668):

```
apk add python3
python3 -m venv /opt/ballmar
/opt/ballmar/bin/pip install /path/to/pi-ballmar
```

## Sanity checks

```
# raw bytes flowing? (~150 B/s with display attached)
ballmar-reflector --dump          # decoded SmartLink frames
```

If `--dump` shows nothing with the display attached, check A/B polarity
on RS485_0.

## Signal K server side

Add a data connection: Input Type **Signal K**, protocol **UDP**,
port **4123** (or whatever `--port` is set to). Decoded values arrive as
`electrical.batteries.<battery>.*` deltas; no plugin needed.

## Protocol

See [rs-485 decode.md](rs-485%20decode.md) for the reverse-engineered
SmartLink frame format, checksum, and page-code map.
