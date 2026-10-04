"""보고서 — results.json 과 report.html (한 폴더에 스크린숏과 함께, 그대로 열어 보면 된다)."""
import html
import json
import os

STYLE = """
:root { --bg:#151517; --fg:#ececf0; --mut:#9a9aa6; --card:#1f1f24; --line:#30303a; --pass:#39c5bb; --warn:#e6b450;
        --fail:#ef5a6f; --skip:#777; }
* { box-sizing:border-box } body { margin:0; background:var(--bg); color:var(--fg); font:15px/1.5 Pretendard, system-ui, sans-serif; }
main { max-width:1100px; margin:0 auto; padding:24px 16px 64px }
h1 { font-size:24px; margin:0 0 4px } .sub { color:var(--mut); margin-bottom:20px }
.sum { display:flex; gap:12px; flex-wrap:wrap; margin-bottom:24px }
.pill { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:10px 16px; min-width:90px }
.pill b { display:block; font-size:22px } h2 { font-size:18px; margin:28px 0 8px; border-bottom:1px solid var(--line); padding-bottom:6px }
.t { background:var(--card); border:1px solid var(--line); border-left:4px solid var(--skip); border-radius:8px; padding:10px 14px; margin:8px 0 }
.t.pass { border-left-color:var(--pass) } .t.warn { border-left-color:var(--warn) } .t.fail,.t.error { border-left-color:var(--fail) }
.t .h { display:flex; justify-content:space-between; gap:12px } .t .st { color:var(--mut); white-space:nowrap }
.t pre { white-space:pre-wrap; background:#0e0e10; padding:8px; border-radius:6px; font-size:12.5px; overflow-x:auto }
.t ul { margin:6px 0; padding-left:20px; color:var(--mut) } .t img { max-width:100%; border-radius:6px; margin-top:6px; border:1px solid var(--line) }
.find { color:var(--warn) }
"""


def write(outdir, results, meta):
    with open(os.path.join(outdir, "results.json"), "w") as f:
        json.dump({"meta": meta, "results": results}, f, ensure_ascii=False, indent=1)
    cnt = {k: sum(1 for r in results if r["status"] == k) for k in ("pass", "warn", "fail", "error", "skip")}
    e = html.escape
    out = [f"<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
           f"<title>MafuyuMom 보고서</title><style>{STYLE}</style><main>",
           f"<h1>MafuyuMom 보고서</h1><div class=sub>{e(meta.get('when', ''))} · "
           f"{e(' · '.join(f'{k} {v}' for k, v in (meta.get('versions') or {}).items()))} · {meta.get('secs', 0)}초</div>",
           "<div class=sum>"]
    for k, label in (("pass", "통과"), ("warn", "경고"), ("fail", "실패"), ("error", "오류"), ("skip", "건너뜀")):
        out.append(f"<div class=pill>{label}<b>{cnt[k]}</b></div>")
    out.append("</div>")
    suites = []
    for r in results:
        if r["suite"] not in suites:
            suites.append(r["suite"])
    for s in suites:
        out.append(f"<h2>{e(s)}</h2>")
        for r in [r for r in results if r["suite"] == s]:
            out.append(f"<div class='t {r['status']}'><div class=h><b>{e(r['name'])}</b>"
                       f"<span class=st>{r['status']} · {r['secs']}s</span></div>")
            if r["msg"]:
                out.append(f"<pre>{e(r['msg'])}</pre>")
            if r["problems"]:
                out.append("<ul>" + "".join(f"<li>{e(p)}</li>" for p in r["problems"]) + "</ul>")
            if r["findings"]:
                out.append("<ul>" + "".join(f"<li class=find>{e(p)}</li>" for p in r["findings"]) + "</ul>")
            if r["notes"]:
                out.append("<ul>" + "".join(f"<li>{e(n)}</li>" for n in r["notes"][-12:]) + "</ul>")
            if r["traceback"]:
                out.append(f"<pre>{e(r['traceback'])}</pre>")
            for sh in r["shots"]:
                out.append(f"<img loading=lazy src='shots/{e(sh)}'>")
            out.append("</div>")
    out.append("</main>")
    with open(os.path.join(outdir, "report.html"), "w") as f:
        f.write("\n".join(out))
    return cnt
