# app.py
import os
import logging
import random
import string
import smtplib
import ssl
from datetime import timedelta
from email.mime.text import MIMEText

from flask import (
    Flask,
    request,
    jsonify,
    session,
    make_response,
    render_template,
    redirect,
    url_for,
)

from psn_service import get_account_report  # دالة فحص حساب PSN (باستخدام NPSSO الفريق)

# =============================
# إعداد اللوقنغ
# =============================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("deman-psn-panel")

# -----------------------------
# إعداد تطبيق Flask
# -----------------------------
app = Flask(
    __name__,
    template_folder="templates",
    static_folder="static",
)

# =============================
# إعداد القيم الثابتة (بدون .env)
# =============================

# NPSSO الخاص بفريق DEMAN
DEMANTEAM_NPSSO = "emiRdjV5igMsmrfukmJpzluip8ucmSNwiin5aiJCQ1Z33bq6WR2eiJZPt0ttrWtr"

# سر الجلسة
app.secret_key = "qqww1122asd"

# مدة الجلسة
app.permanent_session_lifetime = timedelta(days=7)

# إعدادات SMTP (إيميل جنى)
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_USER = "jana123216@gmail.com"
SMTP_PASSWORD = "jror yolk axwd sufc"  # app password

# تشغيل/إيقاف الإرسال الحقيقي للإيميل
USE_SMTP = False  # خله False الآن عشان ما يسبب مشاكل على Render
SMTP_TIMEOUT = 10.0

# الموظفين المصرّح لهم بالدخول
EMPLOYEES = {
    "jana123216@gmail.com": {
        "name": "Jana",
        "password": "1234",
    },
    "khl.lhk901@gmail.com": {
        "name": "AbdulFattah",
        "password": "qqww1122asd",
    },
    # زِد موظفينك هنا...
}


# =========================
# صفحة فحص حساب PSN بالفريق (نسخة HTML تقليدية)
# =========================
@app.route("/tools/psn-check", methods=["GET", "POST"])
def psn_check():
    if not session.get("logged_in"):
        return redirect(url_for("index"))

    report = None
    error = None

    if request.method == "POST":
        online_id = (request.form.get("online_id") or "").strip()
        if not online_id:
            error = "رجاءً اكتب Online ID."
        else:
            try:
                if not DEMANTEAM_NPSSO or len(DEMANTEAM_NPSSO) < 40:
                    raise RuntimeError("NPSSO الخاص بالفريق غير مضبوط أو غير صالح.")

                logger.info("Request PSN report (HTML) for online_id=%s", online_id)
                data = get_account_report(online_id, DEMANTEAM_NPSSO)

                if not isinstance(data, dict):
                    error = "تعذر قراءة بيانات التقرير."
                elif not data.get("ok", True):
                    error = data.get("message", "تعذر تحليل الحساب.")
                else:
                    report = data
            except Exception:
                logger.exception("Error while generating PSN report (HTML)")
                error = "حدث خطأ غير متوقع أثناء تحليل الحساب."

    return render_template("tools_psn_check.html", report=report, error=error)


# =========
# الصفحة الرئيسية
# =========
@app.route("/")
def index():
    return render_template("index.html")


# -------------
# وظائف مساعدة
# -------------
def generate_code(length: int = 6) -> str:
    return "".join(random.choices(string.digits, k=length))


def mask_email(email: str) -> str:
    try:
        local, domain = email.split("@")
        if len(local) <= 2:
            masked_local = local[0] + "***"
        else:
            masked_local = local[0] + "***" + local[-1]
        return f"{masked_local}@{domain}"
    except Exception:
        return email


def send_email_code(to_email: str, code: str, employee_name: str) -> None:
    """
    إرسال كود التحقق على إيميل الموظف.
    إذا USE_SMTP=False → ما يرسل فعليًا، بس يطبع في اللوق.
    """
    if not USE_SMTP:
        logger.warning(
            "[LOGIN CODE] SMTP معطّل (USE_SMTP=False) — الكود %s للبريد %s (الموظف: %s)",
            code,
            to_email,
            employee_name,
        )
        return

    subject = "رمز الدخول إلى لوحة DEMAN"
    body = f"""
يا {employee_name}،

رمز الدخول الخاص بك هو:

{code}

الرجاء عدم مشاركته مع أي شخص.

فريق DEMAN.STORE
    """.strip()

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = SMTP_USER
    msg["To"] = to_email

    context = ssl.create_default_context()
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=SMTP_TIMEOUT) as server:
        server.starttls(context=context)
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.send_message(msg)
    logger.info("Login code sent to %s", to_email)


# =====================
# APIs لتسجيل الدخول
# =====================
@app.route("/api/login", methods=["POST"])
def api_login():
    data = request.get_json() or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    emp = EMPLOYEES.get(email)
    if not emp or emp.get("password") != password:
        logger.warning("Failed login attempt for email=%s", email)
        return jsonify(ok=False, message="بريد أو كلمة مرور غير صحيحة."), 401

    # 🔥 دخول مباشر بدون SMTP ولا كود تحقق
    session.permanent = True
    session["logged_in"] = True
    session["user_email"] = email
    session["user_name"] = emp["name"]

    logger.info("Direct login (no SMTP, no code) for %s", email)

    return jsonify(
        ok=True,
        skip_code=True,   # عشان الواجهة تعرف إن ما فيه خطوة كود
        name=emp["name"],
    )


@app.route("/api/verify-code", methods=["POST"])
def api_verify_code():
    # بما إن الدخول مباشر، نخلي هذه النهاية ترجع رسالة واضحة
    return jsonify(ok=False, message="تم تفعيل الدخول المباشر بدون كود تحقق."), 400


# =====================
# API لتحليل حساب PSN (تتعامل معها الواجهة JS /api/psn-analyze)
# =====================
@app.route("/api/psn-analyze", methods=["POST"])
def api_psn_analyze():
    """
    تستقبل Online ID وترجع تقرير PSN كـ JSON.
    هذي اللي تستخدمها الواجهة الأمامية في الزر "تحليل الحساب الآن".
    """
    if not session.get("logged_in"):
        return jsonify(ok=False, message="يجب تسجيل الدخول أولاً."), 401

    data = request.get_json() or {}
    online_id = (data.get("online_id") or "").strip()

    if not online_id:
        return jsonify(ok=False, message="رجاءً اكتب Online ID."), 400

    if not DEMANTEAM_NPSSO or len(DEMANTEAM_NPSSO) < 40:
        return jsonify(ok=False, message="NPSSO غير مضبوط أو غير صالح في الباك إند."), 500

    try:
        logger.info("API /api/psn-analyze for online_id=%s", online_id)
        report = get_account_report(online_id, DEMANTEAM_NPSSO)

        if not isinstance(report, dict):
            return jsonify(ok=False, message="تعذر قراءة بيانات التقرير."), 500

        # لو الدالة رجعت ok=False نخلي الرسالة تمر كما هي
        if not report.get("ok", True):
            # نخلي الواجهة تشوف الرسالة وتعرضها
            return jsonify(report), 400

        # نجاح: نرجع الدكت كامل
        return jsonify(report), 200

    except Exception:
        logger.exception("Error in /api/psn-analyze")
        return jsonify(
            ok=False,
            message="حدث خطأ غير متوقع أثناء تحليل الحساب."
        ), 500


@app.route("/api/logout", methods=["POST"])
def api_logout():
    user_email = session.get("user_email")
    session.clear()
    resp = make_response(jsonify(ok=True))
    resp.set_cookie("trusted_device_email", "", max_age=0)
    logger.info("Logout for %s", user_email)
    return resp


if __name__ == "__main__":
    debug_mode = os.getenv("FLASK_DEBUG", "false").lower() == "true"
    port = int(os.getenv("PORT", "8000"))
    app.run(host="0.0.0.0", port=port, debug=debug_mode)
