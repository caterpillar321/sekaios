#!/usr/bin/env python3
"""MafuyuMom 실기 모드 — 시험할 PC 에서 root 로 도는 가상 키보드·태블릿 (QEMU 의 usb-kbd · usb-tablet 과 같은 꼴)

표준 입력의 한 줄 = 명령 하나, 처리하면 "ok" 한 줄로 답한다 (보내는 쪽이 순서·시간을 맞출 수 있게).
    k <키 번호> <0|1>      키 (리눅스 KEY_* 번호)
    b <단추 번호> <0|1>    마우스 단추 (BTN_LEFT=272 …)
    a <x> <y>              절대 좌표 0..32767 (화면 전체)
    w <n>                  휠 (+ 위 · - 아래)
    q                      끝
외부 모듈(python3-evdev) 없이 /dev/uinput 을 직접 — 시험대에 따로 깔 것이 없게.
"""
import fcntl
import os
import struct
import sys
import time

UI_SET_EVBIT, UI_SET_KEYBIT, UI_SET_RELBIT, UI_SET_ABSBIT = 0x40045564, 0x40045565, 0x40045566, 0x40045567
UI_SET_PROPBIT = 0x4004556e
UI_DEV_CREATE, UI_DEV_DESTROY = 0x5501, 0x5502
EV_SYN, EV_KEY, EV_REL, EV_ABS = 0, 1, 2, 3
ABS_X, ABS_Y, REL_WHEEL = 0, 1, 8
BTNS = (272, 273, 274)                      # BTN_LEFT · RIGHT · MIDDLE
ABS_MAX = 32767


def make(name, keys=(), abs_xy=False, wheel=False):
    fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
    fcntl.ioctl(fd, UI_SET_EVBIT, EV_KEY)
    for k in keys:
        fcntl.ioctl(fd, UI_SET_KEYBIT, k)
    if abs_xy:
        fcntl.ioctl(fd, UI_SET_EVBIT, EV_ABS)
        fcntl.ioctl(fd, UI_SET_ABSBIT, ABS_X)
        fcntl.ioctl(fd, UI_SET_ABSBIT, ABS_Y)
    if wheel:
        fcntl.ioctl(fd, UI_SET_EVBIT, EV_REL)
        fcntl.ioctl(fd, UI_SET_RELBIT, REL_WHEEL)
    absmax = [0] * 64
    if abs_xy:
        absmax[ABS_X] = absmax[ABS_Y] = ABS_MAX
    # struct uinput_user_dev: name[80], input_id(4×u16), ff_effects_max(u32), absmax·absmin·absfuzz·absflat[64](i32)
    dev = struct.pack("80sHHHHI", name.encode(), 0x03, 0x5ec1, 0x0001 if abs_xy else 0x0002, 1, 0)
    dev += struct.pack("64i", *absmax) + struct.pack("64i", *([0] * 64)) * 3
    os.write(fd, dev)
    fcntl.ioctl(fd, UI_DEV_CREATE)
    return fd


def emit(fd, typ, code, val):
    t = time.time()
    os.write(fd, struct.pack("llHHi", int(t), int((t % 1) * 1e6), typ, code, val))


def syn(fd):
    emit(fd, EV_SYN, 0, 0)


def main():
    kbd = make("MafuyuMom keyboard", keys=range(1, 249))
    tab = make("MafuyuMom tablet", keys=BTNS, abs_xy=True, wheel=True)
    time.sleep(1.0)                          # 합성기가 새 장치를 알아볼 때까지
    print("ready", flush=True)
    try:
        for line in sys.stdin:
            p = line.split()
            if not p:
                continue
            c = p[0]
            if c == "q":
                break
            if c == "k":
                emit(kbd, EV_KEY, int(p[1]), int(p[2]))
                syn(kbd)
            elif c == "b":
                emit(tab, EV_KEY, int(p[1]), int(p[2]))
                syn(tab)
            elif c == "a":
                emit(tab, EV_ABS, ABS_X, int(p[1]))
                emit(tab, EV_ABS, ABS_Y, int(p[2]))
                syn(tab)
            elif c == "w":
                emit(tab, EV_REL, REL_WHEEL, int(p[1]))
                syn(tab)
            print("ok", flush=True)
    finally:
        for fd in (kbd, tab):
            try:
                fcntl.ioctl(fd, UI_DEV_DESTROY)
            except OSError:
                pass


if __name__ == "__main__":
    main()
