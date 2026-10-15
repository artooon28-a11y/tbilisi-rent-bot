"""Collects new Tbilisi rental listings from ss.ge and posts them to a Telegram channel.

Runs once per invocation (GitHub Actions calls it on a schedule).
Secrets come from environment variables:
  TELEGRAM_BOT_TOKEN  bot token from @BotFather (bot must be a channel admin)
  CHANNEL_ID          e.g. @my_channel or -100123456789
  ESIM_URL            optional, overrides esim_url from config.json
If TELEGRAM_BOT_TOKEN is missing the bot runs in preview mode: it writes the
posts it would send to preview.md and does not touch the state file.
"""

import json
import os
import re
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(ROOT, "config.json")
STATE_PATH = os.path.join(ROOT, "state", "posted.json")
PREVIEW_PATH = os.path.join(ROOT, "preview.md")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/129.0 Safari/537.36")
SS_BASE = "https://home.ss.ge"
NEXT_DATA_RE = re.compile(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S)


LOG_PATH = os.path.join(ROOT, "state", "last_run.log")
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


# ---------------------------------------------------------------- fetching

def http_get(url, timeout=55, attempts=3):
    headers = {
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru,en;q=0.8",
    }
    last = None
    for n in range(attempts):
        t0 = time.time()
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read().decode("utf-8", "replace")
            log(f"  GET ok in {time.time() - t0:.1f}s: {url[:110]}")
            return body
        except Exception as e:  # noqa: BLE001
            last = e
            log(f"  attempt {n + 1} failed after {time.time() - t0:.1f}s: {e!r}")
            if time.time() - START > 480:
                break  # stay well inside the job time limit
            time.sleep(5 * (n + 1))
    raise last


def fetch_ss_page(slug, city_id, page, locale="ru", newest=True, attempts=3):
    params = {"cityIdList": city_id, "page": page}
    if newest:
        params["order"] = 1  # "newest first" sorting on ss.ge
    url = f"{SS_BASE}/{locale}/udzravi-qoneba/l/bina/{slug}?{urllib.parse.urlencode(params)}"
    html = http_get(url, attempts=attempts)
    m = NEXT_DATA_RE.search(html)
    if not m:
        raise RuntimeError(f"no __NEXT_DATA__ on {url}")
    data = json.loads(m.group(1))
    return data["props"]["pageProps"]["applicationList"]["realStateItemModel"]


def fetch_any(slug, city_id, page, newest):
    """Tries the Russian page, then the Georgian one. Returns None if both fail."""
    for locale in ("ru", "ka"):
        try:
            return fetch_ss_page(slug, city_id, page, locale, newest, attempts=2 if newest else 3)
        except Exception as e:  # noqa: BLE001
            log(f"ss.ge {slug} p{page} {locale} {'newest' if newest else 'default'}: {e!r}")
            if newest:
                return None  # the sorted listing is failing on ss.ge's side; caller falls back
    return None


def collect(cfg):
    """Collects listings. ss.ge's "newest first" sorting sometimes times out on their
    side (HTTP 504) while the default listing still works, so we fall back to it."""
    items = []
    for sec in cfg["sections"]:
        newest = True
        pages = sec.get("pages", 1)
        page = 1
        while page <= pages:
            batch = fetch_any(sec["slug"], cfg["city_id"], page, newest)
            if batch is None and newest:
                log(f"falling back to default order for {sec['slug']}")
                newest, page, pages = False, 1, pages + 2
                continue
            if not batch:
                break
            for it in batch:
                it["_kind"] = sec["kind"]
                items.append(it)
            page += 1
            time.sleep(1.5)
    return items


# ---------------------------------------------------------------- helpers

def parse_dt(s):
    if not s:
        return None
    s = re.sub(r"(\.\d{6})\d+", r"\1", s)  # trim 7-digit fractions
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def rooms_of(it):
    m = re.search(r"(\d+)\s*-?\s*комн", it.get("title") or "")
    if m:
        return int(m.group(1))
    m = re.search(r"(\d+)\s*(room|ოთახ)", it.get("title") or "")
    if m:
        return int(m.group(1))
    return it.get("numberOfBedrooms")


def hashtag(text):
    t = re.sub(r"[^\w]+", "_", (text or "").strip()).strip("_")
    return "#" + t if t else ""


def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def full_image(url):
    return url.replace("_Thumb.", ".") if url else None


def listing_url(it):
    slug = urllib.parse.quote(it["detailUrl"])
    return f"{SS_BASE}/ru/%D0%BD%D0%B5%D0%B4%D0%B2%D0%B8%D0%B6%D0%B8%D0%BC%D0%BE%D1%81%D1%82%D1%8C/{slug}"


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


def metro_name(title):
    key = re.sub(r"[^a-z0-9]", "", (title or "").lower())
    return METRO_RU.get(key) or (title or "").replace("_", " ").title()


def price_bucket(usd, kind):
    steps = [20, 30, 50, 80, 120] if kind == "day" else [400, 500, 700, 1000, 1500, 2000]
    for s in steps:
        if usd <= s:
            return f"#до{s}"
    return None


# ---------------------------------------------------------------- formatting

def format_post(it, cfg, market):
    a = it.get("address") or {}
    p = it.get("price") or {}
    kind = it["_kind"]
    usd, gel = p.get("priceUsd"), p.get("priceGeo")
    rooms = rooms_of(it)
    area = it.get("totalArea")
    sub = a.get("subdistrictTitle") or a.get("districtTitle") or "Тбилиси"

    head = []
    head.append(f"{rooms}-комн. квартира" if rooms else "Квартира")
    if area:
        head.append(f"{area:g} м²")
    title = "🏠 <b>" + esc(" · ".join(head)) + f"</b> — {esc(sub)}"
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
    if it.get("floorNumber"):
        fl = f"🏢 этаж {it['floorNumber']}"
        if it.get("totalAmountOfFloor"):
            fl += f"/{it['totalAmountOfFloor']}"
        details.append(fl)
    if it.get("furniture"):
        details.append("🛋 с мебелью")
    if details:
        lines.append(" · ".join(details))

    st = (it.get("nearbySubwayStations") or [])
    if st:
        s0 = st[0]
        name = metro_name(s0.get("stationTitle"))
        if name:
            t = f", {s0['timeDistance']} мин" if s0.get("timeDistance") else ""
            lines.append(f"🚇 м. {esc(name)}{t}")

    street = " ".join(x for x in [a.get("streetTitle"), a.get("streetNumber")] if x)
    loc = ", ".join(x for x in [a.get("districtTitle"), street] if x)
    if loc:
        lines.append(f"📍 {esc(loc)}")

    tags = [hashtag(sub)]
    if rooms:
        tags.append(f"#{rooms}комн")
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


def buttons(it, cfg):
    rows = [[{"text": "🔗 Открыть объявление", "url": listing_url(it)}]]
    esim = os.environ.get("ESIM_URL") or cfg.get("esim_url")
    if esim:
        rows.append([{"text": cfg.get("esim_button_text", "📶 eSIM для Грузии"), "url": esim}])
    return {"inline_keyboard": rows}


# ---------------------------------------------------------------- telegram

def tg(method, payload, token):
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        try:
            return json.loads(body)
        except ValueError:
            return {"ok": False, "description": body[:300]}
    except Exception as e:  # noqa: BLE001  network errors, bad token characters, etc.
        return {"ok": False, "description": f"{type(e).__name__}: {e}"}


def photo_urls(it, limit):
    """Full-size photo URLs, main photo first. The search page only carries a few
    images, so the listing page is fetched for the complete set."""
    imgs = []
    try:
        html = http_get(listing_url(it), attempts=2)
        m = NEXT_DATA_RE.search(html)
        if m:
            data = json.loads(m.group(1))
            imgs = data["props"]["pageProps"]["applicationData"].get("appImages") or []
    except Exception as e:  # noqa: BLE001
        log("listing page for photos failed:", repr(e))
    if not imgs:
        imgs = it.get("appImages") or []
    imgs = [i for i in imgs if i.get("fileName") and not i.get("is360")]
    imgs.sort(key=lambda i: (not i.get("isMain"), i.get("orderNo") or 0))
    full = [full_image(i["fileName"]) for i in imgs][:limit]
    thumbs = [i.get("fileNameThumb") or i["fileName"] for i in imgs][:limit]
    return full, thumbs


def tg_retry(method, payload, token):
    r = tg(method, payload, token)
    if r.get("error_code") == 429:
        time.sleep(int((r.get("parameters") or {}).get("retry_after", 5)) + 1)
        r = tg(method, payload, token)
    return r


def send_photos(urls, token, chat):
    if not urls:
        return False
    if len(urls) == 1:
        r = tg_retry("sendPhoto", {"chat_id": chat, "photo": urls[0]}, token)
    else:
        media = [{"type": "photo", "media": u} for u in urls]
        r = tg_retry("sendMediaGroup", {"chat_id": chat, "media": media}, token)
    if not r.get("ok"):
        log("sending photos failed:", r.get("description"))
    return bool(r.get("ok"))


def send(it, text, kb, token, chat, photos=5):
    """Album of up to `photos` pictures, then the text with buttons as the next message
    (Telegram does not allow buttons on albums)."""
    full, thumbs = photo_urls(it, photos)
    if full and not send_photos(full, token, chat):
        if not send_photos(thumbs, token, chat):
            # one bad picture breaks the whole album; fall back to the main photo only
            send_photos(full[:1], token, chat)
    time.sleep(1)
    r = tg_retry("sendMessage", {"chat_id": chat, "text": text, "parse_mode": "HTML",
                                 "reply_markup": kb, "disable_web_page_preview": True}, token)
    if not r.get("ok"):
        log("sendMessage failed:", r.get("description"))
    return bool(r.get("ok"))


# ---------------------------------------------------------------- state

def load_state():
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None


def save_state(state):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    state["posted"] = {k: v for k, v in state["posted"].items() if v >= cutoff}
    state["sampled"] = {k: v for k, v in state["sampled"].items() if v >= cutoff}
    for key in list(state["unit_prices"]):
        state["unit_prices"][key] = state["unit_prices"][key][-200:]
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=0, sort_keys=True)


def market_info(it, state):
    """Marks a listing as below market if its $/m² is <80% of the area's median."""
    p, a = it.get("price") or {}, it.get("address") or {}
    unit, area = p.get("unitPriceUsd"), it.get("totalArea")
    if not unit and p.get("priceUsd") and area:
        unit = p["priceUsd"] / area
    key = f"{it['_kind']}:{a.get('subdistrictId')}"
    hist = state["unit_prices"].setdefault(key, [])
    hot = bool(unit and len(hist) >= 15 and unit < 0.8 * statistics.median(hist))
    return {"hot": hot, "unit": unit, "key": key}


# ---------------------------------------------------------------- main

def eligible(it, cfg, now):
    p = it.get("price") or {}
    usd = p.get("priceUsd")
    if not usd:
        return False
    rng = cfg["price_usd_day"] if it["_kind"] == "day" else cfg["price_usd_month"]
    if not (rng["min"] <= usd <= rng["max"]):
        return False
    if cfg.get("require_photo") and not it.get("appImages"):
        return False
    created = parse_dt(it.get("createDate"))
    if not created or now - created > timedelta(hours=cfg["max_age_hours"]):
        return False  # skip old listings that were merely bumped up
    return True


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
        if me.get("ok") and ch.get("ok"):
            mem = tg("getChatMember", {"chat_id": chat, "user_id": me["result"]["id"]}, token)
            r = mem.get("result") or {}
            log("bot in channel:", r.get("status"), "can_post:", r.get("can_post_messages"))

    items = collect(cfg)
    log(f"collected {len(items)} listings")
    if not items:
        # Usually ss.ge is temporarily slow or unreachable; the next run will try again.
        log("nothing collected this time (ss.ge unreachable or layout changed) — skipping run")
        return

    state = load_state()
    first_run = state is None
    if state is not None:
        state.setdefault("sampled", {})
    if first_run:
        state = {"posted": {}, "unit_prices": {}, "sampled": {}}

    now = datetime.now(timezone.utc)
    seen, fresh = set(), []
    for it in items:
        aid = str(it["applicationId"])
        if aid in seen:
            continue
        seen.add(aid)
        m = market_info(it, state)
        if aid not in state["sampled"] and m["unit"]:
            state["unit_prices"][m["key"]].append(round(m["unit"], 2))
            state["sampled"][aid] = now.isoformat()
        if aid in state["posted"] or not eligible(it, cfg, now):
            continue
        it["_market"] = m
        fresh.append(it)

    # Best first: below-market deals, then listings with metro nearby, furniture and
    # more photos, then the newest. With one post per run this picks the most
    # attractive listing of the period; the rest age out.
    def score(x):
        return (
            x["_market"].get("hot", False),
            bool(x.get("nearbySubwayStations")),
            bool(x.get("furniture")),
            min(x.get("imageCount") or 0, 8),
            parse_dt(x.get("createDate")) or now,
        )
    fresh.sort(key=score, reverse=True)
    log(f"{len(fresh)} new eligible listings")

    if preview:
        out = ["# Preview (no TELEGRAM_BOT_TOKEN / CHANNEL_ID set)\n"]
        for it in fresh[:cfg["max_posts_per_run"]]:
            out.append(format_post(it, cfg, it["_market"]))
            out.append("Buttons: " + json.dumps(buttons(it, cfg), ensure_ascii=False))
            img = (it.get("appImages") or [{}])[0].get("fileName")
            out.append(f"Photo: {full_image(img)}\n\n---\n")
        with open(PREVIEW_PATH, "w", encoding="utf-8") as f:
            f.write("\n".join(out))
        log("preview written")
        return

    if first_run:
        # Don't flood the channel on the very first run: remember what's there now
        # and post only the few newest.
        for it in fresh[cfg["max_posts_per_run"]:]:
            state["posted"][str(it["applicationId"])] = now.isoformat()
        fresh = fresh[:cfg["max_posts_per_run"]]

    sent = 0
    for it in fresh:
        if sent >= cfg["max_posts_per_run"]:
            break
        text = format_post(it, cfg, it["_market"])
        if send(it, text, buttons(it, cfg), token, chat, cfg.get("photos_per_post", 5)):
            state["posted"][str(it["applicationId"])] = now.isoformat()
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
