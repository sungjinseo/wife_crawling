#!/usr/bin/env bash
# 처음 한 번: .env, 폴더 준비
set -e
cd "$(dirname "$0")"
[ -f .env ] || { cp .env.example .env; echo ".env 를 만들었습니다. 키를 채운 뒤 다시 실행하세요."; exit 0; }
mkdir -p data/raw data/cache output
set -a; . ./.env; set +a
OUT="${OUTPUT_PATH:-./output}"
mkdir -p "$OUT"
echo "브리핑 출력 폴더: $OUT"

# Traefik 기본 인증 (BRIEFING_AUTH 가 비어 있으면 생성해서 .env 에 기록)
if [ -z "${BRIEFING_AUTH}" ]; then
  read -rp "브리핑 아이디: " U
  read -rsp "비밀번호: " PW; echo
  HASH=$(docker run --rm httpd:2.4-alpine htpasswd -nb "$U" "$PW" | head -1)
  sed -i "/^BRIEFING_AUTH=/d" .env
  printf "BRIEFING_AUTH='%s'\n" "$HASH" >> .env
  echo "BRIEFING_AUTH 를 .env 에 기록했습니다"
fi
docker network inspect traefik-proxy >/dev/null 2>&1 || echo "! traefik-proxy 네트워크가 없습니다 (Traefik 쪽에서 만든 네트워크 이름 확인)"
echo "준비 완료: docker compose up -d --build"
