#!/usr/bin/env bash
# 네이버 매물 정기 수집 (집 PC, WSL 또는 리눅스·맥)
#   1) 가상환경 준비 (처음 한 번)        2) collect_naver.py 실행
#   3) 막히면(429·응답 없음) 기다렸다 이어서 재시도   4) 서버 data/ 로 업로드   5) 오래된 기록 정리
# 설정: tools/naver.env (naver.env.example 참고)   로그: data/logs/naver_YYYY-MM-DD.log
set -uo pipefail
TOOLS="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$TOOLS/.." && pwd)"
[ -f "$TOOLS/naver.env" ] && { set -a; . "$TOOLS/naver.env"; set +a; }

SERVER="${SERVER:-}"                       # 예: ubuntu@서버IP  (비우면 업로드 생략)
REMOTE_DIR="${REMOTE_DIR:-~/getting-home}"   # 서버의 프로젝트 폴더 (그 아래 data/ 로 올림)
SSH_KEY="${SSH_KEY:-}"                      # 기본 키(~/.ssh/id_*)가 아니면 경로 지정
SSH_PORT="${SSH_PORT:-22}"
RETRIES="${RETRIES:-3}"                     # 막혔을 때 다시 시도할 횟수
RETRY_WAIT_MIN="${RETRY_WAIT_MIN:-40}"     # 다시 시도 전 기다릴 시간(분)
KEEP_DAYS="${KEEP_DAYS:-30}"               # 로그·진행 기록 보관 기간 (naver_*.json 은 지우지 않음)
export DATA_DIR="${DATA_DIR:-$ROOT/data}"

TODAY="$(date +%F)"
LOGDIR="$DATA_DIR/logs"; mkdir -p "$LOGDIR"
LOG="$LOGDIR/naver_$TODAY.log"
exec >>"$LOG" 2>&1                         # 이후 출력은 모두 로그 파일로
log(){ echo "[$(date '+%F %T')] $*"; }

# 같은 시각에 두 번 돌지 않게 잠금
LOCK="$DATA_DIR/.naver.lock"
exec 9>"$LOCK"
if ! flock -n 9; then log "이미 실행 중이라 건너뜀"; exit 0; fi

# 1) 가상환경 (requests 만 필요)
VENV="$TOOLS/.venv"
if [ ! -x "$VENV/bin/python" ]; then
  log "가상환경 만드는 중"
  python3 -m venv "$VENV" && "$VENV/bin/pip" install -q requests || { log "가상환경 준비 실패 (sudo apt install python3-venv)"; exit 1; }
fi
PY="$VENV/bin/python"

# 2)~3) 수집: 같은 날 다시 돌리면 data/progress_날짜.json 으로 이어받음
OUT="$DATA_DIR/naver_$TODAY.json"
ok=0
for i in $(seq 0 "$RETRIES"); do
  log "수집 시작 (시도 $((i+1))/$((RETRIES+1)))"
  if "$PY" "$TOOLS/collect_naver.py"; then ok=1; break; fi
  [ "$i" -lt "$RETRIES" ] && { log "막힘 → ${RETRY_WAIT_MIN}분 뒤 이어서 다시 시도"; sleep $((RETRY_WAIT_MIN * 60)); }
done
if [ "$ok" != 1 ] || [ ! -s "$OUT" ]; then log "수집 실패: 오늘 파일 없음"; exit 1; fi
log "수집 완료: $OUT ($(du -h "$OUT" | cut -f1))"

# 4) 업로드 (SSH 키로 비밀번호 없이 접속되어야 함)
if [ -n "$SERVER" ]; then
  for i in 1 2 3; do
    if scp -q -P "$SSH_PORT" ${SSH_KEY:+-i "$SSH_KEY"} -o BatchMode=yes -o ConnectTimeout=20 "$OUT" "$SERVER:$REMOTE_DIR/data/"; then
      log "업로드 완료 → $SERVER:$REMOTE_DIR/data/ (서버가 10분 안에 브리핑 생성)"; break
    fi
    log "업로드 실패 ($i/3), 1분 뒤 다시"; sleep 60
    [ "$i" = 3 ] && { log "업로드 포기: SSH 키·서버 주소 확인"; exit 2; }
  done
else
  log "SERVER 설정이 없어 업로드는 건너뜀"
fi

# 5) 정리: 오래된 로그·진행 기록만 (수집 결과 naver_*.json 은 추이 분석용으로 보관)
find "$LOGDIR" -name 'naver_*.log' -mtime +"$KEEP_DAYS" -delete 2>/dev/null
find "$DATA_DIR" -maxdepth 1 -name 'progress_*.json' -mtime +3 -delete 2>/dev/null
log "끝"
