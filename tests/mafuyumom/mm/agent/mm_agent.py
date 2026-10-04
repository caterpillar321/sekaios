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


# ── 신에이(화면 깨짐): 창 안 위젯의 배치를 훑어 글자 잘림·겹침·창 밖 위젯을 찾는다 ──
TEXTY = {"label", "button", "push button", "toggle button", "check box", "radio button", "menu item", "page tab",
         "heading", "link", "list item", "table cell", "menu", "combo box", "text", "password text", "spin button"}
SCROLLY = {"scroll pane", "viewport"}


def _box(o):
    try:
        e = o.get_component_iface().get_extents(Atspi.CoordType.WINDOW)
        return (e.x, e.y, e.width, e.height) if e.x != HIDDEN else None
    except Exception:
        return None


def _clip(o, box, tol=2, by_width=False):
    """글자가 위젯 상자 밖으로 나가나 — 마지막 글자의 자리로 잰다. "잘림" / "말줄임" / None.
    tol: 넘쳐도 봐주는 화소 (표 칸은 GTK 가 칸 여백을 빼고 알려 몇 화소 넘친 것처럼 보인다)"""
    try:
        n = Atspi.Text.get_character_count(o)
        if n <= 0:
            return None
        last = Atspi.Text.get_character_extents(o, n - 1, Atspi.CoordType.WINDOW)
        first = Atspi.Text.get_character_extents(o, 0, Atspi.CoordType.WINDOW)
    except Exception:
        return None
    x, y, w, h = box
    if last.width <= 0 and last.height <= 0:
        return "말줄임" if first.width > 0 else None       # 끝 글자 자리가 없다 — 말줄임(…)으로 잘렸다
    if by_width:
        # 표 칸: GTK 는 오른쪽 정렬 칸의 글자 자리를 길이와 상관없이 같은 데서 시작한다고 알린다 — 자리는 믿지 않고
        #   글자 폭(처음~끝)이 칸보다 넓은지만 본다 (날짜가 "2026-10-01 오…"로 줄었으면 글자 폭이 칸보다 넓다)
        return "잘림" if (last.x + last.width - first.x) > w + tol else None
    if last.x + last.width > x + w + tol or last.y + last.height > y + h + tol or last.y < y - tol:
        return "잘림"
    return None


def layout(req):
    """요청: {"op": "layout", "app": "…", "frame": "<창 제목 정규식>"} → 창마다 {"frame", "box", "nodes", "issues"}"""
    res = []
    for a in apps(req.get("app")):
        pid = a.get_process_id()
        for i in range(a.get_child_count()):
            frame = a.get_child_at_index(i)
            if frame is None or "showing" not in states(frame):
                continue
            fname = frame.get_name() or ""
            if "frame" in req and not re.search(req["frame"], fname):
                continue
            fbox = _box(frame)
            if not fbox:
                continue
            nodes, issues = [], []

            def isect(a, b):
                if a is None:
                    return b
                x0, y0 = max(a[0], b[0]), max(a[1], b[1])
                x1, y1 = min(a[0] + a[2], b[0] + b[2]), min(a[1] + a[3], b[1] + b[3])
                return (x0, y0, max(0, x1 - x0), max(0, y1 - y0))

            def rec(o, path, scrolled, d, clip=None):
                if d > 40:
                    return
                st = states(o)
                if "showing" not in st:
                    return
                role = o.get_role_name()
                box = _box(o)
                name = (o.get_name() or "")[:60]
                vis = isect(clip, box) if box else None          # 스크롤 영역 밖(가려진 곳)은 빼고 보이는 부분만
                if box and box[2] > 0 and box[3] > 0 and vis[2] > 0 and vis[3] > 0:
                    try:
                        kids = o.get_child_count()
                    except Exception:
                        kids = 0
                    if role in TEXTY:
                        # 표 칸은 겹침에서 뺀다 — 한 칸에 아이콘과 글자가 따로 잡혀 늘 겹쳐 보인다
                        nodes.append({"role": role, "name": name, "box": vis, "path": path, "scrolled": scrolled,
                                      "leaf": role != "table cell" and (kids == 0 or role in (
                                          "button", "push button", "toggle button", "check box", "radio button",
                                          "label", "heading", "link"))})
                        c = _clip(o, box, 4, by_width=True) if role == "table cell" else _clip(o, box)
                        if c and name:
                            issues.append({"kind": c, "role": role, "name": name, "box": box})
                    if not scrolled:
                        fx, fy, fw, fh = 0, 0, fbox[2], fbox[3]
                        x, y, w, h = box
                        if x < fx - 2 or y < fy - 2 or x + w > fx + fw + 2 or y + h > fy + fh + 2:
                            if role in TEXTY:
                                issues.append({"kind": "창 밖", "role": role, "name": name, "box": box})
                try:
                    n = min(o.get_child_count(), 400)
                except Exception:
                    n = 0
                for j in range(n):
                    try:
                        c = o.get_child_at_index(j)
                    except Exception:
                        continue
                    if c is not None:
                        rec(c, path + (j,), scrolled or role in SCROLLY, d + 1,
                            isect(clip, box) if (role in SCROLLY and box) else clip)
            rec(frame, (), False, 0)
            # 겹침 — 위아래(조상·자손)가 아닌 위젯끼리, 작은 쪽 넓이의 25% 넘게
            leaves = [n for n in nodes if n["leaf"]]
            for ai in range(len(leaves)):
                A = leaves[ai]
                ax, ay, aw, ah = A["box"]
                for B in leaves[ai + 1:]:
                    if A["path"] == B["path"][:len(A["path"])] or B["path"] == A["path"][:len(B["path"])]:
                        continue
                    bx, by, bw, bh = B["box"]
                    ix = min(ax + aw, bx + bw) - max(ax, bx)
                    iy = min(ay + ah, by + bh) - max(ay, by)
                    if ix > 2 and iy > 2 and ix * iy > 0.25 * min(aw * ah, bw * bh):
                        issues.append({"kind": "겹침", "role": A["role"] + "/" + B["role"],
                                       "name": f"{A['name']} ↔ {B['name']}", "box": A["box"], "box2": B["box"]})
            res.append({"app": a.get_name(), "frame": fname, "box": fbox, "origin": origin(frame, pid),
                        "count": len(nodes), "issues": issues[:80]})
    return res


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
    if op == "layout":
        return layout(req)
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
