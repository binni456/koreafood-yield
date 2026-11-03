#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""코리아식품 수율 일지 — 사내 서버

회사 PC 한 대에서 실행하면 같은 와이파이의 태블릿·PC가 브라우저로 접속합니다.
파이썬 3.8 이상만 있으면 되고, 다른 프로그램은 설치하지 않아도 됩니다.
데이터는 data/yield.db (SQLite)에 저장되고, 하루 한 번 backup 폴더로 자동 백업됩니다.
"""
import hashlib
import json
import os
import queue
import re
import secrets
import socket
import sqlite3
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

try:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = os.path.dirname(os.path.abspath(__file__))
CFG = {
    "port": 8080,
    "backup_dir": "backup",
    "backup_keep_days": 30,
    "session_hours": 12,
    "allow_self_register": True,
}
_cfg_path = os.path.join(BASE, "config.json")
if os.path.exists(_cfg_path):
    with open(_cfg_path, encoding="utf-8") as f:
        CFG.update(json.load(f))

DATA_DIR = os.path.join(BASE, "data")
PUBLIC_DIR = os.path.join(BASE, "public")
BACKUP_DIR = CFG["backup_dir"] if os.path.isabs(CFG["backup_dir"]) else os.path.join(BASE, CFG["backup_dir"])
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "yield.db")

COLLS = {"lots", "packs", "products", "raws", "staff", "config", "admin", "logs"}
MANAGER_COLLS = {"products", "config", "admin", "staff"}
SECRET = ("salt", "pinHash")
SEG = re.compile(r"^[A-Za-z0-9_\-.~:@+]{1,200}$")
MAX_BODY = 2 * 1024 * 1024

LOCK = threading.RLock()
DB = sqlite3.connect(DB_PATH, check_same_thread=False)
DB.execute("PRAGMA journal_mode=WAL")
DB.execute("CREATE TABLE IF NOT EXISTS docs(path TEXT PRIMARY KEY, coll TEXT, data TEXT NOT NULL, ver INTEGER NOT NULL, updated TEXT)")
DB.execute("CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, staff_id TEXT NOT NULL, created REAL, last REAL)")
DB.commit()
VER = DB.execute("SELECT COALESCE(MAX(ver),0) FROM docs").fetchone()[0]

SUBSCRIBERS = []  # [queue, role]
LOGIN_FAILS = {}  # staff_id -> [count, lock_until]


# ───────── 저장소 ─────────
def now_local():
    return datetime.now().isoformat(timespec="seconds")


def now_utc_iso():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def valid_path(p):
    s = p.split("/")
    return len(s) == 2 and s[0] in COLLS and all(SEG.match(x) and x not in (".", "..") for x in s)


def get_doc(path):
    with LOCK:
        row = DB.execute("SELECT data FROM docs WHERE path=?", (path,)).fetchone()
    return json.loads(row[0]) if row else None


def all_docs():
    with LOCK:
        rows = DB.execute("SELECT path, data FROM docs").fetchall()
    return [(p, json.loads(d)) for p, d in rows]


def public_staff(d):
    x = {k: v for k, v in d.items() if k not in SECRET}
    x["hasPin"] = bool(d.get("pinHash"))
    return x


def view_for(path, d, role):
    """역할에 맞게 보여 줄 문서. None = 숨김."""
    coll = path.split("/")[0]
    if coll == "admin" and role != "manager":
        return None
    if coll == "staff":
        return public_staff(d)
    return d


def broadcast(path, d):
    for q, role in list(SUBSCRIBERS):
        if d is None:
            if path.startswith("admin/") and role != "manager":
                continue
            q.put({"type": "change", "path": path, "data": None})
            continue
        v = view_for(path, d, role)
        if v is not None:
            q.put({"type": "change", "path": path, "data": v})


def put_doc(path, d):
    global VER
    with LOCK:
        VER += 1
        DB.execute(
            "INSERT INTO docs(path,coll,data,ver,updated) VALUES(?,?,?,?,?) "
            "ON CONFLICT(path) DO UPDATE SET data=excluded.data, ver=excluded.ver, updated=excluded.updated",
            (path, path.split("/")[0], json.dumps(d, ensure_ascii=False), VER, now_local()),
        )
        DB.commit()
    broadcast(path, d)


def delete_doc(path):
    global VER
    with LOCK:
        VER += 1
        DB.execute("DELETE FROM docs WHERE path=?", (path,))
        DB.commit()
    broadcast(path, None)


def server_log(act, detail="", who="서버", ref=""):
    date = datetime.now().strftime("%Y-%m-%d")
    path = "logs/%s_server" % date
    with LOCK:
        doc = get_doc(path) or {"date": date, "dev": "server", "entries": []}
        doc["entries"].append({"t": now_utc_iso(), "who": who or "미지정", "act": act, "d": detail, "ref": ref})
        put_doc(path, doc)


# ───────── 직원 · 로그인 ─────────
def norm_name(n):
    return re.sub(r"\s+", " ", str(n or "")).strip()


def name_key(n):
    return re.sub(r"\s", "", norm_name(n))


ID_RE = re.compile(r"^[A-Za-z0-9._-]{3,20}$")


def norm_id(s):
    return str(s or "").strip().lower()


def pin_hash(salt, pin):  # 예전 형식(확인용)
    return hashlib.sha256((salt + ":" + pin).encode("utf-8")).hexdigest()


def pw_hash(salt, pw):
    return hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), salt.encode("utf-8"), 100000).hex()


def pw_check(d, pw):
    if not d or not d.get("pinHash"):
        return False
    h = pw_hash(d.get("salt", ""), pw) if d.get("algo") == "pbkdf2" else pin_hash(d.get("salt", ""), pw)
    return secrets.compare_digest(h, d["pinHash"])


def set_pw(d, pw):
    d["salt"] = secrets.token_hex(8)
    d["pinHash"] = pw_hash(d["salt"], pw)
    d["algo"] = "pbkdf2"


def find_staff_by_login(lid):
    k = norm_id(lid)
    for sid, d in staff_docs():
        if norm_id(d.get("loginId")) == k:
            return sid, d
    return None, None


def staff_docs():
    return [(p.split("/")[1], d) for p, d in all_docs() if p.startswith("staff/") and not d.get("deleted")]


def find_staff_by_name(name):
    k = name_key(name)
    for sid, d in staff_docs():
        if name_key(d.get("name")) == k:
            return sid, d
    return None, None


def new_session(staff_id):
    token = secrets.token_urlsafe(24)
    t = time.time()
    with LOCK:
        DB.execute("INSERT INTO sessions(token,staff_id,created,last) VALUES(?,?,?,?)", (token, staff_id, t, t))
        DB.commit()
    return token


def get_session(token):
    """토큰 → (staff_id, staff_doc, role) 또는 None."""
    if not token:
        return None
    with LOCK:
        row = DB.execute("SELECT staff_id, last FROM sessions WHERE token=?", (token,)).fetchone()
    if not row:
        return None
    sid, last = row
    if time.time() - last > CFG["session_hours"] * 3600:
        end_session(token)
        return None
    d = get_doc("staff/" + sid)
    if not d or d.get("deleted") or not d.get("pinHash"):
        return None
    if time.time() - last > 60:
        with LOCK:
            DB.execute("UPDATE sessions SET last=? WHERE token=?", (time.time(), token))
            DB.commit()
    return sid, d, d.get("role") or "worker"


def end_session(token):
    with LOCK:
        DB.execute("DELETE FROM sessions WHERE token=?", (token,))
        DB.commit()


def ensure_login_config():
    cfg = get_doc("config/main") or {}
    if not cfg.get("loginRequired"):
        cfg["loginRequired"] = True
        put_doc("config/main", cfg)


# ───────── 처음 실행 ─────────
def seed():
    if all_docs():
        return
    path = os.path.join(BASE, "seed.json")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    for p, d in data.items():
        if valid_path(p):
            put_doc(p, d)
    print("  처음 실행: 기본 설정과 제품 기준을 넣었습니다.")


# ───────── 백업 ─────────
def backup_once():
    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    dst = os.path.join(BACKUP_DIR, "yield-%s.db" % stamp)
    b = sqlite3.connect(dst)
    with LOCK:
        DB.backup(b)
    b.close()
    docs = {p: (public_staff(d) if p.startswith("staff/") else d) for p, d in all_docs()}
    with open(os.path.join(BACKUP_DIR, "yield-%s.json" % stamp), "w", encoding="utf-8") as f:
        json.dump({"exportedAt": now_local(), "docs": docs}, f, ensure_ascii=False, indent=1)
    cutoff = time.time() - CFG["backup_keep_days"] * 86400
    for name in os.listdir(BACKUP_DIR):
        fp = os.path.join(BACKUP_DIR, name)
        if name.startswith("yield-") and os.path.getmtime(fp) < cutoff:
            try:
                os.remove(fp)
            except OSError:
                pass
    return dst


def backup_loop():
    while True:
        try:
            files = [f for f in os.listdir(BACKUP_DIR) if f.endswith(".db")] if os.path.isdir(BACKUP_DIR) else []
            newest = max((os.path.getmtime(os.path.join(BACKUP_DIR, f)) for f in files), default=0)
            if time.time() - newest > 20 * 3600:
                dst = backup_once()
                print("  [%s] 백업했습니다: %s" % (datetime.now().strftime("%m-%d %H:%M"), dst))
        except Exception as e:  # 백업 실패가 서버를 멈추지 않게
            print("  백업하지 못했습니다:", e)
        time.sleep(3600)


# ───────── HTTP ─────────
TYPES = {".html": "text/html; charset=utf-8", ".js": "application/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
         ".json": "application/json; charset=utf-8", ".png": "image/png", ".svg": "image/svg+xml", ".ico": "image/x-icon"}


class Handler(BaseHTTPRequestHandler):
    server_version = "YieldServer/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # 접속 기록은 화면에 찍지 않음
        pass

    # 응답 도우미
    def send_json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def err(self, code, msg, kind=None):
        self.send_json(code, {"error": msg, "code": kind or ("invalid_argument" if code == 403 else "error")})

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY:
            raise ValueError("too large")
        raw = self.rfile.read(n) if n else b"{}"
        obj = json.loads(raw.decode("utf-8") or "{}")
        if not isinstance(obj, dict):
            raise ValueError("object expected")
        return obj

    def token(self):
        h = self.headers.get("Authorization") or ""
        if h.startswith("Bearer "):
            return h[7:].strip()
        q = parse_qs(urlparse(self.path).query)
        return (q.get("token") or [""])[0]

    def session(self):
        return get_session(self.token())

    # 라우팅
    def do_GET(self):
        u = urlparse(self.path)
        p = u.path
        try:
            if p == "/api/public":
                return self.send_json(200, {"config": get_doc("config/main") or {},
                                            "setupNeeded": not any(d.get("pinHash") for _, d in staff_docs()),
                                            "allowRegister": bool(CFG.get("allow_self_register", True))})
            if p == "/api/all":
                s = self.session()
                if not s:
                    return self.err(401, "로그인이 필요합니다", "unauthenticated")
                out = {}
                for path, d in all_docs():
                    v = view_for(path, d, s[2])
                    if v is not None:
                        out[path] = v
                return self.send_json(200, {"docs": out, "ver": VER})
            if p == "/api/events":
                s = self.session()
                if not s:
                    return self.err(401, "로그인이 필요합니다", "unauthenticated")
                return self.events(s[2])
            return self.static(p)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            print("  오류:", e)
            try:
                self.err(500, "서버 오류가 났습니다")
            except Exception:
                pass

    def do_POST(self):
        p = urlparse(self.path).path
        try:
            b = self.body()
        except Exception:
            self.close_connection = True
            return self.err(400, "요청을 읽지 못했습니다")
        try:
            if p == "/api/login":
                return self.login(b)
            if p == "/api/logout":
                end_session(self.token())
                return self.send_json(200, {"ok": True})
            if p == "/api/register":
                return self.register(b)
            if p == "/api/setup":
                return self.api_setup(b)
            if p == "/api/staff":
                return self.add_staff(b)
            m = re.match(r"^/api/staff/([A-Za-z0-9_\-.~:@+]+)/password$", p)
            if m:
                return self.set_pin(m.group(1), b)
            return self.err(404, "없는 주소입니다")
        except Exception as e:
            print("  오류:", e)
            return self.err(500, "서버 오류가 났습니다")

    def do_PUT(self):
        p = unquote(urlparse(self.path).path)
        if not p.startswith("/api/doc/"):
            return self.err(404, "없는 주소입니다")
        path = p[len("/api/doc/"):]
        try:
            d = self.body()  # 응답 전에 본문을 다 읽어야 연결이 꼬이지 않음
        except Exception:
            self.close_connection = True
            return self.err(400, "요청을 읽지 못했습니다")
        s = self.session()
        if not s:
            return self.err(401, "로그인이 필요합니다", "unauthenticated")
        if not valid_path(path):
            return self.err(400, "잘못된 경로입니다")
        coll = path.split("/")[0]
        if coll in MANAGER_COLLS and s[2] != "manager":
            return self.err(403, "관리자만 바꿀 수 있습니다")
        d.pop("id", None)
        if coll == "staff":
            for k in SECRET + ("hasPin", "algo"):
                d.pop(k, None)
            old = get_doc(path) or {}
            for k in SECRET + ("algo", "loginId"):
                if k in old:
                    d[k] = old[k]
        put_doc(path, d)
        return self.send_json(200, {"ok": True, "ver": VER})

    def do_DELETE(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n:
            self.rfile.read(min(n, MAX_BODY))
        p = unquote(urlparse(self.path).path)
        if not p.startswith("/api/doc/"):
            return self.err(404, "없는 주소입니다")
        path = p[len("/api/doc/"):]
        s = self.session()
        if not s:
            return self.err(401, "로그인이 필요합니다", "unauthenticated")
        if not valid_path(path):
            return self.err(400, "잘못된 경로입니다")
        if path.split("/")[0] in MANAGER_COLLS and s[2] != "manager":
            return self.err(403, "관리자만 지울 수 있습니다")
        delete_doc(path)
        return self.send_json(200, {"ok": True})

    # 화면 파일
    def static(self, p):
        if p in ("", "/"):
            p = "/index.html"
        fp = os.path.normpath(os.path.join(PUBLIC_DIR, p.lstrip("/")))
        if not fp.startswith(PUBLIC_DIR) or not os.path.isfile(fp):
            return self.err(404, "없는 주소입니다")
        with open(fp, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", TYPES.get(os.path.splitext(fp)[1].lower(), "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    # 실시간 변경 전달
    def events(self, role):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        q = queue.Queue()
        sub = [q, role]
        SUBSCRIBERS.append(sub)
        try:
            self.wfile.write(b"retry: 3000\n\n")
            self.wfile.flush()
            while True:
                try:
                    m = q.get(timeout=20)
                    self.wfile.write(("data: %s\n\n" % json.dumps(m, ensure_ascii=False)).encode("utf-8"))
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            if sub in SUBSCRIBERS:
                SUBSCRIBERS.remove(sub)
        self.close_connection = True

    # 로그인 처리
    def login(self, b):
        lid, pw = norm_id(b.get("loginId")), str(b.get("password") or "")
        if not lid or not pw:
            return self.err(400, "아이디와 비밀번호를 입력하세요")
        f = LOGIN_FAILS.get(lid, [0, 0])
        if f[1] > time.time():
            return self.send_json(429, {"error": "여러 번 틀려 잠시 잠겼습니다. 잠시 뒤에 다시 시도하세요.", "lockSec": int(f[1] - time.time()) + 1})
        sid, d = find_staff_by_login(lid)
        if not pw_check(d, pw):
            f[0] += 1
            if f[0] >= 5:
                LOGIN_FAILS[lid] = [0, time.time() + 30]
                server_log("로그인 잠김", "%s 비밀번호 5번 틀림" % lid, (d or {}).get("name") or lid, sid or "")
                return self.send_json(429, {"error": "5번 틀렸습니다. 30초 뒤에 다시 시도하세요.", "lockSec": 30})
            LOGIN_FAILS[lid] = f
            return self.err(400, "아이디 또는 비밀번호가 맞지 않습니다 (%d/5)" % f[0])
        LOGIN_FAILS.pop(lid, None)
        if d.get("algo") != "pbkdf2":  # 예전 형식이면 새 형식으로 바꿔 저장
            set_pw(d, pw)
            put_doc("staff/" + sid, d)
        token = new_session(sid)
        server_log("로그인", "관리자" if d.get("role") == "manager" else "", d.get("name"), sid)
        return self.send_json(200, {"ok": True, "token": token, "id": sid, "name": d.get("name"), "role": d.get("role") or "worker"})

    def read_account(self, b):
        lid, name, pw = norm_id(b.get("loginId")), norm_name(b.get("name")), str(b.get("password") or "")
        if not ID_RE.match(lid):
            return None, "아이디는 영문·숫자 3~20자입니다 (. _ - 사용 가능)"
        if not name:
            return None, "이름을 입력하세요"
        if len(pw) < 4:
            return None, "비밀번호는 4자 이상입니다"
        if find_staff_by_login(lid)[1]:
            return None, "이미 쓰고 있는 아이디입니다"
        return (lid, name, pw), None

    def make_staff(self, lid, name, pw, role, added_by):
        rec = {"loginId": lid, "name": name, "role": role, "addedBy": added_by, "createdAt": now_utc_iso()}
        set_pw(rec, pw)
        sid = "w" + secrets.token_hex(6)
        put_doc("staff/" + sid, rec)
        return sid

    def register(self, b):
        if not CFG.get("allow_self_register", True):
            return self.err(403, "회원가입이 꺼져 있습니다. 관리자에게 계정을 요청하세요.")
        if not any(d.get("pinHash") for _, d in staff_docs()):
            return self.err(400, "먼저 관리자 설정이 필요합니다")
        a, e = self.read_account(b)
        if e:
            return self.err(400, e)
        sid = self.make_staff(a[0], a[1], a[2], "worker", "self")
        server_log("회원가입", "%s (%s)" % (a[1], a[0]), a[1], sid)
        return self.send_json(200, {"ok": True, "id": sid, "token": new_session(sid)})

    def api_setup(self, b):
        if any(d.get("pinHash") for _, d in staff_docs()):
            return self.err(409, "이미 설정되어 있습니다. 새로고침하세요.")
        a, e = self.read_account(b)
        if e:
            return self.err(400, e)
        sid = self.make_staff(a[0], a[1], a[2], "manager", "처음 설정")
        ensure_login_config()
        server_log("처음 설정", "관리자 %s (%s)" % (a[1], a[0]), a[1], sid)
        return self.send_json(200, {"ok": True, "id": sid, "token": new_session(sid)})

    def add_staff(self, b):
        s = self.session()
        if not s:
            return self.err(401, "로그인이 필요합니다", "unauthenticated")
        if s[2] != "manager":
            return self.err(403, "관리자만 추가할 수 있습니다")
        a, e = self.read_account(b)
        if e:
            return self.err(400, e)
        role = "manager" if b.get("role") == "manager" else "worker"
        sid = self.make_staff(a[0], a[1], a[2], role, s[1].get("name"))
        server_log("계정 추가", "%s (%s, %s)" % (a[1], a[0], "관리자" if role == "manager" else "현장"), s[1].get("name"), sid)
        return self.send_json(200, {"ok": True, "id": sid})

    def set_pin(self, sid, b):  # 비밀번호 재설정 (관리자)
        s = self.session()
        if not s:
            return self.err(401, "로그인이 필요합니다", "unauthenticated")
        if s[2] != "manager":
            return self.err(403, "관리자만 바꿀 수 있습니다")
        pw = str(b.get("password") or "")
        if len(pw) < 4:
            return self.err(400, "비밀번호는 4자 이상입니다")
        d = get_doc("staff/" + sid)
        if not d or d.get("deleted"):
            return self.err(400, "없는 직원입니다")
        lid = norm_id(b.get("loginId") or d.get("loginId"))
        if not ID_RE.match(lid):
            return self.err(400, "아이디는 영문·숫자 3~20자입니다")
        osid, other = find_staff_by_login(lid)
        if other and osid != sid:
            return self.err(400, "이미 쓰고 있는 아이디입니다")
        d["loginId"] = lid
        set_pw(d, pw)
        put_doc("staff/" + sid, d)
        with LOCK:
            DB.execute("DELETE FROM sessions WHERE staff_id=?", (sid,))
            DB.commit()
        server_log("비밀번호 재설정", "%s (%s)" % (d.get("name"), lid), s[1].get("name"), sid)
        return self.send_json(200, {"ok": True})

def lan_ips():
    ips = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ips.add(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except Exception:
        pass
    return sorted(i for i in ips if not i.startswith("127."))


def reset_password(lid, pw):
    """비밀번호를 잊었을 때: python server.py --reset-password 아이디 새비밀번호"""
    if len(pw or "") < 4:
        print("  비밀번호는 4자 이상입니다.")
        return 1
    sid, d = find_staff_by_login(lid)
    if not d:
        print("  '%s' 아이디를 찾지 못했습니다. 있는 아이디: %s" % (lid, ", ".join(x.get("loginId", "?") for _, x in staff_docs())))
        return 1
    set_pw(d, pw)
    put_doc("staff/" + sid, d)
    with LOCK:
        DB.execute("DELETE FROM sessions WHERE staff_id=?", (sid,))
        DB.commit()
    server_log("비밀번호 재설정", "%s (서버 PC에서 직접)" % d.get("name"), "서버", sid)
    print("  %s (%s) 님의 비밀번호를 바꿨습니다." % (d.get("name"), lid))
    return 0


def pause_if_windows():
    if os.name == "nt" and sys.stdin and sys.stdin.isatty():
        try:
            input("\n  엔터를 누르면 창이 닫힙니다.")
        except Exception:
            pass


def install_autostart():
    """PC를 켤 때 서버가 자동으로 켜지게 시작프로그램에 등록 (윈도우)"""
    if os.name != "nt":
        print("  자동 실행 등록은 윈도우에서만 됩니다.")
        return 1
    folder = os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Start Menu", "Programs", "Startup")
    target = os.path.join(folder, "yield-server.bat")
    exe = sys.executable
    with open(target, "w", encoding="cp949", errors="replace") as f:
        f.write('@echo off\r\ncd /d "%s"\r\nstart "수율 일지 서버" /min "%s" "%s"\r\n' % (BASE, exe, os.path.join(BASE, "server.py")))
    print("  다음부터 이 PC를 켜면 서버가 자동으로 켜집니다.")
    print("  끄려면 이 파일을 지우세요: %s" % target)
    return 0


def main():
    if len(sys.argv) >= 2 and sys.argv[1] == "--autostart":
        code = install_autostart()
        pause_if_windows()
        sys.exit(code)
    if len(sys.argv) >= 4 and sys.argv[1] in ("--reset-password", "--reset-pin"):
        sys.exit(reset_password(sys.argv[2], sys.argv[3]))
    seed()
    threading.Thread(target=backup_loop, daemon=True).start()
    port = int(CFG["port"])
    try:
        httpd = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    except OSError:
        print()
        print("  포트 %d 을(를) 이미 쓰고 있어 서버를 켤 수 없습니다." % port)
        print("  서버 창이 이미 열려 있는지 확인하세요. 다른 프로그램이 쓰고 있다면 config.json 의 port 를 바꾸세요(예: 8090).")
        pause_if_windows()
        sys.exit(1)
    httpd.daemon_threads = True
    print()
    print("  ==============================================")
    print("   코리아식품 수율 일지 - 사내 서버가 켜졌습니다")
    print("  ==============================================")
    print("  이 PC에서:        http://localhost:%d" % port)
    for ip in lan_ips():
        print("  태블릿·다른 PC:   http://%s:%d" % (ip, port))
    print("  데이터:           %s" % DB_PATH)
    print("  백업 폴더:        %s" % BACKUP_DIR)
    print()
    print("  이 창을 닫으면 서버가 꺼집니다. 끄려면 Ctrl+C 를 누르세요.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  서버를 끕니다.")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        print("\n  서버를 켜지 못했습니다:", e)
        pause_if_windows()
        raise
