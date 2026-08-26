# -*- coding: utf-8 -*-
"""
Проверка договора перед отправкой.

Каждое правило здесь появилось не из общих соображений, а потому что
на разборе 62 договоров ООО «Римейк» за 2026 год оно что-то нашло.
В скобках у правила — договор, на котором оно сработало впервые.

Правило возвращает Finding или None. Ничего не блокирует само по себе:
решение принимает юрист, а система только показывает и запоминает.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Callable, Iterable, Optional

CRITICAL = "critical"
WARNING = "warning"
INFO = "info"


@dataclass
class Finding:
    rule_code: str
    severity: str
    title: str
    detail: str
    clause: str | None = None


@dataclass
class ContractData:
    """Всё, что известно о договоре на момент проверки."""

    number_text: str = ""
    type_code: str = ""
    signed_on: Optional[date] = None
    amount: Optional[Decimal] = None
    advance_amount: Optional[Decimal] = None
    warranty_months: Optional[int] = None
    commissioning_date: Optional[date] = None
    object_address: Optional[str] = None
    folder_address: Optional[str] = None
    estimate_address: Optional[str] = None
    estimate_amount: Optional[Decimal] = None
    text: str = ""
    counterparty_kind: str = "commercial"
    # суммы других договоров: {сумма: "М 04-02-2026"}
    other_amounts: dict = field(default_factory=dict)


# --- справочные значения, продублированы из contract_types --------------

ADVANCE_BY_TYPE = {
    "montazh_uute": Decimal("70"),
    "rekonstrukciya": Decimal("70"),
    "postavka": Decimal("70"),
    "kapremont": Decimal("30"),
    "budget_works": Decimal("30"),
}

WARRANTY_BY_TYPE = {
    "montazh_uute": 36,
    "rekonstrukciya": 36,
    "postavka": 36,
    "kapremont": 60,
    "budget_works": 24,
    "sborka": 12,
}


# --- правила ------------------------------------------------------------


def rule_missing_date(c: ContractData) -> Optional[Finding]:
    """Дата не проставлена (М 12-02-2026: в шапке пустые прочерки)."""
    if c.signed_on is not None:
        return None
    blanks = re.search(r"«_+»\s*_+\s*\d{4}\s*г", c.text)
    detail = (
        "В шапке договора вместо даты стоят прочерки. Пока дата не проставлена, "
        "договор юридически не заключён, а сроки работ и гарантии не от чего считать."
        if blanks
        else "Дата подписания не заполнена."
    )
    return Finding("missing_date", CRITICAL, "Дата договора не проставлена", detail)


def rule_wrong_year(c: ContractData) -> Optional[Finding]:
    """Год в дате не совпадает с годом номера (М 16 → 2022, М 18 → 2025)."""
    m = re.search(r"-(\d{4})\s*$", c.number_text.strip())
    if not m:
        return None
    year_in_number = int(m.group(1))

    years = {int(y) for y in re.findall(r"«\d{1,2}»\s*[а-яё]+\s*(\d{4})\s*г", c.text)}
    if c.signed_on:
        years.add(c.signed_on.year)
    wrong = sorted(y for y in years if y != year_in_number)
    if not wrong:
        return None
    return Finding(
        "wrong_year",
        CRITICAL,
        f"Год в тексте ({', '.join(map(str, wrong))}) не совпадает с номером ({year_in_number})",
        "Похоже, при копировании шаблона год не поправили. "
        "На разборе 2026 года это встретилось дважды: М 16 с 2022 и М 18 с 2025.",
    )


def rule_no_object_address(c: ContractData) -> Optional[Finding]:
    """Объект не назван (М 51-08-2026: только «на объекте заказчика»)."""
    if c.object_address:
        return None
    vague = re.search(r"на объекте заказчика|на сво[её]м объекте", c.text, re.I)
    if not vague:
        return None
    known = c.folder_address or c.estimate_address
    detail = "В тексте сказано «на объекте заказчика», но адрес объекта не указан."
    if known:
        detail += f" В папке и смете значится: {known}. Договор об этом молчит."
    detail += " Если дойдёт до спора о месте выполнения работ, договор не отвечает."
    return Finding("no_object_address", CRITICAL, "В договоре не назван объект", detail)


def rule_no_penalty(c: ContractData) -> Optional[Finding]:
    """Нет неустойки за просрочку оплаты (М 01-01-2026, остаток 2 028 635 ₽)."""
    if re.search(r"неустойк|пен[юия]|штраф", c.text, re.I):
        return None
    detail = "За просрочку оплаты заказчиком ответственность не предусмотрена."
    if c.amount and c.advance_amount:
        rest = c.amount - c.advance_amount
        detail += f" Остаток после аванса — {rest:,.0f} ₽ — ничем не обеспечен.".replace(",", " ")
    elif c.amount:
        detail += f" Под риском вся сумма — {c.amount:,.0f} ₽.".replace(",", " ")
    return Finding("no_penalty", CRITICAL, "Нет неустойки за просрочку оплаты", detail, "раздел 5")


def rule_advance_mismatch(c: ContractData) -> Optional[Finding]:
    """Аванс не соответствует типу работ (правило 30/70)."""
    expected = ADVANCE_BY_TYPE.get(c.type_code)
    if expected is None or not c.amount or not c.advance_amount or c.amount == 0:
        return None
    actual = (c.advance_amount / c.amount * 100).quantize(Decimal("1"))
    if abs(actual - expected) <= 2:
        return None
    return Finding(
        "advance_mismatch",
        WARNING,
        f"Аванс {actual}% вместо обычных {expected:.0f}%",
        f"Для типа «{c.type_code}» в компании принят аванс {expected:.0f}%. "
        "Отклонений не было ни разу на 62 договорах — проверьте, так ли задумано.",
        "пункт 2.4",
    )


def rule_warranty_mismatch(c: ContractData) -> Optional[Finding]:
    """Гарантия не соответствует типу работ."""
    expected = WARRANTY_BY_TYPE.get(c.type_code)
    if expected is None:
        return None
    if c.warranty_months is None:
        return Finding(
            "warranty_missing",
            WARNING,
            "Гарантийный срок не указан",
            f"Для этого типа работ обычно {expected} месяцев.",
            "раздел 7",
        )
    if c.warranty_months != expected:
        return Finding(
            "warranty_mismatch",
            WARNING,
            f"Гарантия {c.warranty_months} мес. вместо обычных {expected}",
            "Проверьте, осознанное ли это отклонение.",
            "раздел 7",
        )
    return None


def rule_commissioning_unset(c: ContractData) -> Optional[Finding]:
    """Гарантия есть, а точки отсчёта нет."""
    if not c.warranty_months or c.commissioning_date:
        return None
    return Finding(
        "commissioning_unset",
        WARNING,
        "Гарантия без точки отсчёта",
        "Гарантия считается с даты ввода в эксплуатацию, а она не заполнена. "
        "Пока поле пустое, дату окончания гарантии вычислить не из чего. "
        "Дата есть в акте — перенесите её в карточку.",
    )


def rule_deadline_out_of_control(c: ContractData) -> Optional[Finding]:
    """Срок привязан к событию, которым мы не управляем (М 01-01-2026)."""
    hits = []
    if re.search(r"с момента поступления аванса", c.text, re.I):
        hits.append("срок работ отсчитывается от поступления аванса")
    if re.search(r"передачи е?го на обслуживание", c.text, re.I):
        hits.append("гарантия — от передачи объекта на обслуживание сторонней организации")
    if not hits:
        return None
    return Finding(
        "deadline_out_of_control",
        WARNING,
        "Сроки зависят от действий заказчика",
        "; ".join(hits).capitalize()
        + ". Календарной даты нет: пока заказчик не сделает свой шаг, срок не начинается.",
        "пункты 3.1 и 7.3",
    )


def rule_amount_vs_estimate(c: ContractData) -> Optional[Finding]:
    """Сумма в тексте не сходится со сметой."""
    if not c.amount or not c.estimate_amount:
        return None
    diff = abs(c.amount - c.estimate_amount)
    if diff < Decimal("1"):
        return None
    return Finding(
        "amount_vs_estimate",
        CRITICAL,
        "Сумма договора не совпадает со сметой",
        f"В тексте {c.amount:,.2f} ₽, в приложении {c.estimate_amount:,.2f} ₽. "
        f"Расхождение {diff:,.2f} ₽.".replace(",", " "),
        "пункт 2.1",
    )


def rule_duplicate_amount(c: ContractData) -> Optional[Finding]:
    """Такая же сумма у другого договора (М 04 = М 07, М 05 = М 06)."""
    if not c.amount:
        return None
    twin = c.other_amounts.get(c.amount)
    if not twin or twin == c.number_text:
        return None
    return Finding(
        "duplicate_amount",
        WARNING,
        f"Точно такая же сумма у {twin}",
        "Совпадение до рубля. Может быть верно при одинаковом типоразмере узла, "
        "а может означать, что смету скопировали и не пересчитали.",
    )


def rule_vat_article(c: ContractData) -> Optional[Finding]:
    """Ставка 5% со ссылкой на ст. 145 (встречается в 23 договорах)."""
    if not re.search(r"НДС\s*5\s*%.{0,40}ст\.?\s*145", c.text, re.I | re.S):
        return None
    return Finding(
        "vat_article",
        WARNING,
        "Ссылка на статью НК не соответствует ставке",
        "Статья 145 — про освобождение от обязанностей плательщика НДС, "
        "а ставку 5% для УСН вводит пункт 8 статьи 164. "
        "Формулировка повторяется в шаблоне, поправить стоит разом. Показать бухгалтеру.",
        "пункт 2.1",
    )


def rule_number_month_mismatch(c: ContractData) -> Optional[Finding]:
    """Месяц в номере не совпадает с месяцем подписания (М 21, М 34, М 44)."""
    if not c.signed_on:
        return None
    m = re.search(r"-(\d{2})-\d{4}\s*$", c.number_text.strip())
    if not m:
        return None
    month_in_number = int(m.group(1))
    if month_in_number == c.signed_on.month:
        return None
    return Finding(
        "number_month_mismatch",
        INFO,
        f"Номер за {month_in_number:02d} месяц, подписан в {c.signed_on.month:02d}",
        "Если номер выдаётся при заведении, а не при подписании, это нормально. "
        "Но тогда отчёт по месяцам на основе номера будет врать.",
    )


def rule_address_mismatch(c: ContractData) -> Optional[Finding]:
    """Адрес в договоре, папке и смете расходится (М 13-02-2026)."""
    known = {
        "в договоре": c.object_address,
        "в папке": c.folder_address,
        "в смете": c.estimate_address,
    }
    present = {k: _norm_address(v) for k, v in known.items() if v}
    if len(set(present.values())) <= 1:
        return None
    lines = "; ".join(f"{k}: {known[k]}" for k in present)
    return Finding(
        "address_mismatch",
        CRITICAL,
        "Адрес объекта в разных местах разный",
        f"{lines}. Где-то указан неверный объект — до подписания надо решить, где.",
    )


def _norm_address(s: str) -> str:
    s = s.lower().replace("ё", "е")
    s = re.sub(r"\b(ул|улица|д|дом|кв|квартира|пр|проспект|пгт|с|г|корп)\b\.?", " ", s)
    return re.sub(r"[^а-я0-9]+", "", s)


# --- запуск -------------------------------------------------------------

ALL_RULES: tuple[Callable[[ContractData], Optional[Finding]], ...] = (
    rule_missing_date,
    rule_wrong_year,
    rule_no_object_address,
    rule_address_mismatch,
    rule_amount_vs_estimate,
    rule_no_penalty,
    rule_advance_mismatch,
    rule_warranty_mismatch,
    rule_commissioning_unset,
    rule_deadline_out_of_control,
    rule_duplicate_amount,
    rule_vat_article,
    rule_number_month_mismatch,
)

_ORDER = {CRITICAL: 0, WARNING: 1, INFO: 2}


def review(contract: ContractData) -> list[Finding]:
    """Прогнать все правила. Критичные — первыми."""
    found: list[Finding] = []
    for rule in ALL_RULES:
        result = rule(contract)
        if result is not None:
            found.append(result)
    found.sort(key=lambda f: _ORDER[f.severity])
    return found
