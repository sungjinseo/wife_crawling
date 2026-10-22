#!/usr/bin/env bash
# 직접 취합한 네이버 파일을 서버에 올리기
#   ./upload_naver.sh data/naver_2026-10-04.json ubuntu@서버IP
#   파일 이름은 naver_YYYY-MM-DD.json 이어야 합니다. 서버가 10분 안에 감지해 브리핑을 다시 만듭니다.
set -e
F="$1"; SERVER="$2"; REMOTE_DIR="${3:-~/getting-home}"
[ -f "$F" ] && [ -n "$SERVER" ] || { echo "사용법: $0 <naver_YYYY-MM-DD.json> <user@서버> [원격폴더]"; exit 1; }
case "$(basename "$F")" in naver_????-??-??.json) ;; *) echo "파일 이름은 naver_YYYY-MM-DD.json 형식이어야 합니다"; exit 1;; esac
python3 -c "import json,sys; d=json.load(open(sys.argv[1],encoding='utf-8')); assert d.get('date') and d.get('complexes'), 'date/complexes 없음'; print('형식 확인:', d['date'], '단지', len(d['complexes']), '개')" "$F"
scp "$F" "$SERVER:$REMOTE_DIR/data/"
echo "업로드 완료"
