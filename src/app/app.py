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

from flask import (
    Flask, session, request, redirect, url_for, render_template, abort, flash, g
)
from werkzeug.security import check_password_hash, generate_password_hash

import db
import review_engine

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY") or secrets.token_hex(32)

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

app.jinja_env.globals.update(STAGE_RU=STAGE_RU, SEV_RU=SEV_RU, KIND_RU=KIND_RU)


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
        # заставляем сменить временный пароль до входа в остальные разделы
        if g.user and g.user["must_change_password"] and request.endpoint not in (
                "change_password", "logout", "static"):
            return redirect(url_for("change_password"))


CAN_CREATE = ("manager", "lawyer", "admin")


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
        SELECT c.id, c.number_text, c.subject, c.amount, c.stage,
               cp.name AS counterparty,
               (SELECT count(*) FROM review_findings rf
                 WHERE rf.contract_id=c.id AND rf.resolution IS NULL
                   AND rf.severity='critical') AS crit
        FROM contracts c LEFT JOIN counterparties cp ON cp.id=c.counterparty_id
        ORDER BY c.amount DESC NULLS LAST
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
    return render_template("contract.html", c=c, findings=findings)


# ------------------------------------------------------------------
#  Контрагенты
# ------------------------------------------------------------------

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
@login_required
def users():
    rows = db.query("""
        SELECT u.id, u.login, u.full_name, u.role_code, r.name AS role_name,
               d.name AS dept, u.is_active, u.last_login_at
        FROM users u JOIN roles r ON r.code=u.role_code
        LEFT JOIN departments d ON d.id=u.department_id
        ORDER BY u.id
    """)
    return render_template("users.html", rows=rows)


@app.route("/permissions")
@login_required
def permissions():
    roles = db.query("SELECT code, name, description FROM roles ORDER BY "
                     "CASE code WHEN 'manager' THEN 0 WHEN 'lawyer' THEN 1 "
                     "WHEN 'head' THEN 2 WHEN 'accountant' THEN 3 ELSE 4 END")
    return render_template("permissions.html", roles=roles)


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
@login_required
@admin_required
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
@login_required
@admin_required
def user_toggle(uid):
    if uid != g.user["id"]:
        db.execute("UPDATE users SET is_active = NOT is_active WHERE id=%s", (uid,))
    return redirect(url_for("users"))


@app.route("/contract/new", methods=["GET", "POST"])
@login_required
def contract_new():
    if g.user["role_code"] not in CAN_CREATE:
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
            flash(f"Договор {num} создан. Проверка нашла замечаний: {n}")
            return redirect(url_for("contract", cid=cid))
    return render_template("contract_new.html", types=types, cps=cps, next_num=next_number())


@app.route("/contract/<int:cid>/review", methods=["POST"])
@login_required
def contract_review(cid):
    n = review_engine.run(cid)
    if n:
        flash(f"Проверка добавила замечаний: {n}")
    else:
        flash("Проверка завершена, новых замечаний нет")
    return redirect(url_for("contract", cid=cid))


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
