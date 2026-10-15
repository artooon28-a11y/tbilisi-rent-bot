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
  mv "$OUT/$name.body" "$OUT/$name.txt"
}

: > "$OUT/summary.txt"
get ss_list "https://home.ss.ge/ka/udzravi-qoneba/l/bina/qiravdeba?cityIdList=95"
get ss_detail "https://home.ss.ge/ka/udzravi-qoneba/qiravdeba-2-otaxiani-bina-saburtaloze-37011943"
get ss_list_daily "https://home.ss.ge/ka/udzravi-qoneba/l/bina/qiravdeba-dghiurad?cityIdList=95"
get myhome_api_v2 "https://api-statements.tnet.ge/v1/statements?deal_types=2&cities=1&page=1" -H "Accept: application/json" -H "Origin: https://www.myhome.ge" -H "Referer: https://www.myhome.ge/" -H "locale: ka" -H "x-website-key: myhome.ge"
get myhome_ru "https://www.myhome.ge/ru/s/"
echo done
