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
from werkzeug.security import check_password_hash

import db

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
