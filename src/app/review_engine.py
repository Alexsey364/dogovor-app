# -*- coding: utf-8 -*-
"""
Проверка договора по каталогу правил (таблица review_rules).

Каждое правило — функция, помеченная @rule("код"). Запускаются только
те, что включены в каталоге (enabled=true). Правила с needs_text
активируются, когда к договору привязан текст (пока — заглушка, будет
работать после распознавания файлов).

Название и важность замечания берутся из каталога, поэтому админ может
поменять формулировку и серьёзность, не трогая код.
"""

from __future__ import annotations

import re
from decimal import Decimal
from datetime import date

import db

ADVANCE_PCT = {"montazh_uute": 70, "rekonstrukciya": 70, "postavka": 70,
               "kapremont": 30, "budget_works": 30}
WARRANTY_M = {"montazh_uute": 36, "rekonstrukciya": 36, "postavka": 36,
              "kapremont": 60, "budget_works": 24, "sborka": 12}

_RULES = {}  # code -> function(ctx) -> detail|None


def rule(code):
    def deco(fn):
        _RULES[code] = fn
        return fn
    return deco


# ---- финансы ----

@rule("no_penalty")
def _no_penalty(c):
    if c["has_penalty"] is not False:
        return None
    d = "За просрочку оплаты заказчиком ответственность не предусмотрена."
    if c["amount"] and c["advance_amount"]:
        rest = Decimal(c["amount"]) - Decimal(c["advance_amount"])
        d += f" Остаток после аванса — {rest:,.0f} ₽ — ничем не обеспечен.".replace(",", " ")
    return d


@rule("large_no_penalty")
def _large_no_penalty(c):
    if c["has_penalty"] is False and c["amount"] and Decimal(c["amount"]) > 1_000_000:
        return f"Сумма {Decimal(c['amount']):,.0f} ₽ и без неустойки.".replace(",", " ")
    return None


@rule("advance_mismatch")
def _advance_mismatch(c):
    exp = ADVANCE_PCT.get(c["type_code"])
    if exp is None or c["advance_pct"] is None:
        return None
    actual = float(c["advance_pct"])
    if abs(actual - exp) > 2:
        return f"Аванс {actual:.0f}% вместо обычных {exp}% для этого типа работ."
    return None


@rule("advance_too_high")
def _advance_too_high(c):
    if c["advance_pct"] is not None and float(c["advance_pct"]) > 70:
        return f"Аванс {float(c['advance_pct']):.0f}% — выше 70%."
    return None


@rule("duplicate_amount")
def _duplicate_amount(c):
    if not c["amount"]:
        return None
    twin = db.query_one(
        "SELECT number_text FROM contracts WHERE amount=%s AND id<>%s LIMIT 1",
        (c["amount"], c["id"]))
    return f"Совпадает до рубля с {twin['number_text']}." if twin else None


@rule("amount_missing")
def _amount_missing(c):
    if c["amount"] is None and c["stage"] not in ("draft", "cancelled"):
        return "Сумма не заполнена."
    return None


@rule("vat_rate_odd")
def _vat_rate_odd(c):
    if c["vat_rate"] is not None and float(c["vat_rate"]) != 5:
        return f"Ставка НДС {float(c['vat_rate']):.0f}% вместо обычных 5%."
    return None


# ---- сроки и гарантия ----

@rule("missing_date")
def _missing_date(c):
    if c["signed_on"] is None and c["stage"] not in ("draft", "cancelled"):
        return "Договор в работе, но дата подписания не заполнена."
    return None


@rule("signed_future")
def _signed_future(c):
    if c["signed_on"] and c["signed_on"] > date.today():
        return f"Дата подписания {c['signed_on'].strftime('%d.%m.%Y')} — в будущем."
    return None


@rule("warranty_missing")
def _warranty_missing(c):
    exp = WARRANTY_M.get(c["type_code"])
    if exp is not None and c["warranty_months"] is None:
        return f"Обычно для этого типа {exp} месяцев."
    return None


@rule("warranty_mismatch")
def _warranty_mismatch(c):
    exp = WARRANTY_M.get(c["type_code"])
    if exp is not None and c["warranty_months"] is not None and c["warranty_months"] != exp:
        return f"Гарантия {c['warranty_months']} мес. вместо обычных {exp}."
    return None


@rule("commissioning_unset")
def _commissioning_unset(c):
    if c["warranty_months"] and not c["commissioning_date"]:
        return "Гарантия считается с даты ввода, а она не заполнена."
    return None


@rule("warranty_expired")
def _warranty_expired(c):
    if c["warranty_until"] and c["warranty_until"] < date.today() and c["stage"] != "archived":
        return f"Гарантия истекла {c['warranty_until'].strftime('%d.%m.%Y')}, а договор не в архиве."
    return None


# ---- реквизиты и структура ----

@rule("no_counterparty")
def _no_counterparty(c):
    return "Контрагент не привязан." if not c["counterparty_id"] else None


@rule("no_object")
def _no_object(c):
    return "Объект (адрес) не указан." if not c["object_id"] else None


@rule("subject_short")
def _subject_short(c):
    if not c["subject"] or len(c["subject"].strip()) < 10:
        return "Предмет договора описан слишком кратко."
    return None


@rule("no_file")
def _no_file(c):
    n = db.query_one("SELECT count(*) AS n FROM contract_files WHERE contract_id=%s AND kind='contract'",
                     (c["id"],))["n"]
    return "Файл договора не приложен." if not n else None


# ---- текстовые (по распознанному тексту файла договора) ----

MONTHS = "январ|феврал|март|апрел|мая|мае|июн|июл|август|сентябр|октябр|ноябр|декабр"


@rule("wrong_year")
def _wrong_year(c):
    txt = c.get("_text") or ""
    if not txt:
        return None
    m = re.search(r"-(\d{4})\s*$", (c["number_text"] or "").strip())
    if not m:
        return None
    year_num = int(m.group(1))
    years = set()
    for mm in re.finditer(r"«?\d{1,2}»?\s*(?:" + MONTHS + r")\w*\s*(\d{4})", txt):
        years.add(int(mm.group(1)))
    wrong = sorted(y for y in years if abs(y - year_num) <= 5 and y != year_num)
    if wrong:
        return (f"В тексте дата {wrong[0]} года, а номер и папка — {year_num}. "
                "Похоже, год не поправили при копировании шаблона.")
    return None


@rule("object_not_named")
def _object_not_named(c):
    txt = c.get("_text") or ""
    if not txt:
        return None
    if re.search(r"на объекте заказчика|на сво[её]м объекте", txt, re.I) and not c["object_id"]:
        return "В тексте «на объекте заказчика», конкретный адрес не указан, объект не привязан."
    return None


@rule("vat_article_145")
def _vat_article_145(c):
    txt = c.get("_text") or ""
    if txt and re.search(r"НДС\s*5\s*%.{0,40}ст\.?\s*145", txt, re.I | re.S):
        return ("«НДС 5% согласно ст. 145 НК РФ». Ст. 145 — про освобождение от НДС, "
                "ставку 5% для УСН вводит п. 8 ст. 164. Показать бухгалтеру.")
    return None


@rule("deadline_out_of_control")
def _deadline_out_of_control(c):
    txt = c.get("_text") or ""
    if not txt:
        return None
    hits = []
    if re.search(r"с момента поступления аванса", txt, re.I):
        hits.append("срок работ — от поступления аванса")
    if re.search(r"передачи е?го на обслуживание", txt, re.I):
        hits.append("гарантия — от передачи объекта на обслуживание сторонней организации")
    if hits:
        return "; ".join(hits).capitalize() + " — событие вне вашего контроля."
    return None


# ---- запуск ----

def run(contract_id: int) -> int:
    """Прогнать включённые правила, добавить новые замечания. Вернуть число добавленных."""
    c = db.query_one("SELECT * FROM contracts WHERE id=%s", (contract_id,))
    if not c:
        return 0
    # текст договора из распознанных файлов
    t = db.query_one(
        "SELECT string_agg(extracted_text, chr(10)) AS t FROM contract_files "
        "WHERE contract_id=%s AND extracted_text IS NOT NULL", (contract_id,))
    c = dict(c)
    c["_text"] = (t or {}).get("t") or ""
    rules = db.query(
        "SELECT code, name, severity FROM review_rules WHERE enabled=true ORDER BY ord")
    added = 0
    for r in rules:
        fn = _RULES.get(r["code"])
        if not fn:
            continue
        try:
            detail = fn(c)
        except Exception:  # noqa: BLE001
            detail = None
        if not detail:
            continue
        exists = db.query_one(
            "SELECT 1 FROM review_findings WHERE contract_id=%s AND rule_code=%s",
            (contract_id, r["code"]))
        if exists:
            continue
        db.execute(
            "INSERT INTO review_findings(contract_id, severity, rule_code, title, detail) "
            "VALUES(%s,%s,%s,%s,%s)",
            (contract_id, r["severity"], r["code"], r["name"], detail))
        added += 1
    return added
