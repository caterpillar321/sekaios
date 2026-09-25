"""시작 메뉴 검색 — 앱 · 설정 · 파일 · 계산기 (윈도우 검색처럼).

매칭은 GTK 없는 순수 함수다 (따로 시험할 수 있게). 파일만 작업 스레드에서 찾는다 (FileSearcher).
  순위: 이름이 그대로 시작 > 단어 시작 > 들어 있음 > 초성 > 한/영 변환 > 설명
  한글: 초성(ㅋㄹㅁ → 크로미움), 조합 중인 마지막 글자(크로ㅁ·설저 → 크로미움·설정),
        한/영을 잘못 둔 채 친 것(zmfhal → 크로미, 초개ㅡㄷ → chrome)
"""
import ast
import json
import math
import operator
import os
import re
import threading
import time
import unicodedata
import urllib.parse
import xml.etree.ElementTree as ET
from collections import deque
from decimal import Decimal

from . import dbg

# ── 점수 (클수록 앞) ─────────────────────────────────────────────
EXACT = 1000         # 이름이 검색어와 같다
PREFIX = 900         # 이름이 검색어로 시작
WORD = 800           # 단어가 검색어로 시작
CONTAIN = 700        # 어딘가에 들어 있다
TOKENS = 640         # 여러 낱말이 모두 어딘가에 (순서 무관)
INITIALS = 600       # 초성 (+30 맨 앞, +15 단어 앞)
CONVERT = 500        # 한/영 변환해서 맞다 (+10~40)
DESC = 300           # 설명에만 있다
COMPOSE_PEN = 20     # 조합 중인 글자로 맞은 것은 같은 단계에서 조금 뒤로
KEYWORD = 650        # 일반 이름·키워드·실행 파일·별칭에 그대로 맞음 (650~680) — 이름에 든 것보다 뒤,
SECONDARY_PEN = 40   #   초성·변환으로 맞으면 같은 방법의 이름보다 이만큼 뒤
RECENT_BONUS = 60    # 최근에 연 파일

# ── 한글 ────────────────────────────────────────────────────────
CHO = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"
JUNG = "ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ"
JONG = ("", "ㄱ", "ㄲ", "ㄳ", "ㄴ", "ㄵ", "ㄶ", "ㄷ", "ㄹ", "ㄺ", "ㄻ", "ㄼ", "ㄽ", "ㄾ", "ㄿ", "ㅀ",
        "ㅁ", "ㅂ", "ㅄ", "ㅅ", "ㅆ", "ㅇ", "ㅈ", "ㅊ", "ㅋ", "ㅌ", "ㅍ", "ㅎ")
CHO_SET, JUNG_SET, JONG_SET = set(CHO), set(JUNG), set(JONG) - {""}
# 겹모음·겹받침 → 자판에서 따로 치는 낱자 (두벌식은 이것들을 두 번 눌러 만든다)
SPLIT = {"ㅘ": "ㅗㅏ", "ㅙ": "ㅗㅐ", "ㅚ": "ㅗㅣ", "ㅝ": "ㅜㅓ", "ㅞ": "ㅜㅔ", "ㅟ": "ㅜㅣ", "ㅢ": "ㅡㅣ",
         "ㄳ": "ㄱㅅ", "ㄵ": "ㄴㅈ", "ㄶ": "ㄴㅎ", "ㄺ": "ㄹㄱ", "ㄻ": "ㄹㅁ", "ㄼ": "ㄹㅂ", "ㄽ": "ㄹㅅ",
         "ㄾ": "ㄹㅌ", "ㄿ": "ㄹㅍ", "ㅀ": "ㄹㅎ", "ㅄ": "ㅂㅅ"}
JOIN = {v: k for k, v in SPLIT.items()}
_HANGUL_RE = re.compile("[가-힣ㄱ-ㅣ]")

# 두벌식 자판 (영문 자판의 같은 자리)
KEY2JAMO = dict(zip("qwertyuiopasdfghjklzxcvbnm", "ㅂㅈㄷㄱㅅㅛㅕㅑㅐㅔㅁㄴㅇㄹㅎㅗㅓㅏㅣㅋㅌㅊㅍㅠㅜㅡ"))
KEY2JAMO.update({"Q": "ㅃ", "W": "ㅉ", "E": "ㄸ", "R": "ㄲ", "T": "ㅆ", "O": "ㅒ", "P": "ㅖ"})
JAMO2KEY = {v: k.lower() for k, v in KEY2JAMO.items()}


def is_syllable(c):
    return "가" <= c <= "힣"


def has_hangul(s):
    return bool(_HANGUL_RE.search(s))


def keys_of(c):
    """한 글자 → 자판에서 친 낱자들 (각 → ㄱㅏㄱ, 왜 → ㅇㅗㅐ). 한글이 아니면 그대로"""
    if "가" <= c <= "힣":
        o = ord(c) - 0xAC00
        j, t = JUNG[o % 588 // 28], JONG[o % 28]
        return CHO[o // 588] + SPLIT.get(j, j) + SPLIT.get(t, t)
    return SPLIT.get(c, c)


def jamo_keys(s):
    return "".join(keys_of(c) for c in s)


def initial_of(c):
    return CHO[(ord(c) - 0xAC00) // 588] if "가" <= c <= "힣" else c


def compose(seq):
    """낱자 줄 → 한글 (두벌식 입력기와 같은 규칙: 받침은 다음 모음이 오면 다음 글자로 넘어간다)"""
    out = []
    cho = jung = jong = ""

    def flush():
        nonlocal cho, jung, jong
        if cho and jung:
            out.append(chr(0xAC00 + (CHO.index(cho) * 21 + JUNG.index(jung)) * 28 + JONG.index(jong)))
        elif cho or jung:
            out.append(cho or jung)
        cho = jung = jong = ""

    for ch in seq:
        if ch in CHO_SET:
            if cho and jung:
                if not jong and ch in JONG_SET:
                    jong = ch
                    continue
                if jong and JOIN.get(jong + ch) in JONG_SET:
                    jong = JOIN[jong + ch]
                    continue
            flush()
            cho = ch
        elif ch in JUNG_SET:
            if jong:
                parts = SPLIT.get(jong, jong)
                keep, move = (parts[0], parts[1]) if len(parts) == 2 else ("", parts)
                jong = keep
                flush()
                cho, jung = move, ch
            elif jung and JOIN.get(jung + ch) in JUNG_SET:
                jung = JOIN[jung + ch]
            elif cho and not jung:
                jung = ch
            else:
                flush()
                jung = ch
        else:
            flush()
            out.append(ch)
    flush()
    return "".join(out)


def eng2kor(s):
    """한글 모드를 켜지 않고 친 두벌식 (tjfwjd → 설정). 대문자는 된소리·ㅒㅖ 자리만 가린다"""
    return compose("".join(KEY2JAMO.get(c) or KEY2JAMO.get(c.lower(), c) for c in s))


def kor2eng(s):
    """한글 모드인 채 친 영어 (초개ㅡㄷ → chrome)"""
    return "".join(JAMO2KEY.get(k, k) for k in jamo_keys(s)).lower()


# ── 검색어 · 비교할 글 ───────────────────────────────────────────
def _norm(text):
    return " ".join(unicodedata.normalize("NFC", text or "").split()).lower()


class Query:
    """검색어 하나 — 매칭에 필요한 것을 한 번만 계산해 둔다"""

    def __init__(self, text, convert=True):
        self.raw = raw = " ".join(unicodedata.normalize("NFC", text or "").split())
        self.q = q = raw.lower()
        self.ns = q.replace(" ", "")
        self.single = len(self.ns) <= 1          # 한 글자는 단어 시작만 (다 걸리지 않게)
        # 초성으로 겹자음 낱자(ㄳ)가 들어와도 두 초성으로 본다
        self.ini_q = "".join(SPLIT.get(c, c) if c not in JUNG_SET else c for c in self.ns)
        self.cons = any(c in CHO_SET for c in self.ini_q)
        self.pure_cons = self.cons and all(c in CHO_SET for c in self.ini_q)
        last = q[-1:]
        self.partial = bool(last) and has_hangul(last)   # 마지막 글자는 아직 조합 중일 수 있다
        self.head, self.tail = q[:-1], jamo_keys(last)
        self.keys = jamo_keys(q)
        self.words = [w for w in q.split(" ") if w]
        self._wq = None
        self.alt, self.alt_strict = None, False
        if convert and self.ns:
            alt = None
            if has_hangul(q):
                alt = kor2eng(q)
                if len(alt.replace(" ", "")) < 2:
                    alt = None
            elif raw.isascii() and any(c.isalpha() for c in raw):
                alt = eng2kor(raw)
                if not any(is_syllable(c) for c in alt):
                    alt = None                   # 낱자만 나오면 (zfa → ㅋㄹㅁ) 너무 넓다
            if alt and alt.lower() != q:
                self.alt = Query(alt, convert=False)
                # 한 글자짜리 변환(rk → 가)은 단어 시작만
                self.alt_strict = self.single or len(self.alt.ns) <= 1

    def word_queries(self):
        if self._wq is None:
            self._wq = [Query(w) for w in self.words]
        return self._wq


class Field:
    """비교할 글 하나 (이름·키워드·파일 이름). 한글이 없으면 초성·자모 비교를 건너뛴다"""
    __slots__ = ("t", "ns", "ini", "keys", "hangul", "_starts")

    def __init__(self, text):
        self.t = t = _norm(text)
        self.hangul = has_hangul(t)
        if self.hangul:
            self.ns = t.replace(" ", "")
            self.ini = "".join(initial_of(c) for c in self.ns)
            self.keys = jamo_keys(t)
        else:
            self.ns = self.ini = self.keys = t
        self._starts = None

    @property
    def starts(self):
        """띄어쓰기를 뺀 글(ns)에서 단어가 시작하는 자리"""
        if self._starts is None:
            out, k, t = set(), 0, self.t
            for i, c in enumerate(t):
                if c == " ":
                    continue
                if i == 0 or not t[i - 1].isalnum():
                    out.add(k)
                k += 1
            self._starts = out
        return self._starts


_fcache = {}


def field(text):
    """앱·설정 글은 입력마다 다시 쓰므로 기억해 둔다 (파일 이름은 색인이 따로 갖는다)"""
    f = _fcache.get(text)
    if f is None:
        if len(_fcache) > 5000:
            _fcache.clear()
        f = _fcache[text] = Field(text)
    return f


# ── 매칭 ────────────────────────────────────────────────────────
def _direct(q, t):
    i = t.find(q)
    if i < 0 or not q:
        return 0
    if i == 0:
        return EXACT if len(t) == len(q) else PREFIX
    while i >= 0:
        if not t[i - 1].isalnum():
            return WORD
        i = t.find(q, i + 1)
    return CONTAIN


def _compose(qo, f):
    """마지막 글자가 조합 중이어도 (크로ㅁ → 크로미움, 설저 → 설정, 크롬 → 크로미움).
    앞 글자들은 그대로 맞고, 마지막 글자의 낱자가 그 자리부터의 낱자로 시작하면 맞다
    (받침은 다음 모음이 오면 다음 글자 첫소리가 되므로 두 글자까지 본다)"""
    if not (qo.partial and f.hangul) or qo.keys not in f.keys:
        return 0
    t, head, tail = f.t, qo.head, qo.tail
    n, best = len(head), 0
    pos = t.find(head)
    while 0 <= pos < len(t):
        j = pos + n
        if j < len(t) and jamo_keys(t[j:j + 2]).startswith(tail):
            tier = PREFIX if pos == 0 else (WORD if not t[pos - 1].isalnum() else CONTAIN)
            best = max(best, tier)
            if tier == PREFIX:
                break
        pos = t.find(head, pos + 1)
    return best - COMPOSE_PEN if best else 0


def _initials(qo, f):
    """초성 (ㅋㄹㅁ → 크로미움, ㅍㅇ ㄱㄹㅈ → 파일 관리자). 한글과 섞여도 (크ㄹㅁ). 띄어쓰기는 무시"""
    if not qo.cons or not f.hangul:
        return 0
    q, ns, ini = qo.ini_q, f.ns, f.ini
    n, best = len(q), -1
    if n > len(ns):
        return 0
    if qo.pure_cons:
        pos = ini.find(q)
        while pos >= 0:
            best = max(best, 30 if pos == 0 else (15 if pos in f.starts else 0))
            if best == 30:
                break
            pos = ini.find(q, pos + 1)
    elif all(c in ns for c in q if c not in CHO_SET):
        for pos in range(len(ns) - n + 1):
            for k in range(n):
                c = q[k]
                if c != ns[pos + k] and not (c in CHO_SET and c == ini[pos + k]):
                    break
            else:
                best = max(best, 30 if pos == 0 else (15 if pos in f.starts else 0))
    return INITIALS + best if best >= 0 else 0


def _convert(qo, f):
    alt = qo.alt
    if alt is None:
        return 0
    tier = max(_direct(alt.q, f.t), _compose(alt, f))
    if not tier or (qo.alt_strict and tier < WORD - COMPOSE_PEN):
        return 0
    return CONVERT + (tier - 600) // 10


def field_score(qo, f):
    """글 하나에 대한 점수 (0 = 안 맞음). 단계가 높은 방법부터 — 맞으면 아래 단계는 보지 않는다"""
    s = max(_direct(qo.q, f.t), _compose(qo, f))
    if qo.single:
        return s if s >= WORD - COMPOSE_PEN else _convert(qo, f)
    return s or _initials(qo, f) or _convert(qo, f)


def score_fields(qo, primary, secondary=(), desc=(), context=()):
    """primary: 이름들, secondary: 일반 이름·키워드 따위, desc: 설명,
    context: 여러 낱말 검색에서만 쓰는 글 (설정 항목이 든 페이지 이름)"""
    best = 0
    for text in primary:
        if text:
            best = max(best, field_score(qo, field(text)))
    for text in secondary:
        if text:
            s = field_score(qo, field(text))
            if s >= CONTAIN - COMPOSE_PEN:           # 키워드가 검색어와 같아도 이름에 든 것보다는 뒤
                best = max(best, KEYWORD + (s - CONTAIN) // 10)
            elif s:
                best = max(best, s - SECONDARY_PEN)
    if best:
        return best
    if len(qo.words) > 1:
        texts = [x for x in (*primary, *secondary, *context) if x]
        if all(any(field_score(w, field(x)) for x in texts) for w in qo.word_queries()):
            return TOKENS
    if len(qo.ns) >= 2:
        for text in desc:
            if text:
                s = _direct(qo.q, field(text).t)
                if s:
                    best = max(best, DESC + (10 if s >= WORD else 0))
    return best


# ── 앱 ──────────────────────────────────────────────────────────
# 앱 이름에 없는 흔한 부름말 (윈도우에서 "계산기"·"메모장"으로 찾던 것처럼)
APP_ALIASES = {
    "chromium": ("크로미움", "웹 브라우저", "인터넷"),
    "google-chrome": ("크롬", "구글 크롬"),
    "firefox-esr": ("파이어폭스",),
    "firefox": ("파이어폭스",),
    "thunar": ("파일 탐색기", "탐색기", "내 폴더", "투나"),
    "org.xfce.mousepad": ("메모장", "텍스트 편집기", "마우스패드"),
    "mousepad": ("메모장", "텍스트 편집기"),
    "galculator": ("계산기", "calculator"),
    "org.gnome.calculator": ("계산기",),
    "kitty": ("명령 프롬프트", "파워셸", "powershell"),
    "xfce4-taskmanager": ("작업 관리자", "task manager"),
    "htop": ("프로세스", "작업 관리자"),
    "org.gnome.evince": ("pdf", "pdf 뷰어", "문서 뷰어"),
    "org.xfce.ristretto": ("사진", "이미지 뷰어", "그림 보기", "사진 보기"),
    "xarchiver": ("압축", "압축 풀기", "zip"),
    "org.pulseaudio.pavucontrol": ("볼륨", "음량", "소리", "믹서"),
    "sekai-settings": ("제어판", "control panel", "환경 설정"),
    "nm-connection-editor": ("네트워크 연결", "vpn", "고정 ip"),
    "im-config": ("입력기", "한글 입력"),
}


def _exec_name(cmd):
    """Exec 의 실행 파일 이름 (/usr/bin/chromium %U → chromium, env A=B foo → foo)"""
    for tok in (cmd or "").split():
        if tok == "env" or "=" in tok:
            continue
        return os.path.basename(tok.strip("\"'"))
    return ""


def _as_list(v):
    return [v] if isinstance(v, str) else list(v or ())


def rank_apps(qo, apps, usage=None):
    """[(점수, 앱)] — 점수 순, 같으면 짧은 이름 · 목록 순서"""
    usage = usage or {}
    out = []
    for i, a in enumerate(apps):
        aid = (a.get("id") or "").lower()
        second = (_as_list(a.get("generic")) + _as_list(a.get("keywords"))
                  + [_exec_name(a.get("exec"))] + list(APP_ALIASES.get(aid, ())))
        s = score_fields(qo, (a.get("name", ""), a.get("name_en", "")), second,
                         desc=(a.get("comment", ""),))
        if s:
            out.append((s + usage_bonus(usage.get(aid, 0)), len(a.get("name", "")), i, a))
    out.sort(key=lambda p: (-p[0], p[1], p[2]))
    return [(p[0], p[3]) for p in out]


# ── 실행 횟수 (자주 연 앱이 앞으로) ──────────────────────────────
USAGE_FILE = os.path.expanduser("~/.local/state/sekai/app-usage.json")


def load_usage():
    """{소문자 앱 id: 실행 횟수}"""
    try:
        with open(USAGE_FILE, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(d, dict):
        return {}
    return {str(k).lower(): int(v) for k, v in d.items() if isinstance(v, (int, float))}


def note_launch(app_id):
    """앱을 실행할 때마다 — 실패해도 실행은 막지 않는다"""
    if not app_id:
        return
    d = load_usage()
    k = app_id.lower()
    d[k] = d.get(k, 0) + 1
    try:
        os.makedirs(os.path.dirname(USAGE_FILE), exist_ok=True)
        tmp = f"{USAGE_FILE}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
        os.replace(tmp, USAGE_FILE)
    except OSError as e:
        dbg("실행 횟수 기록 실패", e)


def usage_bonus(n):
    """1번 15 · 3번 30 · 7번 이상 45 — 한 단계(100)를 넘기지는 않는다"""
    return min(45.0, 15.0 * math.log2(1 + n)) if n > 0 else 0.0


# ── 설정 ────────────────────────────────────────────────────────
# 페이지: id → (제목 — 설정 앱 소스를 못 읽을 때, 아이콘, 키워드).
#   제목·있는지는 설정 앱 소스(pages/*.py 의 PAGES)에서 다시 읽는다 — 없는 페이지로 보내지 않게
SETTINGS_PAGES = {
    "about": ("시스템 정보", ["computer", "distributor-logo", "computer-symbolic"],
              ("정보", "about", "system info", "내 컴퓨터", "사양", "spec", "pc 정보")),
    "power": ("전원 및 잠금", ["battery", "preferences-system-power", "gnome-power-manager", "battery-symbolic"],
              ("전원", "power", "잠금", "lock", "절전", "배터리", "battery")),
    "defaults": ("기본 앱", ["preferences-desktop-default-applications", "application-x-executable"],
                 ("default apps", "기본 프로그램", "연결 프로그램")),
    "installed": ("설치된 앱", ["applications-other", "applications-system", "view-grid-symbolic"],
                  ("installed apps", "앱 목록", "프로그램 목록", "앱 및 기능", "프로그램 및 기능", "apps")),
    "account": ("계정", ["avatar-default", "system-users", "user-identity", "avatar-default-symbolic"],
                ("account", "내 계정", "내 정보", "로그인")),
    "sound": ("소리", ["audio-volume-high", "multimedia-volume-control", "audio-volume-high-symbolic"],
              ("sound", "audio", "오디오", "사운드")),
    "input": ("키보드 및 마우스", ["input-keyboard", "preferences-desktop-peripherals", "input-keyboard-symbolic"],
              ("키보드", "마우스", "keyboard", "mouse", "입력 장치", "input")),
    "display": ("디스플레이", ["video-display", "preferences-desktop-display", "display",
                          "preferences-desktop-display-symbolic", "video-display-symbolic"],
                ("display", "monitor", "모니터", "화면", "screen")),
    "graphics": ("그래픽", ["hardinfo", "video-display", "preferences-desktop-display"],
                 ("graphics", "그래픽 설정")),
    "locale": ("시간 및 언어", ["preferences-desktop-locale", "config-language", "preferences-system-time",
                           "preferences-desktop-locale-symbolic"],
               ("시간", "언어", "time", "language", "region", "지역")),
    "network": ("네트워크", ["network-wired", "network-workgroup", "preferences-system-network",
                         "network-wired-symbolic"],
                ("network", "인터넷", "internet", "연결")),
    "notifications": ("알림", ["preferences-system-notifications", "dialog-information", "notification",
                             "dialog-information-symbolic"],
                      ("notification", "notifications", "알람")),
    "wallpaper": ("배경화면", ["preferences-desktop-wallpaper", "image-x-generic", "image-x-generic-symbolic"],
                  ("배경", "바탕화면", "바탕 화면", "wallpaper", "background")),
    "appearance": ("색 및 모양", ["preferences-desktop-theme", "applications-graphics",
                              "applications-graphics-symbolic"],
                   ("개인 설정", "personalize", "personalization", "테마", "theme", "모양", "꾸미기")),
    "update": ("업데이트", ["system-software-update", "software-update-available", "update-manager",
                         "system-software-install"],
               ("update", "updates", "windows update", "업그레이드", "upgrade")),
    "users": ("가족 및 다른 사용자", ["system-users", "user-others", "avatar-default"],
              ("users", "사용자", "가족", "family", "다른 사용자", "계정 관리")),
    "bluetooth": ("블루투스", ["bluetooth", "bluetooth-active", "preferences-system-bluetooth", "blueman",
                           "bluetooth-active-symbolic", "bluetooth-symbolic"],
                  ("bluetooth", "블투", "무선 이어폰", "에어팟", "airpods", "헤드셋", "headset")),
}

# 페이지 안의 주요 항목: (페이지 id, 항목 이름, 키워드) — 각 페이지의 행 제목에서 뽑았다
SETTINGS_ITEMS = [
    ("about", "SekaiOS 버전", ("버전", "version", "운영체제", "os", "커널", "kernel", "debian", "데비안",
                              "코드네임")),
    ("about", "하드웨어 정보", ("프로세서", "cpu", "메모리", "ram", "memory", "저장소", "디스크", "disk",
                          "storage", "용량", "사양")),
    ("about", "호스트 이름", ("컴퓨터 이름", "hostname", "pc 이름")),
    ("power", "화면 끄기 시간", ("화면 끄기", "모니터 끄기", "screen off", "blank", "dpms",
                           "화면 보호기", "screensaver")),
    ("power", "자동 화면 잠금", ("화면 잠그기", "잠금 화면", "lock screen", "자동 잠금")),
    ("power", "절전 모드", ("절전", "대기 모드", "sleep", "suspend", "자동 절전")),
    ("power", "로그아웃 · 다시 시작 · 종료", ("로그아웃", "logout", "다시 시작", "재시작", "재부팅",
                                       "reboot", "restart", "시스템 종료", "shutdown", "종료", "끄기")),
    ("defaults", "기본 웹 브라우저", ("브라우저", "browser", "웹", "web", "chromium", "firefox")),
    ("defaults", "기본 터미널", ("터미널", "terminal", "cmd")),
    ("defaults", "기본 파일 관리자", ("파일 관리자", "file manager", "탐색기")),
    ("defaults", "Google Chrome 설치", ("구글 크롬 설치", "구글")),   # "크롬"만으로는 깔린 브라우저가 먼저
    ("defaults", "소프트웨어 설치", ("앱 설치", "프로그램 설치", "apt", "install", "설치")),
    ("account", "비밀번호 변경", ("비밀번호", "암호", "password", "비번", "passwd", "암호 변경",
                             "change password")),
    ("account", "내 계정 정보", ("사용자 이름", "username", "표시 이름", "uid", "홈 디렉터리",
                            "로그인 셸", "shell", "그룹", "group", "관리자 권한", "sudo")),
    ("sound", "출력 장치", ("스피커", "speaker", "헤드폰", "headphone", "이어폰", "output",
                        "오디오 장치", "audio device")),
    ("sound", "볼륨", ("음량", "volume", "소리 크기")),
    ("sound", "음소거", ("mute", "소리 끄기")),
    ("sound", "고급 소리 설정", ("pavucontrol", "마이크", "mic", "microphone", "녹음", "입력 장치",
                           "프로파일", "profile")),
    ("input", "키보드 레이아웃", ("레이아웃", "layout", "자판", "배열", "keyboard layout", "xkb",
                            "전환 단축키", "keyboard")),
    ("input", "키 반복 속도", ("키 반복", "repeat", "반복 시작 지연", "delay", "repeat rate")),
    ("input", "포인터 속도", ("마우스 속도", "mouse speed", "감도", "sensitivity", "커서 속도", "pointer",
                          "mouse")),
    ("input", "스크롤 방향", ("스크롤", "scroll", "natural scroll", "자연스러운 스크롤", "반대로")),
    ("input", "마우스를 따라 포커스", ("focus follows mouse", "포커스", "focus")),
    ("input", "터치패드", ("touchpad", "탭하여 클릭", "tap to click", "trackpad", "트랙패드")),
    ("display", "해상도", ("resolution", "화면 크기")),
    ("display", "주사율", ("refresh rate", "hz", "헤르츠", "화면 재생 빈도", "fps")),
    ("display", "주 디스플레이", ("주 모니터", "primary", "main display", "기본 모니터",
                            "주 디스플레이로 사용")),
    ("display", "배율", ("scale", "scaling", "크기 조정", "글자 크기", "ui 크기", "dpi", "확대")),
    ("display", "화면 방향", ("회전", "rotate", "rotation", "세로 모드", "orientation")),
    ("display", "가변 주사율 (VRR)", ("vrr", "freesync", "g-sync", "gsync", "adaptive sync", "가변")),
    ("display", "모니터 배치", ("배치", "arrange", "여러 모니터", "multi monitor", "듀얼 모니터",
                          "dual monitor", "식별", "identify")),
    ("display", "모니터 켜기 · 끄기", ("이 모니터 사용", "모니터 끄기", "disable monitor")),
    ("graphics", "NVIDIA 드라이버", ("nvidia", "엔비디아", "지포스", "geforce", "드라이버", "driver",
                                 "그래픽 드라이버", "rtx", "gtx", "cuda")),
    ("graphics", "그래픽 카드", ("gpu", "그래픽카드", "video card", "graphics card", "amd", "radeon",
                           "라데온", "intel", "인텔", "내장 그래픽")),
    ("graphics", "보안 부팅", ("secure boot", "mok", "시큐어 부트", "서명")),
    ("graphics", "그래픽 진단 정보", ("진단", "diagnostic", "report", "문제 해결")),
    ("locale", "표시 언어", ("언어", "language", "한국어", "korean", "english", "영어", "locale", "로캘",
                         "lang")),
    ("locale", "시간대", ("timezone", "time zone", "표준시", "지역", "서울", "seoul")),
    ("locale", "날짜 및 시간", ("시간", "time", "날짜", "date", "시계", "clock", "시간 자동 맞춤", "ntp",
                           "시간 동기화")),
    ("locale", "Windows 와 시간 맞추기", ("윈도우 시간", "rtc", "localtime", "듀얼 부팅", "dual boot",
                                     "시간이 틀림")),
    ("locale", "한/영 전환", ("한영", "한/영", "한영키", "한글 입력", "입력기", "ime", "ibus", "hangul",
                          "오른쪽 alt", "right alt", "한자", "hanja", "두벌식", "세벌식")),
    ("network", "Wi-Fi", ("wifi", "와이파이", "무선", "wireless", "wlan", "무선 네트워크", "무선랜")),
    ("network", "유선 연결", ("이더넷", "ethernet", "랜", "lan", "유선", "wired", "ip 주소", "ip address")),
    ("network", "고급 연결 편집기", ("고정 ip", "static ip", "vpn", "프록시", "proxy", "dns",
                              "nm-connection-editor")),
    ("notifications", "방해 금지", ("do not disturb", "dnd", "집중 모드", "알림 끄기", "방해금지")),
    ("notifications", "알림 팝업 시간", ("팝업", "popup", "timeout", "표시 시간")),
    ("notifications", "알림 기록", ("기록", "history", "기록 지우기", "보관", "알림 센터")),
    ("wallpaper", "배경화면 그림 고르기", ("사진", "그림", "이미지", "image", "picture", "표시 방식",
                                  "맞춤", "채우기", "fill")),
    ("appearance", "다크 모드", ("다크", "dark", "dark mode", "어두운 테마", "라이트 모드", "라이트",
                            "light mode", "밝은 테마", "밝기")),
    ("appearance", "강조색", ("색", "색상", "color", "colour", "accent", "강조 색", "테마 색", "프리셋",
                          "글자색", "패널 배경색")),
    ("appearance", "제목 표시줄", ("타이틀바", "titlebar", "title bar", "제목 표시줄 높이",
                            "제목 표시줄 색")),
    ("appearance", "창 모서리 · 테두리 · 여백", ("모서리", "둥글기", "rounding", "corner", "테두리",
                                        "border", "여백", "gap", "간격", "창 사이 여백")),
    ("appearance", "투명도 · 흐림 · 그림자", ("투명도", "opacity", "transparency", "흐림", "blur",
                                      "그림자", "shadow", "애니메이션", "animation", "효과",
                                      "effects")),
    ("appearance", "작업 표시줄", ("taskbar", "패널", "panel", "작업 표시줄 높이", "시계 형식",
                            "clock format")),
    ("appearance", "커서 크기", ("커서", "cursor", "마우스 포인터", "포인터 크기")),
    ("update", "업데이트 확인", ("업데이트", "update", "upgrade", "apt", "보안 업데이트", "security",
                            "패치", "새 버전")),
    ("update", "자동 업데이트 확인", ("자동 업데이트", "auto update", "자동으로 확인")),
    ("update", "SekaiOS 업데이트 저장소", ("저장소", "repository", "repo", "sources")),
    ("users", "다른 사용자 추가", ("사용자 추가", "계정 추가", "add user", "새 계정", "계정 만들기",
                             "새 사용자")),
    ("users", "관리자로 만들기", ("관리자", "admin", "administrator", "sudo", "권한")),
    ("users", "사용자 삭제", ("계정 삭제", "remove user", "delete user", "사용자 제거")),
    ("bluetooth", "블루투스 장치 추가", ("장치 추가", "기기 연결", "장치 연결", "페어링", "pairing",
                                "짝 맺기", "블루투스 이어폰", "블루투스 키보드", "블루투스 마우스")),
    ("bluetooth", "블루투스 켜기 · 끄기", ("블루투스 끄기", "블루투스 켜기", "bluetooth on", "bluetooth off")),
    ("bluetooth", "이 PC 의 이름", ("장치 이름", "블루투스 이름", "다른 장치가 찾을 수 있게", "검색 허용",
                             "discoverable")),
]

_PAGES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "sekaisettings", "pages")
_ID_RE = re.compile(r'"id"\s*:\s*"([\w-]+)"')
_TITLE_RE = re.compile(r'"title"\s*:\s*"([^"]+)"')
_pages_cache = {"key": None, "pages": None, "entries": None}


def installed_pages():
    """설정 앱에 실제로 있는 페이지 {id: 제목} — 모듈을 가져오지 않고 소스를 읽는다
    (페이지 모듈은 GTK 화면 코드라 무겁다). 못 읽으면 None"""
    init = os.path.join(_PAGES_DIR, "__init__.py")
    try:
        key = (os.stat(_PAGES_DIR).st_mtime, os.stat(init).st_mtime)
    except OSError:
        return None
    if _pages_cache["key"] == key:
        return _pages_cache["pages"]
    try:
        with open(init, encoding="utf-8") as f:
            m = re.search(r"MODULES\s*=\s*\[([^\]]*)\]", f.read())
        names = re.findall(r"\w+", m.group(1)) if m else [
            fn[:-3] for fn in sorted(os.listdir(_PAGES_DIR)) if fn.endswith(".py") and fn != "__init__.py"]
    except OSError:
        return None
    pages = {}
    for name in names:
        try:
            with open(os.path.join(_PAGES_DIR, name + ".py"), encoding="utf-8") as f:
                src = f.read()
        except OSError:
            continue
        at = src.find("\nPAGES")
        for chunk in src[at:].split("{")[1:] if at >= 0 else ():
            i, t = _ID_RE.search(chunk), _TITLE_RE.search(chunk)
            if i and t:
                pages.setdefault(i.group(1), t.group(1))
    _pages_cache.update(key=key, pages=pages or None, entries=None)
    return _pages_cache["pages"]


def settings_entries():
    """검색할 설정: 페이지마다 하나 + 주요 항목. 설정 앱에 없는 페이지(아직 없는 블루투스 따위)는 뺀다"""
    pages = installed_pages()
    if _pages_cache["entries"] is not None and _pages_cache["pages"] is pages:
        return _pages_cache["entries"]
    out = []
    for pid, (title, icons, kw) in SETTINGS_PAGES.items():
        if pages is not None and pid not in pages:
            continue
        out.append({"page": pid, "title": (pages or {}).get(pid, title), "sub": "",
                    "icons": icons, "kw": kw})
    for pid, title in (pages or {}).items():
        if pid not in SETTINGS_PAGES:                    # 새로 생긴 페이지 — 이름으로만이라도
            out.append({"page": pid, "title": title, "sub": "", "icons": ["preferences-system"], "kw": ()})
    for pid, title, kw in SETTINGS_ITEMS:
        if pages is not None and pid not in pages:
            continue
        ptitle = (pages or {}).get(pid) or SETTINGS_PAGES.get(pid, (pid,))[0]
        out.append({"page": pid, "title": title, "sub": ptitle,
                    "icons": SETTINGS_PAGES.get(pid, (None, ["preferences-system"]))[1], "kw": kw})
    _pages_cache["entries"] = out
    return out


def rank_settings(qo):
    """[(점수, 항목)] — 같은 점수면 페이지가 항목보다, 짧은 이름이 먼저"""
    out = []
    for e in settings_entries():
        s = score_fields(qo, (e["title"],), e["kw"], context=(e["sub"],))
        if s:
            out.append((s, e))
    out.sort(key=lambda p: (-p[0], bool(p[1]["sub"]), len(p[1]["title"])))
    return out


# ── 계산기 ──────────────────────────────────────────────────────
_CALC_CHARS = re.compile(r"^[\d\s.,+\-*/×÷^%()]+$")
_BIN = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
        ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod}
MAX_BITS = 4000      # 정수 결과 상한 (약 1200자리) — 9^9^9 같은 것으로 셸이 멈추지 않게


class _CalcError(Exception):
    pass


def _ev(n, depth=0):
    if depth > 100:
        raise _CalcError
    if isinstance(n, ast.Constant) and type(n.value) in (int, float):
        if isinstance(n.value, int) and n.value.bit_length() > MAX_BITS:
            raise _CalcError
        return n.value
    if isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.UAdd, ast.USub)):
        v = _ev(n.operand, depth + 1)
        return -v if isinstance(n.op, ast.USub) else v
    if isinstance(n, ast.BinOp) and (type(n.op) in _BIN or isinstance(n.op, ast.Pow)):
        a, b = _ev(n.left, depth + 1), _ev(n.right, depth + 1)
        if isinstance(n.op, ast.Pow):
            # 거듭제곱은 결과 크기를 먼저 어림한다 (계산부터 하면 이미 늦다)
            if abs(b) > 10000 or (abs(a) > 1 and abs(b) * math.log2(abs(a)) > MAX_BITS):
                raise _CalcError
            v = a ** b
        else:
            v = _BIN[type(n.op)](a, b)
        if isinstance(v, complex) or (isinstance(v, int) and v.bit_length() > MAX_BITS):
            raise _CalcError
        return v
    raise _CalcError


def _fmt(v):
    if isinstance(v, float):
        if math.isnan(v) or math.isinf(v):
            raise _CalcError
        if v.is_integer() and abs(v) < 1e15:
            v = int(v)
        else:
            return f"{v:.12g}"
    if abs(v) < 10 ** 21:
        return str(v)
    # 긴 정수는 1.23456789012e+30 꼴로 (float 로는 1e308 을 넘으면 넘친다)
    mant, _, exp = format(Decimal(v), ".12e").partition("e")
    return f"{mant.rstrip('0').rstrip('.')}e{exp}"


def calculate(text):
    """수식처럼 보이면 계산한 결과 글자, 아니면 None ("12*3+4" → "40", "15%" → "0.15")"""
    s = (text or "").strip().rstrip("=").strip()
    if not s or len(s) > 120 or not _CALC_CHARS.match(s) or not re.search(r"\d", s):
        return None
    if not re.search(r"[+\-*/×÷^%]", s) or re.fullmatch(r"\d+(-\d+){2,}", s):
        return None                                  # 숫자만, 또는 날짜·전화번호 (2026-09-25)
    e = s.replace("×", "*").replace("÷", "/").replace("^", "**")
    e = re.sub(r"(?<=\d),(?=\d{3}(?!\d))", "", e)   # 1,000 → 1000
    if "," in e:
        return None
    e = re.sub(r"%(?!\s*[\d(.])", "/100", e)        # 15% → 15/100 (10 % 3 은 나머지)
    e = re.sub(r"(?<![\d.])0+(?=\d)", "", e)         # 08 → 8 (파이썬은 앞의 0 을 받지 않는다)
    try:
        tree = ast.parse(e, mode="eval").body
    except (SyntaxError, ValueError, MemoryError, RecursionError):
        return None
    if isinstance(tree, ast.Constant) or (isinstance(tree, ast.UnaryOp) and isinstance(tree.operand, ast.Constant)):
        return None
    try:
        return _fmt(_ev(tree))
    except (_CalcError, ArithmeticError, ValueError, TypeError, RecursionError):
        return None


def find(qo, apps, usage=None):
    """앱·설정·계산 — 동기로 (입력마다 불러도 충분히 빠르다). 파일은 FileSearcher"""
    return {"apps": rank_apps(qo, apps, usage), "settings": rank_settings(qo), "calc": calculate(qo.raw)}


# ── 파일 ────────────────────────────────────────────────────────
MAX_DEPTH = 4            # 사용자 폴더 아래 네 단계까지
MAX_SCAN = 20000         # 훑는 항목 상한 (사진 수만 장이 든 폴더에서도 금방 끝나게)
SCAN_SECS = 2.0
SKIP_DIRS = {"node_modules", "__pycache__"}
RECENT_FILE = os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"),
                           "recently-used.xbel")
# user-dirs 가 비어 있거나 언어를 바꿔 이름이 둘일 때도 찾게
_FALLBACK_DIRS = ("Desktop", "바탕화면", "Documents", "문서", "Downloads", "다운로드",
                  "Pictures", "사진", "Music", "음악", "Videos", "동영상")


class FileEntry:
    __slots__ = ("name", "path", "isdir", "recent", "f")

    def __init__(self, name, path, isdir, recent):
        self.name, self.path, self.isdir, self.recent = name, path, isdir, recent
        self.f = Field(name)


def user_roots():
    """훑을 사용자 폴더 (바탕 화면·문서·다운로드·사진·음악·동영상). GLib 의 폴더 캐시는
    스레드 안전하지 않으므로 메인 스레드에서 부른다. 홈 자체와 다른 폴더 안에 든 폴더는 뺀다"""
    from gi.repository import GLib
    GLib.reload_user_special_dirs_cache()
    U = GLib.UserDirectory
    home = os.path.realpath(os.path.expanduser("~"))
    cands = [GLib.get_user_special_dir(k) for k in (
        U.DIRECTORY_DESKTOP, U.DIRECTORY_DOCUMENTS, U.DIRECTORY_DOWNLOAD,
        U.DIRECTORY_PICTURES, U.DIRECTORY_MUSIC, U.DIRECTORY_VIDEOS)]
    cands += [os.path.join(home, n) for n in _FALLBACK_DIRS]
    out = []
    for p in cands:
        if not p or not os.path.isdir(p):
            continue
        rp = os.path.realpath(p)
        if rp != home and rp not in [r for _p, r in out]:
            out.append((p, rp))
    return [p for p, rp in out if not any(rp.startswith(o + os.sep) for _q, o in out)]


def recent_files(limit=200):
    """최근 연 파일 (GTK 앱이 적는 recently-used.xbel) — 새것부터, 지금 있는 파일만"""
    try:
        root = ET.parse(RECENT_FILE).getroot()
    except (OSError, ET.ParseError):
        return []
    items = []
    for b in root.iter("bookmark"):
        href = b.get("href") or ""
        if href.startswith("file://"):
            items.append((b.get("visited") or b.get("modified") or b.get("added") or "",
                          urllib.parse.unquote(urllib.parse.urlsplit(href).path)))
    items.sort(reverse=True)
    out = []
    for _t, path in items:
        if path not in out and os.path.exists(path):
            out.append(path)
            if len(out) >= limit:
                break
    return out


def build_index(roots):
    """최근 파일 + 사용자 폴더 (숨김 제외, 깊이·개수·시간 상한). 얕은 것부터 (상한에 걸려도 가까운 것은 남게)"""
    out, seen = [], set()
    for p in recent_files():
        seen.add(p)
        out.append(FileEntry(os.path.basename(p.rstrip("/")) or p, p, os.path.isdir(p), True))
    end = time.monotonic() + SCAN_SECS
    todo, n = deque((r, 1) for r in roots), 0
    while todo:
        d, level = todo.popleft()
        try:
            it = os.scandir(d)
        except OSError:
            continue
        with it:
            for e in it:
                if e.name.startswith(".") or e.name in SKIP_DIRS:
                    continue
                n += 1
                if n > MAX_SCAN or (not n % 500 and time.monotonic() > end):
                    dbg("파일 색인 상한", n)
                    return out
                try:
                    isdir = e.is_dir(follow_symlinks=False)
                except OSError:
                    isdir = False
                if e.path not in seen:
                    out.append(FileEntry(e.name, e.path, isdir, False))
                if isdir and level < MAX_DEPTH:
                    todo.append((e.path, level + 1))
    return out


def find_files(qo, index, cancelled=lambda: False, limit=12):
    """[{"name","path","dir","recent"}] — 점수 순. 취소되면 None"""
    res = []
    for i, fe in enumerate(index):
        if not i % 2000 and cancelled():
            return None
        s = field_score(qo, fe.f)
        if s:
            res.append((s + (RECENT_BONUS if fe.recent else 0), fe))
    if cancelled():
        return None
    res.sort(key=lambda p: (-p[0], p[1].isdir, len(p[1].name)))
    return [{"name": fe.name, "path": fe.path, "dir": fe.isdir, "recent": fe.recent, "score": s}
            for s, fe in res[:limit]]


class FileSearcher:
    """파일 이름 검색 — 입력이 150ms 멈추면 작업 스레드에서. 새 입력이 오면 앞 검색은 버린다 (세대 번호).
    색인은 한 번 만들어 잠시(TTL) 다시 쓴다 — 입력마다 디스크를 훑지 않게. 결과는 on_done(질의, 결과)
    으로 메인 루프에서 받는다"""
    DELAY = 150
    TTL = 30

    def __init__(self, on_done):
        self.on_done = on_done
        self.gen = 0
        self._src = 0
        self._roots = None
        self._lock = threading.Lock()
        self._index = None
        self._index_key = None

    def search(self, qo):
        """qo 가 None 이면 취소만. 검색을 시작했으면 True"""
        from gi.repository import GLib
        self.gen += 1
        if self._src:
            GLib.source_remove(self._src)
            self._src = 0
        if qo is None or not (len(qo.ns) >= 2 or has_hangul(qo.ns)):
            return False
        self._src = GLib.timeout_add(self.DELAY, self._start, self.gen, qo)
        return True

    def cancel(self):
        self.search(None)

    def drop(self):
        """메뉴를 닫을 때 — 색인을 놓아 메모리를 돌려준다 (폴더도 다음에 다시 본다)"""
        self.cancel()
        self._index = self._index_key = self._roots = None

    def _start(self, gen, qo):
        self._src = 0
        if self._roots is None:
            self._roots = user_roots()
        threading.Thread(target=self._work, args=(gen, qo, self._roots), daemon=True).start()
        return False

    def _get_index(self, roots):
        with self._lock:                          # 앞 스레드가 만드는 중이면 기다렸다가 같이 쓴다
            try:
                rmt = os.stat(RECENT_FILE).st_mtime
            except OSError:
                rmt = None
            key = (tuple(roots), rmt)
            k = self._index_key
            if self._index is not None and k and k[0] == key and time.monotonic() - k[1] < self.TTL:
                return self._index
            t0 = time.monotonic()
            idx = build_index(roots)
            dbg("파일 색인", len(idx), "개", f"{(time.monotonic() - t0) * 1000:.0f}ms")
            self._index, self._index_key = idx, (key, time.monotonic())
            return idx

    def _work(self, gen, qo, roots):
        from gi.repository import GLib
        try:
            idx = self._get_index(roots)
            if gen != self.gen:
                return
            res = find_files(qo, idx, lambda: gen != self.gen)
        except Exception as e:                    # 스레드에서 죽으면 "검색 중…"이 남는다 — 빈 결과라도
            dbg("파일 검색 실패", e)
            res = []
        if res is not None:
            GLib.idle_add(self._done, gen, qo, res)

    def _done(self, gen, qo, res):
        if gen == self.gen:
            self.on_done(qo, res)
        return False
