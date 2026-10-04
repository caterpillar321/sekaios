#!/usr/bin/python3
"""MafuyuMom 에이전트 — VM 의 로그인 세션 안에서 AT-SPI 로 화면의 위젯을 찾는다.

  python3 mm_agent.py '<JSON 요청>'   → 표준 출력에 JSON
  python3 mm_agent.py --serve          → 한 줄 요청 · 한 줄 답 (하네스는 이것을 한 번 띄워 계속 쓴다)
  요청: {"op": "find", "app": "<앱 이름 정규식>", "role": "button", "name": "확인", "name_re": "…",
         "frame": "<창 제목 정규식>", "not_frame": "…", "all": false, "showing": true, "max": 50}
        {"op": "tree", "app": "…", "depth": 8}
        {"op": "action", …찾기 조건…, "action": "click"}     (마우스 없이 위젯의 동작 — 마우스가 안 되는 곳에만)
        {"op": "text", …찾기 조건…}                          (글자 칸·라벨의 내용)
        {"op": "focus"}                                       (지금 키보드 초점)
        {"op": "apps"}

  화면 좌표: 웨이랜드에선 앱이 창 안 좌표만 안다 → 그 창의 자리를 hyprctl 로 찾아 더한다
  (일반 창은 clients, 작업 표시줄·시작 메뉴 같은 레이어는 layers — 같은 프로세스이고 크기가 맞는 것)
"""
import json
import re
import subprocess
import sys

import gi
gi.require_version("Atspi", "2.0")
from gi.repository import Atspi, GLib  # noqa: E402

HIDDEN = -2147483648


def hypr(what):
    try:
        return json.loads(subprocess.run(["hyprctl", "-j", what], capture_output=True, text=True, timeout=5).stdout)
    except Exception:
        return []


_places = None


def places():
    """[(pid, x, y, w, h, 제목)] — 일반 창과 레이어"""
    global _places
    if _places is None:
        out = []
        for c in hypr("clients"):
            if c.get("mapped", True) and not c.get("hidden"):
                out.append((c["pid"], c["at"][0], c["at"][1], c["size"][0], c["size"][1], c.get("title", "")))
        for mon in (hypr("layers") or {}).values():
            for lv in (mon.get("levels") or {}).values():
                for l in lv:
                    out.append((l.get("pid"), l["x"], l["y"], l["w"], l["h"], l.get("namespace", "")))
        _places = out
    return _places


def origin(frame, pid):
    """최상위 창(frame)의 화면 자리 (x, y) — 모르면 None"""
    try:
        e = frame.get_component_iface().get_extents(Atspi.CoordType.WINDOW)
        w, h = e.width, e.height
        name = frame.get_name() or ""
    except Exception:
        return None
    cands = [p for p in places() if p[0] == pid]
    for p in cands:                                    # 크기가 맞는 것 (±4 — 테두리)
        if abs(p[3] - w) <= 4 and abs(p[4] - h) <= 4:
            return p[1], p[2]
    for p in cands:                                    # 제목이 같은 것
        if name and p[5] == name:
            return p[1], p[2]
    if len(cands) == 1:
        return cands[0][1], cands[0][2]
    return None


def apps(pat=None):
    d = Atspi.get_desktop(0)
    out = []
    for i in range(d.get_child_count()):
        a = d.get_child_at_index(i)
        try:
            n = a.get_name() or ""
        except Exception:
            continue
        if pat is None or re.search(pat, n):
            out.append(a)
    return out


def states(o):
    try:
        ss = o.get_state_set()
        return [n for n, s in (("showing", Atspi.StateType.SHOWING), ("visible", Atspi.StateType.VISIBLE),
                               ("focused", Atspi.StateType.FOCUSED), ("sensitive", Atspi.StateType.SENSITIVE),
                               ("checked", Atspi.StateType.CHECKED), ("selected", Atspi.StateType.SELECTED),
                               ("active", Atspi.StateType.ACTIVE), ("editable", Atspi.StateType.EDITABLE))
                if ss.contains(s)]
    except Exception:
        return []


def node(o, frame_xy):
    try:
        e = o.get_component_iface().get_extents(Atspi.CoordType.WINDOW)
        x, y, w, h = e.x, e.y, e.width, e.height
    except Exception:
        x = y = HIDDEN
        w = h = 0
    sx = sy = None
    if frame_xy and x != HIDDEN:
        sx, sy = frame_xy[0] + x, frame_xy[1] + y
    return {"role": o.get_role_name(), "name": o.get_name() or "", "x": sx, "y": sy, "w": w, "h": h,
            "cx": None if sx is None else sx + w // 2, "cy": None if sy is None else sy + h // 2,
            "states": states(o)}


def walk(o, cb, frame_xy, depth=0, maxd=30):
    if depth > maxd:
        return False
    try:
        n = o.get_child_count()
    except Exception:
        return False
    for i in range(min(n, 400)):
        try:
            c = o.get_child_at_index(i)
        except Exception:
            continue
        if c is None:
            continue
        if cb(c, frame_xy):
            return True
        if walk(c, cb, frame_xy, depth + 1, maxd):
            return True
    return False


def match(req, o):
    try:
        if req.get("role") and o.get_role_name() != req["role"]:
            return False
        n = o.get_name() or ""
        if "name" in req and n != req["name"]:
            return False
        if "name_re" in req and not re.search(req["name_re"], n):
            return False
        if req.get("showing", True):
            st = states(o)
            if "showing" not in st:
                return False
        return True
    except Exception:
        return False


def find(req):
    found = []
    want = 1 if not req.get("all") else req.get("max", 50)
    for a in apps(req.get("app")):
        pid = a.get_process_id()
        for i in range(a.get_child_count()):
            frame = a.get_child_at_index(i)
            if frame is None:
                continue
            fname = frame.get_name() or ""
            if "frame" in req and not re.search(req["frame"], fname):
                continue                           # 그 창(제목)에서만 — 대화상자 단추를 뒤 창의 같은 이름 단추와 가른다
            if "not_frame" in req and re.search(req["not_frame"], fname):
                continue
            fxy = origin(frame, pid)

            def cb(o, frame_xy):
                if match(req, o):
                    found.append((o, frame_xy, a.get_name()))
                    return len(found) >= want
                return False
            if match(req, frame):
                found.append((frame, fxy, a.get_name()))
            if len(found) >= want or walk(frame, cb, fxy):
                break
        if len(found) >= want:
            break
    return found


def tree(req):
    lines = []
    maxd = req.get("depth", 8)
    for a in apps(req.get("app")):
        pid = a.get_process_id()
        lines.append(f"app {a.get_name()!r} pid {pid}")
        for i in range(a.get_child_count()):
            frame = a.get_child_at_index(i)
            fxy = origin(frame, pid)

            def rec(o, d):
                if d > maxd:
                    return
                nd = node(o, fxy)
                if "showing" in nd["states"] or d <= 1:
                    lines.append("  " * d + f"{nd['role']} {nd['name']!r} @{nd['x']},{nd['y']} {nd['w']}x{nd['h']}")
                try:
                    for j in range(min(o.get_child_count(), 200)):
                        rec(o.get_child_at_index(j), d + 1)
                except Exception:
                    pass
            rec(frame, 1)
    return lines


def handle(req):
    global _places
    _places = None                                 # 창 자리는 요청마다 새로
    op = req.get("op", "find")
    if op == "apps":
        return [{"name": a.get_name(), "pid": a.get_process_id()} for a in apps()]
    if op == "find":
        return [dict(node(o, fxy), app=an) for o, fxy, an in find(req)]
    if op == "tree":
        return tree(req)
    if op == "action":
        f = find(req)
        ok = False
        if f:
            o = f[0][0]
            ai = o.get_action_iface()
            if ai:
                for i in range(ai.get_n_actions()):
                    if ai.get_action_name(i) == req.get("action", "click"):
                        ok = ai.do_action(i)
                        break
        return {"ok": bool(ok), "found": bool(f)}
    if op == "text":
        if req.get("largest"):                   # 여러 개면 가장 큰 것 (본문 칸 — 검색 칸 말고)
            req["all"] = True
        f = find(req)
        out = None
        if f:
            if req.get("largest"):
                f.sort(key=lambda h: -((node(h[0], h[1])["w"] or 0) * (node(h[0], h[1])["h"] or 0)))
            o = f[0][0]
            # o.get_text_iface() 는 Accessible 을 돌려줘 get_text 이름이 겹친다 — Atspi.Text 로 직접
            try:
                out = Atspi.Text.get_text(o, 0, Atspi.Text.get_character_count(o))
            except Exception:
                out = o.get_name() or ""
        return {"text": out, "found": bool(f)}
    if op == "focus":
        hit = []
        for a in apps():
            def cb(o, frame_xy):
                if "focused" in states(o):
                    hit.append(dict(node(o, frame_xy), app=a.get_name()))
                    return True
                return False
            pid = a.get_process_id()
            for i in range(a.get_child_count()):
                frame = a.get_child_at_index(i)
                if "active" not in states(frame):
                    continue
                if walk(frame, cb, origin(frame, pid)):
                    break
            if hit:
                break
        return hit[0] if hit else None
    return {"error": f"모르는 op {op}"}


def fresh():
    """오래 사는 클라이언트 — 쌓인 이벤트를 처리하고 캐시를 비워 이름·상태를 새로 읽게 한다"""
    ctx = GLib.MainContext.default()
    while ctx.iteration(False):
        pass
    try:
        Atspi.get_desktop(0).clear_cache()
    except Exception:
        pass


def serve():
    """한 줄에 요청 하나(JSON) → 한 줄에 답 하나. 요청마다 프로세스를 새로 띄우면 앱마다 AT-SPI 직접 연결이 새로 생기고,
    GLib 2.84 가 그 연결의 pidfd 를 놓지 않아 오래 켜 둔 앱이 파일 1024개에 막혀 멈춘다 (MafuyuMom 이 찾음)"""
    print(json.dumps({"ready": True}), flush=True)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            fresh()
            out = handle(json.loads(line))
        except Exception as e:
            out = {"error": f"{type(e).__name__}: {e}"}
        print(json.dumps(out, ensure_ascii=False), flush=True)


def main():
    Atspi.init()
    try:
        Atspi.set_timeout(3000, 15000)
    except Exception:
        pass
    if sys.argv[1:] == ["--serve"]:
        serve()
    else:
        print(json.dumps(handle(json.loads(sys.argv[1])), ensure_ascii=False))

if __name__ == "__main__":
    main()
