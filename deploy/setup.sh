#!/usr/bin/env bash
# UQI 전체 서비스 환경 셋업 자동화 — README "Installation" 1~6 단계 자동화
#
# 사용:
#   bash deploy/setup.sh                    # 전 단계 인터랙티브
#   bash deploy/setup.sh --yes              # 모든 prompt 'yes' (CI / 자동 셋업)
#   bash deploy/setup.sh --skip-clone       # clone 단계 건너뛰기
#   bash deploy/setup.sh --skip-aer-build   # qiskit-aer fork 빌드 건너뛰기
#   bash deploy/setup.sh --skip-docker      # docker build 건너뛰기
#   bash deploy/setup.sh --skip-systemd     # systemd 설치 건너뛰기
#   bash deploy/setup.sh --skip-notion      # notion-backup (quartz-site) 건너뛰기
#
# 환경:
#   ORIENTOM_DIR    부모 디렉토리 (default: $HOME/work/orientom)
#   PYTHON_BIN      Python 실행파일 (default: python3.12)
#   ENV_GPG_PATH    .env.gpg 백업본 경로 (지정 시 자동 복구. --yes 와 함께 권장)

set -euo pipefail

# ─── 인자 파싱 ───────────────────────────────────────
ASSUME_YES=0
SKIP_CLONE=0
SKIP_AER_BUILD=0
SKIP_DOCKER=0
SKIP_SYSTEMD=0
SKIP_NOTION=0
for arg in "$@"; do
  case "$arg" in
    --yes)             ASSUME_YES=1 ;;
    --skip-clone)      SKIP_CLONE=1 ;;
    --skip-aer-build)  SKIP_AER_BUILD=1 ;;
    --skip-docker)     SKIP_DOCKER=1 ;;
    --skip-systemd)    SKIP_SYSTEMD=1 ;;
    --skip-notion)     SKIP_NOTION=1 ;;
    -h|--help)
      sed -n '2,16p' "$0"; exit 0 ;;
    *)
      echo "알 수 없는 인자: $arg" >&2; exit 1 ;;
  esac
done

ORIENTOM_DIR="${ORIENTOM_DIR:-$HOME/work/orientom}"
PYTHON_BIN="${PYTHON_BIN:-python3.12}"
# 부모 ORIENTOM_DIR 자체가 joygoLive/orientom 의 working tree 가 된다.
# 그 안에 QUWA/, alg-files/, azure/ 등 orientom 의 subfolder 가 자동 생성.
# uqi/, quartz-site/, obsidian-vault/, orientom-notion-pipeline/ 은
# orientom 안에 nested 된 **별도 GitHub repo** 들.
UQI_DIR="$ORIENTOM_DIR/uqi"
QUWA_DIR="$ORIENTOM_DIR/QUWA"                       # orientom 의 subfolder
QUARTZ_DIR="$ORIENTOM_DIR/quartz-site"
OBSIDIAN_DIR="$ORIENTOM_DIR/obsidian-vault"
NOTION_PIPELINE_DIR="$ORIENTOM_DIR/orientom-notion-pipeline"
AER_DIR="$HOME/work/qiskit/qiskit-aer"
VENV="$QUWA_DIR/.venv_transpile"                    # = $ORIENTOM_DIR/QUWA/.venv_transpile

# 본 스크립트는 Ubuntu/Debian Linux + NVIDIA GPU + Docker + Python 3.12
# 환경 (DGX Spark 기준) 에 맞춰 작성. 다른 환경에서의 조정 포인트는
# README "기타 OS 안내" 섹션 참조 (macOS 는 CUDA/qiskit-aer GPU/Docker GPU 비호환).

# ─── 헬퍼 ───────────────────────────────────────────
log()  { printf "\n\033[1;36m▶ %s\033[0m\n" "$*"; }
warn() { printf "\033[1;33m⚠ %s\033[0m\n" "$*"; }
err()  { printf "\033[1;31m✗ %s\033[0m\n" "$*" >&2; }
ok()   { printf "\033[1;32m✓ %s\033[0m\n" "$*"; }

confirm() {
  [ "$ASSUME_YES" -eq 1 ] && return 0
  local prompt="${1:-진행할까요?} [y/N] "
  read -r -p "$prompt" yn
  [[ "$yn" =~ ^[Yy] ]]
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || { err "$1 명령 필요"; exit 1; }
}

# ─── 환경 감지 ──────────────────────────────────────
# 가정하는 3가지 환경:
#   (1) 동일환경 — aarch64 Linux + NVIDIA (DGX Spark)
#   (2) macOS    — M-series (arm64) / Intel — GPU/CUDA/systemd 모두 X
#   (3) x86 Linux + NVIDIA (H100 등) — 모든 단계 동작, 단 qiskit-aer fork 는 aarch64 전용
OS="$(uname -s)"                # Linux / Darwin
ARCH="$(uname -m)"              # aarch64 / x86_64 / arm64
HAVE_NVIDIA=0;  command -v nvidia-smi >/dev/null 2>&1 && HAVE_NVIDIA=1
HAVE_SYSTEMD=0; command -v systemctl >/dev/null 2>&1 && HAVE_SYSTEMD=1
HAVE_DOCKER=0;  command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1 && HAVE_DOCKER=1
# qiskit-aer Jetson-patch fork 는 aarch64+NVIDIA 에서만 의미 있음
USE_AER_FORK=0
[ "$ARCH" = "aarch64" ] && [ "$HAVE_NVIDIA" -eq 1 ] && USE_AER_FORK=1

# ─── 사전 체크 (필수만 hard fail) ───────────────────
log "환경: OS=$OS / ARCH=$ARCH / NVIDIA=$HAVE_NVIDIA / systemd=$HAVE_SYSTEMD / docker=$HAVE_DOCKER"
require_cmd git
require_cmd "$PYTHON_BIN"
$PYTHON_BIN -c "import sys; assert sys.version_info[:2] == (3,12)" 2>/dev/null \
  || warn "Python 3.12 권장. 현재: $($PYTHON_BIN --version)"
[ "$HAVE_DOCKER" -eq 0 ]  && warn "docker 없음/미동작 — embed/rerank 컨테이너 빌드 자동 skip"
[ "$HAVE_NVIDIA" -eq 0 ]  && warn "NVIDIA GPU 없음 — qiskit-aer GPU 빌드 / CUDA 패키지 자동 skip"
[ "$HAVE_SYSTEMD" -eq 0 ] && warn "systemd 없음 (macOS 등) — systemd unit 설치 자동 skip"
[ "$USE_AER_FORK" -eq 0 ] && warn "qiskit-aer Jetson fork 미적용 — PyPI stock (CPU) 사용"
ok "필수 환경 OK (git / $PYTHON_BIN)"

# ─── 1. clone ───────────────────────────────────────
if [ "$SKIP_CLONE" -eq 0 ]; then
  log "1) 프로젝트 clone (부모 $ORIENTOM_DIR = joygoLive/orientom working tree)"

  # 1-a. 부모 orientom repo — $ORIENTOM_DIR 자체가 working tree
  #      (QUWA/, alg-files/, azure/ ... 가 subfolder 로 자동 생성)
  if [ -d "$ORIENTOM_DIR/.git" ]; then
    ok "  orientom 이미 clone 됨 ($ORIENTOM_DIR)"
  elif [ -e "$ORIENTOM_DIR" ] && [ -n "$(ls -A "$ORIENTOM_DIR" 2>/dev/null)" ]; then
    err "  $ORIENTOM_DIR 이미 존재하고 비어있지 않음 — 정리 후 재실행 필요"
    exit 1
  else
    git clone git@github.com:joygoLive/orientom.git "$ORIENTOM_DIR"
  fi

  # 1-b. uqi (nested 별도 repo, $ORIENTOM_DIR 안에)
  [ -d "$UQI_DIR/.git" ] || git clone git@github.com:joygoLive/uqi.git "$UQI_DIR"

  # 1-c. qiskit-aer Jetson fork — aarch64+NVIDIA 일 때만 자동 clone
  #      ($ORIENTOM_DIR 밖, $HOME/work/qiskit/ 아래)
  if [ "$USE_AER_FORK" -eq 1 ] && [ "$SKIP_AER_BUILD" -eq 0 ]; then
    mkdir -p "$(dirname "$AER_DIR")"
    [ -d "$AER_DIR/.git" ] || git clone -b jetson-patch git@github.com:joygoLive/qiskit-aer.git "$AER_DIR"
  fi

  # 1-d. (선택) notion-backup 스택 — quartz fork + 콘텐츠 vault + symlink
  if [ "$SKIP_NOTION" -eq 0 ] && confirm "notion-backup 스택 (quartz fork + obsidian-vault) 도 clone 하시겠습니까?"; then
    # quartz fork (커스터마이징 포함) — upstream 은 'upstream' remote 로 추가
    if [ ! -d "$QUARTZ_DIR/.git" ]; then
      git clone git@github.com:joygoLive/quartz-site.git "$QUARTZ_DIR"
      git -C "$QUARTZ_DIR" remote add upstream https://github.com/jackyzha0/quartz.git 2>/dev/null || true
    fi
    # obsidian-vault (Notion markdown 백업)
    [ -d "$OBSIDIAN_DIR/.git" ] || git clone git@github.com:joygoLive/orientom-notion-backup.git "$OBSIDIAN_DIR"
    # quartz-site/content → ../obsidian-vault 심볼릭
    if [ ! -L "$QUARTZ_DIR/content" ]; then
      # upstream Quartz 의 빈 content/ (or .gitkeep) 가 있다면 비운다
      rm -rf "$QUARTZ_DIR/content"
      ln -s ../obsidian-vault "$QUARTZ_DIR/content"
    fi
    # (선택의 선택) notion sync 자동화 스크립트
    if confirm "  → orientom-notion-pipeline (주기적 sync 스크립트) 도 clone?"; then
      [ -d "$NOTION_PIPELINE_DIR/.git" ] || git clone git@github.com:joygoLive/orientom-notion-pipeline.git "$NOTION_PIPELINE_DIR"
    fi
  fi
  ok "clone 단계 완료"
else
  warn "1) clone 단계 skip"
fi

# ─── 2. venv ────────────────────────────────────────
log "2) 공유 venv 생성/활성화: $VENV"
if [ ! -d "$VENV" ]; then
  $PYTHON_BIN -m venv "$VENV"
  ok "venv 생성"
else
  ok "venv 이미 존재 — 재사용"
fi
# shellcheck disable=SC1091
source "$VENV/bin/activate"
pip install --upgrade pip -q

# ─── 3. qiskit-aer GPU 빌드 (aarch64+NVIDIA 자동) ───
if [ "$USE_AER_FORK" -eq 1 ] && [ "$SKIP_AER_BUILD" -eq 0 ] && [ -d "$AER_DIR" ]; then
  log "3) qiskit-aer fork 빌드 ($AER_DIR, CUDA backend)"
  pip install -q pybind11 scikit-build cmake
  pushd "$AER_DIR" >/dev/null
    python setup.py bdist_wheel -- -DAER_THRUST_BACKEND=CUDA
    pip install dist/qiskit_aer-*linux_*.whl --force-reinstall
  popd >/dev/null
  ok "qiskit-aer GPU wheel 설치"
else
  warn "3) qiskit-aer GPU 빌드 skip — PyPI stock 사용 (CPU)"
fi

# ─── 4. UQI 의존성 설치 ─────────────────────────────
if [ "$HAVE_NVIDIA" -eq 1 ]; then
  log "4) UQI requirements 설치 (시간 소요 — quantum SDK 많음)"
  pip install -r "$UQI_DIR/requirements.txt"
else
  log "4) UQI requirements 설치 — CUDA 패키지 제외 (NVIDIA 없음)"
  filtered="$(mktemp)"
  grep -vE "^(cudaq|cuda-(quantum|bindings|core|pathfinder)|cupy-cuda|jax-cuda12-|nvidia-)" \
    "$UQI_DIR/requirements.txt" > "$filtered"
  pip install -r "$filtered"
  rm -f "$filtered"
fi
ok "pip install 완료"

# ─── 5. embed/rerank Docker 이미지 (NVIDIA + docker 필요) ───
if [ "$SKIP_DOCKER" -eq 0 ] && [ "$HAVE_DOCKER" -eq 1 ] && [ "$HAVE_NVIDIA" -eq 1 ]; then
  if docker image inspect uqi-rag:0.1 >/dev/null 2>&1; then
    ok "5) uqi-rag:0.1 이미 존재 — 재빌드 skip (강제 재빌드는 'docker build -t uqi-rag:0.1 deploy/')"
  else
    log "5) embed/rerank Docker 이미지 빌드 (uqi-rag:0.1, 약 24GB, 5~15분)"
    docker build -t uqi-rag:0.1 "$UQI_DIR/deploy"
    ok "docker 이미지 빌드 완료"
  fi
else
  warn "5) docker GPU 이미지 빌드 skip — embed/rerank 는 호스트에서 직접 실행 필요 (macOS / non-NVIDIA)"
fi

# ─── 6. systemd unit 설치 (Linux+systemd 자동) ──────
if [ "$SKIP_SYSTEMD" -eq 0 ] && [ "$HAVE_SYSTEMD" -eq 1 ]; then
  log "6) systemd 유닛 설치 (/etc/systemd/system/, sudo 필요)"
  for unit in uqi-mcp.service uqi-embed.service uqi-rerank.service ngrok-8765.service; do
    sudo cp "$UQI_DIR/deploy/systemd/$unit" "/etc/systemd/system/"
  done
  # 사용자명이 'sean' 이 아니면 경로 보정
  if [ "$USER" != "sean" ]; then
    warn "현재 사용자 ($USER) ≠ unit 파일의 hardcoded 'sean' — 경로 일괄 치환"
    sudo sed -i "s|/home/sean/|/home/$USER/|g; s|User=sean|User=$USER|g" \
      /etc/systemd/system/uqi-mcp.service \
      /etc/systemd/system/ngrok-8765.service
  fi
  sudo systemctl daemon-reload
  sudo systemctl enable uqi-embed uqi-rerank uqi-mcp ngrok-8765
  ok "systemd 유닛 enable (start 는 .env 채운 후 수동)"
else
  warn "6) systemd 설치 skip — macOS 는 launchd 로 별도 구성 또는 'python mcp_server.py' 수동 실행"
fi

# ─── 7. (선택) notion-backup 빌드 + symlink ─────────
# 1-d 에서 clone 된 경우만 진행. uqi 와는 분리된 프로젝트지만 1-command UX
# 위해 같이 오케스트레이션.
if [ "$SKIP_NOTION" -eq 0 ] && [ -d "$QUARTZ_DIR" ]; then
  # content/ symlink 검증 — 없거나 깨졌으면 step 1-d 가 안 돌았다는 신호
  if [ ! -L "$QUARTZ_DIR/content" ] || [ ! -d "$QUARTZ_DIR/content" ]; then
    warn "7) quartz-site/content symlink 없음 (obsidian-vault clone 안 됐을 가능성). build 스킵."
  elif confirm "notion-backup quartz 빌드 + uqi/webapp symlink 도 진행하시겠습니까?"; then
    log "7) notion-backup 빌드 ($QUARTZ_DIR)"
    if ! command -v npm >/dev/null 2>&1; then
      warn "npm 미설치 — quartz 빌드 스킵. 'sudo apt install nodejs npm' 후 재실행"
    else
      pushd "$QUARTZ_DIR" >/dev/null
        [ -d node_modules ] || npm install
        npx quartz build
      popd >/dev/null
      # uqi/webapp/notion-backup → quartz-site/public 심볼릭 (재생성)
      webapp_link="$UQI_DIR/webapp/notion-backup"
      if [ -L "$webapp_link" ] || [ -e "$webapp_link" ]; then
        rm -f "$webapp_link"
      fi
      ln -s ../../quartz-site/public "$webapp_link"
      ok "notion-backup symlinks 설정:"
      ok "  $QUARTZ_DIR/content     → ../obsidian-vault"
      ok "  $webapp_link → ../../quartz-site/public"
    fi
  fi
else
  warn "7) notion-backup skip (--skip-notion 또는 step 1-d 미진행)"
fi

# ─── 8. (선택) .env 백업본 복구 ─────────────────────
# - ENV_GPG_PATH 지정 시 자동 (--yes 모드와 호환)
# - 미지정 + 인터랙티브: 흔한 경로 자동 검색 → 없으면 수동 입력
# - --yes + ENV_GPG_PATH 없으면 skip (수동 작성 필요)
ENV_TARGET="$UQI_DIR/.env"
if [ -f "$ENV_TARGET" ]; then
  warn "8) .env 이미 존재 — 복구 skip"
else
  ENV_SRC=""
  if [ -n "${ENV_GPG_PATH:-}" ] && [ -f "$ENV_GPG_PATH" ]; then
    ENV_SRC="$ENV_GPG_PATH"
    log "8) .env 복구 — ENV_GPG_PATH 사용: $ENV_SRC"
  elif [ "$ASSUME_YES" -eq 0 ]; then
    # 흔한 경로 자동 검색 (인터랙티브 모드만)
    for cand in \
        "$HOME/GoogleDrive/secrets/.env.gpg" \
        "$HOME/Google Drive/My Drive/secrets/.env.gpg" \
        "$HOME/Insync/secrets/.env.gpg" \
        "$HOME/Downloads/.env.gpg" \
        "$HOME/.env.gpg"; do
      [ -f "$cand" ] && { ENV_SRC="$cand"; break; }
    done
    if [ -n "$ENV_SRC" ]; then
      log "8) .env.gpg 자동 발견: $ENV_SRC"
      confirm "  → 이 파일로 .env 복구하시겠습니까?" || ENV_SRC=""
    else
      log "8) .env.gpg 자동 검색 실패 (Google Drive sync 폴더 등 확인)"
      read -r -p "  .env.gpg 경로 직접 입력 (Enter 건너뛰기): " gpath
      [ -n "$gpath" ] && [ -f "$gpath" ] && ENV_SRC="$gpath"
    fi
  fi

  if [ -n "$ENV_SRC" ]; then
    if gpg -d -o "$ENV_TARGET" "$ENV_SRC"; then
      chmod 600 "$ENV_TARGET"
      ok ".env 복구 완료 ($ENV_SRC → $ENV_TARGET, chmod 600)"
    else
      err ".env 복구 실패 (passphrase 오류 가능). 수동: gpg -d -o $ENV_TARGET <path>"
    fi
  else
    warn "8) .env 복구 skip — README 'Environment setup' 보고 수동 작성 (또는 ENV_GPG_PATH=... 로 재실행)"
  fi
fi

# ─── 마무리 안내 ────────────────────────────────────
echo ""
echo "────────────────────────────────────────────────────────────"
echo "✓ 셋업 완료 — 남은 수동 단계:"
echo ""
if [ -f "$ENV_TARGET" ]; then
  echo "1. .env ✓ 존재 — 내용 검토만 ($ENV_TARGET)"
else
  echo "1. .env 채우기 ($ENV_TARGET)"
  echo "   README 'Environment setup' 섹션 참조 — Anthropic / Pasqal / Azure /"
  echo "   IBM / IQM / Braket / Quandela API 키 등"
  echo "   백업본 있으면: ENV_GPG_PATH=<path> bash deploy/setup.sh --yes  (재실행)"
fi
echo ""

if [ "$HAVE_SYSTEMD" -eq 1 ]; then
  cat <<EOF
2. ngrok authtoken (외부 접근 시)
   sudo snap install ngrok    # 미설치 시
   ngrok config add-authtoken <YOUR_TOKEN>
   # reserved domain 쓰려면 ngrok-8765.service 의 --url= 교체 후
   sudo systemctl daemon-reload

3. 서비스 시작
   sudo systemctl start uqi-embed uqi-rerank uqi-mcp ngrok-8765
   sudo systemctl is-active uqi-embed uqi-rerank uqi-mcp ngrok-8765

4. 헬스체크
   curl -s http://127.0.0.1:7997/health
   curl -s http://127.0.0.1:7998/health
   ss -ltn 'sport = :8765'
EOF
else
  cat <<EOF
2. embed / rerank 서버 직접 실행 (systemd 없음 — 별도 터미널 권장)
   source $VENV/bin/activate
   UQI_EMBED_DEVICE=cpu  python $UQI_DIR/deploy/embed_server.py   &
   UQI_RERANK_DEVICE=cpu python $UQI_DIR/deploy/rerank_server.py  &
   # macOS GPU 가속은 없음 — CPU 모드라 매우 느림

3. uqi-mcp 직접 실행
   source $VENV/bin/activate
   python $UQI_DIR/src/mcp_server.py --host 0.0.0.0 --port 8765 --transport sse

4. (선택) ngrok 외부 접근
   brew install ngrok/ngrok/ngrok
   ngrok config add-authtoken <YOUR_TOKEN>
   ngrok http 8765

5. 헬스체크
   curl -s http://127.0.0.1:7997/health
   curl -s http://127.0.0.1:7998/health
   curl -s http://127.0.0.1:8765/   # webapp HTML
EOF
fi

cat <<EOF

webapp: http://localhost:8765/  (ngrok 시작 시 외부 URL 도 접근 가능)
────────────────────────────────────────────────────────────
EOF
