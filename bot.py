"""Collects new Tbilisi rental listings from ss.ge and myhome.ge and posts them to a
Telegram channel.

Runs once per invocation (GitHub Actions calls it on a schedule).
Secrets come from environment variables:
  TELEGRAM_BOT_TOKEN  bot token from @BotFather (bot must be a channel admin)
  CHANNEL_ID          e.g. @my_channel or -100123456789
  ESIM_URL            optional, overrides esim_url from config.json
If TELEGRAM_BOT_TOKEN is missing the bot runs in preview mode: it writes the
posts it would send to preview.md and does not touch the state file.

Every source is turned into the same flat listing dict (see `new_listing`), so
filtering, scoring, formatting and sending don't care where a listing came from.
"""

import io
import json
import math
import os
import re
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(ROOT, "config.json")
STATE_PATH = os.path.join(ROOT, "state", "posted.json")
PREVIEW_PATH = os.path.join(ROOT, "preview.md")
LOG_PATH = os.path.join(ROOT, "state", "last_run.log")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/129.0 Safari/537.36")
NEXT_DATA_RE = re.compile(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S)
TBILISI_TZ = timezone(timedelta(hours=4))

_LOG = []
START = time.time()


def log(*a):
    line = " ".join(str(x) for x in a)
    _LOG.append(line)
    print(line, file=sys.stderr, flush=True)


def write_log():
    """Saves this run's log next to the state so problems can be read from the repo."""
    text = "\n".join(_LOG) + "\n"
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if token:
        text = text.replace(token, "<TOKEN>")
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, "w", encoding="utf-8") as f:
        f.write(f"run at {datetime.now(timezone.utc).isoformat()}\n" + text)


# ---------------------------------------------------------------- http

def http_get_bytes(url, timeout=55, attempts=3, quiet=False):
    headers = {
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/*,*/*;q=0.8",
        "Accept-Language": "ru,en;q=0.8",
    }
    last = None
    for n in range(attempts):
        t0 = time.time()
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read()
            if not quiet:
                log(f"  GET ok in {time.time() - t0:.1f}s: {url[:110]}")
            return body
        except Exception as e:  # noqa: BLE001
            last = e
            log(f"  attempt {n + 1} failed after {time.time() - t0:.1f}s: {e!r} {url[:90]}")
            if isinstance(e, urllib.error.HTTPError) and e.code in (403, 404):
                break  # blocked or gone; retrying won't help
            if time.time() - START > 480:
                break  # stay well inside the job time limit
            time.sleep(5 * (n + 1))
    raise last


def http_get(url, **kw):
    return http_get_bytes(url, **kw).decode("utf-8", "replace")


def next_data(html, url=""):
    m = NEXT_DATA_RE.search(html)
    if not m:
        raise RuntimeError(f"no __NEXT_DATA__ on {url}")
    return json.loads(m.group(1))


# ---------------------------------------------------------------- common listing

def new_listing(**kw):
    base = dict(
        key="", source="", kind="month", usd=None, gel=None, rooms=None, area=None,
        floor=None, total_floors=None, furniture=None, metro=None, metro_min=None,
        sub="", district="", street="", lat=None, lng=None, created=None,
        photos=[], thumbs=[], photo_count=0, url="", market_key="", detail_loaded=False,
        raw=None,
    )
    base.update(kw)
    return base


def parse_dt(s, tz=None):
    if not s:
        return None
    s = re.sub(r"(\.\d{6})\d+", r"\1", str(s)).replace(" ", "T", 1)
    try:
        d = datetime.fromisoformat(s)
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=tz or TBILISI_TZ)
    return d


def to_int(x):
    try:
        return int(float(x))
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- ss.ge

SS_BASE = "https://home.ss.ge"
SS_RU_PATH = "/ru/%D0%BD%D0%B5%D0%B4%D0%B2%D0%B8%D0%B6%D0%B8%D0%BC%D0%BE%D1%81%D1%82%D1%8C/"

METRO_RU = {
    "akhmetelitheatre": "Театр Ахметели", "akhmeteli": "Театр Ахметели",
    "sarajishvili": "Сараджишвили", "guramishvili": "Гурамишвили", "grmagele": "Грмагеле",
    "didube": "Дидубе", "gotsiridze": "Гоциридзе", "nadzaladevi": "Надзаладеви",
    "stationsquare": "Вокзальная площадь", "sadguriskhoedani": "Вокзальная площадь",
    "marjanishvili": "Марджанишвили", "rustaveli": "Руставели",
    "libertysquare": "Площадь Свободы", "tavisuplebismoedani": "Площадь Свободы",
    "avlabari": "Авлабари", "300aragveli": "300 Арагвели", "isani": "Исани",
    "samgori": "Самгори", "varketili": "Варкетили", "tsereteli": "Церетели",
    "techuni": "Технический университет", "technicaluniversity": "Технический университет",
    "meduni": "Медицинский университет", "medicaluniversity": "Медицинский университет",
    "delisi": "Делиси", "vazhapshavela": "Важа-Пшавела",
    "stateuniversity": "Государственный университет", "university": "Государственный университет",
}


def ss_metro_name(title):
    key = re.sub(r"[^a-z0-9]", "", (title or "").lower())
    return METRO_RU.get(key) or (title or "").replace("_", " ").title()


def ss_full_image(url):
    return url.replace("_Thumb.", ".") if url else None


def ss_rooms(it):
    t = it.get("title") or ""
    m = re.search(r"(\d+)\s*-?\s*комн", t) or re.search(r"(\d+)\s*(room|ოთახ)", t)
    return int(m.group(1)) if m else it.get("numberOfBedrooms")


def ss_normalize(it, kind):
    a = it.get("address") or {}
    p = it.get("price") or {}
    imgs = sorted([i for i in (it.get("appImages") or []) if i.get("fileName")],
                  key=lambda i: (not i.get("isMain"), i.get("orderNo") or 0))
    st = (it.get("nearbySubwayStations") or [None])[0] or {}
    street = " ".join(x for x in [a.get("streetTitle"), a.get("streetNumber")] if x)
    return new_listing(
        key=str(it["applicationId"]),  # plain id: compatible with earlier state files
        source="ss.ge", kind=kind,
        usd=p.get("priceUsd"), gel=p.get("priceGeo"),
        rooms=ss_rooms(it), area=it.get("totalArea"),
        floor=it.get("floorNumber"), total_floors=it.get("totalAmountOfFloor"),
        furniture=it.get("furniture"),
        metro=ss_metro_name(st.get("stationTitle")) if st.get("stationTitle") else None,
        metro_min=st.get("timeDistance"),
        sub=a.get("subdistrictTitle") or a.get("districtTitle") or "",
        district=a.get("districtTitle") or "", street=street,
        created=parse_dt(it.get("createDate")),
        photos=[ss_full_image(i["fileName"]) for i in imgs],
        thumbs=[i["fileName"] for i in imgs],
        photo_count=it.get("imageCount") or len(imgs),
        url=SS_BASE + SS_RU_PATH + urllib.parse.quote(it["detailUrl"]),
        market_key=f"{kind}:{a.get('subdistrictId')}",
        raw=it,
    )


def ss_load_detail(L):
    """Listing page: full photo set and map point."""
    try:
        d = next_data(http_get(L["url"], attempts=2), L["url"])["props"]["pageProps"].get("applicationData") or {}
    except Exception as e:  # noqa: BLE001
        log("ss.ge listing page failed:", repr(e))
        return
    imgs = [i for i in (d.get("appImages") or []) if i.get("fileName") and not i.get("is360")]
    imgs.sort(key=lambda i: (not i.get("isMain"), i.get("orderNo") or 0))
    if imgs:
        L["photos"] = [ss_full_image(i["fileName"]) for i in imgs]
        L["thumbs"] = [i.get("fileNameThumb") or i["fileName"] for i in imgs]
    L["lat"] = d.get("locationLatitude") or d.get("streetLocationLatitude")
    L["lng"] = d.get("locationLongitude") or d.get("streetLocationLongitude")


def ss_fetch_page(slug, city_id, page, locale, newest, attempts):
    params = {"cityIdList": city_id, "page": page}
    if newest:
        params["order"] = 1  # "newest first" sorting on ss.ge
    url = f"{SS_BASE}/{locale}/udzravi-qoneba/l/bina/{slug}?{urllib.parse.urlencode(params)}"
    data = next_data(http_get(url, attempts=attempts), url)
    return data["props"]["pageProps"]["applicationList"]["realStateItemModel"]


def ss_fetch_any(slug, city_id, page, newest):
    """Tries the Russian page, then the Georgian one. Returns None if both fail."""
    for locale in ("ru", "ka"):
        try:
            return ss_fetch_page(slug, city_id, page, locale, newest, attempts=2 if newest else 3)
        except Exception as e:  # noqa: BLE001
            log(f"ss.ge {slug} p{page} {locale} {'newest' if newest else 'default'}: {e!r}")
            if newest:
                return None  # the sorted listing is failing on ss.ge's side; caller falls back
    return None


def ss_collect(cfg):
    """ss.ge's "newest first" sorting sometimes times out on their side (HTTP 504)
    while the default listing still works, so we fall back to it."""
    out = []
    for sec in cfg.get("sections", []):
        newest, pages, page = True, sec.get("pages", 1), 1
        while page <= pages:
            batch = ss_fetch_any(sec["slug"], cfg["city_id"], page, newest)
            if batch is None and newest:
                log(f"ss.ge: falling back to default order for {sec['slug']}")
                newest, page, pages = False, 1, pages + 2
                continue
            if not batch:
                break
            out.extend(ss_normalize(it, sec["kind"]) for it in batch)
            page += 1
            time.sleep(1.5)
    return out


# ---------------------------------------------------------------- myhome.ge

MH_BASE = "https://www.myhome.ge"
MH_CURRENCY_GEL, MH_CURRENCY_USD = "1", "2"
# Public list from api-locations.tnet.ge/v2/metro-stations (locale ru)
MH_METRO = {
    1: "Государственный университет", 2: "Важа-Пшавела", 3: "Делиси",
    4: "Медицинский университет", 5: "Технический университет", 6: "Церетели",
    7: "Вокзальная площадь", 8: "Варкетили", 9: "Самгори", 10: "Исани",
    11: "300 Арагвели", 12: "Авлабари", 13: "Площадь Свободы", 14: "Руставели",
    15: "Марджанишвили", 16: "Надзаладеви", 17: "Гоциридзе", 18: "Дидубе",
    19: "Грмагеле", 20: "Гурамишвили", 21: "Сараджишвили", 22: "Театр Ахметели",
}


def mh_query(pp, name):
    for q in (pp.get("dehydratedState") or {}).get("queries", []):
        key = q.get("queryKey") or []
        if len(key) >= 2 and key[0] == "statements" and key[1] == name:
            return (q.get("state") or {}).get("data")
    return None


def mh_normalize(it, kind):
    price = (it.get("price") or {})
    usd = (price.get(MH_CURRENCY_USD) or {}).get("price_total")
    gel = (price.get(MH_CURRENCY_GEL) or {}).get("price_total")
    imgs = sorted([i for i in (it.get("images") or []) if i.get("large")], key=lambda i: not i.get("is_main"))
    slug = it.get("dynamic_slug") or "statement"
    return new_listing(
        key=f"mh:{it['id']}", source="myhome.ge", kind=kind,
        usd=to_int(usd), gel=to_int(gel),
        rooms=to_int(it.get("room")), area=it.get("area"),
        floor=it.get("floor"), total_floors=it.get("total_floors"),
        metro=MH_METRO.get(to_int(it.get("metro_station_id"))),
        sub=it.get("urban_name") or it.get("district_name") or "",
        district=it.get("district_name") or "", street=it.get("address") or "",
        lat=it.get("lat"), lng=it.get("lng"),
        created=parse_dt(it.get("created_at")),
        photos=[i["large"] for i in imgs], thumbs=[i.get("thumb") or i["large"] for i in imgs],
        photo_count=len(imgs),
        url=f"{MH_BASE}/ru/nedvizhimost/{slug}-{it['id']}/",
        market_key=f"mh:{kind}:{it.get('urban_id')}",
        raw=it,
    )


def mh_load_detail(L):
    """Listing page: creation date, exact map point, furniture."""
    try:
        pp = next_data(http_get(L["url"], attempts=2), L["url"])["props"]["pageProps"]
        d = (mh_query(pp, "details") or {}).get("data") or {}
        st = d.get("statement") or d
    except Exception as e:  # noqa: BLE001
        log("myhome.ge listing page failed:", repr(e))
        return False
    if not st.get("id"):
        return False
    L["created"] = parse_dt(st.get("created_at")) or L["created"]
    L["lat"], L["lng"] = st.get("lat") or L["lat"], st.get("lng") or L["lng"]
    keys = {p.get("key") for p in (st.get("parameters") or [])}
    L["furniture"] = "furniture" in keys
    if st.get("address"):
        L["street"] = st["address"]
    imgs = sorted([i for i in (st.get("images") or []) if i.get("large")], key=lambda i: not i.get("is_main"))
    if imgs:
        L["photos"] = [i["large"] for i in imgs]
        L["thumbs"] = [i.get("thumb") or i["large"] for i in imgs]
    return True


def mh_collect(cfg):
    out = []
    mh = cfg.get("myhome") or {}
    for sec in mh.get("sections", []):
        for page in range(1, sec.get("pages", 1) + 1):
            qs = urllib.parse.urlencode({"deal_types": sec["deal_type"], "real_estate_types": 1,
                                         "cities": mh.get("city_id", 1), "page": page})
            url = f"{MH_BASE}/ru/nedvizhimost/?{qs}"
            try:
                pp = next_data(http_get(url, attempts=2), url)["props"]["pageProps"]
                batch = ((mh_query(pp, "list") or {}).get("data") or {}).get("data") or []
            except Exception as e:  # noqa: BLE001
                log(f"myhome.ge {sec['kind']} p{page}: {e!r}")
                if not out:
                    log("myhome.ge is not responding — skipping it this run")
                    return out
                break
            if not batch:
                break
            out.extend(mh_normalize(it, sec["kind"]) for it in batch if it.get("id"))
            time.sleep(1.5)
    return out


def load_detail(L):
    if L["detail_loaded"]:
        return True
    L["detail_loaded"] = True
    if L["source"] == "myhome.ge":
        return mh_load_detail(L)
    ss_load_detail(L)
    return True


# ---------------------------------------------------------------- formatting

def hashtag(text):
    t = re.sub(r"[^\w]+", "_", (text or "").strip()).strip("_")
    return "#" + t if t else ""


def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def price_bucket(usd, kind):
    steps = [20, 30, 50, 80, 120] if kind == "day" else [400, 500, 700, 1000, 1500, 2000]
    for s in steps:
        if usd <= s:
            return f"#до{s}"
    return None


def maps_url(L):
    """Google Maps link: the listing's map point if known, else a street search."""
    if L.get("lat") and L.get("lng"):
        q = f"{L['lat']},{L['lng']}"
    elif L.get("street"):
        q = f"{L['street']}, Tbilisi"
    else:
        return None
    return "https://www.google.com/maps/search/?api=1&query=" + urllib.parse.quote(q)


def format_post(L, cfg, market):
    kind, usd, gel = L["kind"], L["usd"], L["gel"]
    head = [f"{L['rooms']}-комн. квартира" if L["rooms"] else "Квартира"]
    if L["area"]:
        head.append(f"{float(L['area']):g} м²")
    title = "🏠 <b>" + esc(" · ".join(head)) + "</b>" + (f" — {esc(L['sub'])}" if L["sub"] else "")
    if kind == "day":
        title = "🗓 <b>Посуточно</b>\n" + title

    lines = [title]
    per = "сутки" if kind == "day" else "мес"
    price = f"💵 <b>${usd:,}</b>/{per}".replace(",", " ")
    if gel:
        price += f"  (≈{gel:,} ₾)".replace(",", " ")
    if market.get("hot"):
        price += "  🔥 ниже рынка"
    lines.append(price)

    details = []
    if L["floor"]:
        fl = f"🏢 этаж {L['floor']}"
        if L["total_floors"]:
            fl += f"/{L['total_floors']}"
        details.append(fl)
    if L["furniture"]:
        details.append("🛋 с мебелью")
    if details:
        lines.append(" · ".join(details))

    if L["metro"]:
        t = f", {L['metro_min']} мин" if L["metro_min"] else ""
        lines.append(f"🚇 м. {esc(L['metro'])}{t}")

    loc = ", ".join(x for x in [L["district"], L["street"]] if x)
    if loc:
        url = maps_url(L)
        lines.append(f'📍 <a href="{esc(url)}">{esc(loc)}</a>' if url else f"📍 {esc(loc)}")

    tags = [hashtag(L["sub"])]
    if L["rooms"]:
        tags.append(f"#{L['rooms']}комн")
    tags.append("#посуточно" if kind == "day" else "#помесячно")
    b = price_bucket(usd, kind)
    if b:
        tags.append(b)
    lines.append("")
    lines.append(" ".join(t for t in tags if t))

    if cfg.get("promo_line"):
        lines.append("")
        lines.append(cfg["promo_line"])
    if cfg.get("contact_line"):
        lines.append(cfg["contact_line"])
    return "\n".join(lines)


def buttons(L, cfg):
    rows = [[{"text": f"🔗 Открыть объявление на {L['source']}", "url": L["url"]}]]
    esim = os.environ.get("ESIM_URL") or cfg.get("esim_url")
    if esim:
        rows.append([{"text": cfg.get("esim_button_text", "📶 eSIM для Грузии"), "url": esim}])
    return {"inline_keyboard": rows}


# ---------------------------------------------------------------- telegram

def _tg_request(url, data, content_type, timeout=60):
    req = urllib.request.Request(url, data=data, headers={"Content-Type": content_type})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        try:
            return json.loads(body)
        except ValueError:
            return {"ok": False, "description": body[:300]}
    except Exception as e:  # noqa: BLE001  network errors, bad token characters, etc.
        return {"ok": False, "description": f"{type(e).__name__}: {e}"}


def tg(method, payload, token):
    return _tg_request(f"https://api.telegram.org/bot{token}/{method}",
                       json.dumps(payload).encode(), "application/json")


def tg_upload(method, fields, files, token):
    """multipart/form-data call; files = {name: (filename, bytes, mime)}."""
    boundary = uuid.uuid4().hex
    buf = io.BytesIO()
    for k, v in fields.items():
        if not isinstance(v, str):
            v = json.dumps(v, ensure_ascii=False)
        buf.write(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n".encode())
        buf.write(v.encode() + b"\r\n")
    for k, (fname, data, mime) in files.items():
        buf.write(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"; "
                  f"filename=\"{fname}\"\r\nContent-Type: {mime}\r\n\r\n".encode())
        buf.write(data + b"\r\n")
    buf.write(f"--{boundary}--\r\n".encode())
    return _tg_request(f"https://api.telegram.org/bot{token}/{method}", buf.getvalue(),
                       f"multipart/form-data; boundary={boundary}", timeout=120)


def with_retry(fn, *args):
    r = fn(*args)
    if r.get("error_code") == 429:
        time.sleep(int((r.get("parameters") or {}).get("retry_after", 5)) + 1)
        r = fn(*args)
    return r


def to_jpeg(data):
    """Telegram is picky about WebP photos (myhome.ge serves WebP), so convert."""
    try:
        from PIL import Image  # installed by the workflow
    except ImportError:
        return data, "image/webp"
    try:
        im = Image.open(io.BytesIO(data))
        im = im.convert("RGB")
        if max(im.size) > 2560:
            im.thumbnail((2560, 2560))
        out = io.BytesIO()
        im.save(out, "JPEG", quality=88)
        return out.getvalue(), "image/jpeg"
    except Exception as e:  # noqa: BLE001
        log("image conversion failed:", repr(e))
        return data, "image/webp"


def send_photos_by_url(urls, token, chat):
    if len(urls) == 1:
        return with_retry(tg, "sendPhoto", {"chat_id": chat, "photo": urls[0]}, token)
    return with_retry(tg, "sendMediaGroup",
                      {"chat_id": chat, "media": [{"type": "photo", "media": u} for u in urls]}, token)


def send_photos_upload(urls, token, chat):
    files = {}
    for n, u in enumerate(urls):
        try:
            data, mime = to_jpeg(http_get_bytes(u, attempts=2, quiet=True))
        except Exception as e:  # noqa: BLE001
            log("photo download failed:", repr(e))
            continue
        files[f"p{n}"] = (f"p{n}.jpg" if mime == "image/jpeg" else f"p{n}.webp", data, mime)
    if not files:
        return {"ok": False, "description": "no photos could be downloaded"}
    if len(files) == 1:
        (name, f), = files.items()
        return with_retry(tg_upload, "sendPhoto", {"chat_id": chat}, {"photo": f}, token)
    media = [{"type": "photo", "media": f"attach://{k}"} for k in files]
    return with_retry(tg_upload, "sendMediaGroup", {"chat_id": chat, "media": media}, files, token)


def send_album(L, token, chat, limit):
    full, thumbs = L["photos"][:limit], L["thumbs"][:limit]
    if not full:
        return False
    webp = any(u.lower().endswith(".webp") for u in full)
    attempts = ([] if webp else [(send_photos_by_url, full), (send_photos_by_url, thumbs)])
    attempts += [(send_photos_upload, full), (send_photos_upload, full[:1])]
    for fn, urls in attempts:
        r = fn(urls, token, chat)
        if r.get("ok"):
            return True
        log(f"photos via {fn.__name__} ({len(urls)}) failed:", r.get("description"))
    return False


def send(L, text, kb, token, chat, photos=5):
    """Album of up to `photos` pictures, then the text with buttons as the next message
    (Telegram does not allow buttons on albums)."""
    send_album(L, token, chat, photos)
    time.sleep(1)
    r = with_retry(tg, "sendMessage", {"chat_id": chat, "text": text, "parse_mode": "HTML",
                                       "reply_markup": kb, "disable_web_page_preview": True}, token)
    if not r.get("ok"):
        log("sendMessage failed:", r.get("description"))
    return bool(r.get("ok"))


# ---------------------------------------------------------------- state

def load_state():
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            state = json.load(f)
    except FileNotFoundError:
        return None
    for k, v in (("posted", {}), ("sampled", {}), ("unit_prices", {}), ("fingerprints", [])):
        state.setdefault(k, v)
    return state


def save_state(state):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    state["posted"] = {k: v for k, v in state["posted"].items() if v >= cutoff}
    state["sampled"] = {k: v for k, v in state["sampled"].items() if v >= cutoff}
    state["fingerprints"] = [f for f in state["fingerprints"] if f.get("ts", "") >= cutoff][-500:]
    for key in list(state["unit_prices"]):
        state["unit_prices"][key] = state["unit_prices"][key][-200:]
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=0, sort_keys=True)


def market_info(L, state):
    """Marks a listing as below market if its $/m² is <80% of the area's median."""
    unit = None
    try:
        if L["usd"] and L["area"]:
            unit = L["usd"] / float(L["area"])
    except (TypeError, ValueError, ZeroDivisionError):
        unit = None
    hist = state["unit_prices"].setdefault(L["market_key"], [])
    hot = bool(unit and len(hist) >= 15 and unit < 0.8 * statistics.median(hist))
    return {"hot": hot, "unit": unit}


# ---------------------------------------------------------------- duplicates across sites

def fingerprint(L, now):
    return {"kind": L["kind"], "rooms": L["rooms"], "area": L["area"], "usd": L["usd"],
            "lat": L["lat"], "lng": L["lng"], "sub": (L["sub"] or "").lower(),
            "key": L["key"], "ts": now.isoformat()}


def _dist_m(a, b):
    try:
        lat1, lng1, lat2, lng2 = map(float, (a["lat"], a["lng"], b["lat"], b["lng"]))
    except (TypeError, ValueError, KeyError):
        return None
    k = 111_000
    return math.hypot((lat1 - lat2) * k, (lng1 - lng2) * k * math.cos(math.radians(lat1)))


def same_flat(a, b):
    """Same apartment listed on two sites: same type, rooms, area ±3 m², price ±5%,
    and close on the map (or the same neighbourhood when a map point is missing)."""
    if a["kind"] != b["kind"] or not a["rooms"] or a["rooms"] != b["rooms"]:
        return False
    try:
        if abs(float(a["area"]) - float(b["area"])) > 3:
            return False
        if abs(a["usd"] - b["usd"]) > 0.05 * max(a["usd"], b["usd"]):
            return False
    except (TypeError, ValueError):
        return False
    d = _dist_m(a, b)
    if d is not None:
        return d < 250
    return bool(a.get("sub")) and (a.get("sub") or "").lower() == (b.get("sub") or "").lower()


# ---------------------------------------------------------------- main

def eligible(L, cfg, now, need_created=True):
    if not L["usd"]:
        return False
    rng = cfg["price_usd_day"] if L["kind"] == "day" else cfg["price_usd_month"]
    if not (rng["min"] <= L["usd"] <= rng["max"]):
        return False
    if cfg.get("require_photo") and not L["photos"]:
        return False
    if L["created"] is None:
        return not need_created
    return now - L["created"] <= timedelta(hours=cfg["max_age_hours"])  # skip bumped-up old ads


def score(L, now):
    """Best first: below-market deals, then near metro, furnished, more photos, newest."""
    return (L["_market"].get("hot", False), bool(L["metro"]), bool(L["furniture"]),
            min(L["photo_count"] or 0, 8), L["created"] or now)


def main():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        cfg = json.load(f)
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.environ.get("CHANNEL_ID", "").strip()
    preview = not (token and chat)
    log(f"mode: {'preview' if preview else 'publish'}; token set: {bool(token)}; channel set: {bool(chat)}")

    if not preview:
        me = tg("getMe", {}, token)
        log("getMe:", "ok @" + me["result"]["username"] if me.get("ok") else me.get("description"))
        ch = tg("getChat", {"chat_id": chat}, token)
        log("getChat:", ("ok " + str(ch["result"].get("title")) + " type=" + str(ch["result"].get("type")))
            if ch.get("ok") else ch.get("description"))

    sources = cfg.get("sources", {"ss.ge": True})
    items = []
    if sources.get("ss.ge", True):
        got = ss_collect(cfg)
        log(f"ss.ge: collected {len(got)}")
        items += got
    if sources.get("myhome.ge"):
        got = mh_collect(cfg)
        log(f"myhome.ge: collected {len(got)}")
        items += got
    if not items:
        log("nothing collected this time (sites unreachable or layout changed) — skipping run")
        return

    state = load_state()
    first_run = state is None
    if first_run:
        state = {"posted": {}, "unit_prices": {}, "sampled": {}, "fingerprints": []}

    now = datetime.now(timezone.utc)
    seen, fresh = set(), []
    for L in items:
        if L["key"] in seen:
            continue
        seen.add(L["key"])
        m = market_info(L, state)
        if L["key"] not in state["sampled"] and m["unit"]:
            state["unit_prices"][L["market_key"]].append(round(m["unit"], 2))
            state["sampled"][L["key"]] = now.isoformat()
        if L["key"] in state["posted"]:
            continue
        # myhome.ge lists have no creation date: it comes with the listing page below
        if not eligible(L, cfg, now, need_created=(L["source"] != "myhome.ge")):
            continue
        L["_market"] = m
        fresh.append(L)

    # myhome.ge: open the newest few listing pages (ids grow over time) to get the
    # creation date, map point and furniture; drop old ones that were just bumped up.
    mh = sorted([L for L in fresh if L["source"] == "myhome.ge"],
                key=lambda L: int(L["key"].split(":")[1]), reverse=True)
    keep = set()
    for L in mh[:(cfg.get("myhome") or {}).get("detail_checks", 10)]:
        if load_detail(L) and eligible(L, cfg, now):
            keep.add(L["key"])
        time.sleep(1)
    fresh = [L for L in fresh if L["source"] != "myhome.ge" or L["key"] in keep]

    fresh.sort(key=lambda L: score(L, now), reverse=True)
    log(f"{len(fresh)} new eligible listings "
        f"(ss.ge {sum(L['source'] == 'ss.ge' for L in fresh)}, myhome.ge {sum(L['source'] == 'myhome.ge' for L in fresh)})")

    def is_duplicate(L):
        for fp in state["fingerprints"]:
            if fp.get("key") != L["key"] and same_flat(L, fp):
                log(f"skip {L['key']}: same flat as already posted {fp.get('key')}")
                return True
        return False

    limit = cfg["max_posts_per_run"]
    if preview:
        out, n = ["# Preview (no TELEGRAM_BOT_TOKEN / CHANNEL_ID set)\n"], 0
        for L in fresh:
            if n >= max(limit, 3):
                break
            load_detail(L)
            if is_duplicate(L):
                continue
            out.append(f"[{L['source']}] " + format_post(L, cfg, L["_market"]))
            out.append("Buttons: " + json.dumps(buttons(L, cfg), ensure_ascii=False))
            out.append("Photos: " + ", ".join(L["photos"][:cfg.get("photos_per_post", 5)]) + "\n\n---\n")
            n += 1
        with open(PREVIEW_PATH, "w", encoding="utf-8") as f:
            f.write("\n".join(out))
        log("preview written")
        return

    if first_run:
        # Don't flood the channel on the very first run.
        for L in fresh[limit:]:
            state["posted"][L["key"]] = now.isoformat()
        fresh = fresh[:limit]

    sent = 0
    for L in fresh:
        if sent >= limit:
            break
        load_detail(L)
        if is_duplicate(L):
            state["posted"][L["key"]] = now.isoformat()
            continue
        text = format_post(L, cfg, L["_market"])
        if send(L, text, buttons(L, cfg), token, chat, cfg.get("photos_per_post", 5)):
            state["posted"][L["key"]] = now.isoformat()
            state["fingerprints"].append(fingerprint(L, now))
            log(f"posted {L['key']} from {L['source']}")
            sent += 1
            time.sleep(cfg["delay_between_posts_sec"])
    log(f"posted {sent}")
    save_state(state)


if __name__ == "__main__":
    import traceback
    try:
        main()
    except SystemExit:
        write_log()
        raise
    except Exception:  # noqa: BLE001
        log(traceback.format_exc())
        write_log()
        sys.exit(1)
    write_log()
