"""greetd IPC 클라이언트.

greetd 는 로그인 화면(그리터)에게 UNIX 소켓($GREETD_SOCK)을 준다.
메시지는 [4바이트 길이(시스템 바이트 순서)][JSON] 형식.

  create_session {username}             → 인증 대화 시작
  post_auth_message_response {response} → PAM 질문(비밀번호 등)에 답
  start_session {cmd, env}              → 인증 끝, 이 명령으로 세션을 연다
  cancel_session                        → 취소
응답: success / error {error_type, description} / auth_message {auth_message_type, auth_message}
"""
import json
import os
import socket
import struct


class GreetdError(Exception):
    pass


class Greetd:
    def __init__(self, path=None):
        path = path or os.environ.get("GREETD_SOCK")
        if not path:
            raise GreetdError("GREETD_SOCK 이 없습니다 (greetd 밖에서 실행됨)")
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(path)

    def _recv_exact(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise GreetdError("greetd 연결이 끊겼습니다")
            buf += chunk
        return buf

    def request(self, payload):
        data = json.dumps(payload).encode()
        self.sock.sendall(struct.pack("=I", len(data)) + data)
        n = struct.unpack("=I", self._recv_exact(4))[0]
        return json.loads(self._recv_exact(n))

    def create_session(self, user):
        return self.request({"type": "create_session", "username": user})

    def respond(self, text):
        return self.request({"type": "post_auth_message_response", "response": text})

    def start_session(self, cmd, env=None):
        return self.request({"type": "start_session", "cmd": cmd, "env": env or []})

    def cancel(self):
        try:
            return self.request({"type": "cancel_session"})
        except Exception:
            return None

    def login(self, user, password, cmd, env=None):
        """한 번에 로그인. 성공하면 None, 실패하면 사용자에게 보여줄 문장."""
        r = self.create_session(user)
        answered = False
        while r.get("type") == "auth_message":
            kind = r.get("auth_message_type")
            if kind in ("secret", "visible"):
                if answered:                       # 비밀번호를 두 번 묻는다 = 틀림
                    self.cancel()
                    return "비밀번호가 올바르지 않습니다."
                r = self.respond(password)
                answered = True
            else:                                  # info / error 는 그냥 넘긴다
                r = self.respond(None)
        if r.get("type") == "error":
            self.cancel()
            if r.get("error_type") == "auth_error":
                return "비밀번호가 올바르지 않습니다."
            return r.get("description") or "로그인하지 못했습니다."
        r = self.start_session(cmd, env)
        if r.get("type") != "success":
            self.cancel()
            return (r.get("description") or "세션을 시작하지 못했습니다.")
        return None
