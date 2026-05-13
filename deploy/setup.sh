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
UQI_DIR="$ORIENTOM_DIR/uqi"
QUWA_DIR="$ORIENTOM_DIR/QUWA"
QUARTZ_DIR="$ORIENTOM_DIR/quartz-site"
AER_DIR="$HOME/work/qiskit/qiskit-aer"
VENV="$QUWA_DIR/.venv_transpile"

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

# ─── 사전 체크 ──────────────────────────────────────
log "사전 체크"
require_cmd git
require_cmd "$PYTHON_BIN"
require_cmd docker
$PYTHON_BIN -c "import sys; assert sys.version_info[:2] == (3,12)" 2>/dev/null \
  || warn "Python 3.12 권장. 현재: $($PYTHON_BIN --version)"
docker info >/dev/null 2>&1 || { err "docker daemon 미동작 / 권한 X"; exit 1; }
ok "git / $PYTHON_BIN / docker 확인"

# ─── 1. clone ───────────────────────────────────────
if [ "$SKIP_CLONE" -eq 0 ]; then
  log "1) 프로젝트 clone (sibling 구조: $ORIENTOM_DIR/)"
  mkdir -p "$ORIENTOM_DIR"
  [ -d "$UQI_DIR/.git" ]   || git clone git@github.com:joygoLive/uqi.git "$UQI_DIR"
  [ -d "$QUWA_DIR/.git" ]  || git clone git@github.com:joygoLive/orientom.git "$QUWA_DIR"
  if confirm "qiskit-aer Jetson/GH200 fork 도 clone 하시겠습니까?"; then
    mkdir -p "$(dirname "$AER_DIR")"
    [ -d "$AER_DIR/.git" ] || git clone -b jetson-patch git@github.com:joygoLive/qiskit-aer.git "$AER_DIR"
  fi
  if confirm "quartz-site (notion-backup 정적 서빙) 도 clone 하시겠습니까?"; then
    [ -d "$QUARTZ_DIR/.git" ] || git clone https://github.com/jackyzha0/quartz.git "$QUARTZ_DIR"
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

# ─── 3. (선택) qiskit-aer GPU 빌드 ──────────────────
if [ "$SKIP_AER_BUILD" -eq 0 ] && [ -d "$AER_DIR" ]; then
  if confirm "qiskit-aer fork 를 GPU 모드로 빌드/설치하시겠습니까? (Jetson/GH200)"; then
    log "3) qiskit-aer fork 빌드 ($AER_DIR)"
    pip install -q pybind11 scikit-build cmake
    pushd "$AER_DIR" >/dev/null
      python setup.py bdist_wheel -- -DAER_THRUST_BACKEND=CUDA
      pip install dist/qiskit_aer-*linux_*.whl --force-reinstall
    popd >/dev/null
    ok "qiskit-aer GPU wheel 설치"
  else
    warn "3) qiskit-aer GPU 빌드 skip — PyPI stock 으로 fallback"
  fi
else
  warn "3) qiskit-aer GPU 빌드 skip"
fi

# ─── 4. UQI 의존성 설치 ─────────────────────────────
log "4) UQI requirements 설치 (시간 소요 — quantum SDK 많음)"
pip install -r "$UQI_DIR/requirements.txt"
ok "pip install 완료"

# ─── 5. embed/rerank Docker 이미지 ──────────────────
if [ "$SKIP_DOCKER" -eq 0 ]; then
  log "5) embed/rerank Docker 이미지 빌드 (uqi-rag:0.1, 약 24GB)"
  if docker image inspect uqi-rag:0.1 >/dev/null 2>&1; then
    if confirm "uqi-rag:0.1 이미 존재. 재빌드 하시겠습니까?"; then
      docker build -t uqi-rag:0.1 "$UQI_DIR/deploy"
    fi
  else
    docker build -t uqi-rag:0.1 "$UQI_DIR/deploy"
  fi
  ok "docker 이미지 준비"
else
  warn "5) docker 빌드 skip"
fi

# ─── 6. systemd unit 설치 ───────────────────────────
if [ "$SKIP_SYSTEMD" -eq 0 ]; then
  log "6) systemd 유닛 설치 (/etc/systemd/system/)"
  if confirm "systemd 유닛 4개 (uqi-mcp/embed/rerank/ngrok-8765) 를 sudo 로 설치?"; then
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
    ok "systemd 유닛 enable (아직 start 안 함)"
  fi
else
  warn "6) systemd 설치 skip"
fi

# ─── 7. (선택) notion-backup 빌드 + symlink ─────────
# uqi 와는 분리된 프로젝트지만, setup.sh 가 한 번에 오케스트레이션
if [ "$SKIP_NOTION" -eq 0 ] && [ -d "$QUARTZ_DIR" ]; then
  if confirm "notion-backup (quartz-site) 빌드 + symlink 도 진행하시겠습니까?"; then
    log "7) notion-backup 빌드 ($QUARTZ_DIR)"
    if ! command -v npm >/dev/null 2>&1; then
      warn "npm 미설치 — quartz 빌드는 스킵. 'sudo apt install nodejs npm' 후 재실행"
    else
      pushd "$QUARTZ_DIR" >/dev/null
        [ -d node_modules ] || npm install
        npx quartz build
      popd >/dev/null
      # symlink (이미 있으면 재생성)
      local_link="$UQI_DIR/webapp/notion-backup"
      if [ -L "$local_link" ] || [ -e "$local_link" ]; then
        rm -f "$local_link"
      fi
      ln -s "$QUARTZ_DIR/public" "$local_link"
      ok "notion-backup symlink: $local_link → $QUARTZ_DIR/public"
    fi
  fi
else
  warn "7) notion-backup skip (quartz-site clone 없음 또는 --skip-notion)"
fi

# ─── 마무리 안내 ────────────────────────────────────
cat <<EOF

────────────────────────────────────────────────────────────
✓ 셋업 완료 — 남은 수동 단계:

1. .env 채우기 ($UQI_DIR/.env)
   README "Environment setup" 섹션 참조 — Anthropic / Pasqal / Azure /
   IBM / IQM / Braket / Quandela API 키 등

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

webapp: http://localhost:8765/  (ngrok 시작 시 외부 URL 도)
────────────────────────────────────────────────────────────
EOF
