#!/usr/bin/env bash
# Temporary probe: checks which listing endpoints answer from GitHub's servers.
# Saves the first part of each response to probe/out/ for inspection.
set -u
OUT=probe/out
mkdir -p "$OUT"
UA="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36"

get() { # name url [extra curl args...]
  local name=$1 url=$2; shift 2
  local code
  code=$(curl -sS -L -m 30 -A "$UA" -H "Accept-Language: ru,en;q=0.8" "$@" \
    -o "$OUT/$name.body" -w "%{http_code} %{content_type}" "$url" 2>"$OUT/$name.err")
  echo "$name $code $(wc -c < "$OUT/$name.body") bytes" | tee -a "$OUT/summary.txt"
  head -c 300000 "$OUT/$name.body" > "$OUT/$name.txt"; rm -f "$OUT/$name.body"
}

: > "$OUT/summary.txt"
# myhome.ge (TNET)
get myhome_html "https://www.myhome.ge/s/qiravdeba-bina-Tbilisi/?deal_types=2&real_estate_types=1&cities=1&currency_id=1"
get tnet_api1 "https://api-statements.tnet.ge/v1/statements?deal_types=2&real_estate_types=1&cities=1&currency_id=1&page=1" \
  -H "Accept: application/json" -H "Origin: https://www.myhome.ge" -H "Referer: https://www.myhome.ge/" -H "x-website-key: myhome"
get tnet_api2 "https://api-statements.tnet.ge/v1/statements?deal_types=2&cities=1&page=1" -H "Accept: application/json" -H "x-website-key: myhome" -H "Origin: https://www.myhome.ge"
# ss.ge
get ss_html "https://home.ss.ge/ka/udzravi-qoneba/l/bina/qiravdeba?cityIdList=95"
get ss_html_ru "https://home.ss.ge/ru/%D0%BD%D0%B5%D0%B4%D0%B2%D0%B8%D0%B6%D0%B8%D0%BC%D0%BE%D1%81%D1%82%D1%8C/l/%D0%9A%D0%B2%D0%B0%D1%80%D1%82%D0%B8%D1%80%D0%B0/%D0%A1%D0%B4%D0%B0%D0%B5%D1%82%D1%81%D1%8F?cityIdList=95"
get ss_api "https://api-gateway.ss.ge/v1/RealEstate/LegendSearch" -X POST -H "Content-Type: application/json" \
  -H "Origin: https://home.ss.ge" -H "Referer: https://home.ss.ge/" -H "Accept: application/json" \
  --data '{"realEstateType":5,"realEstateDealType":1,"cityIdList":[95],"currencyId":1,"page":1,"pageSize":16,"order":1}'
echo done
