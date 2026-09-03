# -*- coding: utf-8 -*-
"""
Договорной контур — веб-приложение.

Управленческий контур: боковое меню, «Мой день» с проверками,
доска стадий, реестр, карточка договора с разбором замечаний,
контрагенты, гарантии, пользователи и права.
"""

from __future__ import annotations

import os
import secrets
import threading
from functools import wraps

import hashlib
import uuid

from flask import (
    Flask, session, request, redirect, url_for, render_template, abort, flash, g,
    send_file
)
from werkzeug.security import check_password_hash, generate_password_hash

import db
import review_engine

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY") or secrets.token_hex(32)
app.config["MAX_CONTENT_LENGTH"] = 60 * 1024 * 1024  # до 60 МБ на файл

FILES_DIR = os.getenv("FILES_DIR") or "C:/Users/claude/dogovor/files"

FILE_KINDS = [
    ("contract", "Договор"), ("estimate", "Смета"), ("ks2", "КС-2"),
    ("ks3", "КС-3"), ("act", "Акт"), ("supplement", "Допсоглашение"),
    ("protocol", "Протокол разногласий"), ("scan", "Скан"), ("other", "Другое"),
]
FILE_KIND_RU = dict(FILE_KINDS)

STAGE_RU = {
    "draft": "черновик", "internal_review": "согласование",
    "legal_review": "проверка юриста", "at_counterparty": "у заказчика",
    "in_progress": "в работе", "completed": "завершён", "warranty": "на гарантии",
    "archived": "архив", "cancelled": "аннулирован", "on_hold": "на стопе",
}
# порядок стадий на доске
STAGE_ORDER = ["draft", "internal_review", "legal_review", "at_counterparty",
               "in_progress", "completed", "on_hold", "cancelled"]
SEV_RU = {"critical": "критично", "warning": "предупреждение", "info": "к сведению"}
KIND_RU = {"commercial": "коммерческий", "budget": "бюджет", "uk_tsj": "УК/ТСЖ",
           "government": "госконтракт", "individual": "физлицо/ИП"}

def human_size(n):
    if n is None:
        return "—"
    for unit in ("Б", "КБ", "МБ"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "Б" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} ГБ"


app.jinja_env.globals.update(FILE_KIND_RU=FILE_KIND_RU, FILE_KINDS=FILE_KINDS,
                             human_size=human_size)
app.jinja_env.globals.update(STAGE_RU=STAGE_RU, SEV_RU=SEV_RU, KIND_RU=KIND_RU,
                             STAGE_ORDER=["draft", "internal_review", "legal_review",
                                          "at_counterparty", "in_progress", "completed",
                                          "warranty", "archived", "cancelled", "on_hold"])


@app.template_filter("money")
def money(v):
    if v is None:
        return "—"
    return f"{float(v):,.0f}".replace(",", " ") + " ₽"


@app.template_filter("dt")
def dt(v, fmt="%d.%m.%Y"):
    return v.strftime(fmt) if v else "—"


# ------------------------------------------------------------------
#  Аутентификация и права
# ------------------------------------------------------------------

def login_required(f):
    @wraps(f)
    def wrapper(*a, **kw):
        if not session.get("user_id"):
            return redirect(url_for("login", next=request.path))
        return f(*a, **kw)
    return wrapper


def admin_required(f):
    @wraps(f)
    def wrapper(*a, **kw):
        if not g.user or g.user["role_code"] != "admin":
            abort(403)
        return f(*a, **kw)
    return wrapper


@app.before_request
def load_user():
    g.user = None
    uid = session.get("user_id")
    if uid:
        g.user = db.query_one(
            "SELECT u.id, u.login, u.full_name, u.role_code, r.name AS role_name, "
            "u.must_change_password, d.name AS dept "
            "FROM users u JOIN roles r ON r.code=u.role_code "
            "LEFT JOIN departments d ON d.id=u.department_id WHERE u.id=%s", (uid,))
        # права роли
        g.perms = {r["cap_code"]: r["level"] for r in db.query(
            "SELECT cap_code, level FROM role_permissions WHERE role_code=%s",
            (g.user["role_code"],))}
        # заставляем сменить временный пароль до входа в остальные разделы
        if g.user and g.user["must_change_password"] and request.endpoint not in (
                "change_password", "logout", "static"):
            return redirect(url_for("change_password"))


def can(cap: str) -> bool:
    """Есть ли у текущего пользователя доступ к возможности (уровень не 'none')."""
    return g.get("perms", {}).get(cap, "none") != "none"


def perm_level(cap: str) -> str:
    return g.get("perms", {}).get(cap, "none")


app.jinja_env.globals.update(can=can, perm_level=perm_level)


def require_cap(cap):
    def deco(f):
        @wraps(f)
        def wrapper(*a, **kw):
            if not g.get("user"):
                return redirect(url_for("login", next=request.path))
            if not can(cap):
                abort(403)
            return f(*a, **kw)
        return wrapper
    return deco


def audit(action: str, entity: str, entity_id: int, before=None, after=None):
    """Запись в журнал действий (не удаляется и не правится триггерами)."""
    import json
    try:
        db.execute(
            "INSERT INTO audit_log(user_id, action, entity, entity_id, before, after, ip) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s)",
            (g.user["id"] if g.get("user") else None, action, entity, entity_id,
             json.dumps(before, ensure_ascii=False, default=str) if before else None,
             json.dumps(after, ensure_ascii=False, default=str) if after else None,
             request.remote_addr))
    except Exception:  # noqa: BLE001
        pass


def next_number() -> str:
    """Следующий свободный номер вида М NN-MM-YYYY."""
    import datetime
    import re
    now = datetime.date.today()
    mx = 0
    for r in db.query("SELECT number_text FROM contracts"):
        m = re.match(r"М\s*(\d+)-", r["number_text"] or "")
        if m:
            mx = max(mx, int(m.group(1)))
    return f"М {mx + 1:02d}-{now.month:02d}-{now.year}"


@app.context_processor
def inject_today():
    import datetime
    return {"today": datetime.date.today()}


@app.context_processor
def inject_nav():
    """Счётчики для бокового меню."""
    if not g.get("user"):
        return {}
    counts = db.query_one("""
        SELECT
          (SELECT count(*) FROM contracts) AS contracts,
          (SELECT count(*) FROM contracts WHERE stage NOT IN ('archived','cancelled')) AS active,
          (SELECT count(*) FROM review_findings WHERE resolution IS NULL) AS findings,
          (SELECT count(*) FROM counterparties) AS counterparties,
          (SELECT count(*) FROM contracts WHERE warranty_months IS NOT NULL) AS warranty,
          (SELECT count(*) FROM users) AS users
    """) or {}
    return {"nav": counts}


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        row = db.query_one(
            "SELECT id, password_hash, is_active FROM users WHERE login=%s",
            ((request.form.get("login") or "").strip(),))
        if row and row["is_active"] and check_password_hash(
                row["password_hash"], request.form.get("password") or ""):
            session.clear()
            session["user_id"] = row["id"]
            db.execute("UPDATE users SET last_login_at=now() WHERE id=%s", (row["id"],))
            return redirect(request.args.get("next") or url_for("index"))
        flash("Неверный логин или пароль")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ------------------------------------------------------------------
#  Мой день (сводка)
# ------------------------------------------------------------------

@app.route("/")
@login_required
def index():
    stats = db.query_one("""
        SELECT
          count(*) AS total,
          count(*) FILTER (WHERE stage NOT IN ('archived','cancelled')) AS active,
          count(*) FILTER (WHERE stage='cancelled') AS cancelled,
          count(*) FILTER (WHERE stage='on_hold') AS on_hold,
          coalesce(sum(amount) FILTER (WHERE stage <> 'cancelled'),0) AS total_amount
        FROM contracts
    """) or {}
    sev = db.query_one("""
        SELECT
          count(*) FILTER (WHERE severity='critical') AS crit,
          count(*) FILTER (WHERE severity='warning') AS warn,
          count(*) FILTER (WHERE severity='info') AS info
        FROM review_findings WHERE resolution IS NULL
    """) or {}
    # договоры с критическими замечаниями — на стол
    critical = db.query("""
        SELECT c.id, c.number_text, cp.name AS counterparty,
               count(*) AS n, min(rf.title) AS sample
        FROM review_findings rf
        JOIN contracts c ON c.id=rf.contract_id
        LEFT JOIN counterparties cp ON cp.id=c.counterparty_id
        WHERE rf.resolution IS NULL AND rf.severity='critical'
        GROUP BY c.id, c.number_text, cp.name
        ORDER BY c.number_text DESC
    """)
    warns = db.query("""
        SELECT c.id, c.number_text, cp.name AS counterparty,
               count(*) AS n, min(rf.title) AS sample
        FROM review_findings rf
        JOIN contracts c ON c.id=rf.contract_id
        LEFT JOIN counterparties cp ON cp.id=c.counterparty_id
        WHERE rf.resolution IS NULL AND rf.severity='warning'
        GROUP BY c.id, c.number_text, cp.name
        ORDER BY c.number_text DESC LIMIT 12
    """)
    by_stage = {r["stage"]: r["n"] for r in db.query(
        "SELECT stage, count(*) AS n FROM contracts GROUP BY stage")}
    return render_template("dashboard.html", stats=stats, sev=sev,
                           critical=critical, warns=warns,
                           by_stage=by_stage, stage_order=STAGE_ORDER)


# ------------------------------------------------------------------
#  Доска стадий
# ------------------------------------------------------------------

@app.route("/board")
@login_required
def board():
    rows = db.query("""
        SELECT c.id, c.number_text, c.subject, c.amount, c.stage, c.stage_since,
               cp.name AS counterparty,
               (SELECT count(*) FROM review_findings rf
                 WHERE rf.contract_id=c.id AND rf.resolution IS NULL
                   AND rf.severity='critical') AS crit
        FROM contracts c LEFT JOIN counterparties cp ON cp.id=c.counterparty_id
        ORDER BY c.stage_since DESC NULLS LAST, c.id DESC
    """)
    cols = {s: [] for s in STAGE_ORDER}
    for r in rows:
        cols.setdefault(r["stage"], []).append(r)
    return render_template("board.html", cols=cols, stage_order=STAGE_ORDER)


# ------------------------------------------------------------------
#  Реестр
# ------------------------------------------------------------------

@app.route("/registry")
@login_required
def registry():
    flt = request.args.get("f", "all")
    where = "1=1"
    if flt == "active":
        where = "c.stage NOT IN ('archived','cancelled')"
    elif flt == "cancelled":
        where = "c.stage='cancelled'"
    elif flt == "findings":
        where = "EXISTS (SELECT 1 FROM review_findings rf WHERE rf.contract_id=c.id AND rf.resolution IS NULL)"
    elif flt == "warranty":
        where = "c.warranty_months IS NOT NULL"
    rows = db.query(f"""
        SELECT c.id, c.number_text, c.subject, c.amount, c.advance_pct, c.stage,
               c.signed_on, c.warranty_months, cp.name AS counterparty,
               ct.name AS type_name, o.address AS object,
               (SELECT count(*) FROM review_findings rf
                 WHERE rf.contract_id=c.id AND rf.resolution IS NULL) AS findings
        FROM contracts c
        LEFT JOIN counterparties cp ON cp.id=c.counterparty_id
        LEFT JOIN contract_types ct ON ct.code=c.type_code
        LEFT JOIN objects o ON o.id=c.object_id
        WHERE {where}
        ORDER BY c.number_text DESC LIMIT 300
    """)
    return render_template("registry.html", rows=rows, flt=flt)


@app.route("/contract/<int:cid>")
@login_required
def contract(cid: int):
    c = db.query_one("""
        SELECT c.*, cp.name AS counterparty, cp.kind AS cp_kind,
               ct.name AS type_name, o.address AS object_address
        FROM contracts c
        LEFT JOIN counterparties cp ON cp.id=c.counterparty_id
        LEFT JOIN contract_types ct ON ct.code=c.type_code
        LEFT JOIN objects o ON o.id=c.object_id
        WHERE c.id=%s
    """, (cid,))
    if not c:
        abort(404)
    findings = db.query(
        "SELECT id, severity, title, detail, clause, resolution FROM review_findings "
        "WHERE contract_id=%s ORDER BY CASE severity "
        "WHEN 'critical' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END, id", (cid,))
    files = db.query("""
        SELECT cf.id, cf.kind, cf.version, cf.file_name, cf.size_bytes,
               cf.uploaded_at, u.full_name AS uploader
        FROM contract_files cf LEFT JOIN users u ON u.id=cf.uploaded_by
        WHERE cf.contract_id=%s
        ORDER BY cf.kind, cf.version DESC, cf.id DESC
    """, (cid,))
    work_stages = db.query("""
        SELECT id, ord, name, volume, planned_on, actual_on, amount, is_done
        FROM stages WHERE contract_id=%s ORDER BY ord, id
    """, (cid,))
    payments = []
    pay_sum = {}
    if perm_level("payments") != "none":
        payments = db.query("""
            SELECT id, kind, direction, planned_on, amount, paid_on, paid_amount, condition
            FROM payments WHERE contract_id=%s ORDER BY planned_on NULLS LAST, id
        """, (cid,))
        pay_sum = db.query_one("""
            SELECT coalesce(sum(amount),0) AS planned,
                   coalesce(sum(paid_amount) FILTER (WHERE paid_on IS NOT NULL),0) AS paid
            FROM payments WHERE contract_id=%s AND direction='incoming'
        """, (cid,)) or {}
    return render_template("contract.html", c=c, findings=findings, files=files,
                           payments=payments, pay_sum=pay_sum)


# ------------------------------------------------------------------
#  Контрагенты
# ------------------------------------------------------------------

PAY_KIND_RU = {"advance": "аванс", "stage": "этап", "final": "окончательный"}
DIR_RU = {"incoming": "нам платят", "outgoing": "мы платим"}
app.jinja_env.globals.update(PAY_KIND_RU=PAY_KIND_RU, DIR_RU=DIR_RU)


@app.route("/payments")
@login_required
def payments_page():
    if perm_level("payments") == "none":
        abort(403)
    flt = request.args.get("f", "open")
    where = "p.paid_on IS NULL"
    if flt == "overdue":
        where = "p.paid_on IS NULL AND p.planned_on < current_date"
    elif flt == "incoming":
        where = "p.paid_on IS NULL AND p.direction='incoming'"
    elif flt == "outgoing":
        where = "p.paid_on IS NULL AND p.direction='outgoing'"
    elif flt == "paid":
        where = "p.paid_on IS NOT NULL"
    rows = db.query(f"""
        SELECT p.id, p.kind, p.direction, p.planned_on, p.amount, p.paid_on,
               p.condition, c.id AS cid, c.number_text, cp.name AS counterparty
        FROM payments p JOIN contracts c ON c.id=p.contract_id
        LEFT JOIN counterparties cp ON cp.id=c.counterparty_id
        WHERE {where}
        ORDER BY p.planned_on NULLS LAST, p.id
    """)
    totals = db.query_one("""
        SELECT
          coalesce(sum(amount) FILTER (WHERE paid_on IS NULL AND direction='incoming'),0) AS to_get,
          coalesce(sum(amount) FILTER (WHERE paid_on IS NULL AND direction='outgoing'),0) AS to_pay,
          count(*) FILTER (WHERE paid_on IS NULL AND planned_on < current_date) AS overdue
        FROM payments
    """) or {}
    return render_template("payments.html", rows=rows, flt=flt, totals=totals)


@app.route("/contract/<int:cid>/payment", methods=["POST"])
@login_required
def payment_add(cid):
    if perm_level("payments") != "yes":
        abort(403)
    kind = request.form.get("kind", "final")
    direction = request.form.get("direction", "incoming")
    planned = request.form.get("planned_on") or None
    amount = request.form.get("amount") or None
    condition = (request.form.get("condition") or "").strip() or None
    if kind not in PAY_KIND_RU:
        kind = "final"
    if direction not in DIR_RU:
        direction = "incoming"
    if not amount:
        flash("Укажите сумму платежа")
        return redirect(url_for("contract", cid=cid))
    pid = db.query_one(
        "INSERT INTO payments(contract_id, kind, direction, planned_on, amount, condition) "
        "VALUES(%s,%s,%s,%s,%s,%s) RETURNING id",
        (cid, kind, direction, planned, amount, condition))["id"]
    audit("payment_add", "contract", cid, after={"amount": amount, "kind": kind})
    flash("Платёж добавлен в график")
    return redirect(url_for("contract", cid=cid))


@app.route("/payment/<int:pid>/paid", methods=["POST"])
@login_required
def payment_paid(pid):
    if perm_level("payments") != "yes":
        abort(403)
    p = db.query_one("SELECT contract_id, amount FROM payments WHERE id=%s", (pid,))
    if not p:
        abort(404)
    if request.form.get("undo") == "1":
        db.execute("UPDATE payments SET paid_on=NULL, paid_amount=NULL WHERE id=%s", (pid,))
    else:
        paid_on = request.form.get("paid_on") or None
        paid_amount = request.form.get("paid_amount") or p["amount"]
        db.execute("UPDATE payments SET paid_on=coalesce(%s, current_date), paid_amount=%s WHERE id=%s",
                   (paid_on, paid_amount, pid))
    audit("payment_paid", "contract", p["contract_id"], after={"payment": pid})
    return redirect(url_for("contract", cid=p["contract_id"]))


@app.route("/contract/<int:cid>/payment/schedule", methods=["POST"])
@login_required
def payment_schedule(cid):
    """Быстрый график: аванс (если есть) + остаток."""
    if perm_level("payments") != "yes":
        abort(403)
    c = db.query_one("SELECT amount, advance_amount, advance_pct FROM contracts WHERE id=%s", (cid,))
    if not c or not c["amount"]:
        flash("У договора не указана сумма — график не построить")
        return redirect(url_for("contract", cid=cid))
    if db.query_one("SELECT 1 FROM payments WHERE contract_id=%s LIMIT 1", (cid,)):
        flash("График уже есть — добавляйте платежи вручную")
        return redirect(url_for("contract", cid=cid))
    from decimal import Decimal
    total = Decimal(c["amount"])
    adv = Decimal(c["advance_amount"]) if c["advance_amount"] else Decimal(0)
    if adv > 0:
        db.execute("INSERT INTO payments(contract_id, kind, direction, amount, condition) "
                   "VALUES(%s,'advance','incoming',%s,%s)",
                   (cid, adv, "аванс по договору"))
    rest = total - adv
    if rest > 0:
        db.execute("INSERT INTO payments(contract_id, kind, direction, amount, condition) "
                   "VALUES(%s,'final','incoming',%s,%s)",
                   (cid, rest, "окончательный расчёт по акту"))
    audit("payment_schedule", "contract", cid)
    flash("Простой график создан: аванс и остаток")
    return redirect(url_for("contract", cid=cid))


@app.route("/counterparty/<int:pid>")
@login_required
def counterparty(pid):
    cp = db.query_one("SELECT * FROM counterparties WHERE id=%s", (pid,))
    if not cp:
        abort(404)
    m = db.query_one("""
        SELECT count(*) AS total,
               count(*) FILTER (WHERE stage='cancelled') AS cancelled,
               count(*) FILTER (WHERE stage='completed') AS completed,
               count(*) FILTER (WHERE stage NOT IN ('cancelled','archived','completed')) AS active,
               count(*) FILTER (WHERE warranty_months IS NOT NULL) AS warranty,
               coalesce(sum(amount) FILTER (WHERE stage<>'cancelled'),0) AS total_amount,
               (SELECT count(*) FROM review_findings rf JOIN contracts c2 ON c2.id=rf.contract_id
                 WHERE c2.counterparty_id=%s AND rf.resolution IS NULL) AS findings
        FROM contracts WHERE counterparty_id=%s
    """, (pid, pid)) or {}
    contracts = db.query("""
        SELECT c.id, c.number_text, c.subject, c.amount, c.stage, c.signed_on,
               o.address AS object,
               (SELECT count(*) FROM review_findings rf WHERE rf.contract_id=c.id AND rf.resolution IS NULL) AS findings
        FROM contracts c LEFT JOIN objects o ON o.id=c.object_id
        WHERE c.counterparty_id=%s ORDER BY c.number_text DESC
    """, (pid,))
    total = m.get("total") or 0
    cancelled = m.get("cancelled") or 0
    reliability = round((total - cancelled) / total * 100) if total else None
    return render_template("counterparty.html", cp=cp, m=m, contracts=contracts,
                           reliability=reliability)


@app.route("/counterparties")
@login_required
def counterparties():
    rows = db.query("""
        SELECT cp.id, cp.name, cp.kind, count(c.id) AS n,
               coalesce(sum(c.amount),0) AS total,
               count(*) FILTER (WHERE c.stage='cancelled') AS cancelled
        FROM counterparties cp LEFT JOIN contracts c ON c.counterparty_id=cp.id
        GROUP BY cp.id, cp.name, cp.kind
        ORDER BY count(c.id) DESC, cp.name
    """)
    return render_template("counterparties.html", rows=rows)


# ------------------------------------------------------------------
#  Гарантии
# ------------------------------------------------------------------

@app.route("/warranty")
@login_required
def warranty():
    rows = db.query("""
        SELECT c.id, c.number_text, c.warranty_months, c.warranty_until,
               c.commissioning_date, c.type_code, cp.name AS counterparty,
               o.address AS object
        FROM contracts c
        LEFT JOIN counterparties cp ON cp.id=c.counterparty_id
        LEFT JOIN objects o ON o.id=c.object_id
        WHERE c.warranty_months IS NOT NULL
        ORDER BY c.warranty_months DESC, c.number_text
    """)
    return render_template("warranty.html", rows=rows)


# ------------------------------------------------------------------
#  Пользователи и права
# ------------------------------------------------------------------

@app.route("/users")
@require_cap("manage_users")
def users():
    rows = db.query("""
        SELECT u.id, u.login, u.full_name, u.role_code, r.name AS role_name,
               d.name AS dept, u.is_active, u.last_login_at
        FROM users u JOIN roles r ON r.code=u.role_code
        LEFT JOIN departments d ON d.id=u.department_id
        ORDER BY u.id
    """)
    return render_template("users.html", rows=rows)


LEVELS = [("none", "нет"), ("own", "свои"), ("dept", "свой отдел"),
          ("view", "просмотр"), ("all", "все"), ("yes", "да")]


def role_order():
    return ("CASE code WHEN 'manager' THEN 0 WHEN 'lawyer' THEN 1 "
            "WHEN 'head' THEN 2 WHEN 'accountant' THEN 3 WHEN 'admin' THEN 5 ELSE 4 END")


@app.route("/permissions", methods=["GET", "POST"])
@require_cap("manage_permissions")
def permissions():
    roles = db.query(f"SELECT code, name, is_builtin FROM roles ORDER BY {role_order()}")
    caps = db.query("SELECT code, name FROM capabilities ORDER BY ord")
    if request.method == "POST":
        for cap in caps:
            for role in roles:
                if role["code"] == "admin":
                    continue  # админ всегда может всё
                key = f"{role['code']}__{cap['code']}"
                if key not in request.form:
                    continue  # не трогаем поля, которых нет в форме
                level = request.form.get(key, "none")
                if level not in dict(LEVELS):
                    level = "none"
                db.execute(
                    "INSERT INTO role_permissions(role_code, cap_code, level) VALUES(%s,%s,%s) "
                    "ON CONFLICT (role_code, cap_code) DO UPDATE SET level=EXCLUDED.level",
                    (role["code"], cap["code"], level))
        audit("permissions", "role_permissions", 0)
        flash("Права сохранены")
        return redirect(url_for("permissions"))
    matrix = {}
    for r in db.query("SELECT role_code, cap_code, level FROM role_permissions"):
        matrix[(r["role_code"], r["cap_code"])] = r["level"]
    return render_template("permissions.html", roles=roles, caps=caps,
                           matrix=matrix, levels=LEVELS)


@app.route("/roles")
@require_cap("manage_permissions")
def roles_page():
    rows = db.query(f"""
        SELECT r.code, r.name, r.description, r.is_builtin,
               (SELECT count(*) FROM users u WHERE u.role_code=r.code) AS users
        FROM roles r ORDER BY {role_order()}""")
    return render_template("roles.html", rows=rows)


@app.route("/roles/new", methods=["POST"])
@require_cap("manage_permissions")
def role_new():
    import re
    name = (request.form.get("name") or "").strip()
    desc = (request.form.get("description") or "").strip()
    code = (request.form.get("code") or "").strip().lower()
    if not code:
        code = re.sub(r"[^a-z0-9_]", "", (name.lower().replace(" ", "_")))[:20] or None
    if not name or not code:
        flash("Укажите название роли")
    elif db.query_one("SELECT 1 FROM roles WHERE code=%s", (code,)):
        flash("Роль с таким кодом уже есть")
    else:
        db.execute("INSERT INTO roles(code, name, description, is_builtin) VALUES(%s,%s,%s,false)",
                   (code, name, desc))
        # новой роли — всё запрещено по умолчанию
        for cap in db.query("SELECT code FROM capabilities"):
            db.execute("INSERT INTO role_permissions(role_code, cap_code, level) VALUES(%s,%s,'none') "
                       "ON CONFLICT DO NOTHING", (code, cap["code"]))
        flash(f"Роль «{name}» создана — настройте её права в матрице")
    return redirect(url_for("roles_page"))


@app.route("/roles/<code>/edit", methods=["POST"])
@require_cap("manage_permissions")
def role_edit(code):
    name = (request.form.get("name") or "").strip()
    desc = (request.form.get("description") or "").strip()
    if name:
        db.execute("UPDATE roles SET name=%s, description=%s WHERE code=%s", (name, desc, code))
        flash("Роль обновлена")
    return redirect(url_for("roles_page"))


@app.route("/roles/<code>/delete", methods=["POST"])
@require_cap("manage_permissions")
def role_delete(code):
    r = db.query_one("SELECT is_builtin FROM roles WHERE code=%s", (code,))
    n = db.query_one("SELECT count(*) AS n FROM users WHERE role_code=%s", (code,))["n"]
    if not r or r["is_builtin"]:
        flash("Встроенную роль удалить нельзя")
    elif n:
        flash(f"Нельзя удалить: роль назначена {n} пользователям")
    else:
        db.execute("DELETE FROM roles WHERE code=%s", (code,))
        flash("Роль удалена")
    return redirect(url_for("roles_page"))


# ------------------------------------------------------------------
#  Служебное
# ------------------------------------------------------------------

@app.route("/account/password", methods=["GET", "POST"])
@login_required
def change_password():
    if request.method == "POST":
        old = request.form.get("old") or ""
        new = request.form.get("new") or ""
        new2 = request.form.get("new2") or ""
        row = db.query_one("SELECT password_hash FROM users WHERE id=%s", (g.user["id"],))
        forced = g.user["must_change_password"]
        if not forced and not check_password_hash(row["password_hash"], old):
            flash("Текущий пароль неверный")
        elif len(new) < 8:
            flash("Новый пароль слишком короткий — минимум 8 символов")
        elif new != new2:
            flash("Пароли не совпадают")
        else:
            db.execute("UPDATE users SET password_hash=%s, must_change_password=false WHERE id=%s",
                       (generate_password_hash(new), g.user["id"]))
            flash("Пароль изменён")
            return redirect(url_for("index"))
    return render_template("password.html", forced=g.user["must_change_password"])


@app.route("/users/new", methods=["GET", "POST"])
@require_cap("manage_users")
def user_new():
    depts = db.query("SELECT id, name FROM departments ORDER BY name")
    roles = db.query("SELECT code, name FROM roles ORDER BY name")
    if request.method == "POST":
        login_name = (request.form.get("login") or "").strip()
        full_name = (request.form.get("full_name") or "").strip()
        role = request.form.get("role") or "manager"
        dept = request.form.get("department") or None
        if not login_name or not full_name:
            flash("Заполните имя и логин")
        elif db.query_one("SELECT 1 FROM users WHERE login=%s", (login_name,)):
            flash("Такой логин уже есть")
        else:
            import secrets, string
            pw = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(10))
            db.execute(
                "INSERT INTO users(login, full_name, role_code, department_id, password_hash, "
                "must_change_password, is_active) VALUES(%s,%s,%s,%s,%s,true,true)",
                (login_name, full_name, role, dept or None, generate_password_hash(pw)))
            return render_template("user_created.html", login=login_name, password=pw)
    return render_template("user_new.html", depts=depts, roles=roles)


@app.route("/users/<int:uid>/toggle", methods=["POST"])
@require_cap("manage_users")
def user_toggle(uid):
    if uid != g.user["id"]:
        db.execute("UPDATE users SET is_active = NOT is_active WHERE id=%s", (uid,))
    return redirect(url_for("users"))


@app.route("/contract/new", methods=["GET", "POST"])
@login_required
def contract_new():
    if not can("create_contract"):
        abort(403)
    types = db.query("SELECT code, name, default_advance_pct, warranty_months FROM contract_types ORDER BY name")
    cps = db.query("SELECT id, name FROM counterparties ORDER BY name")
    if request.method == "POST":
        type_code = request.form.get("type_code")
        cp_id = request.form.get("counterparty_id") or None
        cp_new = (request.form.get("counterparty_new") or "").strip()
        cp_kind = request.form.get("cp_kind") or "commercial"
        address = (request.form.get("address") or "").strip()
        subject = (request.form.get("subject") or "").strip()
        amount = request.form.get("amount") or None
        advance_pct = request.form.get("advance_pct") or None
        warranty = request.form.get("warranty_months") or None
        has_penalty = True if request.form.get("has_penalty") == "on" else False

        if not type_code or not subject:
            flash("Укажите тип и предмет договора")
        else:
            if cp_new and not cp_id:
                cp_id = db.query_one(
                    "INSERT INTO counterparties(name, kind) VALUES(%s,%s) RETURNING id",
                    (cp_new, cp_kind))["id"]
            obj_id = None
            if address:
                obj = db.query_one("SELECT id FROM objects WHERE address=%s", (address,))
                obj_id = obj["id"] if obj else db.query_one(
                    "INSERT INTO objects(address) VALUES(%s) RETURNING id", (address,))["id"]
            advance_amount = None
            if amount and advance_pct:
                advance_amount = round(float(amount) * float(advance_pct) / 100, 2)
            num = next_number()
            cid = db.query_one("""
                INSERT INTO contracts(number_text, type_code, counterparty_id, object_id,
                    subject, amount, advance_pct, advance_amount, warranty_months,
                    has_penalty, stage, created_by)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'draft',%s) RETURNING id""",
                (num, type_code, cp_id, obj_id, subject, amount or None, advance_pct or None,
                 advance_amount, warranty or None, has_penalty, g.user["id"]))["id"]
            n = review_engine.run(cid)
            audit("create", "contract", cid, after={"number": num})
            flash(f"Договор {num} создан. Проверка нашла замечаний: {n}")
            return redirect(url_for("contract", cid=cid))
    return render_template("contract_new.html", types=types, cps=cps, next_num=next_number())


@app.route("/contract/<int:cid>/review", methods=["POST"])
@login_required
def contract_review(cid):
    n = review_engine.run(cid)
    audit("review", "contract", cid, after={"added": n})
    flash(f"Проверка добавила замечаний: {n}" if n else "Проверка завершена, новых замечаний нет")
    return redirect(url_for("contract", cid=cid))


STAGES = ["draft", "internal_review", "legal_review", "at_counterparty",
          "in_progress", "completed", "warranty", "archived", "cancelled", "on_hold"]


@app.route("/rules-catalog", methods=["GET", "POST"])
@require_cap("manage_permissions")
def rules_catalog():
    if request.method == "POST":
        all_rules = db.query("SELECT code FROM review_rules")
        for r in all_rules:
            on = request.form.get("rule_" + r["code"]) == "on"
            db.execute("UPDATE review_rules SET enabled=%s WHERE code=%s", (on, r["code"]))
        audit("rules", "review_rules", 0)
        flash("Набор правил сохранён")
        return redirect(url_for("rules_catalog"))
    rules = db.query("SELECT code, name, description, severity, category, needs_text, enabled "
                     "FROM review_rules ORDER BY ord")
    groups = {}
    for r in rules:
        groups.setdefault(r["category"], []).append(r)
    return render_template("rules_catalog.html", groups=groups)


@app.route("/contract/<int:cid>/ai_review", methods=["POST"])
@login_required
def contract_ai_review(cid):
    if not can("review_decide") and g.user["role_code"] != "admin":
        abort(403)
    # ИИ-проверка требует: текст договора в системе + ключ API + согласие.
    # Пока не настроена — честно сообщаем и не отправляем ничего наружу.
    if not os.getenv("AI_API_KEY"):
        flash("Умная проверка ИИ ещё не подключена: нужен ключ API и распознанный "
              "текст договора. Для секретных договоров она будет отключена.")
        return redirect(url_for("contract", cid=cid))
    flash("Умная проверка ИИ пока в разработке")
    return redirect(url_for("contract", cid=cid))


@app.route("/contract/<int:cid>/stage", methods=["POST"])
@login_required
def contract_stage(cid):
    if not can("change_stage"):
        abort(403)
    new_stage = request.form.get("stage")
    if new_stage not in STAGES:
        abort(400)
    old = db.query_one("SELECT stage FROM contracts WHERE id=%s", (cid,))
    if old and old["stage"] == new_stage:
        flash("Стадия не изменилась")
        return redirect(url_for("contract", cid=cid))
    db.execute("UPDATE contracts SET stage=%s, stage_since=now(), updated_at=now() WHERE id=%s",
               (new_stage, cid))
    audit("stage", "contract", cid, before=old,
          after={"stage": new_stage, "from": STAGE_RU.get(old["stage"] if old else "", "")})
    flash(f"Стадия изменена: {STAGE_RU.get(new_stage, new_stage)}")
    return redirect(url_for("contract", cid=cid))


@app.route("/contract/<int:cid>/edit", methods=["GET", "POST"])
@login_required
def contract_edit(cid):
    if not can("edit_contract"):
        abort(403)
    c = db.query_one("SELECT * FROM contracts WHERE id=%s", (cid,))
    if not c:
        abort(404)
    types = db.query("SELECT code, name FROM contract_types ORDER BY name")
    if request.method == "POST":
        f = request.form
        amount = f.get("amount") or None
        adv = f.get("advance_pct") or None
        advance_amount = round(float(amount) * float(adv) / 100, 2) if (amount and adv) else None
        db.execute("""
            UPDATE contracts SET subject=%s, amount=%s, advance_pct=%s, advance_amount=%s,
                warranty_months=%s, commissioning_date=%s, signed_on=%s,
                has_penalty=%s, updated_at=now() WHERE id=%s""",
            (f.get("subject"), amount, adv, advance_amount,
             f.get("warranty_months") or None, f.get("commissioning_date") or None,
             f.get("signed_on") or None, f.get("has_penalty") == "on", cid))
        audit("edit", "contract", cid)
        flash("Договор сохранён")
        return redirect(url_for("contract", cid=cid))
    return render_template("contract_edit.html", c=c, types=types)


@app.route("/contract/<int:cid>/execution", methods=["POST"])
@login_required
def contract_execution(cid):
    if not can("execution"):
        abort(403)
    comm = request.form.get("commissioning_date") or None
    comp = request.form.get("completed_on") or None
    db.execute(
        "UPDATE contracts SET commissioning_date=%s, completed_on=%s, updated_at=now() WHERE id=%s",
        (comm, comp, cid))
    audit("execution", "contract", cid,
          after={"commissioning": comm, "completed": comp})
    flash("Даты исполнения сохранены")
    return redirect(url_for("contract", cid=cid))


@app.route("/contract/<int:cid>/work-stage", methods=["POST"])
@login_required
def work_stage_add(cid):
    if not can("execution") and not can("edit_contract"):
        abort(403)
    name = (request.form.get("name") or "").strip()
    volume = (request.form.get("volume") or "").strip() or None
    planned = request.form.get("planned_on") or None
    amount = request.form.get("amount") or None
    if not name:
        flash("Укажите название этапа")
        return redirect(url_for("contract", cid=cid))
    ordn = (db.query_one("SELECT coalesce(max(ord),0)+1 AS o FROM stages WHERE contract_id=%s",
                         (cid,)) or {"o": 1})["o"]
    db.execute("INSERT INTO stages(contract_id, ord, name, volume, planned_on, amount) "
               "VALUES(%s,%s,%s,%s,%s,%s)", (cid, ordn, name, volume, planned, amount))
    audit("stage_add", "contract", cid, after={"name": name})
    flash("Этап работ добавлен")
    return redirect(url_for("contract", cid=cid))


@app.route("/work-stage/<int:sid>/done", methods=["POST"])
@login_required
def work_stage_done(sid):
    if not can("execution") and not can("edit_contract"):
        abort(403)
    s = db.query_one("SELECT contract_id, is_done FROM stages WHERE id=%s", (sid,))
    if not s:
        abort(404)
    if s["is_done"]:
        db.execute("UPDATE stages SET is_done=false, actual_on=NULL WHERE id=%s", (sid,))
    else:
        actual = request.form.get("actual_on") or None
        db.execute("UPDATE stages SET is_done=true, actual_on=coalesce(%s, current_date) WHERE id=%s",
                   (actual, sid))
    audit("stage_done", "contract", s["contract_id"], after={"stage": sid})
    return redirect(url_for("contract", cid=s["contract_id"]))


@app.route("/work-stage/<int:sid>/delete", methods=["POST"])
@login_required
def work_stage_delete(sid):
    if not can("execution") and not can("edit_contract"):
        abort(403)
    s = db.query_one("SELECT contract_id FROM stages WHERE id=%s", (sid,))
    if s:
        db.execute("DELETE FROM stages WHERE id=%s", (sid,))
        audit("stage_del", "contract", s["contract_id"])
        return redirect(url_for("contract", cid=s["contract_id"]))
    abort(404)


@app.route("/finding/<int:fid>/resolve", methods=["POST"])
@login_required
def finding_resolve(fid):
    if not can("review_decide"):
        abort(403)
    decision = request.form.get("decision")  # accepted | rejected | reopen
    f = db.query_one("SELECT contract_id FROM review_findings WHERE id=%s", (fid,))
    if not f:
        abort(404)
    if decision == "reopen":
        db.execute("UPDATE review_findings SET resolution=NULL, resolved_by=NULL, "
                   "resolved_at=NULL WHERE id=%s", (fid,))
    elif decision in ("accepted", "rejected"):
        db.execute("UPDATE review_findings SET resolution=%s, resolved_by=%s, resolved_at=now() "
                   "WHERE id=%s", (decision, g.user["id"], fid))
    audit("finding", "review_finding", fid, after={"decision": decision})
    return redirect(url_for("contract", cid=f["contract_id"]))


@app.route("/search")
@login_required
def search():
    q = (request.args.get("q") or "").strip()
    rows = []
    if q:
        like = f"%{q}%"
        rows = db.query("""
            SELECT c.id, c.number_text, c.subject, c.amount, c.stage, c.signed_on,
                   cp.name AS counterparty, o.address AS object,
                   (SELECT count(*) FROM review_findings rf
                     WHERE rf.contract_id=c.id AND rf.resolution IS NULL) AS findings
            FROM contracts c
            LEFT JOIN counterparties cp ON cp.id=c.counterparty_id
            LEFT JOIN objects o ON o.id=c.object_id
            WHERE c.number_text ILIKE %s OR c.subject ILIKE %s
               OR cp.name ILIKE %s OR o.address ILIKE %s
               OR c.external_number ILIKE %s
            ORDER BY c.number_text DESC LIMIT 100
        """, (like, like, like, like, like))
    return render_template("search.html", q=q, rows=rows)


@app.route("/contract/<int:cid>/upload", methods=["POST"])
@login_required
def contract_upload(cid):
    if not can("manage_files"):
        abort(403)
    c = db.query_one("SELECT id FROM contracts WHERE id=%s", (cid,))
    if not c:
        abort(404)
    f = request.files.get("file")
    kind = request.form.get("kind", "other")
    if kind not in FILE_KIND_RU:
        kind = "other"
    if not f or not f.filename:
        flash("Файл не выбран")
        return redirect(url_for("contract", cid=cid))
    data = f.read()
    if not data:
        flash("Пустой файл")
        return redirect(url_for("contract", cid=cid))
    sha = hashlib.sha256(data).hexdigest()
    # уже загружали ровно этот файл?
    dup = db.query_one("SELECT id FROM contract_files WHERE contract_id=%s AND sha256=%s",
                       (cid, sha))
    if dup:
        flash("Такой файл уже загружен (совпадает содержимое)")
        return redirect(url_for("contract", cid=cid))
    ext = os.path.splitext(f.filename)[1][:12]
    disk = uuid.uuid4().hex + ext
    folder = os.path.join(FILES_DIR, str(cid))
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, disk), "wb") as out:
        out.write(data)
    ver = (db.query_one(
        "SELECT coalesce(max(version),0)+1 AS v FROM contract_files WHERE contract_id=%s AND kind=%s",
        (cid, kind)) or {"v": 1})["v"]
    fid = db.query_one(
        "INSERT INTO contract_files(contract_id, version, kind, file_name, file_path, "
        "size_bytes, sha256, uploaded_by) VALUES(%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
        (cid, ver, kind, f.filename, os.path.join(folder, disk), len(data), sha,
         g.user["id"]))["id"]
    audit("upload", "contract", cid,
          after={"file": f.filename, "kind": kind, "version": ver})
    flash(f"Файл загружен: {f.filename} (версия {ver})")
    return redirect(url_for("contract", cid=cid))


@app.route("/file/<int:fid>")
@login_required
def file_download(fid):
    if perm_level("view_contracts") == "none":
        abort(403)
    row = db.query_one(
        "SELECT file_name, file_path, contract_id FROM contract_files WHERE id=%s", (fid,))
    if not row or not os.path.exists(row["file_path"]):
        abort(404)
    audit("download", "contract", row["contract_id"], after={"file": row["file_name"]})
    return send_file(row["file_path"], as_attachment=True, download_name=row["file_name"])


@app.route("/contract/<int:cid>/history")
@login_required
def contract_history(cid):
    rows = db.query("""
        SELECT a.at, a.action, a.before, a.after, u.full_name
        FROM audit_log a LEFT JOIN users u ON u.id=a.user_id
        WHERE a.entity='contract' AND a.entity_id=%s
        ORDER BY a.at DESC LIMIT 100
    """, (cid,))
    c = db.query_one("SELECT number_text FROM contracts WHERE id=%s", (cid,))
    return render_template("history.html", rows=rows, c=c, cid=cid)


@app.route("/health")
def health():
    try:
        n = db.query_one("SELECT count(*) AS n FROM contracts")
        return {"ok": True, "contracts": n["n"]}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}, 500


@app.route("/admin/reload")
def admin_reload():
    """Перезапуск процесса для подхвата нового кода. Батник поднимет заново."""
    if request.args.get("token") != os.getenv("RELOAD_TOKEN"):
        abort(403)
    threading.Timer(0.4, lambda: os._exit(0)).start()
    return "reloading"


@app.errorhandler(403)
def forbidden(e):
    return render_template("error.html", code=403, msg="Нет доступа"), 403


@app.errorhandler(404)
def notfound(e):
    return render_template("error.html", code=404, msg="Не найдено"), 404


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8080, debug=True)
