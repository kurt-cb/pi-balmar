import os, sys, termios, select, time, json

dev, baud, secs, out = sys.argv[1], int(sys.argv[2]), float(sys.argv[3]), sys.argv[4]
BAUDS = {115200: termios.B115200}
fd = os.open(dev, os.O_RDONLY | os.O_NOCTTY | os.O_NONBLOCK)
a = termios.tcgetattr(fd)
a[0] = a[1] = a[3] = 0
a[2] = termios.CREAD | termios.CLOCAL | termios.CS8
a[4] = a[5] = BAUDS[baud]
termios.tcsetattr(fd, termios.TCSANOW, a)
termios.tcflush(fd, termios.TCIFLUSH)

t0 = time.time()
recs = []
while time.time() - t0 < secs:
    r, _, _ = select.select([fd], [], [], 0.2)
    if r:
        data = os.read(fd, 4096)
        recs.append({"t": round(time.time() - t0, 4), "hex": data.hex()})
os.close(fd)
with open(out, "w") as f:
    json.dump(recs, f)
print("chunks:", len(recs), "bytes:", sum(len(r["hex"]) // 2 for r in recs))
