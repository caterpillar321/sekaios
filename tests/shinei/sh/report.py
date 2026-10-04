"""신에이 보고서 — 대상(줄) × 환경(칸) 격자. 빨강 = 깨짐(잘림·겹침·창 밖), 주황 = 기준과 달라짐, 회색 = 기준 없음"""
import html
import json
import os

SEVERE = {"잘림", "겹침", "창 밖", "창이 화면을 넘는다", "안 뜸"}


def status(r):
    kinds = {i["kind"] for i in r["issues"]}
    if kinds & SEVERE:
        return "bad"
    if r.get("diff") is not None and r["diff"] > r.get("diff_limit", 0.003):
        return "changed"
    if r.get("size_changed"):
        return "changed"
    if r.get("baseline") is False:
        return "new"
    return "ok"


def write(outdir, results, configs, targets, meta):
    with open(os.path.join(outdir, "results.json"), "w", encoding="utf-8") as f:
        json.dump({"meta": meta, "results": results}, f, ensure_ascii=False, indent=1)
    by = {(r["target"], r["config"]): r for r in results}
    e = html.escape
    cnt = {"bad": 0, "changed": 0, "new": 0, "ok": 0}
    for r in results:
        cnt[status(r)] += 1
    out = ["<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>",
           "<title>신에이 보고서</title><style>",
           ":root{--bg:#0f1218;--fg:#e8ecf2;--sub:#97a1b0;--card:#181d26;--bad:#e5484d;--chg:#f5a524;--new:#5b6577;--ok:#30a46c}",
           "body{margin:0;padding:16px;background:var(--bg);color:var(--fg);font:14px/1.5 system-ui,'Pretendard',sans-serif}",
           "h1{font-size:20px;margin:0 0 4px}.sub{color:var(--sub);margin-bottom:12px}",
           ".wrap{overflow-x:auto}table{border-collapse:collapse}th,td{padding:6px;vertical-align:top}",
           "th{position:sticky;top:0;background:var(--bg);font-weight:600;text-align:left;font-size:12px;color:var(--sub)}",
           "td.t{font-weight:600;white-space:nowrap}.cell{width:180px}.cell img{width:180px;border:3px solid var(--new);border-radius:6px;display:block}",
           ".bad img{border-color:var(--bad)}.changed img{border-color:var(--chg)}.ok img{border-color:var(--ok)}",
           ".iss{font-size:11px;color:var(--sub);max-width:180px;word-break:break-all}.iss b{color:var(--bad)}",
           ".pill{display:inline-block;padding:2px 8px;border-radius:99px;margin-right:6px;font-size:12px}",
           "</style>",
           f"<h1>신에이 보고서</h1><div class=sub>{e(meta.get('when',''))} · {e(' · '.join(f'{k} {v}' for k,v in (meta.get('versions') or {}).items()))} · {meta.get('secs',0)}초</div>",
           f"<div class=sub><span class=pill style='background:var(--bad)'>깨짐 {cnt['bad']}</span>"
           f"<span class=pill style='background:var(--chg);color:#000'>달라짐 {cnt['changed']}</span>"
           f"<span class=pill style='background:var(--new)'>기준 없음 {cnt['new']}</span>"
           f"<span class=pill style='background:var(--ok)'>같음 {cnt['ok']}</span></div>",
           "<div class=wrap><table><tr><th>대상</th>" + "".join(f"<th>{e(c['desc'])}</th>" for c in configs) + "</tr>"]
    for t in targets:
        out.append(f"<tr><td class=t>{e(t['id'])}</td>")
        for c in configs:
            r = by.get((t["id"], c["id"]))
            if not r:
                out.append("<td></td>")
                continue
            st = status(r)
            img = f"<a href='{e(r['shot'])}'><img loading=lazy src='{e(r['shot'])}'></a>" if r.get("shot") else ""
            if r.get("diffimg"):
                img += f"<div class=iss><a href='{e(r['diffimg'])}'>달라진 곳</a> {r['diff']*100:.2f}%</div>"
            iss = "".join(f"<div><b>{e(i['kind'])}</b> {e(i.get('role',''))} {e(i.get('name',''))[:70]}</div>" for i in r["issues"][:6])
            if len(r["issues"]) > 6:
                iss += f"<div>… {len(r['issues']) - 6}개 더</div>"
            out.append(f"<td class='cell {st}'>{img}<div class=iss>{iss}</div></td>")
        out.append("</tr>")
    out.append("</table></div>")
    with open(os.path.join(outdir, "report.html"), "w", encoding="utf-8") as f:
        f.write("\n".join(out))
    return cnt
