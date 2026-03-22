from __future__ import annotations

import os
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional, Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi import Request
from pydantic import BaseModel, Field

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "readings.db"

READINGS_ROOT = Path(os.environ.get("READINGS_ROOT", "/Users/ronin/Documents/READ"))
ALLOWED_EXT = {".pdf", ".epub", ".doc", ".docx", ".md", ".txt", ".html"}

app = FastAPI(title="Reading Manager")

STATIC_DIR = BASE_DIR / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init_db() -> None:
    with conn() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                path TEXT UNIQUE NOT NULL,
                filename TEXT NOT NULL,
                topic TEXT,
                ext TEXT,
                size_bytes INTEGER,
                mtime REAL,
                indexed_at REAL
            );

            CREATE TABLE IF NOT EXISTS document_meta (
                doc_id INTEGER PRIMARY KEY,
                status TEXT DEFAULT 'to_read',
                priority INTEGER DEFAULT 3,
                tags TEXT DEFAULT '',
                notes TEXT DEFAULT '',
                updated_at REAL,
                FOREIGN KEY(doc_id) REFERENCES documents(id) ON DELETE CASCADE
            );
            """
        )


def age_bucket_from_mtime(mtime: float) -> str:
    years = (datetime.now().timestamp() - mtime) / (365.25 * 24 * 3600)
    if years < 2:
        return "fresh"
    if years < 5:
        return "recent"
    if years < 8:
        return "aging"
    return "archive"


def scan_library() -> dict:
    if not READINGS_ROOT.exists():
        return {"indexed": 0, "removed": 0, "error": f"Missing root: {READINGS_ROOT}"}

    found_paths = set()
    indexed = 0
    now = datetime.now().timestamp()

    with conn() as c:
        for p in READINGS_ROOT.rglob("*"):
            if not p.is_file() or p.suffix.lower() not in ALLOWED_EXT:
                continue
            rel = str(p.relative_to(READINGS_ROOT))
            found_paths.add(rel)
            st = p.stat()
            parts = Path(rel).parts
            topic = parts[0] if parts else "ROOT"

            c.execute(
                """
                INSERT INTO documents(path, filename, topic, ext, size_bytes, mtime, indexed_at)
                VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(path) DO UPDATE SET
                    filename=excluded.filename,
                    topic=excluded.topic,
                    ext=excluded.ext,
                    size_bytes=excluded.size_bytes,
                    mtime=excluded.mtime,
                    indexed_at=excluded.indexed_at
                """,
                (rel, p.name, topic, p.suffix.lower(), st.st_size, st.st_mtime, now),
            )
            indexed += 1

        rows = c.execute("SELECT id, path FROM documents").fetchall()
        removed = 0
        for r in rows:
            if r["path"] not in found_paths:
                c.execute("DELETE FROM document_meta WHERE doc_id=?", (r["id"],))
                c.execute("DELETE FROM documents WHERE id=?", (r["id"],))
                removed += 1

    return {"indexed": indexed, "removed": removed, "error": None}


class MetaUpdate(BaseModel):
    status: Optional[Literal['to_read', 'in_progress', 'done']] = None
    priority: Optional[int] = Field(default=None, ge=1, le=5)
    tags: Optional[str] = None
    notes: Optional[str] = None


@app.on_event("startup")
def _startup() -> None:
    init_db()
    scan_library()


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request, "readings_root": str(READINGS_ROOT)})


@app.get("/api/docs")
def list_docs(
    q: str = "",
    topic: str = "",
    status: str = "",
    age: str = "",
    sort: Literal['mtime_desc', 'mtime_asc', 'priority_desc', 'priority_asc', 'filename_asc', 'filename_desc'] = 'mtime_desc',
    limit: int = 100,
    offset: int = 0,
):
    clauses = []
    params: list = []

    if q:
        clauses.append("(d.filename LIKE ? OR d.path LIKE ?)")
        params.extend([f"%{q}%", f"%{q}%"])
    if topic:
        clauses.append("d.topic = ?")
        params.append(topic)
    if status:
        clauses.append("COALESCE(m.status, 'to_read') = ?")
        params.append(status)

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    limit = max(1, min(limit, 500))
    offset = max(0, offset)

    order_by = {
        'mtime_desc': 'd.mtime DESC',
        'mtime_asc': 'd.mtime ASC',
        'priority_desc': 'COALESCE(m.priority,3) DESC, d.mtime DESC',
        'priority_asc': 'COALESCE(m.priority,3) ASC, d.mtime DESC',
        'filename_asc': 'd.filename COLLATE NOCASE ASC',
        'filename_desc': 'd.filename COLLATE NOCASE DESC',
    }[sort]

    sql = f"""
    SELECT d.*, COALESCE(m.status,'to_read') AS status,
           COALESCE(m.priority,3) AS priority,
           COALESCE(m.tags,'') AS tags,
           COALESCE(m.notes,'') AS notes
    FROM documents d
    LEFT JOIN document_meta m ON m.doc_id = d.id
    {where}
    ORDER BY {order_by}
    LIMIT ? OFFSET ?
    """

    with conn() as c:
        total = c.execute(
            f"""
            SELECT COUNT(*)
            FROM documents d
            LEFT JOIN document_meta m ON m.doc_id = d.id
            {where}
            """,
            params,
        ).fetchone()[0]
        rows = c.execute(sql, [*params, limit, offset]).fetchall()
        topics = [r[0] for r in c.execute("SELECT DISTINCT topic FROM documents ORDER BY topic").fetchall()]

    items = []
    for r in rows:
        bucket = age_bucket_from_mtime(r["mtime"] or datetime.now().timestamp())
        if age and bucket != age:
            continue
        items.append({
            "id": r["id"],
            "path": r["path"],
            "filename": r["filename"],
            "topic": r["topic"],
            "ext": r["ext"],
            "size_bytes": r["size_bytes"],
            "mtime": r["mtime"],
            "age_bucket": bucket,
            "status": r["status"],
            "priority": r["priority"],
            "tags": r["tags"],
            "notes": r["notes"],
        })

    return {
        "items": items,
        "topics": topics,
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": (offset + len(items)) < total,
    }


@app.get("/api/stats")
def stats():
    with conn() as c:
        total = c.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        by_status_rows = c.execute(
            """
            SELECT COALESCE(m.status, 'to_read') AS status, COUNT(*) AS cnt
            FROM documents d
            LEFT JOIN document_meta m ON m.doc_id = d.id
            GROUP BY COALESCE(m.status, 'to_read')
            """
        ).fetchall()
        by_topic_rows = c.execute(
            "SELECT topic, COUNT(*) AS cnt FROM documents GROUP BY topic ORDER BY cnt DESC LIMIT 10"
        ).fetchall()

    by_status = {r["status"]: r["cnt"] for r in by_status_rows}
    by_topic = [{"topic": r["topic"], "count": r["cnt"]} for r in by_topic_rows]

    return {
        "total": total,
        "by_status": {
            "to_read": by_status.get("to_read", 0),
            "in_progress": by_status.get("in_progress", 0),
            "done": by_status.get("done", 0),
        },
        "top_topics": by_topic,
    }


@app.post("/api/docs/{doc_id}/meta")
def update_meta(doc_id: int, body: MetaUpdate):
    with conn() as c:
        exists = c.execute("SELECT id FROM documents WHERE id=?", (doc_id,)).fetchone()
        if not exists:
            raise HTTPException(404, "Document not found")

        prev = c.execute("SELECT * FROM document_meta WHERE doc_id=?", (doc_id,)).fetchone()
        status = body.status if body.status is not None else (prev["status"] if prev else "to_read")
        priority = body.priority if body.priority is not None else (prev["priority"] if prev else 3)
        tags = body.tags if body.tags is not None else (prev["tags"] if prev else "")
        notes = body.notes if body.notes is not None else (prev["notes"] if prev else "")

        c.execute(
            """
            INSERT INTO document_meta(doc_id,status,priority,tags,notes,updated_at)
            VALUES(?,?,?,?,?,?)
            ON CONFLICT(doc_id) DO UPDATE SET
                status=excluded.status,
                priority=excluded.priority,
                tags=excluded.tags,
                notes=excluded.notes,
                updated_at=excluded.updated_at
            """,
            (doc_id, status, priority, tags, notes, datetime.now().timestamp()),
        )
    return {"ok": True}


@app.post("/api/docs/{doc_id}/open")
def open_doc(doc_id: int):
    with conn() as c:
        row = c.execute("SELECT path FROM documents WHERE id=?", (doc_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Document not found")

    rel = Path(row["path"])
    full_path = (READINGS_ROOT / rel).resolve()
    root_resolved = READINGS_ROOT.resolve()

    try:
        full_path.relative_to(root_resolved)
    except ValueError:
        raise HTTPException(400, "Invalid document path")

    if not full_path.exists():
        raise HTTPException(404, "File no longer exists on disk")

    subprocess.run(["open", str(full_path)], check=False)
    return {"ok": True, "path": str(full_path)}


@app.post("/api/rescan")
def rescan():
    return scan_library()
