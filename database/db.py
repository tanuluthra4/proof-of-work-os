import os
import sqlite3
from contextlib import contextmanager

from flask import current_app, has_app_context

from config import DB_PATH


def _database_path():
    if has_app_context():
        return current_app.config["DATABASE"]
    return DB_PATH


def get_db_connection(path=None):
    """Open a SQLite connection.

    The connection is in autocommit mode (isolation_level=None) so that
    transactions are always explicit - see `transaction()`.
    """
    path = path or _database_path()
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)

    conn = sqlite3.connect(path, timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def transaction(conn=None):
    """Run a block inside BEGIN IMMEDIATE ... COMMIT, rolling back on any error.

    BEGIN IMMEDIATE takes SQLite's write lock up front, so two concurrent
    requests cannot both read the same workflow state and both advance it.
    If `conn` is omitted a connection is opened (and closed) for the block.
    """
    owns_connection = conn is None
    if owns_connection:
        conn = get_db_connection()

    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")
    finally:
        if owns_connection:
            conn.close()
