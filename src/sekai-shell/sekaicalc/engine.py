"""계산기 — 계산 엔진 (GTK 없이 — 따로 시험하기 쉽게).

숫자는 모두 decimal.Decimal, 40자리로 계산한다 (0.1 + 0.2 = 0.3, 1 ÷ 3 × 3 = 1).
화면에는 유효 숫자 16자리까지, 넘으면 지수 표기(1.234e+20) — 윈도우 계산기와 같은 규칙.

한 엔진이 두 모드를 한다:
  표준    — 누르는 대로 바로 계산 (2 + 3 × 4 = 20). 모든 연산의 우선순위가 같다고 보면 된다.
  공학용  — 우선순위와 괄호 (2 + 3 × 4 = 14). 연산자를 누를 때 앞쪽에서 계산할 수 있는 만큼 줄인다
            (윈도우처럼: 2 + 3 × 까지면 3 을, 2 × 3 + 까지면 6 을 보인다).

식 한 줄(위의 작은 글)은 조각 목록(expr)과 "지금 피연산자"의 표기(operand_text: sqr(3), (2 + 3) …)로 만든다.
"""
import math
import random
from decimal import (ROUND_HALF_EVEN, ROUND_HALF_UP, Context, Decimal, DivisionByZero, InvalidOperation,
                     Overflow, localcontext)

PREC = 40                   # 계산 자릿수 (윈도우 계산기는 32)
SHOW = 16                   # 화면에 보일 유효 숫자
MAX_ZEROS = 2               # 소수점 뒤 0 이 이보다 많고 16자리를 넘으면 지수 표기 (0.00333… 까지는 그대로)
LIMIT_EXP = 9999            # 이보다 크면 "오버플로"
CTX = Context(prec=PREC, rounding=ROUND_HALF_EVEN, Emax=10 ** 7, Emin=-10 ** 7,
              traps=[InvalidOperation, DivisionByZero, Overflow])

ERR_DIV0 = "0으로 나눌 수 없습니다"
ERR_UNDEF = "결과가 정의되지 않았습니다"
ERR_INVALID = "잘못된 입력입니다"
ERR_OVERFLOW = "오버플로"

# 연산자 → (식에 보일 기호, 표준 우선순위, 공학용 우선순위)
BINARY = {
    "+": ("+", 1, 1),
    "-": ("−", 1, 1),
    "*": ("×", 1, 2),
    "/": ("÷", 1, 2),
    "mod": ("mod", 1, 2),
    "pow": ("^", 1, 3),
    "root": ("ʸ√", 1, 3),
    "logy": ("logᵧ", 1, 3),
}

# 각도 단위 — 식에서 삼각 함수 이름 뒤에 붙는 아래 첨자 (윈도우와 같은 표기)
ANGLES = ("deg", "rad", "grad")
ANGLE_NAMES = {"deg": "DEG", "rad": "RAD", "grad": "GRAD"}
_ANGLE_SUB = {"deg": "₀", "rad": "ᵣ", "grad": "₉"}
_FULL = {"deg": Decimal(360), "grad": Decimal(400)}

_PI = Decimal("3.14159265358979323846264338327950288419716939937510582097494459230781640628620899")


class CalcError(Exception):
    """계산할 수 없다 — 글은 화면에 그대로 보인다"""


# ── 숫자 → 글 ────────────────────────────────────────────────
def group_int(s):
    """정수 부분 세 자리마다 쉼표"""
    out = []
    while len(s) > 3:
        out.insert(0, s[-3:])
        s = s[:-3]
    out.insert(0, s)
    return ",".join(out)


def _round_sig(x, digits):
    with localcontext(CTX) as c:
        c.prec = digits
        c.rounding = ROUND_HALF_UP
        return +x


def fmt(x, sci=False, group=True):
    """결과 표시 — 16자리까지 고정 소수점, 넘으면 지수 표기. sci=True 면 늘 지수 표기(F-E)"""
    if x.is_zero():
        return "0e+0" if sci else "0"
    q = _round_sig(x, SHOW)
    sign = "-" if q < 0 else ""
    q = abs(q)
    adj = q.adjusted()                      # 가장 큰 자리의 지수 (123 → 2, 0.05 → -2)
    digits = "".join(map(str, q.normalize().as_tuple().digits))
    n = len(digits)
    if not sci and adj < 0:
        zeros = -adj - 1
        if zeros + n > SHOW:
            if zeros <= MAX_ZEROS:           # 앞자리 0 만큼 유효 숫자를 줄여 16자리에 맞춘다
                q = _round_sig(q, SHOW - zeros)
                digits = "".join(map(str, q.normalize().as_tuple().digits))
                adj = q.adjusted()
                n = len(digits)
            else:
                sci = True
    if not sci and adj >= SHOW:
        sci = True
    if sci:
        mant = digits[0] + ("." + digits[1:] if n > 1 else "")
        return f"{sign}{mant}e{'+' if adj >= 0 else '-'}{abs(adj)}"
    if adj >= 0:
        ip = digits[:adj + 1].ljust(adj + 1, "0")
        fp = digits[adj + 1:]
    else:
        ip = "0"
        fp = "0" * (-adj - 1) + digits
    ip = group_int(ip) if group else ip
    return sign + ip + ("." + fp if fp else "")


def fmt_entry(entry, group=True):
    """치는 중인 숫자 — 친 그대로(끝의 0·소수점까지) 쉼표만 넣는다"""
    sign = ""
    if entry.startswith("-"):
        sign, entry = "-", entry[1:]
    exp = ""
    if "e" in entry:
        entry, exp = entry.split("e", 1)
        exp = "e" + exp
    if "." in entry:
        ip, fp = entry.split(".", 1)
        body = (group_int(ip) if group else ip) + "." + fp
    else:
        body = group_int(entry) if group else entry
    return sign + body + exp


def parse_number(text):
    """붙여 넣은 글 → Decimal ("1,234.5" · " -3 " · "1.5e-3" · "1 234"). 숫자가 아니면 None"""
    s = (text or "").strip().replace(",", "").replace(" ", "").replace(" ", "").replace("−", "-")
    if not s or len(s) > 100:
        return None
    try:
        d = Decimal(s)
    except (InvalidOperation, ValueError):
        return None
    if not d.is_finite():
        return None
    return d


# ── 계산 ─────────────────────────────────────────────────────
def _check(x):
    """너무 크면 오버플로, 너무 작으면 0"""
    if x.is_zero():
        return Decimal(0)
    adj = x.adjusted()
    if adj > LIMIT_EXP:
        raise CalcError(ERR_OVERFLOW)
    if adj < -LIMIT_EXP:
        return Decimal(0)
    return x


check_range = _check


def _clean(x):
    """초월 함수 결과를 조금 다듬는다 — 35자리로 반올림해 3.0000…01 · 0.4999…9 같은 꼬리를 없애고,
    정수에 아주 가까우면 정수로 (∛27 = 3, sin 30° = 0.5)"""
    with localcontext(CTX) as c:
        c.prec = PREC - 5
        x = +x
        r = x.to_integral_value()
        if r != x and abs(x - r) < Decimal(10) ** (-(PREC - 8)) * max(Decimal(1), abs(r)):
            x = r
    return x


def binary(op, a, b):
    with localcontext(CTX):
        try:
            if op == "+":
                r = a + b
            elif op == "-":
                r = a - b
            elif op == "*":
                r = a * b
            elif op == "/":
                if b.is_zero():
                    raise CalcError(ERR_UNDEF if a.is_zero() else ERR_DIV0)
                r = a / b
            elif op == "mod":
                if b.is_zero():
                    raise CalcError(ERR_UNDEF if a.is_zero() else ERR_DIV0)
                r = a % b
                if not r.is_zero() and (r < 0) != (b < 0):      # 나누는 수의 부호를 따른다 (엑셀·윈도우처럼)
                    r += b
            elif op == "pow":
                r = _pow(a, b)
            elif op == "root":
                r = _root(a, b)
            elif op == "logy":
                if a <= 0 or b <= 0 or b == 1:
                    raise CalcError(ERR_INVALID)
                r = _clean(a.ln() / b.ln())
            else:
                raise CalcError(ERR_INVALID)
        except (InvalidOperation, ValueError):
            raise CalcError(ERR_INVALID)
        except DivisionByZero:
            raise CalcError(ERR_DIV0)
        except Overflow:
            raise CalcError(ERR_OVERFLOW)
        return _check(r)


def _pow(a, b):
    if a.is_zero():
        if b < 0:
            raise CalcError(ERR_DIV0)
        return Decimal(1) if b.is_zero() else Decimal(0)
    if b == b.to_integral_value():
        if abs(b) > 10 ** 6:
            # 지수가 아주 크면 크기만 먼저 본다 (정확한 거듭제곱은 너무 오래 걸린다)
            if abs(a) != 1 and (abs(a).ln() * b) / Decimal(10).ln() > LIMIT_EXP:
                raise CalcError(ERR_OVERFLOW)
        return a ** int(b)
    if a < 0:
        raise CalcError(ERR_INVALID)
    return _clean(a ** b)


def _root(a, n):
    """a 의 n 제곱근 — 음수는 홀수 제곱근만"""
    if n.is_zero():
        raise CalcError(ERR_INVALID)
    if a < 0:
        if n == n.to_integral_value() and int(n) % 2 == 1:
            return -_root(-a, n)
        raise CalcError(ERR_INVALID)
    if a.is_zero():
        if n < 0:
            raise CalcError(ERR_DIV0)
        return Decimal(0)
    with localcontext(CTX) as c:
        c.prec = PREC + 10
        r = _clean(a ** (Decimal(1) / n))
    # 정확한 제곱근이면 정수로 (∛27 = 3)
    if n == n.to_integral_value() and r == r.to_integral_value() and 0 < int(n) < 1000 and r ** int(n) == a:
        return r
    return r


def _sin_series(x, cos=False):
    """라디안 x 의 sin (cos=True 면 cos) — [-π, π] 로 줄여 테일러 급수"""
    with localcontext(CTX) as c:
        c.prec = PREC + 12
        two_pi = 2 * _PI
        x = x % two_pi
        if x > _PI:
            x -= two_pi
        elif x < -_PI:
            x += two_pi
        eps = Decimal(10) ** (-(PREC + 10))
        x2 = x * x
        if cos:
            term, s, n = Decimal(1), Decimal(1), 0
        else:
            term, s, n = x, x, 1
        while True:
            term = -term * x2 / ((n + 1) * (n + 2))
            n += 2
            if abs(term) < eps:
                break
            s += term
    return s


def _atan(x):
    with localcontext(CTX) as c:
        c.prec = PREC + 12
        if x.is_zero():
            return Decimal(0)
        neg = x < 0
        x = abs(x)
        inv = x > 1
        if inv:
            x = 1 / x
        k = 0
        while x > Decimal("0.1"):               # atan(x) = 2·atan(x / (1 + √(1 + x²)))
            x = x / (1 + (1 + x * x).sqrt())
            k += 1
        eps = Decimal(10) ** (-(PREC + 10))
        s, term, x2, n = x, x, x * x, 1
        while True:
            term = -term * x2
            t = term / (2 * n + 1)
            if abs(t) < eps:
                break
            s += t
            n += 1
        s *= 2 ** k
        if inv:
            s = _PI / 2 - s
        return -s if neg else s


def _to_rad(x, angle):
    if angle == "deg":
        return x * _PI / 180
    if angle == "grad":
        return x * _PI / 200
    return x


def _from_rad(r, angle):
    if angle == "deg":
        return r * 180 / _PI
    if angle == "grad":
        return r * 200 / _PI
    return r


def _trig(name, x, angle):
    """sin · cos · tan — 도·그레이드는 정확한 값(0·1·-1·없음)을 먼저 본다 (sin 180° = 0, tan 90° 는 잘못된 입력)"""
    full = _FULL.get(angle)
    if full is not None:
        r = x % full
        if r < 0:
            r += full
        half, quarter = full / 2, full / 4
        if name == "sin":
            if r % half == 0:
                return Decimal(0)
            if r == quarter:
                return Decimal(1)
            if r == 3 * quarter:
                return Decimal(-1)
        elif name == "cos":
            if r == quarter or r == 3 * quarter:
                return Decimal(0)
            if r == 0:
                return Decimal(1)
            if r == half:
                return Decimal(-1)
        else:
            if r % half == 0:
                return Decimal(0)
            if r % half == quarter:
                raise CalcError(ERR_INVALID)
        x = r
    rad = _to_rad(x, angle)
    tiny = Decimal(10) ** (-(PREC - 5))
    s = _sin_series(rad)
    c = _sin_series(rad, cos=True)
    if abs(s) < tiny:
        s = Decimal(0)
    if abs(c) < tiny:
        c = Decimal(0)
    if name == "sin":
        return _clean(s)
    if name == "cos":
        return _clean(c)
    if c.is_zero():
        raise CalcError(ERR_INVALID)
    with localcontext(CTX):
        return _clean(s / c)


def _inv_trig(name, x, angle):
    with localcontext(CTX) as c:
        c.prec = PREC + 12
        if name == "atan":
            r = _atan(x)
        else:
            if abs(x) > 1:
                raise CalcError(ERR_INVALID)
            if abs(x) == 1:
                r = _PI / 2 * (1 if x > 0 else -1)
            else:
                r = _atan(x / (1 - x * x).sqrt())
            if name == "acos":
                r = _PI / 2 - r
        return _clean(_from_rad(r, angle))


def _factorial(x):
    if x == x.to_integral_value():
        if x < 0:
            raise CalcError(ERR_INVALID)
        if x > 3248:                           # 3249! 는 10^10000 을 넘는다
            raise CalcError(ERR_OVERFLOW)
        return Decimal(math.factorial(int(x)))
    # 정수가 아니면 감마 함수 Γ(x + 1) — 부동소수점(약 15자리)으로 (윈도우도 0.5! = 0.886… 을 준다)
    try:
        return _clean(Decimal(repr(math.gamma(float(x) + 1.0))))
    except (OverflowError, ValueError):
        raise CalcError(ERR_OVERFLOW if x > 0 else ERR_INVALID)


def unary(name, x, angle="deg"):
    """단항 함수 — 결과 Decimal (못 하면 CalcError)"""
    with localcontext(CTX):
        try:
            if name == "sqr":
                r = x * x
            elif name == "cube":
                r = x * x * x
            elif name == "sqrt":
                if x < 0:
                    raise CalcError(ERR_INVALID)
                r = x.sqrt()
            elif name == "cbrt":
                r = _root(x, Decimal(3))
            elif name == "recip":
                if x.is_zero():
                    raise CalcError(ERR_DIV0)
                r = 1 / x
            elif name == "negate":
                r = -x
            elif name == "abs":
                r = abs(x)
            elif name == "fact":
                r = _factorial(x)
            elif name == "pow10":
                r = _pow(Decimal(10), x)
            elif name == "pow2":
                r = _pow(Decimal(2), x)
            elif name == "exp":
                r = _clean(x.exp())
            elif name == "log":
                if x <= 0:
                    raise CalcError(ERR_INVALID)
                r = x.log10()
            elif name == "ln":
                if x <= 0:
                    raise CalcError(ERR_INVALID)
                r = x.ln()
            elif name in ("sin", "cos", "tan"):
                r = _trig(name, x, angle)
            elif name in ("asin", "acos", "atan"):
                r = _inv_trig(name, x, angle)
            elif name == "floor":
                r = x.to_integral_value(rounding="ROUND_FLOOR")
            elif name == "ceil":
                r = x.to_integral_value(rounding="ROUND_CEILING")
            else:
                raise CalcError(ERR_INVALID)
        except (InvalidOperation, ValueError):
            raise CalcError(ERR_INVALID)
        except DivisionByZero:
            raise CalcError(ERR_DIV0)
        except Overflow:
            raise CalcError(ERR_OVERFLOW)
        return _check(+r)


def unary_text(name, inner, angle="deg"):
    """식에 보일 단항 함수 표기 — 윈도우 계산기와 같은 모양"""
    sub = _ANGLE_SUB.get(angle, "")
    forms = {
        "sqr": "sqr({})", "cube": "cube({})", "sqrt": "√({})", "cbrt": "cuberoot({})", "recip": "1/({})",
        "negate": "negate({})", "abs": "abs({})", "fact": "fact({})", "pow10": "10^({})", "pow2": "2^({})",
        "exp": "e^({})", "log": "log({})", "ln": "ln({})", "floor": "floor({})", "ceil": "ceil({})",
        "sin": "sin" + sub + "({})", "cos": "cos" + sub + "({})", "tan": "tan" + sub + "({})",
        "asin": "sin" + sub + "⁻¹({})", "acos": "cos" + sub + "⁻¹({})", "atan": "tan" + sub + "⁻¹({})",
    }
    return forms.get(name, name + "({})").format(inner)


with localcontext(CTX):
    PI = +_PI                               # 40자리로 반올림
    E = +Decimal(1).exp()


# ── 계산기 상태 ──────────────────────────────────────────────
class Engine:
    """한 모드의 계산 상태. UI 는 누른 것을 부르고 display_text() · expression_text() 를 보인다."""

    def __init__(self, scientific=False):
        self.scientific = scientific
        self.angle = "deg"
        self.fe = False                     # F-E — 늘 지수 표기
        self.clear()

    # ── 상태 ──
    def clear(self):
        self.entry = None                   # 치는 중인 숫자 글 ("12.30", "1.5e+3") — None 이면 결과를 보이는 중
        self.value = Decimal(0)             # 보이는 값
        self.operand_text = None            # 지금 피연산자의 식 표기 (sqr(3) · (2 + 3) · π)
        self.fresh = False                  # 마지막 연산자·괄호 뒤에 피연산자가 들어왔다
        self.vals = []                      # 피연산자 쌓기
        self.ops = []                       # 연산자·'(' 쌓기
        self.expr = []                      # 식 조각 (마친 피연산자와 연산자)
        self.groups = []                    # 열린 괄호마다 (그때의 expr 길이)
        self.last = None                    # 되풀이 = 를 위한 (연산자, 오른쪽 값)
        self.done = False                   # 방금 = 을 눌렀다 (위 줄에 식 = 이 보인다)
        self.done_text = ""
        self.error = None

    @property
    def open_parens(self):
        return len(self.groups)

    def _prec(self, op):
        return BINARY[op][2] if self.scientific else BINARY[op][1]

    def _max_digits(self):
        return 32 if self.scientific else SHOW

    def _new_calc_if_done(self):
        if self.done:
            ang, fe = self.angle, self.fe
            self.clear()
            self.angle, self.fe = ang, fe

    def _fail(self, msg, text=None):
        self.error = msg
        if text is not None:
            self.done_text = text
            self.done = True
        self.entry = None

    # ── 보이기 ──
    def display_text(self, group=True):
        if self.error:
            return self.error
        if self.entry is not None:
            return fmt_entry(self.entry, group)
        return fmt(self.value, sci=self.fe, group=group)

    def expression_text(self):
        if self.done or (self.error and self.done_text):
            return self.done_text
        parts = list(self.expr)
        if self.operand_text and self.fresh:
            parts.append(self.operand_text)
        return " ".join(parts)

    def current_value(self):
        """메모리·복사에 쓸 값 (오류면 None)"""
        return None if self.error else self.value

    def _operand_repr(self):
        return self.operand_text if self.operand_text else fmt(self.value, sci=self.fe)

    # ── 숫자 치기 ──
    def digit(self, d):
        if self.error:
            self.clear()
        self._new_calc_if_done()
        if self.entry is None:
            self.entry = "0"
            self.operand_text = None
            self.fresh = True
        e = self.entry
        mant, _sep, exp = e.partition("e")
        if _sep:                                          # 지수 치는 중 (exp 단추 뒤)
            sign, digits = exp[0], exp[1:]
            digits = (digits.lstrip("0") + d) if digits != "0" else d
            if len(digits) > 4:
                return
            self.entry = f"{mant}e{sign}{digits or '0'}"
        else:
            # 친 자릿수 (맨 앞의 0 하나는 세지 않는다 — 0.0001 은 4자리)
            body = mant.replace("-", "").replace(".", "")
            count = len(body) - (1 if body.startswith("0") else 0)
            if count >= self._max_digits():
                return
            if mant in ("0", "-0"):
                self.entry = mant[:-1] + d
            else:
                self.entry = mant + d
        self._sync_entry()

    def point(self):
        if self.error:
            self.clear()
        self._new_calc_if_done()
        if self.entry is None:
            self.entry = "0."
            self.operand_text = None
            self.fresh = True
        elif "." not in self.entry and "e" not in self.entry:
            self.entry += "."
        self._sync_entry()

    def exp_entry(self):
        """exp — 지수를 치기 시작한다 (1.5 exp 3 = 1,500)"""
        if self.error:
            return
        self._new_calc_if_done()
        if self.entry is None:
            s = fmt(self.value, group=False)
            self.entry = s if ("e" not in s and len(s) <= 18) else "0"
            self.operand_text = None
            self.fresh = True
        if "e" not in self.entry:
            self.entry = self.entry.rstrip(".") + "e+0"
        self._sync_entry()

    def _sync_entry(self):
        try:
            self.value = Decimal(self.entry)
        except InvalidOperation:
            self.value = Decimal(0)

    def backspace(self):
        if self.error:
            self.clear()
            return
        if self.entry is None:
            if self.done:                          # 결과에서 ⌫ — 위 줄의 식만 지운다 (윈도우처럼)
                self.done_text = ""
            return
        e = self.entry
        if "e" in e:
            mant, exp = e.split("e", 1)
            sign, digits = exp[0], exp[1:]
            if digits in ("", "0"):
                self.entry = mant                  # 지수를 다 지우면 지수 표기를 뺀다
            else:
                digits = digits[:-1] or "0"
                self.entry = f"{mant}e{sign}{digits}"
        else:
            e = e[:-1]
            if e in ("", "-", "-0"):
                e = "0"
            self.entry = e
        self._sync_entry()

    # ── 지우기 ──
    def clear_entry(self):
        """CE — 지금 숫자만 0 으로 (= 뒤·오류면 모두)"""
        if self.error or self.done:
            ang, fe = self.angle, self.fe
            self.clear()
            self.angle, self.fe = ang, fe
            return
        self.entry = "0"
        self.value = Decimal(0)
        self.operand_text = None
        self.fresh = True

    def reset(self):
        """C"""
        ang, fe = self.angle, self.fe
        self.clear()
        self.angle, self.fe = ang, fe

    # ── 값 넣기 (MR · π · 기록 · 붙여넣기) ──
    def set_value(self, x, text=None):
        if self.error:
            self.reset()
        self._new_calc_if_done()
        self.entry = None
        self.value = x
        self.operand_text = text
        self.fresh = True

    def restore(self, expr_text, x):
        """기록을 눌렀다 — 그 식과 결과를 보인다 (이어서 계산할 수 있다)"""
        self.reset()
        self.value = x
        self.done = True
        self.done_text = expr_text

    # ── 단항 ──
    def negate(self):
        if self.error:
            return
        if self.entry is not None:                 # 치는 중 — 부호만 (식에는 안 보인다)
            e = self.entry
            if "e" in e:                           # 지수를 치는 중이면 지수의 부호 (윈도우처럼)
                mant, exp = e.split("e", 1)
                self.entry = mant + "e" + ("-" if exp[0] == "+" else "+") + exp[1:]
            elif e in ("0", "0."):
                return
            else:
                self.entry = e[1:] if e.startswith("-") else "-" + e
            self._sync_entry()
            return
        self.apply_unary("negate")

    def apply_unary(self, name):
        if self.error:
            return
        inner = self._operand_repr()
        base = self.value
        self._new_calc_if_done()
        text = unary_text(name, inner, self.angle)
        try:
            r = unary(name, base, self.angle)
        except CalcError as e:
            self.operand_text = text
            self.fresh = True
            self._fail(str(e), " ".join(self.expr + [text]))
            return
        self.value = r
        self.entry = None
        self.operand_text = text
        self.fresh = True

    def percent(self):
        """% — 윈도우 표준 계산기: + − 뒤면 a × b / 100, × ÷ 뒤면 b / 100, 연산자가 없으면 0"""
        if self.error:
            return
        self._new_calc_if_done()
        op = self.ops[-1] if self.ops and self.ops[-1] != "(" else None
        with localcontext(CTX):
            if op in ("*", "/"):
                r = self.value / 100
            elif op in ("+", "-"):
                r = self.vals[-1] * self.value / 100
            else:
                r = Decimal(0)
        self.value = _check(r)
        self.entry = None
        self.operand_text = fmt(self.value)
        self.fresh = True

    # ── 이항 ──
    def _reduce_once(self):
        op = self.ops.pop()
        b = self.vals.pop()
        a = self.vals.pop()
        self.vals.append(binary(op, a, b))
        return op, b

    def binary(self, op):
        if self.error:
            return
        if self.done:
            v = self.value
            self.reset()
            self.vals, self.ops = [v], [op]
            self.expr = [fmt(v, sci=self.fe), BINARY[op][0]]
            self.value = v
            return
        if not self.fresh and self.ops and self.ops[-1] != "(":
            # 연산자를 바꿔 눌렀다 (2 + × → 2 ×)
            self.ops[-1] = op
            self.expr[-1] = BINARY[op][0]
            return
        self.vals.append(self.value)
        self.expr.append(self._operand_repr())
        try:
            while self.ops and self.ops[-1] != "(" and self._prec(self.ops[-1]) >= self._prec(op):
                self._reduce_once()
        except CalcError as e:
            self._fail(str(e), " ".join(self.expr))
            return
        self.ops.append(op)
        self.expr.append(BINARY[op][0])
        self.value = self.vals[-1]
        self.entry = None
        self.operand_text = None
        self.fresh = False

    # ── 괄호 (공학용) ──
    def open_paren(self):
        if self.error:
            return
        self._new_calc_if_done()
        if self.fresh:                             # 2 ( → 2 × ( (계산기에서 흔한 생략된 곱하기)
            self.binary("*")
            if self.error:
                return
        self.ops.append("(")
        self.groups.append(len(self.expr))
        self.expr.append("(")
        self.entry = None
        self.operand_text = None
        self.fresh = False

    def close_paren(self):
        if self.error or not self.groups:
            return
        self.vals.append(self.value)
        self.expr.append(self._operand_repr())
        try:
            while self.ops and self.ops[-1] != "(":
                self._reduce_once()
        except CalcError as e:
            self._fail(str(e), " ".join(self.expr + [")"]))
            return
        self.ops.pop()                             # '('
        start = self.groups.pop()
        inner = " ".join(self.expr[start + 1:])
        del self.expr[start:]
        self.value = self.vals.pop()
        self.operand_text = f"({inner})"
        self.entry = None
        self.fresh = True

    # ── = ──
    def equals(self):
        """계산을 마친다 — 기록에 남길 (식, 결과) 를 돌려준다 (남길 것이 없으면 None)"""
        if self.error:
            return None
        if self.done:
            if not self.last:
                return None
            op, b = self.last                      # = 을 거듭 — 마지막 연산을 되풀이 (5 + 3 = = → 8, 11)
            a = self.value
            text = f"{fmt(a, sci=self.fe)} {BINARY[op][0]} {fmt(b, sci=self.fe)} ="
            try:
                r = binary(op, a, b)
            except CalcError as e:
                self._fail(str(e), text)
                return None
            self.value, self.done_text = r, text
            return text, r
        if not self.ops and not self.operand_text:
            # 숫자 하나에 = — 식에 "5 =" 만 보인다
            self.done_text = f"{fmt(self.value, sci=self.fe)} ="
            self.entry = None
            self.done = True
            return None
        self.vals.append(self.value)
        self.expr.append(self._operand_repr())
        closing = len(self.groups)
        text = " ".join(self.expr + [")"] * closing + ["="])
        simple = len(self.ops) == 1 and not self.groups
        last = None
        try:
            while self.ops:
                if self.ops[-1] == "(":
                    self.ops.pop()
                    continue
                last = self._reduce_once()
        except CalcError as e:
            self._fail(str(e), text)
            return None
        r = self.vals[-1]
        ang, fe = self.angle, self.fe
        self.clear()
        self.angle, self.fe = ang, fe
        self.value = r
        self.done = True
        self.done_text = text
        self.last = last if simple else None
        return text, r

    # ── 기타 ──
    def constant(self, name):
        if name == "pi":
            self.set_value(PI, "π")
        elif name == "e":
            self.set_value(E, "e")

    def random(self):
        with localcontext(CTX):
            self.set_value(Decimal(repr(random.random())))

    def cycle_angle(self):
        self.angle = ANGLES[(ANGLES.index(self.angle) + 1) % len(ANGLES)]
        return self.angle

    def copy_text(self):
        """Ctrl+C — 쉼표 없는 결과"""
        if self.error:
            return None
        if self.entry is not None:
            return self.entry.rstrip(".") if self.entry.endswith(".") else self.entry
        return fmt(self.value, sci=self.fe, group=False)
