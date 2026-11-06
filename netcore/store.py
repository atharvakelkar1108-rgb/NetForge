"""Tiny SQLite store so named projects survive server restarts."""
import json
import os
import sqlite3
import time

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "netforge.db")


def _conn():
    c = sqlite3.connect(DB)
    c.execute("CREATE TABLE IF NOT EXISTS projects (name TEXT PRIMARY KEY, data TEXT, updated REAL)")
    return c


def list_projects():
    with _conn() as c:
        rows = c.execute("SELECT name, updated, data FROM projects ORDER BY updated DESC").fetchall()
    out = []
    for name, updated, data in rows:
        t = json.loads(data)
        out.append({"name": name, "updated": updated, "nodes": len(t.get("nodes", [])), "links": len(t.get("links", []))})
    return out


def save_project(name, topo):
    with _conn() as c:
        c.execute("INSERT OR REPLACE INTO projects VALUES (?,?,?)", (name, json.dumps(topo), time.time()))


def load_project(name):
    with _conn() as c:
        row = c.execute("SELECT data FROM projects WHERE name=?", (name,)).fetchone()
    return json.loads(row[0]) if row else None


def delete_project(name):
    with _conn() as c:
        c.execute("DELETE FROM projects WHERE name=?", (name,))
