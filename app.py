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

from dotenv import load_dotenv

from psn_service import get_account_report  # دالة فحص حساب PSN (باستخدام NPSSO الفريق)

# =============================
# تحميل المتغيرات من ملف .env
# =============================
load_dotenv()

# -----------------------------
# إعداد اللوقنغ
# -----------------------------
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
    template_folder="templates",  # المجلد اللي فيه index.html و tools_psn_check.html
    static_folder=None           # خليه None حالياً، نضبط static بعدين لو احتجناه
)


# NPSSO الخاص بفريق DEMAN (تحطه في ملف .env)
DEMANTEAM_NPSSO = os.getenv("DEMAN_TEAM_NPSSO")

# سر الجلسة
app.secret_key = os.getenv("FLASK_SECRET_KEY", "CHANGE_ME_TO_RANDOM_SECRET_KEY")
app.permanent_session_lifetime = timedelta(days=7)

# إعدادات SMTP (إيميل جنى)
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", 587))
SMTP_USER = os.getenv("SMTP_USER", "jana123216@gmail.com")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "CHANGE_ME_SMTP_APP_PASSWORD")

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
# صفحة فحص حساب PSN بالفريق
# =========================
@app.route("/tools/psn-check", methods=["GET", "POST"])
def psn_check():
    # تأكد أن الموظف مسجل دخول
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
                if not DEMANTEAM_NPSSO:
                    raise RuntimeError("NPSSO الخاص بالفريق غير مضبوط في .env")

                logger.info("Request PSN report for online_id=%s", online_id)
                data = get_account_report(online_id, DEMANTEAM_NPSSO)

                if not isinstance(data, dict):
                    error = "تعذر قراءة بيانات التقرير."
                elif not data.get("ok", True):
                    # رسالة واضحة من psn_service
                    error = data.get("message", "تعذر تحليل الحساب.")
                else:
                    report = data
            except Exception as e:
                logger.exception("Error while generating PSN report")
                error = "حدث خطأ غير متوقع أثناء تحليل الحساب."

    return render_template("tools_psn_check.html", report=report, error=error)


# =========
# الصفحة الرئيسية (لوحة DEMAN)
# =========
@app.route("/")
def index():
    # هنا تحط واجهة HTML اللي فيها فورم تسجيل الدخول + أزرار أدوات الفريق
    return render_template("index.html")


# -------------
# وظائف مساعدة
# -------------
def generate_code(length: int = 6) -> str:
    """توليد كود رقمي بسيط للتحقق."""
    return "".join(random.choices(string.digits, k=length))


def mask_email(email: str) -> str:
    """
    إخفاء الإيميل لعرضه في الواجهة.
    مثال: a***@gmail.com
    """
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
    """إرسال كود التحقق على إيميل الموظف."""
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
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls(context=context)
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(msg)
        logger.info("Login code sent to %s", to_email)
    except Exception as e:
        logger.exception("SMTP error while sending code to %s", to_email)
        raise


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

    # نخلي السيشن دائم (يمشي مع app.permanent_session_lifetime)
    session.permanent = True

    # 🔹 فحص Cookie للجهاز الموثوق
    trusted_email = request.cookies.get("trusted_device_email")
    if trusted_email and trusted_email.lower() == email:
        # دخول مباشر بدون كود
        session["logged_in"] = True
        session["user_email"] = email
        session["user_name"] = emp["name"]

        # تنظيف أي محاولات سابقة معلّقة
        session.pop("pending_email", None)
        session.pop("pending_name", None)
        session.pop("pending_code", None)

        logger.info("Trusted-device login for %s", email)
        return jsonify(
            ok=True,
            skip_code=True,
            name=emp["name"],
        )

    # 🔹 ما عنده جهاز موثوق: نرسل كود ونسجّله كـ pending
    code = generate_code()
    session["pending_email"] = email
    session["pending_name"] = emp["name"]
    session["pending_code"] = code

    try:
        send_email_code(email, code, emp["name"])
    except Exception:
        return jsonify(ok=False, message="فشل إرسال الكود على الإيميل."), 500

    return jsonify(
        ok=True,
        masked_email=mask_email(email),
    )


@app.route("/api/verify-code", methods=["POST"])
def api_verify_code():
    data = request.get_json() or {}
    code = (data.get("code") or "").strip()
    remember_device = bool(data.get("remember_device"))

    pending_code = session.get("pending_code")
    pending_email = session.get("pending_email")
    pending_name = session.get("pending_name")

    if not pending_code or not pending_email:
        return jsonify(ok=False, message="لا يوجد طلب تسجيل دخول نشط."), 400

    if code != pending_code:
        logger.warning("Wrong code for email=%s", pending_email)
        return jsonify(ok=False, message="الكود غير صحيح."), 400

    # ✅ الكود صحيح → نسجّل الدخول
    session.permanent = True
    session["logged_in"] = True
    session["user_email"] = pending_email
    session["user_name"] = pending_name

    # تنظيف بيانات المحاولة المؤقتة
    session.pop("pending_code", None)
    session.pop("pending_email", None)
    session.pop("pending_name", None)

    resp = make_response(jsonify(ok=True))

    # لو اختار "تذكّر هذا الجهاز" نحط Cookie
    if remember_device:
        resp.set_cookie(
            "trusted_device_email",
            pending_email,
            max_age=60 * 60 * 24 * 30,  # 30 يوم
            httponly=True,
            samesite="Lax",
        )
        logger.info("Device marked as trusted for %s", pending_email)

    return resp


@app.route("/api/logout", methods=["POST"])
def api_logout():
    user_email = session.get("user_email")
    session.clear()
    resp = make_response(jsonify(ok=True))
    # نلغي Cookie الجهاز الموثوق
    resp.set_cookie("trusted_device_email", "", max_age=0)
    logger.info("Logout for %s", user_email)
    return resp


if __name__ == "__main__":
    debug_mode = os.getenv("FLASK_DEBUG", "false").lower() == "true"
    port = int(os.getenv("PORT", "8000"))  # Railway يعطيك PORT من البيئة
    app.run(host="0.0.0.0", port=port, debug=debug_mode)
