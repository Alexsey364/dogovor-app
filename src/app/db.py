# -*- coding: utf-8 -*-
"""Подключение к базе. Настройки берутся из private/db.env."""

from __future__ import annotations

import os
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from dotenv import load_dotenv

# db.env лежит рядом с приложением или в private/ репозитория
for candidate in (
    Path(__file__).resolve().parents[2] / "private" / "db.env",
    Path(__file__).resolve().parent / "db.env",
    Path("C:/Users/claude/dogovor/db.env"),
):
    if candidate.exists():
        load_dotenv(candidate)
        break


def _dsn() -> str:
    return (
        f"host={os.getenv('DB_HOST', '127.0.0.1')} "
        f"port={os.getenv('DB_PORT', '5432')} "
        f"dbname={os.getenv('DB_NAME', 'dogovor')} "
        f"user={os.getenv('DB_USER', 'dogovor_app')} "
        f"password={os.getenv('DB_PASSWORD', '')}"
    )


def connect() -> psycopg.Connection:
    """Новое соединение, строки возвращаются как словари."""
    return psycopg.connect(_dsn(), row_factory=dict_row, autocommit=True)


def query(sql: str, params: tuple = ()) -> list[dict]:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def query_one(sql: str, params: tuple = ()) -> dict | None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()


def execute(sql: str, params: tuple = ()) -> None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
