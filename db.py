"""MySQL helpers: one connection per request, parameterised queries only (prevents SQL injection)."""
from contextlib import contextmanager
from decimal import Decimal

import pymysql
from flask import current_app, g


def get_db():
    if "db" not in g:
        c = current_app.config
        g.db = pymysql.connect(
            host=c["DB_HOST"], port=c["DB_PORT"], user=c["DB_USER"], password=c["DB_PASSWORD"],
            database=c["DB_NAME"], charset="utf8mb4", autocommit=True,
            cursorclass=pymysql.cursors.DictCursor,
        )
    return g.db


def close_db(exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def _clean(row):
    return {k: (float(v) if isinstance(v, Decimal) else v) for k, v in row.items()}


def query(sql, args=(), one=False):
    """SELECT. NOTE: a literal % in SQL (e.g. DATE_FORMAT) must be written %%."""
    with get_db().cursor() as cur:
        cur.execute(sql, args)
        rows = [_clean(r) for r in cur.fetchall()]
    return (rows[0] if rows else None) if one else rows


def execute(sql, args=()):
    """INSERT/UPDATE/DELETE. Returns lastrowid."""
    with get_db().cursor() as cur:
        cur.execute(sql, args)
        return cur.lastrowid


def update(sql, args=()):
    """UPDATE/DELETE. Returns the number of affected rows."""
    with get_db().cursor() as cur:
        return cur.execute(sql, args)


@contextmanager
def transaction():
    db = get_db()
    db.begin()
    try:
        yield
        db.commit()
    except Exception:
        db.rollback()
        raise
