import os
import sqlite3
from functools import wraps
from pathlib import Path

from flask import Flask, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = Path(__file__).resolve().parent
DATABASE_URL = os.environ.get("DATABASE_URL")
USE_PG = bool(DATABASE_URL)
PH = "%s" if USE_PG else "?"

if USE_PG:
    import psycopg2
    import psycopg2.extras
else:
    # Vercel의 서버리스 함수는 프로젝트 디렉터리가 읽기 전용이라 /tmp에만 쓸 수 있고,
    # /tmp는 인스턴스마다 초기화되므로 DATABASE_URL이 없는 경우의 로컬 전용 경로다.
    DB_PATH = Path("/tmp/todos.db") if os.environ.get("VERCEL") else BASE_DIR / "todos.db"

app = Flask(__name__)
# 세션 쿠키 서명에 쓰인다. 재배포/재시작 후에도 로그인이 풀리지 않으려면 고정된 값이어야
# 하므로 반드시 환경변수로 지정한다 (미지정 시 로컬 개발용 값으로만 동작).
app.secret_key = os.environ.get("SECRET_KEY", "dev-only-secret-change-me")
_db_initialized = False


def get_db():
    if USE_PG:
        return psycopg2.connect(
            DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor
        )
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    cur = conn.cursor()
    if USE_PG:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS todos (
                id SERIAL PRIMARY KEY,
                title TEXT NOT NULL,
                done BOOLEAN NOT NULL DEFAULT FALSE,
                created_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
            """
        )
        # 로그인 기능을 나중에 추가했으므로, 이미 있던 todos 테이블에는
        # user_id 컬럼을 뒤늦게 붙여준다 (신규 설치에서는 위 CREATE에서 이미 없는 채로 만들어짐).
        cur.execute(
            "ALTER TABLE todos ADD COLUMN IF NOT EXISTS user_id "
            "INTEGER REFERENCES users(id) ON DELETE CASCADE"
        )
    else:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS todos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                done INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
            )
            """
        )
        try:
            cur.execute("ALTER TABLE todos ADD COLUMN user_id INTEGER REFERENCES users(id)")
        except sqlite3.OperationalError:
            pass  # 컬럼이 이미 있음
    conn.commit()
    cur.close()
    conn.close()


@app.before_request
def ensure_db():
    global _db_initialized
    if not _db_initialized:
        init_db()
        _db_initialized = True


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if session.get("user_id"):
        return redirect(url_for("index"))

    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm", "")

        if not username or not password:
            error = "아이디와 비밀번호를 모두 입력해주세요."
        elif password != confirm:
            error = "비밀번호가 서로 일치하지 않습니다."
        elif len(password) < 4:
            error = "비밀번호는 4자 이상으로 입력해주세요."
        else:
            conn = get_db()
            cur = conn.cursor()
            cur.execute(f"SELECT id FROM users WHERE username = {PH}", (username,))
            if cur.fetchone():
                error = "이미 사용 중인 아이디입니다."
            else:
                password_hash = generate_password_hash(password)
                cur.execute(
                    f"INSERT INTO users (username, password_hash) VALUES ({PH}, {PH})",
                    (username, password_hash),
                )
                conn.commit()
                cur.execute(f"SELECT id FROM users WHERE username = {PH}", (username,))
                user = cur.fetchone()
                session["user_id"] = user["id"]
                session["username"] = username
            cur.close()
            conn.close()
            if not error:
                return redirect(url_for("index"))

    return render_template("signup.html", error=error)


@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("user_id"):
        return redirect(url_for("index"))

    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        conn = get_db()
        cur = conn.cursor()
        cur.execute(f"SELECT * FROM users WHERE username = {PH}", (username,))
        user = cur.fetchone()
        cur.close()
        conn.close()

        if user and check_password_hash(user["password_hash"], password):
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            return redirect(url_for("index"))
        error = "아이디 또는 비밀번호가 올바르지 않습니다."

    return render_template("login.html", error=error)


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def index():
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        f"SELECT * FROM todos WHERE user_id = {PH} ORDER BY done ASC, id DESC",
        (session["user_id"],),
    )
    todos = cur.fetchall()
    cur.close()
    conn.close()
    total = len(todos)
    done_count = sum(1 for t in todos if t["done"])
    return render_template(
        "index.html",
        todos=todos,
        total=total,
        done_count=done_count,
        username=session.get("username"),
    )


@app.route("/add", methods=["POST"])
@login_required
def add():
    title = request.form.get("title", "").strip()
    if title:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            f"INSERT INTO todos (title, user_id) VALUES ({PH}, {PH})",
            (title, session["user_id"]),
        )
        conn.commit()
        cur.close()
        conn.close()
    return redirect(url_for("index"))


@app.route("/toggle/<int:todo_id>", methods=["POST"])
@login_required
def toggle(todo_id):
    conn = get_db()
    cur = conn.cursor()
    if USE_PG:
        cur.execute(
            "UPDATE todos SET done = NOT done WHERE id = %s AND user_id = %s",
            (todo_id, session["user_id"]),
        )
    else:
        cur.execute(
            "UPDATE todos SET done = 1 - done WHERE id = ? AND user_id = ?",
            (todo_id, session["user_id"]),
        )
    conn.commit()
    cur.close()
    conn.close()
    return redirect(url_for("index"))


@app.route("/delete/<int:todo_id>", methods=["POST"])
@login_required
def delete(todo_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        f"DELETE FROM todos WHERE id = {PH} AND user_id = {PH}",
        (todo_id, session["user_id"]),
    )
    conn.commit()
    cur.close()
    conn.close()
    return redirect(url_for("index"))


@app.route("/edit/<int:todo_id>", methods=["POST"])
@login_required
def edit(todo_id):
    title = request.form.get("title", "").strip()
    if title:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            f"UPDATE todos SET title = {PH} WHERE id = {PH} AND user_id = {PH}",
            (title, todo_id, session["user_id"]),
        )
        conn.commit()
        cur.close()
        conn.close()
    return redirect(url_for("index"))


if __name__ == "__main__":
    init_db()
    app.run(debug=True, port=5000)
