## 📋 한두 달 뒤 다시 전체 스냅샷 뜨는 절차 (보관용)

> 모든 스크립트가 레포에 들어가 있으니 이 순서대로만 밟으면 됩니다.

### 0. 선행 체크 (한두 달 뒤 첫 실행 시)

```bash
# 노션 unofficial API 호출에 필요한 client version 이 낡았는지 확인
# 브라우저로 https://www.notion.so 로그인 → F12 → Network 탭
# → 아무 /api/v3/ 요청 클릭 → Request Headers 에서 x-notion-client-version 복사
# → ~/work/orientom/orientom-notion-pipeline/.env 의 NOTION_CLIENT_VERSION 갱신
#
# (NOTION_TOKEN_V2, NOTION_SPACE_ID 는 로그인 유지되어 있으면 그대로 재사용 가능.
#  만료되면 같은 방법으로 브라우저 DevTools 에서 token_v2 쿠키 값 재추출)
```

### 1. Notion export 요청 (서버에서)

```bash
cd ~/work/orientom/orientom-notion-pipeline
source ~/work/orientom/QUWA/.venv_transpile/bin/activate
python3 sync_trigger.py
# 출력: ✓ Export enqueued. taskId=...
```

### 2. 대기 (수 분 ~ 1 시간)

Notion 데스크탑/웹 앱에 "Your export is ready" 인앱 알림이 뜰 때까지 기다립니다. 워크스페이스 규모에 따라 시간이 달라집니다.

### 3. Mac에서 zip 다운로드

1. Notion 알림의 **"Download"** 클릭
2. `~/Downloads/` 에 `<uuid>_Export-*.zip` 형태로 저장됨 (이번엔 18.4 GB였음)

### 4. Mac에서 자동 전송 + 처리 트리거

```bash
cd ~/work/orientom/orientom-notion-pipeline
./weekly_notion_sync.sh
# 최신 Export zip 자동 탐지 → y 확인 → scp -p 로 서버 업로드
# → 서버에서 sync_process.py 원격 실행 (자동)
```

이 한 줄이 돌면 서버에서 아래가 **모두 자동**으로 돕니다:

| # | 단계 | 책임 스크립트 |
|---|---|---|
| 1 | zip 압축 해제 (outer + inner) | `sync_process.py :: extract_zip` |
| 2 | obsidian-vault 기존 내용 정리 | `clean_obsidian_vault` |
| 3 | ≥5 MB 첨부 분할 (obsidian-vault vs gdrive-upload) | `split_vault.py` |
| 4 | 타이틀 없는 UUID 폴더 자식 hoist | `hoist_uuid_dirs.py` |
| 5 | backup-meta.json 기록 (zip mtime 기준) | `write_backup_meta` |
| 6 | GDrive 첨부 동기화 (delete+create) | `rclone sync` |
| 7 | GDrive 파일 ID 조회 → `file_mapping.json` | `get_gdrive_urls.py` |
| 8 | bare 파일명 링크화 + GDrive URL 치환 | `rewrite_md_links.py` |
| 9 | obsidian-vault git commit + push | `git_commit_push` |
| 10 | Quartz overlay 적용 (brand + plugin) | `apply_quartz_overlay` |
| 11 | 정적 사이트 빌드 (UUID 제거, base64 스트립) | `npx quartz build` |
| 12 | 임시 vault 삭제 | `cleanup_temp` |

### 5. 검증 (브라우저)

- 홈: `https://superelegant-terrence-grittiest.ngrok-free.dev/notion-backup/`
- 좌측: **Orientom Notion Archive** / 한글 locale / 팀스페이스 리스트
- 본문: **마지막 백업**이 새 타임스탬프로 반영
- UQI 웹앱 지식베이스 탭 → 이스터에그로 카드 노출 → "마지막 백업: YYYY-MM-DD HH:MM"

### 6. 문제 발생 시 체크리스트

| 증상 | 원인 / 조치 |
|---|---|
| `sync_trigger.py` 401 Unauthorized | `NOTION_TOKEN_V2` 만료 → 브라우저에서 재추출 |
| `sync_trigger.py` 400 bad request | `NOTION_CLIENT_VERSION` 이 너무 낡음 → 재추출 |
| scp 전송 중 중단 | `~/Downloads/` 의 partial zip 삭제 후 재실행 |
| `rclone sync` 실패 | `rclone config` 의 gdrive 토큰 만료 → `rclone config reconnect gdrive:` |
| `npx quartz build` 실패 | `cd ~/work/orientom/quartz-site && rm -rf .quartz-cache node_modules && npm install` |
| Chrome 에서 페이지 크래시 | 새로운 대형 base64 유입 — `contentIndex.tsx` 에 추가 스트립 패턴 필요 |
| URL 에 UUID 가 보임 | `quartz-overlay/` 가 복사 안 됨 → `sync_process.py :: apply_quartz_overlay` 로그 확인 |

### 7. 수동 부분 재실행이 필요할 때

```bash
# Quartz 빌드만 다시
cd ~/work/orientom/quartz-site && rm -rf .quartz-cache && npx quartz build

# bare 파일명 래핑만 1회성 정리 (mapping.json 없이도 동작)
cd ~/work/orientom/orientom-notion-pipeline
OBSIDIAN_VAULT_DIR=~/work/orientom/obsidian-vault python3 rewrite_md_links.py --wrap-only

# UUID-only 폴더 hoist 만
python3 hoist_uuid_dirs.py ~/work/orientom/obsidian-vault
```

### 8. 관련 repo 위치 (동기화 대상)

| repo | 브랜치 | 역할 |
|---|---|---|
| `joygoLive/uqi` | dev | MCP 서버 + 웹앱 — notion-backup 경로 마운트 & 지식베이스 카드 |
| `joygoLive/orientom-notion-pipeline` | main | 이번에 쓴 모든 스크립트 + Quartz overlay |
| `joygoLive/orientom-notion-backup` | main | obsidian-vault 자체 (백업 소스 of truth) |
| `jackyzha0/quartz` (upstream) | v4 | 빌드 엔진 — fork 안 함, overlay 로 커스터마이징 |

---

## 지금 이 세션 남은 작업

현재 수정됐지만 **아직 커밋 안 된** 것들:
1. `obsidian-vault/` — 59개 MD (bare filename wrap 적용)
2. `orientom-notion-pipeline/rewrite_md_links.py` (wrap 로직 + `--wrap-only` 플래그)
3. `quartz-site/quartz.config.ts` — HardLineBreaks 추가 (로컬만)
4. `quartz-site/` 빌드 결과 (자동)

