# -*- coding: utf-8 -*-
"""
Загрузка 62 договоров в базу из import_data.py.

Идемпотентно: повторный запуск не плодит дубли (сверка по номеру,
контрагенту, адресу). Запускать из папки app:
    venv\\Scripts\\python.exe import_contracts.py
"""

from __future__ import annotations

from decimal import Decimal

import db
from import_data import CONTRACTS

ADVANCE_PCT = {"montazh_uute": 70, "rekonstrukciya": 70, "postavka": 70,
               "kapremont": 30, "budget_works": 30}
WARRANTY_START = {"montazh_uute": "signing", "kapremont": "signing"}


def get_or_create_counterparty(name: str, kind: str) -> int:
    row = db.query_one("SELECT id FROM counterparties WHERE name=%s", (name,))
    if row:
        return row["id"]
    return db.query_one(
        "INSERT INTO counterparties(name, kind) VALUES(%s,%s) RETURNING id",
        (name, kind),
    )["id"]


def get_or_create_object(address: str, settlement: str) -> int | None:
    if not address:
        return None
    row = db.query_one("SELECT id FROM objects WHERE address=%s", (address,))
    if row:
        return row["id"]
    return db.query_one(
        "INSERT INTO objects(address, settlement) VALUES(%s,%s) RETURNING id",
        (address, settlement),
    )["id"]


def main() -> None:
    inserted = updated = findings_n = 0

    for row in CONTRACTS:
        (num, seq, month, type_code, cp_name, cp_kind, address, settlement,
         subject, signed_on, amount, advance, warranty, stage, has_penalty,
         ext_number, ikz, findings) = row

        cp_id = get_or_create_counterparty(cp_name, cp_kind)
        obj_id = get_or_create_object(address, settlement)

        advance_pct = None
        if advance and amount:
            advance_pct = round(Decimal(advance) / Decimal(amount) * 100, 2)
        elif type_code in ADVANCE_PCT and advance:
            advance_pct = ADVANCE_PCT[type_code]

        existing = db.query_one("SELECT id FROM contracts WHERE number_text=%s", (num,))
        params = dict(
            number_text=num, external_number=ext_number, ikz=ikz,
            type_code=type_code, counterparty_id=cp_id, object_id=obj_id,
            subject=subject, signed_on=signed_on, amount=amount,
            advance_amount=advance, advance_pct=advance_pct,
            warranty_months=warranty, has_penalty=has_penalty,
            work_starts_from=WARRANTY_START.get(type_code), stage=stage,
        )
        if existing:
            cid = existing["id"]
            db.execute(
                "UPDATE contracts SET external_number=%(external_number)s, ikz=%(ikz)s, "
                "type_code=%(type_code)s, counterparty_id=%(counterparty_id)s, "
                "object_id=%(object_id)s, subject=%(subject)s, signed_on=%(signed_on)s, "
                "amount=%(amount)s, advance_amount=%(advance_amount)s, "
                "advance_pct=%(advance_pct)s, warranty_months=%(warranty_months)s, "
                "has_penalty=%(has_penalty)s, work_starts_from=%(work_starts_from)s, "
                "stage=%(stage)s, updated_at=now() WHERE id=" + str(cid),
                params,
            )
            db.execute("DELETE FROM review_findings WHERE contract_id=%s", (cid,))
            updated += 1
        else:
            cid = db.query_one(
                "INSERT INTO contracts(number_text, external_number, ikz, type_code, "
                "counterparty_id, object_id, subject, signed_on, amount, advance_amount, "
                "advance_pct, warranty_months, has_penalty, work_starts_from, stage) "
                "VALUES(%(number_text)s,%(external_number)s,%(ikz)s,%(type_code)s,"
                "%(counterparty_id)s,%(object_id)s,%(subject)s,%(signed_on)s,%(amount)s,"
                "%(advance_amount)s,%(advance_pct)s,%(warranty_months)s,%(has_penalty)s,"
                "%(work_starts_from)s,%(stage)s) RETURNING id",
                params,
            )["id"]
            inserted += 1

        for sev, rule_code, title, detail, clause in findings:
            db.execute(
                "INSERT INTO review_findings(contract_id, severity, rule_code, title, detail, clause) "
                "VALUES(%s,%s,%s,%s,%s,%s)",
                (cid, sev, rule_code, title, detail, clause),
            )
            findings_n += 1

    total = db.query_one("SELECT count(*) AS n FROM contracts")["n"]
    print(f"добавлено: {inserted}, обновлено: {updated}, замечаний: {findings_n}")
    print(f"всего договоров в базе: {total}")


if __name__ == "__main__":
    main()
