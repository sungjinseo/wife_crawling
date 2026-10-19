# 캡차 라벨러 (Gradio + Docker, CPU)

## 실행
```bash
docker compose up -d --build
# 브라우저: http://localhost:7860
```
데이터는 `./data`에 남습니다 (컨테이너 지워도 유지).

```
data/
  raw/       수집된 원본 (미라벨)
  labeled/   라벨 완료  labeled/<n>/<정답>.png
  trash/     판독불가로 삭제한 것
  model/     weights.h5, meta.json
```

## 탭
1. **수집** – 이미지 URL, 개수, 요청 간격 지정. 받아둔 이미지(zip 포함) 업로드도 가능.
2. **라벨링** – 이미지 보고 정답 입력 후 Enter. 정규식(기본 `\d{6}`)으로 오타 차단.
   모델이 있으면 예측값이 미리 채워져서, 맞으면 Enter만 치면 됨.
3. **학습** – 라벨 데이터로 학습, epoch별 loss 실시간 표시, 끝나면 검증셋(10%) 정확도 출력.
   모델 / 라벨 데이터 zip 내보내기.
4. **테스트** – 이미지 올리면 예측.

## 기존 K-apt 데이터 재사용
`learned.zip` 내용을 `data/labeled/0/` 에 풀면 바로 학습 가능
(단, 그 데이터는 5자리라 라벨링 탭 정규식은 `[0-9a-z]{5}` 로 변경).

## 버전 고정 이유
CaptchaCracker가 `layers.experimental` API를 써서 TF 2.16+에서 동작 안 함 → tensorflow-cpu 2.15.1.
gradio 4.44.1은 최신 huggingface_hub와 충돌 → 0.25.2 고정.

## 모델 가져오기
- 테스트 탭 → "모델 가져오기"에서 `weights.h5`+`meta.json` 또는 둘이 든 zip 업로드 → "이 모델로 교체"
- 또는 컨테이너 밖에서 `./data/model/` 에 두 파일을 직접 넣어도 됨 (볼륨 마운트, 재시작 불필요)
- 기존 모델은 `data/model/backup_<시각>/` 으로 자동 백업

## 투명 배경 처리
실제 캡차는 투명 배경 PNG. 수집·업로드·라벨링 예측·학습·테스트 모든 경로에서
알파 채널을 흰색으로 채운 흑백 이미지로 변환한 뒤 모델에 넣음 (`to_white_l`).
직접 짠 코드에서 예측할 때도 같은 변환이 필요함.
