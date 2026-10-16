# 분당 매물 수집 (안드로이드 앱)

휴대폰에서 네이버 매물을 수집해 서버로 올립니다. 수집 로직은 `tools/collect_naver.py` 를 그대로 앱 안에서 실행합니다 (Chaquopy).

## 서버 준비 (한 번)
1. `.env` 에 `UPLOAD_TOKEN=긴임의문자열` 추가 (예: `openssl rand -hex 24`)
2. `docker compose up -d --build` → `pipeline` 이 `https://BRIEFING_HOST/upload-api/naver` 로 업로드를 받음

## 앱 설치·설정
1. APK 설치 (출처를 알 수 없는 앱 허용)
2. 업로드 토큰 입력 → **저장하고 예약 적용**
3. **배터리 최적화에서 제외** 버튼 (자동 수집이 멈추지 않게)
4. **지금 수집** 으로 한 번 시험 → 끝나면 **서버 상태 확인**

## 빌드
- 로컬: JDK 17+, 파이썬 3.12, Android SDK → `cd android && ./gradlew assembleRelease`
- GitHub: `.github/workflows/android-apk.yml` 이 Actions 결과물로 APK 를 만듦
- 수집기를 고치면 `tools/collect_naver.py` 를 `android/app/src/main/python/` 에도 복사 (Actions 는 자동 복사)
