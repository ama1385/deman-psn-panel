# psn_service.py
import time
import re
from collections import OrderedDict
from typing import Any, Dict, Optional, Tuple, List

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from psnawp_api import PSNAWP
from psnawp_api.core.psnawp_exceptions import PSNAWPNotFound, PSNAWPForbidden

# =========================
# ثوابت ومساعدات عامة
# =========================

HEX_RE = re.compile(r"^(?:0x)?[0-9a-fA-F]+$")


# ===== HTTP Session =====
def make_http() -> requests.Session:
    """سيشن HTTP مشترك لو احتجناه لاحقًا في استعلامات خارجية."""
    s = requests.Session()
    retries = Retry(
        total=3,
        backoff_factor=0.6,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET", "HEAD"]),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retries, pool_connections=50, pool_maxsize=50)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    s.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, مثل Gecko) "
                "Chrome/127.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
        }
    )
    return s


_http = make_http()


# ===== Tiny TTL Cache =====
class TTLLRU:
    """كاش بسيط بزمن صلاحية (TTL) لتخفيف الضغط على PSN / psnawp."""

    def __init__(self, maxsize: int = 256, ttl: int = 90):
        self.maxsize = maxsize
        self.ttl = ttl
        self.store: OrderedDict[str, Tuple[float, Any]] = OrderedDict()

    def get(self, k: str):
        now = time.time()
        v = self.store.get(k)
        if not v:
            return None
        ts, data = v
        if now - ts > self.ttl:
            self.store.pop(k, None)
            return None
        self.store.move_to_end(k)
        return data

    def set(self, k: str, val: Any) -> None:
        now = time.time()
        if k in self.store:
            self.store.move_to_end(k)
        self.store[k] = (now, val)
        if len(self.store) > self.maxsize:
            self.store.popitem(last=False)


_cache = TTLLRU(maxsize=256, ttl=90)


# =========================
# Helpers
# =========================

def account_id_to_hex(acc_id: str) -> Optional[str]:
    """تحويل account_id إلى هيكس موحد 0x.... لسهولة الاستخدام."""
    try:
        if HEX_RE.match(acc_id or ""):
            return acc_id if acc_id.startswith("0x") else "0x" + acc_id.lower()
        if str(acc_id).isdigit():
            return "0x" + format(int(acc_id), "x")
    except Exception:
        return None
    return None


def _normalize_region_value(v: Any) -> Optional[str]:
    """تنظيف قيمة الدولة/المنطقة إلى كود موحد قدر الإمكان (SA, US, JP...)."""
    if isinstance(v, dict):
        for kk in (
            "code",
            "country",
            "countryCode",
            "region",
            "territory",
            "storeRegion",
            "storeCountry",
            "market",
        ):
            if kk in v and isinstance(v[kk], str):
                return _normalize_region_value(v[kk])
        if "locale" in v and isinstance(v["locale"], str):
            return _normalize_region_value(v["locale"])
        return None

    if not isinstance(v, str):
        return None

    s = v.strip()
    if "-" in s:
        # مثلا: ar-SA -> SA
        return s.split("-")[-1]

    mapping = {
        "ksa": "SA",
        "uae": "AE",
        "uk": "GB",
        "usa": "US",
        "us": "US",
        "saudi": "SA",
        "jp": "JP",
        "jpn": "JP",
    }
    return mapping.get(s.lower(), s)


def _dig_any(obj: Any) -> Optional[str]:
    """محاولة استخراج كود دولة/منطقة من أي هيكل بيانات معقد."""
    REGION_KEYS = {
        "region",
        "country",
        "countryCode",
        "country_code",
        "accountCountry",
        "userCountry",
        "currentCountry",
        "market",
        "territory",
        "locale",
        "storeRegion",
        "storeCountry",
    }

    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(k, str) and k in REGION_KEYS:
                val = _normalize_region_value(v)
                if val:
                    return val
        for v in obj.values():
            val = _dig_any(v)
            if val:
                return val

    elif isinstance(obj, list):
        for v in obj:
            val = _dig_any(v)
            if val:
                return val

    elif isinstance(obj, str):
        return _normalize_region_value(obj)

    return None


def get_user_region_clear(user) -> Optional[str]:
    """محاولة قوية لاستخراج منطقة اللاعب من أكثر من مصدر."""
    try:
        r = user.get_region()
        if r:
            if isinstance(r, (str, dict, list)):
                val = _dig_any(r)
                if val:
                    return val
    except Exception:
        pass

    try:
        prof = user.get_profile() or {}
        val = _dig_any(prof)
        if val:
            return val
    except Exception:
        pass

    try:
        legacy = getattr(user, "get_profile_legacy")() or {}
        val = _dig_any(legacy)
        if val:
            return val
    except Exception:
        pass

    try:
        val = _dig_any(getattr(user, "__dict__", {}) or {})
        if val:
            return val
    except Exception:
        pass

    return None


def country_code_to_flag(code: Optional[str]) -> str:
    """تحويل كود دولة إلى إيموجي العلم إن أمكن."""
    if not code:
        return ""
    code = code.upper()
    if code == "KSA":
        code = "SA"
    if code == "UAE":
        code = "AE"
    if len(code) != 2 or not code.isalpha():
        return code
    base = 127397
    return chr(ord(code[0]) + base) + chr(ord(code[1]) + base)


def format_region_pretty(region_raw: Optional[str]) -> str:
    """إرجاع تمثيل لطيف للمنطقة + العلم."""
    if not region_raw:
        return "N/A"
    code = _normalize_region_value(region_raw) or "N/A"
    flag = country_code_to_flag(code) if code and code != "N/A" else ""
    return f"{flag} {code}" if flag else str(code)


def _get_attr_or_key(obj: Any, key: str) -> Any:
    """قراءة قيمة من dict أو object بنفس السطر."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def parse_trophy_summary(summary: Any) -> Dict[str, Optional[int]]:
    """تحويل ملخص التروفيات إلى أرقام منظمة."""
    data = {
        "level": _get_attr_or_key(summary, "level"),
        "progress": _get_attr_or_key(summary, "progress"),
        "platinum": _get_attr_or_key(summary, "platinum"),
        "gold": _get_attr_or_key(summary, "gold"),
        "silver": _get_attr_or_key(summary, "silver"),
        "bronze": _get_attr_or_key(summary, "bronze"),
    }

    # تأكد أنها أعداد صحيحة أو None
    for k in list(data.keys()):
        v = data[k]
        if v is None:
            continue
        try:
            data[k] = int(v)
        except Exception:
            data[k] = None

    # إجمالي التروفيات
    pt = data.get("platinum") or 0
    gd = data.get("gold") or 0
    sv = data.get("silver") or 0
    br = data.get("bronze") or 0
    total = pt + gd + sv + br
    data["total"] = total if total > 0 else None

    return data


def format_trophy_summary(summary: Any) -> Optional[str]:
    """تحويل ملخص التروفيات لنص مختصر للعرض السريع."""
    if not summary:
        return None

    data = parse_trophy_summary(summary)
    parts: List[str] = []
    for key, label in (
        ("level", "Lvl"),
        ("progress", "%"),
        ("platinum", "Pt"),
        ("gold", "G"),
        ("silver", "S"),
        ("bronze", "B"),
    ):
        val = data.get(key)
        if val is not None:
            parts.append(f"{label}:{val}")
    return " | ".join(parts) if parts else None


# =========================
# Presence & Friends
# =========================

def format_presence(p: Dict[str, Any]) -> Optional[str]:
    """تنسيق حالة اللاعب الحالية لنص جاهز للعرض."""
    if not isinstance(p, dict):
        return None
    availability = p.get("availability") or p.get("onlineStatus") or "unknown"
    base = availability

    platform = (
        p.get("primaryPlatformInfo", {}).get("platform")
        or p.get("platform")
        or ""
    )
    if platform:
        base += f" | {platform}"

    if isinstance(p.get("gameTitleInfoList"), list) and p["gameTitleInfoList"]:
        gt = p["gameTitleInfoList"][0]
        title = gt.get("titleName") or gt.get("name")
        if title:
            base += f" | Playing: {title}"

    return base


def extract_current_title(p: Dict[str, Any]) -> Optional[str]:
    """استخراج اسم اللعبة الحالية (إن وجدت) من presence."""
    try:
        lst = p.get("gameTitleInfoList")
        if isinstance(lst, list) and lst:
            gt = lst[0]
            return gt.get("titleName") or gt.get("name")
    except Exception:
        pass
    return None


def quick_friends_stats(user) -> Tuple[Optional[int], Optional[int]]:
    """إحصائيات بسيطة عن الأصدقاء (إجمالي + تقدير للأونلاين)."""
    try:
        friends = list(user.friends())
        if not friends:
            return None, None
        total = len(friends)
        online_est = 0
        for f in friends[:15]:
            try:
                pr = f.get_presence()
                if isinstance(pr, dict) and (pr.get("availability") or pr.get("onlineStatus")) in (
                    "online",
                    "ONLINE",
                ):
                    online_est += 1
            except Exception:
                continue
        return total, online_est
    except Exception:
        return None, None


# =========================
# Avatar Helpers
# =========================

def _find_urls_in_value(v: Any) -> List[str]:
    urls: List[str] = []
    if isinstance(v, str) and ("http://" in v or "https://" in v):
        urls.append(v)
    elif isinstance(v, list):
        for i in v:
            urls.extend(_find_urls_in_value(i))
    elif isinstance(v, dict):
        for vv in v.values():
            urls.extend(_find_urls_in_value(vv))
    return urls


def _score_avatar_url(u: str) -> int:
    score = 0
    lu = u.lower()
    if "avatar" in lu:
        score += 3
    if "profile" in lu:
        score += 2
    if any(ext in lu for ext in (".png", ".jpg", ".jpeg", ".webp")):
        score += 2
    if "image" in lu or "pic" in lu:
        score += 1
    if "size=" in lu or "w=" in lu:
        score += 1
    if any(sz in lu for sz in ("2048", "1024", "512", "256")):
        score += 1
    return score


def extract_avatar_from_any_dict(dct: Dict[str, Any]) -> Optional[str]:
    """محاولة استخراج أفضل رابط صورة كأفاتار من أي دكت."""
    if not isinstance(dct, dict):
        return None

    # مسارات مباشرة
    for k in [
        "avatarUrl",
        "avatar_url",
        "profileAvatarUrl",
        "profilePictureUrl",
        "profile_picture_url",
        "picture",
        "image",
        "primaryAvatarUrl",
    ]:
        v = dct.get(k)
        if isinstance(v, str) and v.startswith(("http://", "https://")):
            return v

    # مصفوفات صور
    for path in ("avatars", "avatarUrls", "profilePictures", "images", "pictures"):
        arr = dct.get(path)
        if isinstance(arr, list):
            best, best_score = None, -1
            for item in arr:
                if isinstance(item, dict):
                    url = item.get("url") or item.get("href") or item.get("src")
                    if isinstance(url, str) and url.startswith(("http://", "https://")):
                        sc = _score_avatar_url(url)
                        if sc > best_score:
                            best, best_score = url, sc
                elif isinstance(item, str) and item.startswith(("http://", "https://")):
                    sc = _score_avatar_url(item)
                    if sc > best_score:
                        best, best_score = item, sc
            if best:
                return best

    # كباك أب: أي URL في الدكت
    urls = _find_urls_in_value(dct)
    if urls:
        urls.sort(key=_score_avatar_url, reverse=True)
        return urls[0]

    return None


def get_avatar_url(user) -> Optional[str]:
    """استخراج صورة البروفايل الحالية من كذا مصدر."""
    direct = getattr(user, "avatar_url", None) or getattr(
        user, "profile_picture_url", None
    )
    if isinstance(direct, str) and direct.startswith(("http://", "https://")):
        return direct

    try:
        prof = user.get_profile() or {}
        url = extract_avatar_from_any_dict(prof)
        if url:
            return url
    except Exception:
        pass

    try:
        legacy = getattr(user, "get_profile_legacy")()
        url = extract_avatar_from_any_dict(legacy)
        if url:
            return url
    except Exception:
        pass

    try:
        url = extract_avatar_from_any_dict(getattr(user, "__dict__", {}) or {})
        if url:
            return url
    except Exception:
        pass

    return None


# =========================
# Core API لموقع الفريق
# =========================

def get_psn_client(npsso: str) -> PSNAWP:
    """إنشاء عميل PSN من NPSSO."""
    if not npsso or len(npsso) < 40:
        raise ValueError("NPSSO غير صالح (أقل من 40 حرف).")
    return PSNAWP(npsso)


def _build_value_and_risk_segments(
    trophies: Dict[str, Optional[int]],
    titles_count: Optional[int],
    friends_total: Optional[int],
    presence_text: Optional[str],
    region_raw: Optional[str],
) -> Dict[str, Any]:
    """
    بناء تقييم مبسط لقيمة الحساب + مستوى المخاطر
    الهدف: تعطيك كلام جاهز تعرضه للعميل أو تستخدمه في قرارك.
    """

    pt = trophies.get("platinum") or 0
    total_trophies = trophies.get("total") or 0
    lvl = trophies.get("level") or 0
    titles = titles_count or 0
    friends = friends_total or 0

    # ===== قيمة الحساب (Score) =====
    value_score = 0

    # مستوى اللاعب
    if lvl >= 400:
        value_score += 4
    elif lvl >= 250:
        value_score += 3
    elif lvl >= 100:
        value_score += 2
    elif lvl >= 50:
        value_score += 1

    # إجمالي التروفيات
    if total_trophies >= 5000:
        value_score += 4
    elif total_trophies >= 2500:
        value_score += 3
    elif total_trophies >= 1000:
        value_score += 2
    elif total_trophies >= 300:
        value_score += 1

    # عدد البلانتينيوم
    if pt >= 50:
        value_score += 4
    elif pt >= 20:
        value_score += 3
    elif pt >= 5:
        value_score += 2
    elif pt >= 1:
        value_score += 1

    # عدد الألعاب
    if titles >= 150:
        value_score += 4
    elif titles >= 80:
        value_score += 3
    elif titles >= 40:
        value_score += 2
    elif titles >= 10:
        value_score += 1

    # الأصدقاء
    if friends >= 200:
        value_score += 3
    elif friends >= 80:
        value_score += 2
    elif friends >= 30:
        value_score += 1

    # تصنيف القيمة
    if value_score >= 12:
        value_segment = "حساب مميز / نادر"
    elif value_score >= 8:
        value_segment = "حساب قوي"
    elif value_score >= 4:
        value_segment = "حساب متوسط"
    elif value_score > 0:
        value_segment = "حساب بسيط"
    else:
        value_segment = "بيانات قليلة – لا يمكن تقييم القيمة بدقة"

    # ===== المخاطر (Risk) =====
    risk_flags: List[str] = []

    if not region_raw:
        risk_flags.append("لم يتم تحديد دولة الحساب بوضوح.")
    if titles_count is None:
        risk_flags.append("قائمة الألعاب/التروفيات غير متاحة (ربما خاصة).")
    if friends_total is None:
        risk_flags.append("قائمة الأصدقاء غير متاحة (قد تكون خاصة).")
    if presence_text is None:
        risk_flags.append("لا توجد حالة أونلاين واضحة (الحالة قد تكون مخفية).")

    # تقدير بسيط
    if len(risk_flags) == 0:
        risk_level = "منخفض"
    elif len(risk_flags) <= 2:
        risk_level = "متوسط"
    else:
        risk_level = "مرتفع"

    return {
        "value_score": value_score,
        "value_segment": value_segment,
        "risk_level": risk_level,
        "risk_flags": risk_flags,
    }


def get_account_report(online_id: str, npsso: str) -> Dict[str, Any]:
    """
    تستخدمها لوحة فريق DEMAN لتحليل حساب العميل.
    ترجع قاموس جاهز للعرض في HTML بشكل مباشر.
    """
    cache_key = f"web_report:{online_id}:{hash(npsso[:10])}"
    cached = _cache.get(cache_key)
    if cached:
        return cached

    if not npsso or len(npsso) < 40:
        return {
            "ok": False,
            "message": "NPSSO غير صالح (أقل من 40 حرف).",
        }

    # ===== إنشاء عميل PSN =====
    try:
        psn = get_psn_client(npsso)
    except Exception as e:
        return {
            "ok": False,
            "message": f"فشل إنشاء عميل PSN: {e}",
        }

    # ===== جلب بيانات المستخدم =====
    try:
        user = psn.user(online_id=online_id)
    except PSNAWPNotFound:
        return {
            "ok": False,
            "message": "الحساب غير موجود. تأكد من Online ID.",
        }
    except PSNAWPForbidden:
        return {
            "ok": False,
            "message": "تم رفض الوصول من Sony (تحقق من NPSSO أو جرّب لاحقًا).",
        }
    except Exception as e:
        return {
            "ok": False,
            "message": f"خطأ أثناء جلب المستخدم: {e}",
        }

    # =========================
    # بيانات أساسية
    # =========================
    acc = str(getattr(user, "account_id", "N/A"))
    acc_hex = account_id_to_hex(acc) or "N/A"

    region_raw = get_user_region_clear(user)
    region_pretty = format_region_pretty(region_raw)

    # Presence
    presence_text = None
    presence_raw: Optional[Dict[str, Any]] = None
    current_title = None
    try:
        presence_raw = user.get_presence() or {}
        presence_text = format_presence(presence_raw)
        current_title = extract_current_title(presence_raw) if presence_raw else None
    except Exception:
        presence_text = None

    # Trophy summary
    try:
        summary = user.trophy_summary()
    except Exception:
        summary = None

    trophies_struct = parse_trophy_summary(summary) if summary else {
        "level": None,
        "progress": None,
        "platinum": None,
        "gold": None,
        "silver": None,
        "bronze": None,
        "total": None,
    }
    summary_text = format_trophy_summary(summary)

    # Titles count فقط
    try:
        titles = list(user.trophy_titles())
        titles_count = len(titles) if titles is not None else None
    except PSNAWPForbidden:
        titles_count = None
    except Exception:
        titles_count = None

    # Friends quick stats
    friends_total, friends_online_est = quick_friends_stats(user)

    # Avatar
    avatar_url = get_avatar_url(user)

    # =========================
    # تحليلات إضافية (قيمة + مخاطر)
    # =========================
    segments = _build_value_and_risk_segments(
        trophies=trophies_struct,
        titles_count=titles_count,
        friends_total=friends_total,
        presence_text=presence_text,
        region_raw=region_raw,
    )

    # تقسيم بسيط لنشاط الحساب
    activity_segment = "غير محدد"
    total_trophies = trophies_struct.get("total") or 0
    if total_trophies == 0 or total_trophies is None:
        activity_segment = "نشاط ضعيف أو جديد جدًا"
    elif total_trophies < 300:
        activity_segment = "نشاط بسيط"
    elif total_trophies < 1500:
        activity_segment = "نشاط جيد"
    else:
        activity_segment = "نشاط عالي / لاعب ثقيل"

    data: Dict[str, Any] = {
        "ok": True,

        # ========== بيانات أساسية ==========
        "online_id": getattr(user, "online_id", online_id),
        "account_id": acc,
        "account_hex": acc_hex,
        "region_raw": region_raw,
        "region_pretty": region_pretty,
        "avatar_url": avatar_url,

        # ========== الحضور والأونلاين ==========
        "presence": presence_text,          # نص جاهز للعرض
        "presence_raw": presence_raw,       # لمن يحتاج تفاصيل خام
        "current_title": current_title,     # اسم اللعبة الحالية إن وجد

        # ========== التروفيات ==========
        "trophy_summary": summary_text,     # نص مختصر
        "trophies": trophies_struct,        # أرقام منظمة: level, platinum, gold, silver, bronze, total

        # ========== الألعاب ==========
        "titles_count": titles_count,

        # ========== الأصدقاء ==========
        "friends_total": friends_total,
        "friends_online_est": friends_online_est,

        # ========== تحليل تجاري للحساب ==========
        "value_score": segments["value_score"],        # رقم تقديري للقيمة
        "value_segment": segments["value_segment"],    # نص: حساب بسيط/متوسط/قوي/مميز
        "activity_segment": activity_segment,          # نص لنشاط الحساب

        # ========== المخاطر ==========
        "risk_level": segments["risk_level"],          # منخفض / متوسط / مرتفع
        "risk_flags": segments["risk_flags"],          # قائمة أسباب
    }

    _cache.set(cache_key, data)
    return data
