"""환경을 바꾸고, 대상을 띄워 찍고, 배치를 잰다."""
import json
import os
import shlex
import time

from PIL import Image, ImageChops

from mm import remote, vm


def hypr(what):
    r = remote.run(f"hyprctl -j {what}", timeout=20)
    try:
        return json.loads(r.out)
    except ValueError:
        return None


def monitor():
    ms = hypr("monitors") or []
    return ms[0] if ms else None


# ── 환경 ──
def apply(cfg):
    """VM 의 화면 환경을 cfg 로 — 테마·대비(설정 저장소로, 설정 앱과 같은 길) · 텍스트 크기 · 해상도·배율"""
    code = ("from sekaisettings.store import Store; s = Store(); "
            f"s.set_contrast({cfg['contrast']}); "
            + ("" if cfg["contrast"] else f"s.set_mode({cfg['mode']!r}); ")
            + f"s.set('a11y', 'text_scale', {cfg['text']})")
    r = remote.run(f"python3 -c {shlex.quote(code)}", timeout=30)
    if not r.ok:
        raise RuntimeError(f"설정을 바꾸지 못했다: {r.err[-300:]}")
    m = monitor()
    w, h = cfg["res"]
    remote.run(f"hyprctl keyword monitor {m['name']},{w}x{h}@60,0x0,{cfg['scale']} >/dev/null")
    end = time.time() + 10
    while time.time() < end:
        m = monitor() or {}
        # 가상 GPU 는 1366 대신 가까운 표준 모드(1360)를 고른다 — 몇 화소 차이는 받아들인다
        if abs(m.get("width", 0) - w) <= 8 and abs(m.get("height", 0) - h) <= 8 and abs((m.get("scale") or 1) - cfg["scale"]) < 0.01:
            break
        time.sleep(0.5)
    if abs(m.get("width", 0) - w) > 8 or abs(m.get("height", 0) - h) > 8 or abs((m.get("scale") or 1) - cfg["scale"]) > 0.01:
        raise RuntimeError(f"해상도를 {w}x{h} 배율 {cfg['scale']} 로 바꾸지 못했다 (지금 {m.get('width')}x{m.get('height')} "
                           f"배율 {m.get('scale')}) — VM 의 virtio-gpu 는 낮춘 해상도를 되돌리지 못한다, 새 VM 으로")
    time.sleep(2.5)                                   # 작업 표시줄·바탕화면이 새 크기로 다시 그릴 시간
    return m


def work_area(m):
    sc = m.get("scale", 1) or 1
    l, t, r, b = (m.get("reserved") or [0, 0, 0, 0])[:4]
    return (m["x"] + l, m["y"] + t, round(m["width"] / sc) - l - r, round(m["height"] / sc) - t - b)


# ── 찍기 ──
def _crop(q, path, box, scale):
    """그 상자(논리 좌표)를 VM 안에서 grim 으로 찍어 가져온다 — 합성기가 그린 그대로(배율이면 실제 화소로).
    QMP screendump 는 해상도를 바꾼 뒤 줄 간격이 어긋나 비스듬히 찍힌 적이 있다 (1366 폭)"""
    x, y, w, h = (int(round(v)) for v in box)
    x, y = max(0, x), max(0, y)
    r = remote.run(f"grim -g '{x},{y} {max(4, w)}x{max(4, h)}' /tmp/shinei.png", timeout=30)
    if not r.ok:
        r = remote.run("grim /tmp/shinei.png", timeout=30)
    remote.pull("/tmp/shinei.png", path)
    return path


def shoot(q, ui, t, cfg, outdir):
    """대상 하나 — 띄우고 찍고 배치를 잰다. 결과 dict"""
    res = {"target": t["id"], "config": cfg["id"], "issues": [], "shot": None, "note": ""}
    vm.close_all_windows()
    q.move(960, 1064)               # 커서는 작업 표시줄 가운데 빈 곳에 — 위젯 위에 있으면 툴팁이 찍힌다 (비율 좌표)
    m = monitor()
    scale = (m or {}).get("scale", 1) or 1
    area = work_area(m)
    shot = os.path.join(outdir, "shots", f"{cfg['id']}__{t['id']}.png")
    os.makedirs(os.path.dirname(shot), exist_ok=True)
    if t["kind"] == "window":
        remote.run(f"hyprctl dispatch exec {shlex.quote(t['cmd'])} >/dev/null")
        c = None
        end = time.time() + 20
        while time.time() < end and not c:
            c = next((x for x in (hypr("clients") or []) if x.get("class") == t["cls"] and x.get("mapped", True)), None)
            time.sleep(0.4)
        if not c:
            res["issues"].append({"kind": "안 뜸", "name": t["cmd"]})
            return res
        time.sleep(t.get("settle", 1.8))
        c = next((x for x in (hypr("clients") or []) if x.get("address") == c["address"]), c)
        top = c.get("sekaiTop", 0) or 0
        x, y = c["at"]
        w, h = c["size"]
        box = (x, y - top, w, h + top)
        ax, ay, aw, ah = area
        if box[0] < ax - 1 or box[1] < ay - 1 or box[0] + box[2] > ax + aw + 1 or box[1] + box[3] > ay + ah + 1:
            res["issues"].append({"kind": "창이 화면을 넘는다", "name": f"창 {box} · 작업 영역 {area}"})
        _crop(q, shot, box, scale)
        frame_re = None
    else:
        before = {(l.get("address"), l.get("namespace")) for l in _layers()}
        how, _, arg = t["open"].partition(":")
        if how == "key":
            q.key(arg)
        else:
            remote.run(f"{arg} >/dev/null 2>&1")
        new = []
        end = time.time() + 6
        while time.time() < end and not new:
            time.sleep(0.4)
            new = [l for l in _layers() if (l.get("address"), l.get("namespace")) not in before and l.get("w", 0) > 50]
        if not new:
            res["issues"].append({"kind": "안 뜸", "name": t["open"]})
            return res
        time.sleep(0.8)
        L = max(new, key=lambda l: l["w"] * l["h"])
        _crop(q, shot, (L["x"], L["y"], L["w"], L["h"]), scale)
        frame_re = None
    res["shot"] = os.path.relpath(shot, outdir)
    ign = set(t.get("ignore_roles", []))
    req = {"op": "layout", "app": t["app"]}
    if frame_re:
        req["frame"] = frame_re
    lay = ui.call(req, timeout=60) or []
    for f in lay:
        for i in f.get("issues", []):
            if i.get("role") not in ign:
                res["issues"].append(i)
    if t["kind"] == "layer":
        how, _, arg = t["close"].partition(":")
        q.key(arg) if how == "key" else remote.run(arg)
    return res


def _layers():
    out = []
    for mon in (hypr("layers") or {}).values():
        for lv in (mon.get("levels") or {}).values():
            out.extend(lv)
    return out


# ── 기준 그림과 비교 ──
def compare(shot, base, thresh=24):
    """바뀐 화소 비율 (0~1). 크기가 다르면 None"""
    a, b = Image.open(shot).convert("RGB"), Image.open(base).convert("RGB")
    if a.size != b.size:
        return None, None
    d = ImageChops.difference(a, b).convert("L").point(lambda v: 255 if v > thresh else 0)
    n = d.histogram()[255]
    return n / (a.size[0] * a.size[1]), d
