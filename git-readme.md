**1. 초기 설정**
```bash
git clone https://github.com/joygoLive/uqi.git
cd uqi
git checkout dev
```

```bash
python -m venv .venv_transpile
source .venv_transpile/bin/activate
pip install -r requirements.txt
```

`.env` 파일은 Sean에게 별도 수령 (repo에 포함 안 됨)

---

**2. 매일 작업 시작 전**
```bash
git checkout dev
git pull origin dev
```

---

**3. 작업 후 push**
```bash
git add .
git commit -m "feat: 작업내용"   # 또는 fix: / chore: / wip:
git push origin dev
```

---

**4. main 머지 요청 (PR)**

GitHub 웹:
**Pull requests** → **New pull request** → `base: main` ← `compare: dev` → 제목/설명 작성 → **Create pull request**

---

**커밋 메시지 규칙**
- `feat:` 기능 추가
- `fix:` 버그 수정
- `chore:` 설정/문서/기타
- `wip:` 진행 중 백업

---

**주의사항**
- `main` 직접 push 금지
- `.env` 절대 커밋 금지