import json, re, urllib.request

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36"
BASE = "https://home.ss.ge"
VARIANTS = {
    "p1": "/ka/udzravi-qoneba/l/bina/qiravdeba?cityIdList=95",
    "p2": "/ka/udzravi-qoneba/l/bina/qiravdeba?cityIdList=95&page=2",
    "p4": "/ka/udzravi-qoneba/l/bina/qiravdeba?cityIdList=95&page=4",
    "order1": "/ka/udzravi-qoneba/l/bina/qiravdeba?cityIdList=95&order=1",
    "order2": "/ka/udzravi-qoneba/l/bina/qiravdeba?cityIdList=95&order=2",
    "sort1": "/ka/udzravi-qoneba/l/bina/qiravdeba?cityIdList=95&sort=1",
    "ru": "/ru/udzravi-qoneba/l/bina/qiravdeba?cityIdList=95",
    "en": "/en/udzravi-qoneba/l/bina/qiravdeba?cityIdList=95",
}


def fetch(path):
    req = urllib.request.Request(BASE + path, headers={"User-Agent": UA, "Accept-Language": "ru,en;q=0.8"})
    with urllib.request.urlopen(req, timeout=40) as r:
        return r.geturl(), r.read().decode("utf-8", "replace")


for name, path in VARIANTS.items():
    try:
        url, html = fetch(path)
        m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.S)
        d = json.loads(m.group(1))
        pp = d["props"]["pageProps"]
        items = pp["applicationList"]["realStateItemModel"]
        print(f"== {name} final={url} locale={pp.get('appLocale')} n={len(items)} query={d.get('query')}")
        for x in items:
            a = x["address"]
            print("  ", x["applicationId"], x["createDate"][:16], x["orderDate"][:16], "vip", x["vipStatus"],
                  x["price"]["priceUsd"], a.get("districtTitle"), "/", a.get("subdistrictTitle"), "|", x["title"][:50])
        if name == "ru":
            cities = pp["locations"]["visibleCities"]
            tb = [c for c in cities if c["cityId"] == 95]
            print("TBILISI_LOCATIONS", json.dumps(tb, ensure_ascii=False)[:20000])
    except Exception as e:
        print(f"== {name} ERROR {e!r}")
