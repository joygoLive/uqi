# 작업 주의사항

## Git 관련
- `git push --force` 금지 (force-with-lease도 명시적 허락 필요)
- `git reset --hard`, `git clean -f` 등 파괴적 명령어 금지
- `git add .` / `git add -A` 금지 → 파일을 명시적으로 지정
- 커밋 전 반드시 `git diff --staged` 확인
- 워크트리 작업 후 반드시 push 여부 확인

## 코드 수정 관련
- `sed` 대량 치환 금지 → Edit 툴 사용
- 파일 전체 재작성 금지 → 필요한 부분만 수정
- 기존 코드 삭제 시 반드시 확인 요청

## Python 관련
- `__pycache__`, `.pyc` 파일 건드리지 않기
- 가상환경(`venv`, `.env`) 수정 금지
- `requirements.txt` 변경 시 반드시 확인 요청

## 테스트/검증
- 코드 수정 후 테스트 실행 확인
- 미확인 상태로 PR/push 금지

## 커뮤니케이션
- 불확실할 때 추측으로 진행하지 말고 먼저 질문
- 큰 변경사항은 계획 먼저 제시 후 승인받고 진행
