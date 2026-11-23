"""Cache de traduções e registro de trabalhos em SQLite.

A chave de cada trecho é um hash de (motor + modelo + idiomas + texto original).
Isso dá retomada de graça: ao repetir um trabalho, só o que falta é traduzido, e
nenhum trecho é pago duas vezes.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path


def make_key(engine: str, source: str, target: str, text: str) -> str:
    h = hashlib.sha256()
    for part in (engine, source, target, text):
        h.update(part.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        # Uma conexão só, usada a partir do loop principal (as corrotinas não
        # disputam o arquivo). WAL deixa leitura e escrita convivendo bem.
        self.db = sqlite3.connect(path, timeout=30, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS translations (
                key TEXT PRIMARY KEY, value TEXT NOT NULL, created REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                params TEXT NOT NULL, status TEXT NOT NULL,
                created REAL NOT NULL, updated REAL NOT NULL
            );
            """
        )
        self.db.commit()

    # ------------------------------------------------------------- traduções
    def get_many(self, keys: list[str]) -> dict[str, str]:
        out: dict[str, str] = {}
        for i in range(0, len(keys), 500):
            chunk = keys[i : i + 500]
            q = "SELECT key, value FROM translations WHERE key IN (%s)" % ",".join("?" * len(chunk))
            out.update(self.db.execute(q, chunk).fetchall())
        return out

    def put_many(self, items: dict[str, str]) -> None:
        now = time.time()
        with self.db:  # uma transação por lote
            self.db.executemany(
                "INSERT OR REPLACE INTO translations (key, value, created) VALUES (?, ?, ?)",
                [(k, v, now) for k, v in items.items()],
            )

    def clear_translations(self) -> int:
        with self.db:
            n = self.db.execute("DELETE FROM translations").rowcount
        self.db.execute("VACUUM")
        return n

    def count(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM translations").fetchone()[0]

    # ------------------------------------------------------------- trabalhos
    def start_job(self, params: dict) -> int:
        now = time.time()
        with self.db:
            cur = self.db.execute(
                "INSERT INTO jobs (params, status, created, updated) VALUES (?, 'running', ?, ?)",
                (json.dumps(params, ensure_ascii=False), now, now),
            )
        return int(cur.lastrowid)

    def set_job_status(self, job_id: int, status: str) -> None:
        with self.db:
            self.db.execute("UPDATE jobs SET status=?, updated=? WHERE id=?", (status, time.time(), job_id))

    def pending_jobs(self) -> list[tuple[int, dict]]:
        rows = self.db.execute(
            "SELECT id, params FROM jobs WHERE status IN ('running', 'paused') ORDER BY updated DESC LIMIT 5"
        ).fetchall()
        return [(r[0], json.loads(r[1])) for r in rows]

    def close(self) -> None:
        self.db.close()
