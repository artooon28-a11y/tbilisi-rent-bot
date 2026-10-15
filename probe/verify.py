import urllib.request, urllib.parse
UA="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36"
slug="Аренда-3-комнатная-Квартира-Диди-Дигоми-37024861"
q=urllib.parse.quote
urls=[
 "https://home.ss.ge/ru/udzravi-qoneba/"+q(slug),
 "https://home.ss.ge/ru/"+q("недвижимость")+"/"+q(slug),
 "https://home.ss.ge/ru/udzravi-qoneba/x-37024861",
 "https://home.ss.ge/ka/udzravi-qoneba/qiravdeba-3-otaxiani-bina-didi-dighomshi-37024861",
 "https://static.ss.ge/20261015/2_ca034bd7-d302-4f83-9d74-c64509c78ae6.jpg",
 "https://static.ss.ge/20261015/2_ca034bd7-d302-4f83-9d74-c64509c78ae6_Thumb.jpg",
]
with open("probe/verify.txt","w") as f:
  for u in urls:
    try:
      r=urllib.request.urlopen(urllib.request.Request(u,headers={"User-Agent":UA}),timeout=40)
      b=r.read(); t=b[:200000].decode("utf-8","replace")
      import re; m=re.search(r"<title>([^<]*)",t)
      f.write(f"{r.status} {len(b)} {r.headers.get('content-type')} final={urllib.parse.unquote(r.geturl())} title={m.group(1) if m else ''}\n")
    except Exception as e:
      f.write(f"ERR {u} {e!r}\n")
