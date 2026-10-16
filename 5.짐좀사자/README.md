# getting-home: 분당 5개 동 아파트 매매 브리핑

## 구성

```
getting-home/
├── docker-compose.yml     서비스 정의
├── .env.example           키·설정 예시 (.env 로 복사)
├── setup.sh               처음 한 번: .env, 폴더 준비
├── app/                   서버 컨테이너 코드
│   ├── Dockerfile
│   ├── scheduler.py       매일 공공 수집 + 올라온 네이버 파일 감지 시 브리핑 생성
│   ├── collect_public.py  국토부 실거래, 브이월드 경계·지오코딩, R-ONE, 역·학교
│   ├── build_briefing.py  브리핑 HTML 생성
│   └── briefing_template.html
├── tools/                 집 PC 에서 쓰는 도구 (컨테이너에 포함되지 않음)
│   ├── collect_naver.py   네이버 매물 수집
│   └── upload_naver.sh    취합한 파일을 서버 data/ 로 업로드
├── web/nginx.conf         프론트(web) 컨테이너 설정, 8080
├── data/                  수집 데이터 (볼륨, git 제외)
└── output/                생성된 브리핑 (web 이 서비스)
```

| 서비스 | 어디서 | 하는 일 |
|---|---|---|
| `web` | 서버 | 프론트. `output/` 을 8080 으로 서비스하고 Traefik 이 `BRIEFING_HOST` 로 HTTPS·기본 인증을 붙임 |
| `pipeline` | 서버 | 매일 `PUBLIC_AT`(기본 06:00)에 공공데이터 수집, `CHECK_MIN`(기본 10)분마다 올라온 네이버 파일·공공데이터를 확인해 브리핑 재생성 |
| `collect-public`, `build` | 어디서나 | 수동 즉시 실행 (`--profile tools`) |

## 서버 배포

```bash
# 1. 파일 올리기 (기존 ~/getting-home 이 있으면 이름부터 바꾸기)
mv ~/getting-home ~/getting-home-old
unzip getting-home.zip && cd getting-home

# 2. 설정
./setup.sh                 # .env 생성
nano .env                  # 키 4개, BRIEFING_HOST
./setup.sh                 # 폴더 준비 + 브리핑 아이디·비밀번호 생성

# 3. 기존 데이터 옮기기 (있으면 다시 받지 않아도 됨)
cp ~/getting-home-old/data/bundang.db data/
cp -r ~/getting-home-old/data/public data/

# 4. 실행
docker compose up -d --build
docker compose logs -f pipeline
```

### Traefik 연결

`web` 서비스에 Traefik 라벨이 들어 있습니다 (라우터 이름 `getting-home`, 포트 8080, `traefik-proxy` 네트워크).
`.env` 에서 두 값만 정하면 됩니다.

- `BRIEFING_HOST`: 접속 주소 (예: `home.greatseo.dev`). DNS 에 서버 IP 로 A 레코드 추가
- `BRIEFING_AUTH`: 기본 인증. `./setup.sh` 가 아이디·비밀번호를 물어 자동으로 넣어 줌 (작은따옴표 필수)

접속: `https://BRIEFING_HOST/`, 지난 브리핑 목록: `/archive/`

## 네이버 파일 올리기

네이버 매물은 직접 취합해서 서버의 `data/` 에 올립니다. 서버가 10분 안에 감지해 브리핑을 다시 만들고,
형식이 잘못된 파일은 건너뛰고 직전 정상 파일을 씁니다 (`docker compose logs pipeline` 에 사유 표시).

```bash
# 집 PC: 수집 (도커 없이)
DATA_DIR=./data python3 tools/collect_naver.py
# 업로드 (이름 형식과 내용을 확인한 뒤 scp)
./tools/upload_naver.sh data/naver_2026-10-04.json ubuntu@서버IP
```

파일 규칙
- 이름: `naver_YYYY-MM-DD.json` (날짜가 가장 늦은 정상 파일을 사용)
- 내용: `collect_naver.py` 결과 형식. 최소 `date`(YYYY-MM-DD)와 `complexes` 목록이 있어야 하고,
  각 단지에 `complexNumber`, `name`, `dong`, `jibun`, `lat`, `lng`, `pyeongs`(공급·전용면적·세대수·방 수), `articles`(호가·면적·노출일 등)

## 자주 쓰는 명령

```bash
docker compose ps
docker compose logs -f pipeline
docker compose --profile tools run --rm collect-public --only facilities   # 역·학교 다시 받기
docker compose --profile tools run --rm build                               # 브리핑 바로 생성
docker compose restart pipeline
```

## 데이터 폴더

| 경로 | 내용 |
|---|---|
| `data/bundang.db` | 실거래, 단지 좌표, R-ONE |
| `data/public/` | 경계, 역, 선로, 학교, `custom_facilities.json` (화면에서 내보낸 시설) |
| `data/naver_YYYY-MM-DD.json` | 네이버 매물 (직접 업로드) |
| `output/` | `index.html`(최신), `briefing_YYYY-MM-DD.html` |
