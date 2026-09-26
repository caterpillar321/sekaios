"""메모장 — 파일 바이트 ↔ 글. 인코딩·줄 끝을 알아내고 저장할 때 그대로 되돌린다.

GTK 를 쓰지 않는다 (작업 스레드에서 부르고, 따로 시험하기 쉽게).

알아내는 순서 (윈도우 메모장과 같은 생각):
  1. BOM — UTF-8(BOM) · UTF-16 LE · UTF-16 BE
  2. BOM 없는 UTF-16 — 영문 글이면 바이트 둘 중 하나가 거의 늘 0 이다
  3. UTF-8 로 읽히면 UTF-8 (순수 ASCII 도 여기)
  4. CP949 — 한국어 윈도우에서 만든 텍스트 파일 ("ANSI")
  5. Latin-1 — 어떤 바이트든 읽히므로 "열 수 없는 파일"이 없게
"""
import codecs

# (키, 상태 표시줄·목록에 보일 이름, 파이썬 코덱, 앞에 붙일 BOM)
ENCODINGS = [
    ("utf-8", "UTF-8", "utf-8", b""),
    ("utf-8-bom", "UTF-8(BOM)", "utf-8", codecs.BOM_UTF8),
    ("utf-16-le", "UTF-16 LE", "utf-16-le", codecs.BOM_UTF16_LE),
    ("utf-16-be", "UTF-16 BE", "utf-16-be", codecs.BOM_UTF16_BE),
    ("cp949", "ANSI(CP949)", "cp949", b""),
    ("latin-1", "Latin-1(ISO-8859-1)", "latin-1", b""),
]
_BY_KEY = {e[0]: e for e in ENCODINGS}

# 줄 끝 — (키, 이름, 실제 글자)
EOLS = [
    ("crlf", "Windows (CRLF)", "\r\n"),
    ("lf", "Unix (LF)", "\n"),
    ("cr", "Macintosh (CR)", "\r"),
]
_EOL_BY_KEY = {e[0]: e for e in EOLS}
# 새 문서의 줄 끝 — 리눅스의 설정 파일·스크립트가 CRLF 로 망가지지 않게 LF
DEFAULT_EOL = "lf"
DEFAULT_ENCODING = "utf-8"

# 글이 아닌 것으로 볼 제어 문자 (탭·줄 바꿈·폼 피드·ESC·백스페이스는 텍스트에도 흔하다)
_CTRL = bytes(b for b in range(32) if b not in (8, 9, 10, 12, 13, 27))
_SAMPLE = 65536


def encoding_name(key):
    e = _BY_KEY.get(key)
    return e[1] if e else key


def eol_name(key):
    e = _EOL_BY_KEY.get(key)
    return e[1] if e else key


def _utf16_guess(data):
    """BOM 없는 UTF-16 — 한쪽 자리(홀·짝)에만 0 이 많으면. 한글만 있는 글은 0 이 거의 없어 못 알아낸다"""
    sample = data[:_SAMPLE]
    if len(sample) < 4 or len(data) % 2:
        return None
    half = len(sample) // 2
    even0 = sample[0::2].count(0)
    odd0 = sample[1::2].count(0)
    if odd0 > half * 0.3 and even0 < half * 0.02:
        return "utf-16-le"
    if even0 > half * 0.3 and odd0 < half * 0.02:
        return "utf-16-be"
    return None


def looks_binary(data):
    """앞부분에 NUL 이 있거나 제어 문자가 많으면 글 파일이 아니다 (UTF-16 은 먼저 걸러 부른다)"""
    sample = data[:_SAMPLE]
    if not sample:
        return False
    if b"\x00" in sample:
        return True
    ctrl = len(sample) - len(sample.translate(None, _CTRL))
    return ctrl > len(sample) * 0.05


def sniff(data):
    """BOM 이나 BOM 없는 UTF-16 이면 그 키, 아니면 None"""
    if data.startswith(codecs.BOM_UTF8):
        return "utf-8-bom"
    if data.startswith(codecs.BOM_UTF16_LE):
        return "utf-16-le"
    if data.startswith(codecs.BOM_UTF16_BE):
        return "utf-16-be"
    return _utf16_guess(data)


def decode(data, forced=None):
    """바이트 → (글, 인코딩 키). forced 는 열기 창에서 고른 인코딩 (없으면 자동 검색)"""
    if forced in _BY_KEY:
        key = forced
        _k, _n, codec, bom = _BY_KEY[key]
        if key == "utf-8" and data.startswith(codecs.BOM_UTF8):
            key, bom = "utf-8-bom", codecs.BOM_UTF8
        body = data[len(bom):] if bom and data.startswith(bom) else data
        return body.decode(codec, errors="replace"), key
    key = sniff(data)
    if key:
        _k, _n, codec, bom = _BY_KEY[key]
        body = data[len(bom):] if data.startswith(bom) else data
        # 짝이 맞지 않는 서로게이트 따위 — 깨진 글자 하나로 파일 전체를 못 여는 일은 없게
        return body.decode(codec, errors="replace"), key
    try:
        return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        pass
    # UTF-8 인데 몇 바이트만 깨진 파일(끝이 잘렸거나 한두 곳이 망가짐)은 UTF-8 로 — 깨진 곳만 � 로 보인다.
    #   예전엔 파일 전체를 Latin-1 로 열어 한글이 모두 깨졌고, 거기에 글을 더해 UTF-8 로 저장하면 파일 전체가
    #   이중으로 인코딩되었다
    text = data.decode("utf-8", errors="replace")
    bad = text.count("\ufffd") - data.count(b"\xef\xbf\xbd")
    multi = sum(1 for ch in text if ch > "\x7f" and ch != "\ufffd")
    if bad <= max(3, len(data) // 4000) and multi >= 4 * bad:
        return text, "utf-8"
    try:
        return data.decode("cp949"), "cp949"
    except UnicodeDecodeError:
        pass
    return data.decode("latin-1"), "latin-1"


def detect_eol(text):
    """가장 많이 쓰인 줄 끝 (줄 바꿈이 없으면 기본값)"""
    crlf = text.count("\r\n")
    cr = text.count("\r") - crlf
    lf = text.count("\n") - crlf
    if not (crlf or cr or lf):
        return DEFAULT_EOL
    best = max((crlf, 2), (lf, 1), (cr, 0))       # 같으면 CRLF → LF → CR
    return {2: "crlf", 1: "lf", 0: "cr"}[best[1]]


def normalize(text):
    """편집기 안에서는 줄 끝을 모두 \\n 으로 (저장할 때 원래 것으로 되돌린다)"""
    if "\r" in text:
        text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text


def unencodable(text, key):
    """그 인코딩으로 저장할 수 없는 첫 글자 (없으면 None) — CP949·Latin-1 에 이모지 따위"""
    e = _BY_KEY.get(key)
    if e is None or e[2].startswith("utf"):
        return None
    try:
        text.encode(e[2])
    except UnicodeEncodeError as err:
        return text[err.start]
    return None


def encode(text, key, eol, errors="strict"):
    """편집기의 글(\\n 줄 끝) → 저장할 바이트. 못 쓰는 글자가 있으면 UnicodeEncodeError
    (errors="replace" 면 ? 로 — 사용자가 "그대로 저장"을 골랐을 때)"""
    _k, _n, codec, bom = _BY_KEY.get(key, _BY_KEY[DEFAULT_ENCODING])
    nl = _EOL_BY_KEY.get(eol, _EOL_BY_KEY[DEFAULT_EOL])[2]
    # 붙여넣기로 들어온 \r\n·\r 도 \n 으로 — 그대로 두면 CRLF 문서에서 \r\r\n 이 되었다
    text = normalize(text)
    if nl != "\n":
        text = text.replace("\n", nl)
    return bom + text.encode(codec, errors=errors)


def read_file(path, forced=None, allow_big=False, allow_binary=False, big_limit=20 * 1024 * 1024):
    """작업 스레드에서 — 파일을 읽어 알아낸다. 돌려주는 것:
        ("ok", (글, 인코딩, 줄 끝, (mtime 마이크로초, 크기)))   — 크기·시각은 바뀜 감지에 쓴다
        ("missing", None)       — 없는 파일 (그 이름으로 새 문서)
        ("big", 크기)            — 너무 크다 (묻고 allow_big=True 로 다시)
        ("binary", None)        — 글이 아닌 것 같다 (묻고 allow_binary=True 로 다시)
        ("error", OSError)
    """
    import os
    try:
        st = os.stat(path)
        if not allow_big and st.st_size > big_limit:
            return "big", st.st_size
        with open(path, "rb") as f:
            data = f.read()
    except FileNotFoundError:
        return "missing", None
    except OSError as e:
        return "error", e
    if not forced and not sniff(data) and not allow_binary and looks_binary(data):
        return "binary", None
    text, key = decode(data, forced)
    eol = detect_eol(text)
    text = normalize(text)
    if "\x00" in text:
        # GTK 의 글 칸은 NUL 을 담지 못한다 — 보이는 기호로 (그대로 저장하면 이 기호가 된다)
        text = text.replace("\x00", "␀")
    return "ok", (text, key, eol, (st.st_mtime_ns // 1000, st.st_size))
