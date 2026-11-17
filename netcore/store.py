"""SQLite storage: user accounts and each user's saved network projects."""
import json
import os
import sqlite3
import time

DB = os.environ.get("NETFORGE_DB") or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "netforge.db")


def _conn():
    c = sqlite3.connect(DB)
    c.execute("""CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL,
        username_lc TEXT NOT NULL UNIQUE, pw_hash TEXT NOT NULL, created REAL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS user_projects (
        user_id INTEGER NOT NULL, name TEXT NOT NULL, data TEXT NOT NULL, updated REAL,
        PRIMARY KEY (user_id, name))""")
    return c


# ---- users ------------------------------------------------------------------
def create_user(username, pw_hash):
    """Returns the new user id, or None if the username is already taken."""
    try:
        with _conn() as c:
            cur = c.execute("INSERT INTO users (username, username_lc, pw_hash, created) VALUES (?,?,?,?)",
                            (username, username.lower(), pw_hash, time.time()))
            return cur.lastrowid
    except sqlite3.IntegrityError:
        return None


def get_user_by_name(username):
    with _conn() as c:
        r = c.execute("SELECT id, username, pw_hash FROM users WHERE username_lc=?", (username.lower(),)).fetchone()
    return {"id": r[0], "username": r[1], "pw_hash": r[2]} if r else None


# ---- projects (always scoped to one user) --------------------------------------
def list_projects(uid):
    with _conn() as c:
        rows = c.execute("SELECT name, updated, data FROM user_projects WHERE user_id=? ORDER BY updated DESC", (uid,)).fetchall()
    out = []
    for name, updated, data in rows:
        t = json.loads(data)
        out.append({"name": name, "updated": updated, "nodes": len(t.get("nodes", [])), "links": len(t.get("links", []))})
    return out


def save_project(uid, name, topo):
    with _conn() as c:
        c.execute("INSERT OR REPLACE INTO user_projects VALUES (?,?,?,?)", (uid, name, json.dumps(topo), time.time()))


def load_project(uid, name):
    with _conn() as c:
        row = c.execute("SELECT data FROM user_projects WHERE user_id=? AND name=?", (uid, name)).fetchone()
    return json.loads(row[0]) if row else None


def delete_project(uid, name):
    with _conn() as c:
        c.execute("DELETE FROM user_projects WHERE user_id=? AND name=?", (uid, name))