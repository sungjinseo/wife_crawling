#!/usr/bin/env bash
# WSL(Ubuntu)에서 안드로이드 빌드 환경 한 번에 준비: JDK 17, 파이썬 3.12, Android SDK(34)
#   실행: cd android && bash setup_wsl.sh   (다 끝나면 터미널을 다시 열기)
set -e
SDK="${ANDROID_HOME:-$HOME/android-sdk}"

echo "[1] JDK 17 · 파이썬 · 압축 도구"
sudo apt-get update -qq
sudo apt-get install -y -qq openjdk-17-jdk unzip curl python3 python3-venv
PYV=$(python3 -c 'import sys;print(f"{sys.version_info[0]}.{sys.version_info[1]}")')
if [ "$PYV" != "3.12" ]; then
  echo "  ! python3 가 $PYV 입니다. 앱은 파이썬 3.12 로 빌드합니다 (Ubuntu 24.04 권장)."
  echo "    22.04 라면: sudo add-apt-repository ppa:deadsnakes/ppa && sudo apt install python3.12"
  echo "    그 뒤 app/build.gradle.kts 의 buildPython(\"python3\") 을 buildPython(\"python3.12\") 로 바꾸세요."
fi

echo "[2] Android SDK → $SDK"
if [ ! -x "$SDK/cmdline-tools/latest/bin/sdkmanager" ]; then
  mkdir -p "$SDK/cmdline-tools" && cd /tmp
  curl -sL -o cmdtools.zip https://dl.google.com/android/repository/commandlinetools-linux-11076708_latest.zip
  unzip -q -o cmdtools.zip -d "$SDK/cmdline-tools" && rm -rf "$SDK/cmdline-tools/latest" && mv "$SDK/cmdline-tools/cmdline-tools" "$SDK/cmdline-tools/latest"
  cd - >/dev/null
fi
yes | "$SDK/cmdline-tools/latest/bin/sdkmanager" --sdk_root="$SDK" --licenses >/dev/null || true
"$SDK/cmdline-tools/latest/bin/sdkmanager" --sdk_root="$SDK" "platforms;android-34" "build-tools;34.0.0" "platform-tools" >/dev/null

echo "[3] 환경 변수 · local.properties"
grep -q "ANDROID_HOME=" ~/.bashrc || cat >> ~/.bashrc <<RC
export ANDROID_HOME="$SDK"
export PATH="\$ANDROID_HOME/platform-tools:\$PATH"
RC
echo "sdk.dir=$SDK" > "$(dirname "$0")/local.properties"
chmod +x "$(dirname "$0")/gradlew"
echo "준비 완료. 새 터미널에서: cd android && ./gradlew assembleRelease"
