"""Temporary: checks whether myhome.ge answers plain requests from GitHub's servers."""
import re, time, urllib.request, urllib.error
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36"
B = "iTm9dHHw1B23TQzLiOFOC"
URLS = {
    "html_ru": "https://www.myhome.ge/ru/nedvizhimost/?deal_types=2&real_estate_types=1&cities=1",
    "html_ka": "https://www.myhome.ge/udzravi-qoneba/?deal_types=2&real_estate_types=1&cities=1",
    "data_json": f"https://www.myhome.ge/_next/data/{B}/ru/nedvizhimost.json?deal_types=2&real_estate_types=1&cities=1&slug=nedvizhimost",
    "sitemap": "https://www.myhome.ge/sitemap_index.xml",
    "img": "https://static-api-statements.tnet.ge/uploads/202610/20261013/statements/idUXqdD6ace0b0d511c2.webp",
    "home_bare": "https://myhome.ge/",
}
with open("probe/myhome_result.txt", "w") as f:
    for name, u in URLS.items():
        t0 = time.time()
        try:
            r = urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": UA, "Accept-Language": "ru,en;q=0.8", "Accept": "text/html,application/json,*/*"}), timeout=40)
            body = r.read(400000).decode("utf-8", "replace")
            title = re.search(r"<title>([^<]*)", body)
            f.write(f"{name} {r.status} {r.headers.get('content-type')} {len(body)}b {time.time()-t0:.1f}s title={title.group(1) if title else ''} has_statements={'price_total' in body}\n")
        except urllib.error.HTTPError as e:
            body = e.read(3000).decode("utf-8", "replace")
            title = re.search(r"<title>([^<]*)", body)
            f.write(f"{name} HTTP {e.code} server={e.headers.get('server')} cf-mitigated={e.headers.get('cf-mitigated')} title={title.group(1) if title else body[:80]!r}\n")
        except Exception as e:
            f.write(f"{name} ERR {e!r}\n")
