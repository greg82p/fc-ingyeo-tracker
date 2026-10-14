# CLAUDE.md

EA FC 27 프로클럽 **FC INGYEO**(clubId 829628, platform common-gen5) 경기 기록을 GitHub Actions로 수집해 쌓고 GitHub Pages로 보여 주는 저장소입니다.
구조·운영은 `README.md`, 설계 배경과 결정 이력은 `DESIGN.md`를 먼저 읽어 주세요. 이 파일에는 그 두 문서에 없는 작업 규칙과 확인된 사실, 남은 일만 적습니다.

## 작업 규칙

- **변경이 없으면 커밋도 없다.** `collect.py`·`build.py`는 실행 시각을 파일에 쓰지 않고, 입력이 같으면 산출물이 바이트까지 같아야 합니다. 새 필드를 추가할 때 이 성질을 깨지 않도록 하고, `tools/selftest.py`의 무변경 재실행 검사를 함께 늘려 주세요.
- **원본(`raw/`)은 고치지 않습니다.** 지표 정의를 바꿀 때는 `build.py`만 고치고 `METRICS_VERSION`을 올립니다.
- **수집은 Actions에서만 돌립니다.** 로컬에서 `collect.py`를 실행하면 `raw/`·`meta/`가 바뀌어 Actions의 수집 커밋과 충돌합니다. 로컬에서는 `python build.py`, `python tools/selftest.py`, 미리보기(`cd docs && python -m http.server 8000`, `?repo=greg82p/fc-ingyeo-tracker`)만 씁니다.
- 수집 커밋(`collect ...`, collector-bot)은 `raw/`, `meta/`, `docs/data/`만 건드립니다. push가 거부되면 `git pull --rebase` 후 다시 push하면 됩니다.
- 표준 라이브러리만 씁니다(`pip install` 없음). 페이지는 `docs/index.html` 한 파일이며 `docs/data/`만 읽습니다.
- **`DESIGN.md`가 설계 문서의 원본입니다.** 2026-10-14까지는 Claude Docs 문서를 내보내 덮어썼지만 이제는 이 파일을 직접 고칩니다. 결정이 바뀌면 해당 절에 날짜와 함께 추가해 주세요.
- 상대 선수 게이머태그는 저장 시점에 마스킹합니다(`collect.py: mask_name`). 공개 저장소이므로 마스킹을 우회하는 변경은 하지 않습니다.
- 커밋 메시지는 한국어로 씁니다.

## EA API 메모

- 비공식 API: `https://proclubs.ea.com/api/fc/<endpoint>`, 모두 GET·인증 없음, 숫자도 문자열로 옵니다.
- Akamai가 막기 때문에 브라우저 헤더(`sec-fetch-site: same-origin`, referer, UA 등, `collect.py: HEADERS`)가 필요합니다. 일반 `curl`은 차단된다고 알려져 있어서 응답을 직접 확인할 때는 그 헤더를 붙인 Python urllib을 쓰는 게 안전합니다.
- `clubs/matches`는 매치타입별로 최근 10경기만 줍니다(`maxResultCount`를 늘려도 10). 두 실행 사이에 10경기를 넘게 치르면 그 경기는 영구 누락됩니다.
- 수집 단계별 호출: `clubs/matches` ×2(리그·플레이오프, 치명적. 친선은 조회하지 않음) → `clubs/overallStats`(우리) → `currentSeasonLeaderboard/search`(우리 디비전) → 새 상대마다 `clubs/overallStats` + 리더보드 검색(디비전) → 백필(`clubs/info`, 리더보드 검색, 각 최대 5회) → `members/stats`. `clubs/matches` 외의 실패는 로그에만 남고 알림이 오지 않습니다.

## 확인된 사실 (README·DESIGN에 아직 없음)

- **아키타입** (`clubs/matches` → `players[].archetypeid`, 경기별 값, 전역 ID): 13 = 타겟, 10 = 스파크, 7 = 리사이클러. 우리 팀이 쓴 5·8·12는 이름 미확인입니다. "선택한 유형"이지 실제로 선 자리는 아닙니다.
- **포지션**: 경기 응답의 `pos`는 goalkeeper/defender/midfielder/forward 네 값뿐이고, 경기별 세부 포지션은 어떤 엔드포인트에도 없습니다. `members/stats`의 `proPos`는 FIFA 포지션 ID 체계로 보이며 2026-10-12 확인값은 25 = ST(lanil, Aparensis), 14 = CM(hoyeol1107, raoshuant)입니다. "현재 프로 설정값"이라 경기 시점 값이 아닙니다. 수집기는 아직 `proPos`를 저장하지 않습니다.
- **리더보드 `points`**: 승 3·무 1 공식과 맞지 않고, 무·패만 추가된 구간에서 줄어든 적도 있어 정의를 모릅니다. 그래서 화면에서 뺐습니다(`history.json`에는 계속 쌓임).
- **상대 디비전 조회**는 가짜 응답(selftest)으로만 검증했습니다. 실제 수집 후 `meta/opponents.json`에 `div`가 채워지는지 확인이 필요합니다.
- **GitHub `schedule`**: 10/11~10/13 기대 57회 중 8회만 실행, 대부분 2~6시간 지연. 그래서 2026-10-14에 제거하고 `workflow_dispatch` 전용으로 바꿨습니다.

## 남은 일

- [ ] **자동 실행**: `tools/cf-dispatcher/` 배포 (fine-grained PAT, Actions RW, 이 저장소만 → `npx wrangler secret put GITHUB_TOKEN` → `npx wrangler deploy`). `?run=1`로 동작을 확인한 뒤 그 수동 엔드포인트(`src/index.js`의 `searchParams.get("run")` 분기)는 지웁니다. 토큰 없이 누구나 호출할 수 있기 때문입니다.
- [ ] **상단 수집 핀**: 수동 운영 중에는 "24시간 이상 성공 없음" 경고가 경기 없는 날마다 뜹니다. 자동 실행 전까지 기준 완화나 경고 끄기를 결정해야 합니다.
- [ ] **DNF 불일치**: "이탈(DNF) 경기 제외"가 기본 켜짐인데, 하단 "수집 누적" 선수 표는 빌드 때 미리 집계해 이탈 경기를 포함합니다. 빌드에서 DNF 제외본을 같이 만들지 결정이 필요합니다.
- [ ] **아키타입·proPos 표시**: 5·8·12번 이름을 확인한 뒤 경기별 선수 행에 아키타입을, 시즌 선수 표에 `proPos`(ST/CM)를 표시합니다. `proPos`는 이미 매 수집마다 호출하는 `members/stats`에 있어 요청이 늘지 않습니다.
- [ ] **비치명적 실패 알림**: 리더보드·members 호출이 계속 실패해도 알림이 없습니다. 연속 실패를 `state.json`에 세어 경고할지 검토합니다.
- [ ] **정리**: `docs/index.html`의 `renderTrend()`에 승점 차트를 빼면서 남은 `const isSr = true`와 관련 분기가 있습니다.
