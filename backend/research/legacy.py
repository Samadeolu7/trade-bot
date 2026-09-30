import sqlite3


def open_legacy_db(path: str) -> sqlite3.Connection:
    """The CLI's SQLite database, opened read-only. On the server it's the
    retired shadow runs' volume, mounted read-only into the web container."""
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)
