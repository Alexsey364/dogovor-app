# -*- coding: utf-8 -*-
"""
Тесты на реальных случаях из папки договоров за 2026 год.

Каждый тест воспроизводит договор, на котором правило сработало
при разборе. Если правило перестанет их ловить — тест упадёт.
"""

from datetime import date
from decimal import Decimal

from rules import (
    CRITICAL,
    INFO,
    WARNING,
    ContractData,
    review,
)


def codes(findings):
    return {f.rule_code for f in findings}


def one(findings, code):
    hits = [f for f in findings if f.rule_code == code]
    assert hits, f"правило {code} не сработало, нашлось: {sorted(codes(findings))}"
    return hits[0]


# --- М 12-02-2026, Агропромышленный колледж ------------------------------
# В шапке «____» ____________ 2026 г. Договор пролежал полгода.

def test_m12_дата_не_проставлена():
    c = ContractData(
        number_text="М 12-02-2026",
        type_code="budget_works",
        signed_on=None,
        amount=Decimal("399627.90"),
        advance_amount=Decimal("119888.37"),
        text='ДОГОВОР ПОДРЯДА № М 12-02-2026\nг. Уссурийск «____» ____________ 2026 г.\n'
             'в т.ч. НДС 5% согласно ст. 145 Налогового кодекса РФ. '
             'выполнить работы в течении 60 календарных дней с момента поступления аванса.',
    )
    f = review(c)
    assert one(f, "missing_date").severity == CRITICAL
    assert "прочерки" in one(f, "missing_date").detail
    # аванс 30% для бюджета — отклонения быть не должно
    assert "advance_mismatch" not in codes(f)


# --- М 16-03-2026 и М 18-03-2026: чужой год -----------------------------

def test_m16_год_2022_вместо_2026():
    c = ContractData(
        number_text="М 16-03-2026",
        type_code="montazh_uute",
        signed_on=date(2022, 3, 19),
        text='ДОГОВОР ПОДРЯДА № М 16-03-2026\nг.Уссурийск «19» марта 2022 г.',
    )
    f = one(review(c), "wrong_year")
    assert f.severity == CRITICAL
    assert "2022" in f.title and "2026" in f.title


def test_m18_год_2025_вместо_2026():
    c = ContractData(
        number_text="М 18-03-2026",
        type_code="montazh_uute",
        signed_on=date(2025, 3, 25),
        text='ДОГОВОР ПОДРЯДА № М 18-03-2026\n«25» марта 2025 г.',
    )
    assert one(review(c), "wrong_year").severity == CRITICAL


def test_правильный_год_не_ругается():
    c = ContractData(
        number_text="М 04-02-2026",
        type_code="montazh_uute",
        signed_on=date(2026, 2, 12),
        text='ДОГОВОР ПОДРЯДА № М 04-02-2026\n«12» февраля 2026 г. неустойка 0,1%',
    )
    assert "wrong_year" not in codes(review(c))


# --- М 51-08-2026: объект не назван -------------------------------------

def test_m51_объект_не_назван():
    c = ContractData(
        number_text="М 51-08-2026",
        type_code="sborka",
        signed_on=date(2026, 8, 6),
        amount=Decimal("323654"),
        object_address=None,
        folder_address="г. Спасск-Дальний, ул. Красногвардейская, 81-2",
        text="Комплектование и сборку узла учета тепловой энергии, а также "
             "электромонтажные и пусконаладочные работы на объекте заказчика. "
             "забрать собранный узел с адреса: г.Уссурийск, ул. Володарского, зд.11. "
             "и произвести его установку на своем объекте",
    )
    f = one(review(c), "no_object_address")
    assert f.severity == CRITICAL
    assert "Красногвардейская" in f.detail


# --- М 01-01-2026: нет неустойки, срок вне контроля ----------------------

def test_m01_нет_неустойки_и_сроки_вне_контроля():
    c = ContractData(
        number_text="М 01-01-2026",
        type_code="montazh_uute",
        signed_on=date(2026, 1, 12),
        amount=Decimal("6762635.25"),
        advance_amount=Decimal("4734000"),
        warranty_months=36,
        commissioning_date=None,
        text="Цена работ составляет 6 762 635 рублей 25 копеек, в т.ч. НДС 5% "
             "согласно ст. 145 Налогового кодекса РФ. Первый платеж производится "
             "авансом в сумме 4 734 000 руб. выполнить работы в течении 60 "
             "календарных дней с момента поступления аванса. Гарантийный срок "
             "составляет 36 месяцев с даты ввода в эксплуатацию объекта и "
             "передачи его на обслуживание специализированной организации.",
    )
    f = review(c)
    got = codes(f)

    assert "no_penalty" in got
    assert "2 028 635" in one(f, "no_penalty").detail

    assert "deadline_out_of_control" in got
    assert "commissioning_unset" in got
    assert "vat_article" in got

    # аванс 70% для монтажа — норма
    assert "advance_mismatch" not in got
    # критичные идут первыми
    assert f[0].severity == CRITICAL


# --- М 13-02-2026: Раздольная против Ровной -----------------------------

def test_m13_адрес_расходится():
    c = ContractData(
        number_text="М 13-02-2026",
        type_code="montazh_uute",
        signed_on=date(2026, 2, 12),
        object_address="г. Уссурийск, ул. Ровная, д. 10А",
        folder_address="г. Уссурийск, ул. Раздольная, д. 10А",
        estimate_address="г. Уссурийск, ул. Раздольная, д. 10А",
        text="неустойка 0,1% за каждый день",
    )
    f = one(review(c), "address_mismatch")
    assert f.severity == CRITICAL
    assert "Ровная" in f.detail and "Раздольная" in f.detail


def test_одинаковый_адрес_разной_записью_не_ругается():
    c = ContractData(
        number_text="М 04-02-2026",
        type_code="montazh_uute",
        object_address="г. Уссурийск, ул. Комсомольская, д. 44А",
        folder_address="Уссурийск, Комсомольская 44А",
        text="неустойка",
    )
    assert "address_mismatch" not in codes(review(c))


# --- М 04 и М 07: одинаковая сумма до рубля -----------------------------

def test_m07_такая_же_сумма_как_у_m04():
    c = ContractData(
        number_text="М 07-02-2026",
        type_code="montazh_uute",
        signed_on=date(2026, 2, 12),
        amount=Decimal("1492596"),
        other_amounts={Decimal("1492596"): "М 04-02-2026"},
        text="неустойка 0,1%",
    )
    f = one(review(c), "duplicate_amount")
    assert "М 04-02-2026" in f.title
    assert f.severity == WARNING


# --- сумма против сметы -------------------------------------------------

def test_сумма_не_сходится_со_сметой():
    c = ContractData(
        number_text="М 99-01-2026",
        type_code="montazh_uute",
        amount=Decimal("1284000"),
        estimate_amount=Decimal("1248000"),
        text="неустойка",
    )
    f = one(review(c), "amount_vs_estimate")
    assert f.severity == CRITICAL
    assert "36 000" in f.detail


# --- М 34 и М 44: номер июньский, подписан в июле -----------------------

def test_m34_месяц_номера_не_совпадает():
    c = ContractData(
        number_text="М 34-06-2026",
        type_code="budget_works",
        signed_on=date(2026, 7, 16),
        text="неустойка",
    )
    f = one(review(c), "number_month_mismatch")
    assert f.severity == INFO


# --- правило аванса -----------------------------------------------------

def test_аванс_70_для_капремонта_это_отклонение():
    c = ContractData(
        number_text="М 21-04-2026",
        type_code="kapremont",
        amount=Decimal("1000000"),
        advance_amount=Decimal("700000"),
        warranty_months=60,
        text="неустойка",
    )
    f = one(review(c), "advance_mismatch")
    assert "70" in f.title and "30" in f.title


def test_тсж_с_капремонтом_30_процентов_норма():
    # М 24-05-2026, ТСЖ «Оптимист»: 132 220 из 440 734 = 30%
    c = ContractData(
        number_text="М 24-05-2026",
        type_code="kapremont",
        amount=Decimal("440734"),
        advance_amount=Decimal("132220"),
        warranty_months=60,
        commissioning_date=date(2026, 6, 1),
        text="неустойка 0,1%",
    )
    assert "advance_mismatch" not in codes(review(c))


def test_тсж_без_капремонта_70_процентов_норма():
    # М 22-04-2026, ТСЖ «Комсомолец»: 120 000 из 168 407 = 71%
    c = ContractData(
        number_text="М 22-04-2026",
        type_code="montazh_uute",
        amount=Decimal("168407"),
        advance_amount=Decimal("120000"),
        warranty_months=36,
        commissioning_date=date(2026, 5, 20),
        text="неустойка 0,1%",
    )
    assert "advance_mismatch" not in codes(review(c))


# --- чистый договор -----------------------------------------------------

def test_нормальный_договор_без_замечаний():
    c = ContractData(
        number_text="М 10-02-2026",
        type_code="montazh_uute",
        signed_on=date(2026, 2, 24),
        amount=Decimal("1528050"),
        warranty_months=36,
        commissioning_date=date(2026, 4, 15),
        object_address="г. Уссурийск, ул. Ленинградская, д. 39Б",
        folder_address="г. Уссурийск, ул. Ленинградская, д. 39Б",
        text="ДОГОВОР ПОДРЯДА № М 10-02-2026 «24» февраля 2026 г. "
             "За нарушение сроков оплаты начисляется неустойка 0,1% за каждый день. "
             "Гарантийный срок 36 месяцев.",
    )
    assert review(c) == []
