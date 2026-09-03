# -*- coding: utf-8 -*-
"""
Проверка договора по данным из базы.

Запускается кнопкой «Проверить» на карточке и при создании договора.
Работает по структурированным полям (сумма, аванс, гарантия, тип, стадия).
Проверки по тексту договора (чужой год, не назван объект и т.п.) появятся,
когда к карточке будут привязаны файлы и распознан текст.

Каждое правило возвращает finding или None. Дубли не создаются:
замечание с тем же rule_code для того же договора не добавляется повторно.
"""

from __future__ import annotations

from decimal import Decimal

import db

# правила предметной области (продублированы из contract_types)
ADVANCE_PCT = {"montazh_uute": 70, "rekonstrukciya": 70, "postavka": 70,
               "kapremont": 30, "budget_works": 30}
WARRANTY_M = {"montazh_uute": 36, "rekonstrukciya": 36, "postavka": 36,
              "kapremont": 60, "budget_works": 24, "sborka": 12}


def run(contract_id: int) -> int:
    """Прогнать проверки, добавить новые замечания. Вернуть число добавленных."""
    c = db.query_one("SELECT * FROM contracts WHERE id=%s", (contract_id,))
    if not c:
        return 0
    found = []

    # аванс не соответствует типу работ
    exp = ADVANCE_PCT.get(c["type_code"])
    if exp is not None and c["advance_pct"] is not None:
        actual = float(c["advance_pct"])
        if abs(actual - exp) > 2:
            found.append(("warning", "advance_mismatch",
                f"Аванс {actual:.0f}% вместо обычных {exp}%",
                f"Для типа «{c['type_code']}» в компании принят аванс {exp}%. "
                "Проверьте, осознанное ли это отклонение.", "пункт 2.4"))

    # гарантия
    exp_w = WARRANTY_M.get(c["type_code"])
    if exp_w is not None:
        if c["warranty_months"] is None:
            found.append(("warning", "warranty_missing", "Гарантийный срок не указан",
                f"Для этого типа работ обычно {exp_w} месяцев.", "раздел 7"))
        elif c["warranty_months"] != exp_w:
            found.append(("warning", "warranty_mismatch",
                f"Гарантия {c['warranty_months']} мес. вместо обычных {exp_w}",
                "Проверьте, осознанное ли это отклонение.", "раздел 7"))

    # гарантия есть, а даты ввода нет
    if c["warranty_months"] and not c["commissioning_date"]:
        found.append(("warning", "commissioning_unset", "Гарантия без точки отсчёта",
            "Гарантия считается с даты ввода в эксплуатацию, а она не заполнена. "
            "Пока поле пустое, дату окончания гарантии вычислить не из чего.", None))

    # нет неустойки за просрочку оплаты
    if c["has_penalty"] is False:
        detail = "За просрочку оплаты заказчиком ответственность не предусмотрена."
        if c["amount"] and c["advance_amount"]:
            rest = Decimal(c["amount"]) - Decimal(c["advance_amount"])
            detail += f" Остаток после аванса — {rest:,.0f} ₽ — ничем не обеспечен.".replace(",", " ")
        found.append(("critical", "no_penalty", "Нет неустойки за просрочку оплаты",
                      detail, "раздел 5"))

    # не проставлена дата подписания
    if c["signed_on"] is None and c["stage"] not in ("draft", "cancelled"):
        found.append(("critical", "missing_date", "Дата договора не проставлена",
            "Договор в работе, но дата подписания не заполнена.", "шапка"))

    # такая же сумма у другого договора
    if c["amount"]:
        twin = db.query_one(
            "SELECT number_text FROM contracts WHERE amount=%s AND id<>%s LIMIT 1",
            (c["amount"], contract_id))
        if twin:
            found.append(("warning", "duplicate_amount",
                f"Такая же сумма у {twin['number_text']}",
                "Совпадение суммы до рубля — проверьте, не скопирована ли смета.", None))

    # добавляем только новые (по rule_code)
    added = 0
    for sev, code, title, detail, clause in found:
        exists = db.query_one(
            "SELECT 1 FROM review_findings WHERE contract_id=%s AND rule_code=%s",
            (contract_id, code))
        if not exists:
            db.execute(
                "INSERT INTO review_findings(contract_id, severity, rule_code, title, detail, clause) "
                "VALUES(%s,%s,%s,%s,%s,%s)", (contract_id, sev, code, title, detail, clause))
            added += 1
    return added
