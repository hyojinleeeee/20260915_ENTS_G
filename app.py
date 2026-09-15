import os
import sqlite3
from pathlib import Path

from flask import Flask, redirect, render_template, request, url_for

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
            CREATE TABLE IF NOT EXISTS todos (
                id SERIAL PRIMARY KEY,
                title TEXT NOT NULL,
                done BOOLEAN NOT NULL DEFAULT FALSE,
                created_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
            """
        )
    else:
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
    conn.commit()
    cur.close()
    conn.close()


@app.before_request
def ensure_db():
    global _db_initialized
    if not _db_initialized:
        init_db()
        _db_initialized = True


@app.route("/")
def index():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM todos ORDER BY done ASC, id DESC")
    todos = cur.fetchall()
    cur.close()
    conn.close()
    total = len(todos)
    done_count = sum(1 for t in todos if t["done"])
    return render_template(
        "index.html", todos=todos, total=total, done_count=done_count
    )


@app.route("/add", methods=["POST"])
def add():
    title = request.form.get("title", "").strip()
    if title:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(f"INSERT INTO todos (title) VALUES ({PH})", (title,))
        conn.commit()
        cur.close()
        conn.close()
    return redirect(url_for("index"))


@app.route("/toggle/<int:todo_id>", methods=["POST"])
def toggle(todo_id):
    conn = get_db()
    cur = conn.cursor()
    if USE_PG:
        cur.execute("UPDATE todos SET done = NOT done WHERE id = %s", (todo_id,))
    else:
        cur.execute("UPDATE todos SET done = 1 - done WHERE id = ?", (todo_id,))
    conn.commit()
    cur.close()
    conn.close()
    return redirect(url_for("index"))


@app.route("/delete/<int:todo_id>", methods=["POST"])
def delete(todo_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(f"DELETE FROM todos WHERE id = {PH}", (todo_id,))
    conn.commit()
    cur.close()
    conn.close()
    return redirect(url_for("index"))


@app.route("/edit/<int:todo_id>", methods=["POST"])
def edit(todo_id):
    title = request.form.get("title", "").strip()
    if title:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            f"UPDATE todos SET title = {PH} WHERE id = {PH}", (title, todo_id)
        )
        conn.commit()
        cur.close()
        conn.close()
    return redirect(url_for("index"))


if __name__ == "__main__":
    init_db()
    app.run(debug=True, port=5000)
