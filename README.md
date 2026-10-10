# fc-ingyeo-tracker

EA FC 27 프로클럽 **FC INGYEO**(clubId 829628, common-gen5)의 경기 기록을 모아 두는 저장소입니다.
GitHub Actions가 주기적으로 EA API를 읽어 새 경기를 쌓고, 그 결과를 GitHub Pages로 보여 줍니다.
별도 서버 없이 무료로 돌아가며, 파이썬 표준 라이브러리만 사용합니다.

```
collect.py            EA API → raw/matches/{matchId}.json (새 경기만 저장, 상대 선수 게이머태그는 마스킹)
build.py              raw/ 전체 → docs/data/ (세션·선수 집계·검색 인덱스) 재생성
docs/index.html       페이지 본체 (docs/data/ 만 읽습니다)
raw/legacy/           자동 수집 전에 손으로 받아 둔 39경기 (2026-09-29~10-08), tools/legacy_import.py 로 변환한 것
meta/                 state.json(시즌 누적·엠블럼·연속 실패 횟수·raw 수 — 실행 시각은 쓰지 않음)
                      opponents.json(상대 SR 첫 관측값·엠블럼 id·디비전), gaps.json(수집 누락 기록)
                      club_history.json(우리 SR·디비전·승점 추이 — 값이 바뀐 실행에서만 한 줄 추가)
                      members.json(EA members/stats 선수별 시즌 누적 — 값이 바뀔 때만 파일이 바뀜)
.github/workflows/    collect.yml(수집 → 빌드 → 커밋 → 배포), pages.yml(docs 를 직접 고쳤을 때 배포)
tools/                legacy_import.py, selftest.py(가짜 API로 수집기 점검), cf-dispatcher/(스케줄 대체용 Cloudflare Worker)
```

설계 배경과 결정 이력은 [DESIGN.md](DESIGN.md) 에 있습니다. 왜 이렇게 만들었는지 궁금할 때 먼저 봐 주세요.

## 처음 설정 (한 번만 하면 됩니다)

1. **저장소 만들기** — GitHub에 공개 저장소 `fc-ingyeo-tracker` 를 빈 상태로 만들고 이 폴더를 push 합니다.
   ```bash
   git init -b main && git add -A && git commit -m "init tracker"
   git remote add origin git@github.com:<계정>/fc-ingyeo-tracker.git && git push -u origin main
   ```
2. **Actions 권한** — Settings → Actions → General → Workflow permissions 를 **Read and write permissions** 로 바꿔 주세요.
   수집기가 `GITHUB_TOKEN` 으로 커밋하고 push 하려면 필요합니다. 기본값(read-only)인 채로 두면 push 가 403으로 실패합니다.
3. **Pages 소스** — Settings → Pages → Build and deployment → Source 를 **GitHub Actions** 로 선택합니다.
   "Deploy from a branch" 가 아닙니다. 봇이 만든 커밋은 다른 워크플로를 깨우지 않기 때문에, 수집 워크플로 안에서 바로 배포하도록 되어 있습니다.
4. **첫 배포** — Actions 탭 → `pages` → Run workflow 를 누르면 1~2분 뒤 `https://<계정>.github.io/fc-ingyeo-tracker/` 가 열립니다.
   이때는 임포트한 39경기만 보이고, 상단에 "마지막 수집 기록 없음" 이 표시되는 것이 정상입니다.
5. **첫 수집** — Actions 탭 → `collect` → Run workflow. 첫 실행은 시즌 누적을 EA 값으로 맞추느라 커밋이 한 번 생깁니다.
   그 뒤로는 스케줄대로 KST 21:00~03:00 사이에 20분 간격(03:03 마감 1회 포함)으로 돌면서 **새 경기가 있을 때만** 커밋·배포합니다.
   낮에는 돌지 않으니 그 시간대에 경기를 했다면 Actions 탭에서 직접 실행해 주세요.
   페이지 상단의 "마지막 수집" 시각은 저장소가 아니라 GitHub Actions 실행 기록(공개 API)에서 읽어 오기 때문에, 커밋이 없어도 갱신됩니다.

## 페이지 보는 법

- 맨 위에는 시즌 누적과 가장 최근 세션 요약이, 그 아래에 그 세션의 경기 이력이 나옵니다. 경기를 누르면 스탯과 출전 선수가 펼쳐지고, 선수를 누르면 세부 지표로 들어갑니다.
- **세션**은 60분 안에 이어진 경기 묶음입니다(보통 저녁 한 번). 세션 목록은 최근 10개씩 보여 주고, "이전 세션 더 보기" 를 누르면 10개씩 더 펼쳐집니다.
  세션 ID 로 바로 들어오면(`#/s/{id}`) 그 세션이 보이는 페이지까지 자동으로 펼칩니다.
- **진행 중** 표시는 마지막 경기가 지금 시각 기준 1시간 이내일 때만 붙습니다. 1시간 동안 새 경기가 없으면 게임을 마친 것으로 보고 표시가 사라지는데,
  이 판단은 페이지를 보고 있는 시점에 하므로 새로고침 없이도 1분 안에 반영됩니다.
- 상대팀 이름으로 검색하면 세션 목록 자리에 해당 경기들이 나옵니다. 결과가 많으면 30개씩 끊어서 보여 줍니다.
- 플레이오프·친선 경기는 상대 이름 뒤에 태그가 붙고, 세션 전적에는 종류 구분 없이 합산됩니다.
- 경기 이력의 "상대 Div · SR" 칸에는 상대 디비전(D5 처럼)과 스킬 레이팅이 함께 나오고, 두 번 이상 만난 클럽은 이름 옆에 통산 전적이 붙습니다.
- "클럽 추이 · 상대 전적" 카드는 수집 누적 기준입니다. SR·디비전과 승점 추이 차트(x축은 EA 시즌 경기 수), 상대 SR 차이별 전적(200점 단위 세 구간, 10경기 미만은 승률 생략),
  자주 만난 상대의 통산 전적을 보여 줍니다. 상단의 DNF 제외 토글이 여기에도 적용됩니다.

## 운영하면서 알아 둘 것

- **실패 알림**: 수집이 3회 연속 실패하면 워크플로가 실패로 끝나 GitHub 기본 알림(이메일)이 옵니다. 1~2회 실패는 로그만 남깁니다.
  페이지 상단의 상태 핀은 24시간 넘게 성공 기록이 없으면 빨간색으로 바뀝니다.
- **스케줄이 돌지 않을 때**: GitHub 쪽 사정으로 `schedule` 이 전혀 실행되지 않는 경우가 있습니다(설정은 다 맞는데 실행 기록만 없는 상태).
  그럴 때는 `tools/cf-dispatcher/` 의 Cloudflare Worker 를 올려 같은 시각에 `workflow_dispatch` 를 쏘게 할 수 있습니다.
  Actions 권한(Read and write)만 있는 fine-grained PAT 를 만들어 `npx wrangler secret put GITHUB_TOKEN` 으로 넣고 `npx wrangler deploy` 하면 됩니다.
  토큰은 Worker 비밀값으로만 두고 저장소에는 절대 넣지 마세요. 두 스케줄이 겹쳐도 `concurrency` 설정 덕분에 한 번에 하나만 돕니다.
- **수집 누락**: EA API 는 매치타입별로 최근 10경기만 돌려줍니다. 두 번의 성공 사이에 10경기를 넘게 치르면 그 사이 경기는 영영 받을 수 없습니다.
  리그 경기에 한해 `gamesPlayed` 가 늘어난 만큼과 실제 받은 경기 수를 비교해 `meta/gaps.json` 에 기록해 두지만, 복구할 방법은 없습니다.
- **지표 규칙을 바꿀 때**: `build.py` 만 고치고 로컬에서 `python build.py` 를 돌린 뒤 커밋해 주세요. 원본(`raw/`)은 건드리지 않습니다. `METRICS_VERSION` 을 하나 올려 두면 좋습니다.
- **점검**: `python tools/selftest.py` 를 돌리면 네트워크 없이 가짜 응답으로 수집기와 빌드 로직을 확인할 수 있습니다.
- **로컬 미리보기**: `python build.py && (cd docs && python -m http.server 8000)` 뒤에 http://localhost:8000 을 여세요.
  `file://` 로 열면 fetch 가 막혀서 데이터가 보이지 않습니다. "마지막 수집" 핀까지 보려면 `?repo=<계정>/fc-ingyeo-tracker` 를 붙이면 됩니다.
- **변경이 없으면 커밋도 없습니다**: `collect.py` 와 `build.py` 는 실행 시각을 파일에 쓰지 않고, 산출물은 입력이 같으면 바이트까지 같게 만들어집니다.
  그래서 새 경기나 시즌 누적 변화, 실패 상태 변화가 없으면 `git diff` 가 비어 커밋도 배포도 생기지 않습니다. `tools/selftest.py` 가 이 성질을 검사합니다.
- **스케줄 자동 중단 방지**: 공개 저장소는 60일간 커밋이 없으면 GitHub 이 스케줄을 꺼 버립니다.
  마지막 커밋 뒤 50일이 지나면 워크플로가 `meta/keepalive.txt` 를 갱신해 커밋하므로 시즌 오프 중에도 꺼지지 않습니다. 그래도 꺼졌다면 Actions 탭에서 다시 켜 주세요.

## 데이터 파일 (docs/data/)

| 파일 | 내용 | 언제 읽나 |
|---|---|---|
| `meta.json` | 데이터 해시(캐시 버스팅), 시즌 누적(EA overallStats), 경기 수, 누락, 수집기 실패 상태, 클럽 엠블럼 id | 항상 |
| `sessions.json` | 세션 목록(60분 안에 이어진 경기 묶음, ID = 첫 경기 matchId) | 항상 |
| `players.json` | 선수별 누적·최근 5세션 합계(파생값은 페이지에서 계산) + `season`(EA members/stats 시즌 누적, 이름→playerId 연결) | 항상 |
| `sessions/{id}.json` | 그 세션의 경기·선수 상세 | 세션을 열 때 |
| `matches.json` | 전체 경기 한 줄 요약 + 상대 엠블럼 id 후보·상대 SR·디비전 (검색, 상대 전적, SR 차이별 전적) | 항상 |
| `history.json` | 우리 클럽 SR·디비전·승점 추이 (`meta/club_history.json` 복사본) | 항상 |

## 알려진 제약

- 비공식 API 입니다. 응답 구조나 차단 정책이 예고 없이 바뀔 수 있습니다. 2026-10-08 기준으로 GitHub 호스팅 러너에서 브라우저 헤더만 붙이면 200 이 오는 것을 확인했습니다.
- 사람 선수 기록만 있습니다. AI 동료나 AI 골키퍼는 응답에 없습니다. 이벤트 ID 해석은 커뮤니티 추정이며, 패스·골·도움·슈팅은 실제 경기와 대조해 확인했습니다.
- `raw/legacy/` 의 20경기(9/29~10/3)는 이벤트 36종만 들어 있어 일부 지표가 "–" 또는 "n경기 제외" 로 표시됩니다.
- 상대 선수 게이머태그는 저장할 때 첫 글자와 끝 글자만 남기고 마스킹합니다(`collect.py: mask_name`). 원래 값으로 되돌릴 수 없습니다.
- 상대 디비전은 `overallStats` 에 없어서 현재 시즌 리더보드를 클럽명으로 검색해 clubId 가 같은 행에서 읽습니다. 리더보드에 없는 클럽은 "–" 로 남고,
  저장되는 값은 조회 시점의 디비전이라 경기 당시와 다를 수 있습니다. 초기 임포트 상대는 수집마다 5클럽씩 채웁니다.
- SR 추이의 10/2~10/8 네 점은 자동 수집 전에 받아 둔 스냅샷(`tools/snapshots/`)에서 옮긴 값이고, 그 뒤로는 수집기가 값이 바뀔 때마다 기록합니다. 소급은 되지 않습니다.
- 클럽 엠블럼은 EA 콘텐츠 CDN 이미지를 페이지에서 직접 불러옵니다(핫링크, 저장소에 내려받지 않음). 경기 응답의 `TEAM` → 커스텀 크레스트 → 실제 배지 순으로 시도하고, 전부 실패하면 이니셜을 보여 줍니다.
  초기 임포트 상대는 엠블럼 정보가 없어서, 수집할 때마다 5클럽씩 `clubs/info` 로 채워 넣습니다(`collect.py: backfill_crests`).
- `members/stats`(EA 가 집계한 선수별 시즌 누적)는 매 수집마다 받아 `meta/members.json` 에 두고, 페이지 하단 "시즌 선수 누적 · EA 집계" 표로 보여 줍니다.
  시즌 전체(수집 시작 전 경기 포함) 값인 데다 정의도 EA 기준이라(예: 패스 성공률에 오프사이드를 빼지 않음) 우리 수집 집계와 직접 비교하지는 않습니다. 선수 연결은 게이머태그로 합니다.
