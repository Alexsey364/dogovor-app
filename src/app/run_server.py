# -*- coding: utf-8 -*-
"""Запуск приложения через waitress (рабочий сервер под Windows)."""

import os

from waitress import serve

from app import app

if __name__ == "__main__":
    host = os.getenv("APP_HOST", "0.0.0.0")
    port = int(os.getenv("APP_PORT", "8080"))
    print(f"Договорной контур слушает http://{host}:{port}")
    serve(app, host=host, port=port, threads=8)
