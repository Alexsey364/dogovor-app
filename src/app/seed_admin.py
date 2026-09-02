# -*- coding: utf-8 -*-
"""
Первоначальное наполнение: отделы и учётная запись администратора.

Запускается один раз. Пароль администратора берётся из аргумента
командной строки или переменной ADMIN_PASSWORD, иначе генерируется
и печатается в консоль.
"""

from __future__ import annotations

import os
import secrets
import string
import sys

from werkzeug.security import generate_password_hash

import db

DEPARTMENTS = [
    "юридический", "отдел поверки", "монтаж",
    "снабжение", "бухгалтерия", "дирекция",
]

ADMIN_LOGIN = "aleksey"
ADMIN_NAME = "Алексей К."


def main() -> None:
    for name in DEPARTMENTS:
        db.execute(
            "INSERT INTO departments(name) VALUES(%s) ON CONFLICT (name) DO NOTHING",
            (name,),
        )

    if len(sys.argv) > 1:
        password = sys.argv[1]
    elif os.getenv("ADMIN_PASSWORD"):
        password = os.getenv("ADMIN_PASSWORD")
    else:
        alphabet = string.ascii_letters + string.digits
        password = "".join(secrets.choice(alphabet) for _ in range(12))

    existing = db.query_one("SELECT id FROM users WHERE login=%s", (ADMIN_LOGIN,))
    pwhash = generate_password_hash(password)
    if existing:
        db.execute(
            "UPDATE users SET password_hash=%s, role_code='admin', is_active=true, "
            "must_change_password=false WHERE login=%s",
            (pwhash, ADMIN_LOGIN),
        )
        action = "обновлён"
    else:
        db.execute(
            "INSERT INTO users(login, full_name, role_code, password_hash, "
            "must_change_password, is_active) VALUES(%s,%s,'admin',%s,false,true)",
            (ADMIN_LOGIN, ADMIN_NAME, pwhash),
        )
        action = "создан"

    print("=" * 46)
    print(f"Администратор {action}.")
    print(f"  логин:  {ADMIN_LOGIN}")
    print(f"  пароль: {password}")
    print("=" * 46)


if __name__ == "__main__":
    main()
