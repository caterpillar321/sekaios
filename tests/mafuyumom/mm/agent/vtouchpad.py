#!/usr/bin/python3
"""가상 터치패드 — uinput 으로 멀티터치 터치패드를 만들어 여러 손가락 쓸기를 넣는다 (root 로).

  python3 vtouchpad.py swipe <손가락 수> <dx> <dy> [단계 수]
  python3 vtouchpad.py serve <fifo>      장치를 만든 채 fifo 에서 "swipe 손가락 dx dy" · "quit" 을 받는다
                                          (터치패드를 끄고 켜는 시험 — 장치가 그동안 있어야 한다)

udev 가 이 장치를 ID_INPUT_TOUCHPAD 로 보고, libinput 이 진짜 터치패드처럼 제스처(쓸기)를 알아본다 —
합성기는 실제 노트북과 같은 길(libinput → aquamarine → WorldLink)로 받는다. 단위는 장치 단위(30 = 1mm).
외부 모듈 없이 ioctl 로만 (VM 에 python3-evdev 가 없어도 되게).
"""
import fcntl
import os
import struct
import sys
import time

EV_SYN, EV_KEY, EV_ABS = 0, 1, 3
SYN_REPORT = 0
BTN_LEFT, BTN_TOOL_FINGER, BTN_TOUCH = 0x110, 0x145, 0x14a
BTN_TOOL_QUINTTAP, BTN_TOOL_DOUBLETAP, BTN_TOOL_TRIPLETAP, BTN_TOOL_QUADTAP = 0x148, 0x14d, 0x14e, 0x14f
ABS_X, ABS_Y = 0x00, 0x01
ABS_MT_SLOT, ABS_MT_POSITION_X, ABS_MT_POSITION_Y, ABS_MT_TRACKING_ID = 0x2f, 0x35, 0x36, 0x39
INPUT_PROP_POINTER, INPUT_PROP_BUTTONPAD = 0, 2
W, H, RES, SLOTS = 3000, 2000, 30, 5          # 100mm x 66mm


def _IOC(d, t, nr, size):
    return (d << 30) | (size << 16) | (ord(t) << 8) | nr


UI_DEV_CREATE = _IOC(0, "U", 1, 0)
UI_DEV_DESTROY = _IOC(0, "U", 2, 0)
UI_DEV_SETUP = _IOC(1, "U", 3, 92)             # struct uinput_setup
UI_ABS_SETUP = _IOC(1, "U", 4, 28)             # struct uinput_abs_setup
UI_SET_EVBIT = _IOC(1, "U", 100, 4)
UI_SET_KEYBIT = _IOC(1, "U", 101, 4)
UI_SET_ABSBIT = _IOC(1, "U", 103, 4)
UI_SET_PROPBIT = _IOC(1, "U", 110, 4)

TOOL = {1: BTN_TOOL_FINGER, 2: BTN_TOOL_DOUBLETAP, 3: BTN_TOOL_TRIPLETAP, 4: BTN_TOOL_QUADTAP, 5: BTN_TOOL_QUINTTAP}


class Pad:
    def __init__(self):
        self.fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
        for ev in (EV_SYN, EV_KEY, EV_ABS):
            fcntl.ioctl(self.fd, UI_SET_EVBIT, ev)
        for k in (BTN_LEFT, BTN_TOUCH, *TOOL.values()):
            fcntl.ioctl(self.fd, UI_SET_KEYBIT, k)
        for p in (INPUT_PROP_POINTER, INPUT_PROP_BUTTONPAD):
            fcntl.ioctl(self.fd, UI_SET_PROPBIT, p)
        for code, mx, res in ((ABS_X, W, RES), (ABS_Y, H, RES), (ABS_MT_POSITION_X, W, RES), (ABS_MT_POSITION_Y, H, RES),
                              (ABS_MT_SLOT, SLOTS - 1, 0), (ABS_MT_TRACKING_ID, 65535, 0)):
            fcntl.ioctl(self.fd, UI_SET_ABSBIT, code)
            fcntl.ioctl(self.fd, UI_ABS_SETUP, struct.pack("HHiiiiii", code, 0, 0, 0, mx, 0, 0, res))
        name = b"MafuyuMom virtual touchpad"
        fcntl.ioctl(self.fd, UI_DEV_SETUP, struct.pack("HHHH80sI", 0x18, 0x6d6d, 0x0001, 1, name, 0))   # BUS_I2C
        fcntl.ioctl(self.fd, UI_DEV_CREATE)
        self.tid = 100
        time.sleep(1.5)                                 # udev 가 태그를 붙이고 libinput 이 장치를 더할 때까지

    def ev(self, t, c, v):
        os.write(self.fd, struct.pack("llHHi", 0, 0, t, c, v))

    def syn(self):
        self.ev(EV_SYN, SYN_REPORT, 0)

    def frame(self, pts, down=None):
        """pts: [(x, y)] 손가락마다. down=True 면 놓기, False 면 떼기"""
        n = len(pts)
        for i, (x, y) in enumerate(pts):
            self.ev(EV_ABS, ABS_MT_SLOT, i)
            if down is True:
                self.tid += 1
                self.ev(EV_ABS, ABS_MT_TRACKING_ID, self.tid)
            if down is False:
                self.ev(EV_ABS, ABS_MT_TRACKING_ID, -1)
            else:
                self.ev(EV_ABS, ABS_MT_POSITION_X, int(x))
                self.ev(EV_ABS, ABS_MT_POSITION_Y, int(y))
        if down is not False:
            self.ev(EV_ABS, ABS_X, int(pts[0][0]))
            self.ev(EV_ABS, ABS_Y, int(pts[0][1]))
        if down is True:
            self.ev(EV_KEY, BTN_TOUCH, 1)
            self.ev(EV_KEY, TOOL[n], 1)
        elif down is False:
            self.ev(EV_KEY, BTN_TOUCH, 0)
            self.ev(EV_KEY, TOOL[n], 0)
        self.syn()

    def swipe(self, fingers, dx, dy, steps=30):
        # 손가락은 가로로 나란히 (12mm 간격), 패드 가운데에서 움직임의 반대쪽으로 비켜 시작
        cx, cy = W / 2 - dx / 2, H / 2 - dy / 2
        base = [(cx + (i - (fingers - 1) / 2) * 360, cy) for i in range(fingers)]
        self.frame(base, down=True)
        time.sleep(0.03)
        for s in range(1, steps + 1):
            f = s / steps
            self.frame([(x + dx * f, y + dy * f) for x, y in base])
            time.sleep(0.012)
        time.sleep(0.03)
        self.frame([(x + dx, y + dy) for x, y in base], down=False)

    def close(self):
        time.sleep(0.3)
        fcntl.ioctl(self.fd, UI_DEV_DESTROY)
        os.close(self.fd)


def main():
    if len(sys.argv) >= 5 and sys.argv[1] == "swipe":
        fingers, dx, dy = int(sys.argv[2]), float(sys.argv[3]), float(sys.argv[4])
        steps = int(sys.argv[5]) if len(sys.argv) > 5 else 30
        p = Pad()
        try:
            p.swipe(fingers, dx, dy, steps)
        finally:
            p.close()
        print("ok")
    elif len(sys.argv) == 3 and sys.argv[1] == "serve":
        fifo = sys.argv[2]
        if not os.path.exists(fifo):
            os.mkfifo(fifo, 0o600)
        p = Pad()
        try:
            while True:
                with open(fifo) as f:
                    for line in f:
                        a = line.split()
                        if a[:1] == ["quit"]:
                            return
                        if len(a) == 4 and a[0] == "swipe":
                            p.swipe(int(a[1]), float(a[2]), float(a[3]))
        finally:
            p.close()
            os.unlink(fifo)
    else:
        print(__doc__)
        sys.exit(2)


if __name__ == "__main__":
    main()
