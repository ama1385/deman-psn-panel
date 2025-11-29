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


# =========
# الصفحة الرئيسية + صفحة الداشبورد
# =========

@app.route("/")
def index():
    """
    نخلي الصفحة الرئيسية تحوّل مباشرة للوحة DEMAN Panel،
    والواجهة نفسها (panel.html) فيها شاشة الدخول + الداشبورد.
    """
    return redirect(url_for("panel"))


@app.route("/panel")
def panel():
    """
    هذه هي صفحة الداشبورد الجديدة اللي أرسلت HTML حقها.
    لازم تكون محفوظة باسم templates/panel.html
    """
    return render_template("panel.html")


# =========================
# صفحة فحص حساب PSN (نسخة HTML قديمة – اختياري)
# =========================
@app.route("/tools/psn-check", methods=["GET", "POST"])
def psn_check():
    # لو تبي تمنع الاستخدام بدون تسجيل دخول:
    if not session.get("logged_in"):
        return redirect(url_for("panel"))

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


# ------------- وظائف مساعدة -------------
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
        masked_email=mask_email(email),
    )


@app.route("/api/verify-code", methods=["POST"])
def api_verify_code():
    """
    بما إنك مفعّل الدخول المباشر (skip_code=True)
    الواجهة ما راح تستدعي هذا المسار غالباً، بس نخليه يرجع رسالة واضحة.
    """
    return jsonify(ok=False, message="تم تفعيل الدخول المباشر بدون كود تحقق."), 400


# =====================
# API لتحليل حساب PSN (تتعامل معها الواجهة JS /api/psn-analyze)
# =====================
@app.route("/api/psn-analyze", methods=["POST"])
def api_psn_analyze():
    """
    تستقبل Online ID وترجع تقرير PSN كـ JSON + نص جاهز (message) للعرض في التكست إيريا.
    """
    if not session.get("logged_in"):
        return jsonify(ok=False, message="يجب تسجيل الدخول أولاً."), 401

    data = request.get_json(silent=True) or {}

    # نجرب كل الأسماء المحتملة للحقل + الفورم
    online_id = (
        (data.get("online_id")
         or data.get("onlineId")
         or data.get("psn_id")
         or data.get("psnId")
         or request.form.get("online_id")
         or request.form.get("onlineId")
         or request.form.get("psn_id")
         or request.form.get("psnId")
         or "")
        .strip()
    )

    if not online_id:
        return jsonify(ok=False, message="رجاءً اكتب Online ID."), 400

    if not DEMANTEAM_NPSSO or len(DEMANTEAM_NPSSO) < 40:
        return jsonify(ok=False, message="NPSSO غير مضبوط أو غير صالح في الباك إند."), 500

    try:
        logger.info("API /api/psn-analyze for online_id=%s", online_id)
        report = get_account_report(online_id, DEMANTEAM_NPSSO)

        if not isinstance(report, dict):
            return jsonify(ok=False, message="تعذر قراءة بيانات التقرير."), 500

        # لو psn_service رجع ok=False (حساب غير موجود، رفض وصول، إلخ)
        if not report.get("ok", True):
            return jsonify(report), 400

        # ===== تجهيز القيم اللي بنعرضها =====
        region_pretty = report.get("region_pretty") or "N/A"
        presence = report.get("presence") or "N/A"
        trophy_summary = report.get("trophy_summary") or "N/A"
        titles_count = report.get("titles_count")
        friends_total = report.get("friends_total")
        friends_online_est = report.get("friends_online_est")
        avatar_url = report.get("avatar_url") or "N/A"

        # القيم التحليلية الجديدة
        value_score = report.get("value_score")
        value_segment = report.get("value_segment") or "غير محدد"
        activity_segment = report.get("activity_segment") or "غير محدد"
        risk_level = report.get("risk_level") or "غير محدد"
        risk_flags = report.get("risk_flags") or []

        # تفاصيل التروفيات كأرقام
        trophies = report.get("trophies") or {}
        lvl = trophies.get("level")
        pt = trophies.get("platinum")
        gd = trophies.get("gold")
        sv = trophies.get("silver")
        br = trophies.get("bronze")
        total_trophies = trophies.get("total")

        current_title = report.get("current_title") or "لا توجد لعبة حالية أو مخفية"

        # ===== تنسيق نص جاهز ومفيد للتقرير =====

        # تنظيف عرض المنطقة (لو طلع رقم غريب)
        region_display_raw = region_pretty or report.get("region") or "غير محددة"
        if any(ch.isdigit() for ch in region_display_raw) and len(region_display_raw) > 6:
            region_display = "غير محددة (مشكلة في قراءة المنطقة من سوني)"
        else:
            region_display = region_display_raw

        # تطبيع الحقول اللي تطلع unknown / None
        presence_display = presence
        if not presence_display or presence_display == "unknown":
            presence_display = "غير ظاهر (غالبًا مخفي/أوفلاين)"

        activity_display = activity_segment or "غير محدد"
        value_display = value_segment or "غير محدد"
        risk_display = risk_level or "غير محدد"

        # جملة ملخص سريعة
        header_line = f"القيمة: {value_display} | النشاط: {activity_display} | المخاطر: {risk_display}"

        # هل الحساب يستاهل التعب؟ (تقدير عام)
        if "عالي" in value_display:
            worth_line = "التقييم: الحساب يستاهل تعب الاسترجاع، اعتبره من الفئة القوية."
        elif "متوسط" in value_display:
            worth_line = "التقييم: حساب متوسط، مناسب لعروض سعر متوسطة، مو نادر ولا ضعيف."
        else:
            worth_line = "التقييم: حساب قيمته ضعيفة، لا تبالغ مع العميل في الوعود أو السعر."

        # ملاحظات إضافية بناءً على النشاط والمخاطر
        notes_lines = []
        if "ضعيف" in activity_display or "جديد" in activity_display:
            notes_lines.append("⚠ النشاط ضعيف/جديد: احتمال يرجع بسهولة لكن ما يعطيك تاريخ طويل أو تروفيز قوية.")
        if "عالي" in risk_display or "مرتفع" in risk_display:
            notes_lines.append("⚠ مخاطر عالية: انتبه قبل ما تعِد بنسبة نجاح كبيرة أو تربط ضمان قوي.")
        if "منخفض" in risk_display:
            notes_lines.append("✅ المخاطر منخفضة: الحساب آمن نسبيًا من ناحية باند/مشاكل ظاهرة.")

        if not notes_lines:
            notes_lines.append("لا توجد ملاحظات تحليلية إضافية مهمة من ناحية النشاط/المخاطر.")

        # تجهيز نص التروفيز
        lvl_display = lvl if lvl is not None else "غير متوفر (سوني ما رجعت المستوى)"
        total_display = total_trophies if total_trophies is not None else "غير متوفر (بيانات ناقصة)"
        pt_display = pt if pt is not None else 0
        gd_display = gd if gd is not None else 0
        sv_display = sv if sv is not None else 0
        br_display = br if br is not None else 0

        titles_display = titles_count if titles_count is not None else "غير متوفر"
        friends_total_display = friends_total if friends_total is not None else "غير متوفر (قائمة أصدقاء خاصة؟)"
        friends_online_display = friends_online_est if friends_online_est is not None else "غير متوفر"

        lines = [
            "🔰 تقرير مختصر لحساب PSN - فريق DEMAN",
            "------------------------------------",
            f"الأيدي: {report.get('online_id', online_id)}",
            f"المنطقة (Region): {region_display}",
            "",
            f"ملخص سريع: {header_line}",
            worth_line,
            "",
            "🔹 حالة الحساب الآن:",
            f"- الحالة الحالية: {presence_display}",
            f"- اللعبة الحالية: {current_title}",
            "",
            "🔹 الأرقام الأساسية:",
            f"- عدد الألعاب (Trophy Titles): {titles_display}",
            f"- عدد الأصدقاء الكلي: {friends_total_display}",
            f"- أصدقاء أونلاين (تقديري): {friends_online_display}",
            "",
            "🔹 التروفيز (إن توفرت بياناتها):",
            f"- الملخص: {trophy_summary}",
            f"- المستوى (Level): {lvl_display}",
            f"- إجمالي التروفيز: {total_display}",
            f"- بلاتينيوم: {pt_display}",
            f"- ذهبي: {gd_display}",
            f"- فضي: {sv_display}",
            f"- برونزي: {br_display}",
            "",
            "🔹 تقييم القيمة والنشاط والمخاطر:",
            f"- القيمة التقديرية: {value_display}",
            f"- نشاط الحساب: {activity_display}",
            f"- مستوى المخاطر: {risk_display}",
            "",
            "ملاحظات الفريق على هذا الحساب:",
        ]

        lines.extend(notes_lines)

        # ملاحظات المخاطر التفصيلية إن وجدت
        if risk_flags:
            lines.append("")
            lines.append("🔹 تفاصيل إضافية عن المخاطر:")
            for flag in risk_flags:
                lines.append(f"  • {flag}")

        lines.extend(
            [
                "",
                "🔹 رابط صورة الأفاتار (للاستخدام مع العميل أو للأرشفة):",
                avatar_url or "N/A",
            ]
        )

        text_summary = "\n".join(lines)

        # نضيف النص داخل نفس الرد عشان الواجهة تستخدمه
        report["message"] = text_summary
        report["ok"] = True

        return jsonify(report), 200

    except Exception:
        logger.exception("Error in /api/psn-analyze")
        return jsonify(
            ok=False,
            message="حدث خطأ غير متوقع أثناء تحليل الحساب."
        ), 500



# =====================
# API لتسجيل الخروج
# =====================
@app.route("/api/logout", methods=["POST"])
def api_logout():
    user_email = session.get("user_email")
    session.clear()
    resp = make_response(jsonify(ok=True))
    resp.set_cookie("trusted_device_email", "", max_age=0)
    logger.info("Logout for %s", user_email)
    return resp


# =====================
# نقطة تشغيل التطبيق
# =====================
if __name__ == "__main__":
    debug_mode = os.getenv("FLASK_DEBUG", "false").lower() == "true"
    port = int(os.getenv("PORT", "8000"))
    app.run(host="0.0.0.0", port=port, debug=debug_mode)

