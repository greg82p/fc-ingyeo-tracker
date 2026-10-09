# fc-ingyeo-tracker

EA FC 27 프로클럽 **FC INGYEO**(clubId 829628, common-gen5)의 경기 기록을 GitHub Actions로 주기 수집해 쌓고,
GitHub Pages로 보여주는 저장소. 서버 없음, 비용 0원, 표준 라이브러리만 사용.

```
collect.py            EA API → raw/matches/{matchId}.json (새 경기만, 상대 선수 게이머태그 마스킹)
build.py              raw/ 전체 → docs/data/ (세션·선수 집계·검색 인덱스) 재생성
docs/index.html       페이지 (docs/data/ 만 읽음)
raw/legacy/           자동 수집 전 수동 스냅샷 39경기 (2026-09-29~10-08), tools/legacy_import.py 산출물
meta/                 state.json(시즌 누적·연속 실패·raw 수 — 실행 시각 없음), opponents.json(상대 SR 첫 관측값), gaps.json(수집 누락)
.github/workflows/    collect.yml(수집→빌드→커밋→배포), pages.yml(docs 수동 수정 시 배포)
tools/                legacy_import.py, selftest.py(가짜 API로 수집기 점검), 페이지 조립 스크립트
```

## 처음 설정 (한 번만)

1. **저장소 생성** — GitHub에서 공개 저장소 `fc-ingyeo-tracker` 를 빈 상태로 만들고 이 폴더를 push.
   ```bash
   git init -b main && git add -A && git commit -m "init tracker"
   git remote add origin git@github.com:<계정>/fc-ingyeo-tracker.git && git push -u origin main
   ```
2. **Actions 권한** — Settings → Actions → General → Workflow permissions 를 **Read and write permissions** 로.
   (수집기가 `GITHUB_TOKEN` 으로 커밋·push 하기 위해 필요. 기본값은 read-only라 push가 403으로 실패한다.)
3. **Pages 소스** — Settings → Pages → Build and deployment → Source 를 **GitHub Actions** 로.
   ("Deploy from a branch"가 아니다. 봇 커밋은 다른 워크플로를 트리거하지 않으므로 수집 워크플로 안에서 직접 배포한다.)
4. **첫 배포** — Actions 탭 → `pages` → Run workflow. 1~2분 뒤 `https://<계정>.github.io/fc-ingyeo-tracker/` 가 열린다.
   이 시점 데이터는 임포트한 39경기뿐이고 상단에 "마지막 수집 기록 없음"이 뜬다.
5. **첫 수집** — Actions 탭 → `collect` → Run workflow. 첫 실행은 시즌 누적을 EA 값으로 갱신하므로 커밋이 한 번 생기고,
   이후는 스케줄대로 KST 22:00~02:30 20분, 그 외 3시간 간격으로 돌며 **새 경기가 있을 때만** 커밋·배포한다.
   페이지 상단의 "마지막 수집" 시각은 저장소가 아니라 GitHub Actions 실행 기록(공개 API)에서 읽으므로 커밋이 없어도 갱신된다.

## 운영

- **실패 알림**: 수집이 3회 연속 실패하면 워크플로가 실패로 끝나 GitHub 기본 알림(이메일)이 간다. 1~2회 실패는 로그만 남긴다.
  페이지 상단 핀은 24시간 넘게 성공 기록이 없으면 빨간색으로 바뀐다.
- **수집 누락**: EA API는 매치타입당 최근 10경기만 준다. 두 번의 성공 사이에 10경기 넘게 치르면 그 사이 경기는 영구 누락이고,
  리그 경기에 한해 `gamesPlayed` 증가분으로 감지해 `meta/gaps.json` 에 기록한다 (복구 수단은 없음).
- **지표 규칙 변경**: `build.py` 만 고치고 로컬에서 `python build.py` 후 커밋. 원본은 건드리지 않는다. `METRICS_VERSION` 을 올린다.
- **점검**: `python tools/selftest.py` — 네트워크 없이 수집기·빌드 로직을 가짜 응답으로 확인.
- **로컬 미리보기**: `python build.py && (cd docs && python -m http.server 8000)` → http://localhost:8000
  (`file://` 로 열면 fetch 가 막혀 데이터가 안 보인다.)
- **변경 없으면 커밋 없음**: `collect.py`/`build.py` 는 실행 시각을 파일에 쓰지 않고 산출물은 결정적이라, 새 경기·시즌 누적 변화·실패 상태 변화가 없으면
  `git diff` 가 비어 커밋도 배포도 생기지 않는다. `tools/selftest.py` 가 이 성질(무변경 재실행 시 바이트 동일)을 검사한다.
- **스케줄 자동 중단**: 공개 저장소는 60일간 커밋이 없으면 스케줄이 꺼진다. 워크플로가 마지막 커밋 후 50일이 지나면 `meta/keepalive.txt` 를 갱신해 커밋하므로
  시즌 오프 중에도 꺼지지 않는다. 그래도 꺼졌다면 Actions 탭에서 다시 켠다.

## 데이터 파일 (docs/data/)

| 파일 | 내용 | 로딩 |
|---|---|---|
| `meta.json` | 데이터 해시(캐시 버스팅), 시즌 누적(EA overallStats), 경기 수, 누락, 수집기 실패 상태 | 항상 |
| `sessions.json` | 세션 목록(60분 안에 이어진 경기 묶음, ID = 첫 경기 matchId) | 항상 |
| `players.json` | 선수별 누적·최근 5세션 합계 (파생값은 페이지에서 계산) | 항상 |
| `sessions/{id}.json` | 세션의 경기·선수 상세 | 세션을 열 때 |
| `matches.json` | 전체 경기 한 줄 요약 (상대팀 검색, 딥링크 → 세션 찾기) | 검색·딥링크 때 |

## 알려진 제약

- 비공식 API. 응답 구조나 차단 정책이 예고 없이 바뀔 수 있다. 2026-10-08 기준 GitHub 호스팅 러너에서 브라우저 헤더만으로 200 확인.
- 사람 선수 기록만 있다 (AI 동료·AI 골키퍼 없음). 이벤트 ID 해석은 커뮤니티 추정이며 패스·골·도움·슈팅은 직접 검증했다.
- `raw/legacy/` 20경기(9/29~10/3)는 이벤트 36종만 있어 일부 지표가 "–" 또는 "n경기 제외"로 표시된다.
- 상대 선수 게이머태그는 저장 시점에 첫/끝 글자만 남기고 마스킹한다 (`collect.py: mask_name`). 원복 불가.
