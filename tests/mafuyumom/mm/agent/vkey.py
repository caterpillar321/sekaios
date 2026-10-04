#!/usr/bin/python3
"""가상 키보드 키 하나 — uinput 으로 그 키만 가진 키보드를 만들어 한 번 누르고 지운다 (root 로).

  python3 vkey.py <리눅스 키 번호>       예: 530 = KEY_TOUCHPAD_TOGGLE, 247 = KEY_RFKILL, 116 = KEY_POWER

QEMU 키 이름(qcode)에 없는 노트북 Fn 키들을 실제와 같은 길(커널 → libinput → 합성기 바인딩)로 넣는다.
"""
import fcntl
import os
import struct
import sys
import time

EV_SYN, EV_KEY = 0, 1


def _IOC(d, t, nr, size):
    return (d << 30) | (size << 16) | (ord(t) << 8) | nr


UI_DEV_CREATE = _IOC(0, "U", 1, 0)
UI_DEV_DESTROY = _IOC(0, "U", 2, 0)
UI_DEV_SETUP = _IOC(1, "U", 3, 92)
UI_SET_EVBIT = _IOC(1, "U", 100, 4)
UI_SET_KEYBIT = _IOC(1, "U", 101, 4)


def main():
    code = int(sys.argv[1], 0)
    fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
    for ev in (EV_SYN, EV_KEY):
        fcntl.ioctl(fd, UI_SET_EVBIT, ev)
    fcntl.ioctl(fd, UI_SET_KEYBIT, code)
    fcntl.ioctl(fd, UI_DEV_SETUP, struct.pack("HHHH80sI", 0x03, 0x6d6d, 0x0003, 1, b"MafuyuMom virtual keys", 0))
    fcntl.ioctl(fd, UI_DEV_CREATE)
    time.sleep(1.5)                                     # libinput 이 장치를 더할 때까지
    for v in (1, 0):
        os.write(fd, struct.pack("llHHi", 0, 0, EV_KEY, code, v))
        os.write(fd, struct.pack("llHHi", 0, 0, EV_SYN, 0, 0))
        time.sleep(0.05)
    time.sleep(0.5)
    fcntl.ioctl(fd, UI_DEV_DESTROY)
    os.close(fd)
    print("ok")


if __name__ == "__main__":
    main()
