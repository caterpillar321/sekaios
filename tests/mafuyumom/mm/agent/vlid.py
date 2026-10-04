#!/usr/bin/python3
"""가상 노트북 덮개 — uinput 으로 덮개 스위치(SW_LID) 장치 "Lid Switch" 를 만든다 (root 로).

  python3 vlid.py serve <fifo>      장치를 만들고 fifo 에서 close · open · quit 을 한 줄씩 받는다

udev 가 이 장치에 power-switch 태그를 붙여 logind 가 덮개로 보고(LidClosed), libinput 도 스위치로 본다 —
합성기의 switch:Lid Switch 바인딩까지 실제 노트북과 같은 길을 지난다. 외부 모듈 없이 ioctl 로만.
"""
import fcntl
import os
import struct
import sys
import time

EV_SYN, EV_SW = 0, 5
SYN_REPORT, SW_LID = 0, 0


def _IOC(d, t, nr, size):
    return (d << 30) | (size << 16) | (ord(t) << 8) | nr


UI_DEV_CREATE = _IOC(0, "U", 1, 0)
UI_DEV_DESTROY = _IOC(0, "U", 2, 0)
UI_DEV_SETUP = _IOC(1, "U", 3, 92)
UI_SET_EVBIT = _IOC(1, "U", 100, 4)
UI_SET_SWBIT = _IOC(1, "U", 109, 4)


class Lid:
    def __init__(self):
        self.fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
        for ev in (EV_SYN, EV_SW):
            fcntl.ioctl(self.fd, UI_SET_EVBIT, ev)
        fcntl.ioctl(self.fd, UI_SET_SWBIT, SW_LID)
        fcntl.ioctl(self.fd, UI_DEV_SETUP, struct.pack("HHHH80sI", 0x19, 0x6d6d, 0x0002, 1, b"Lid Switch", 0))  # BUS_HOST
        fcntl.ioctl(self.fd, UI_DEV_CREATE)

    def set(self, closed):
        os.write(self.fd, struct.pack("llHHi", 0, 0, EV_SW, SW_LID, 1 if closed else 0))
        os.write(self.fd, struct.pack("llHHi", 0, 0, EV_SYN, SYN_REPORT, 0))

    def close(self):
        fcntl.ioctl(self.fd, UI_DEV_DESTROY)
        os.close(self.fd)


def main():
    if len(sys.argv) != 3 or sys.argv[1] != "serve":
        print(__doc__)
        sys.exit(2)
    fifo = sys.argv[2]
    if not os.path.exists(fifo):
        os.mkfifo(fifo, 0o600)
    lid = Lid()
    try:
        while True:
            with open(fifo) as f:                       # 쓰는 쪽이 닫을 때마다 다시 연다
                for line in f:
                    cmd = line.strip()
                    if cmd == "quit":
                        return
                    if cmd in ("close", "open"):
                        lid.set(cmd == "close")
    finally:
        time.sleep(0.2)
        lid.close()
        os.unlink(fifo)


if __name__ == "__main__":
    main()
