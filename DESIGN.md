# FC INGYEO 프로클럽 스탯 트래커 설계 문서

2026-10-08 작성 · 원본: Claude Docs (내부 링크), 이 파일은 저장소용 내보내기

## 1. 개요

FC INGYEO(clubId 829628, common-gen5)의 EA FC 27 Pro Clubs 경기 기록을 **1시간마다 수집해 영구 누적**하고, 상대팀 검색·선수별 누적 집계가 되는 정적 페이지를 **공개 URL에 무료로** 배포한다. EA API가 최근 10경기만 돌려주기 때문에 주기 수집 없이는 과거 기록이 사라진다.

**목표**

- 리그·플레이오프·친선 경기를 빠짐없이 원본(JSON) 그대로 보존한다.
- 지표 정의가 바뀌어도 원본에서 전부 다시 계산할 수 있게 한다.
- 상대팀·기간·선수 기준으로 검색·집계할 수 있는 페이지를 서버 없이 제공한다.
- 월 비용 0원.

**비목표 (이번 범위 밖)**

- 다른 클럽의 즉석 조회, 로그인, 사용자 입력 저장.
- 실시간 갱신(최대 1시간 지연을 허용).
- EA 비공식 API를 넘어서는 데이터(실제 점유율, xG, 활동량 등).

**핵심 수치**

| 항목 | 값 | 근거 |
| --- | --- | --- |
| 수집 주기 | 1시간 (매시 17분) | 가장 빠른 플레이 속도가 경기당 약 15분, 10경기 한도 → 2시간 공백까지 안전 |
| 플레이 패턴 | 저녁 \~ 다음날 새벽, 세션당 9\~10경기, 경기 간격 3\~29분 | 2026-09-29 \~ 10-08 관측 39경기 |
| 경기 원본 크기 | 약 11 KB / 경기 | 월 150경기 기준 연 약 20 MB |
| 목록 요약 크기 | 약 150 B / 경기 | 3,000경기여도 450 KB |
| GitHub Actions 사용량 | 하루 18회 × 1분 = 월 약 540분 (시간대별 주기, 공개 저장소는 무제한) | 공개 저장소면 무제한, 비공개면 2,000분 한도 |

## 2. 데이터 소스: EA Pro Clubs API 스펙

베이스 URL은 `https://proclubs.ea.com/api/fc`, 모두 GET·인증 없음·JSON 응답이다. EA가 문서화하지 않은 비공식 API라 아래 내용은 2026-10-02\~10-08 직접 호출해 관찰한 것과 커뮤니티 문서를 합친 것이다. 숫자 값은 전부 **문자열**로 온다(`"goals":"2"`).

### 2.1 엔드포인트 목록

| 엔드포인트 | 파라미터 | 반환 | 수집 주기 | 비고 |
| --- | --- | --- | --- | --- |
| `/clubs/matches` | `platform`, `clubIds`(1개), `matchType`, `maxResultCount` | 경기 배열, 최신순 | 매 실행, matchType 3종 각각 | **핵심.** 선수별 상세·이벤트 포함 |
| `/clubs/overallStats` | `platform`, `clubIds`(1개) | 배열\[1\] | 매 실행(우리) + 새 상대 1회 | 시즌 누적, 스킬 레이팅 |
| `/allTimeLeaderboard/search` | `platform`, `clubName`, `maxResultCount` | 배열 | 매 실행 | 현재·최고 디비전, 승점, 클린시트는 **여기에만** 있음 |
| `/currentSeasonLeaderboard/search` | 위와 같음 | 배열 | 선택 | 관측 기간에는 allTime과 값이 같았음 |
| `/members/stats` | `platform`, `clubId` | `{members:[], positionCount:{}}` | 매 실행 | 현 멤버의 시즌 누적(탈퇴자 제외) |
| `/members/career/stats` | `platform`, `clubId` | 위와 같음 | 선택 | `members/stats`의 축약판 |
| `/clubs/info` | `platform`, `clubIds` | `{clubId:{…}}` | 주 1회 | 클럽명, 킷, 엠블럼 ID |
| `/club/playoffAchievements` | `platform`, `clubId` | 배열 | 선택 | 이 클럽은 빈 배열 |

`club`/`clubs`, `clubId`/`clubIds` 표기가 엔드포인트마다 다르니 그대로 써야 한다. 엠블럼 이미지는 `https://eafc24.content.easports.com/fifa/fltOnlineAssets/24B23FDE-7835-41C2-87A2-F453DFDB2E82/2024/fcweb/crests/256x256/l{TEAM}.png`에서 받는다(`TEAM` 값, 커스텀이면 `crestAssetId`).

### 2.2 `/clubs/matches` 파라미터 동작 (직접 확인)

| 파라미터 | 값 | 동작 |
| --- | --- | --- |
| `matchType` | `leagueMatch` · `playoffMatch` · `friendlyMatch` | 종류별로 **따로** 10경기 한도. 지정하지 않으면 리그로 추정 |
| `maxResultCount` | 없음 → 5 · 1\~10 → 그 수 · 11 이상 → **10에서 잘림** | 100을 넣어도 10 |
| `offset`, `startIndex`, `page`, `skip`, `beforeTimestamp` | 무시됨 | 항상 같은 최신 10경기. **페이지네이션 없음** |
| `clubIds` | `829628,4933` | HTTP 400 `Invalid query parameter: clubIds`. **1개만** |

### 2.3 `/clubs/matches` 응답 구조

경기 1건 = 약 11 KB. 최상위 키: `matchId`, `timestamp`(초), `timeAgo`, `clubs`, `players`, `aggregate`.

| 경로 | 내용 | 주의 |
| --- | --- | --- |
| `matchId` | 경기 고유 ID(문자열) | 중복 제거 키 |
| `timeAgo` | `{number:2, unit:"days"}` | **조회 시점마다 바뀜** → 저장 전 제거 |
| `clubs.{clubId}` | `goals`, `goalsAgainst`, `result`, `winnerByDnf`, `season_id`, `TEAM`, `details{name, clubId, teamId, customKit}` | `result`는 관찰상 1=승 2=패 4=무, 10·16385는 이탈 관련. `season_id`는 항상 `"0"` |
| `players.{clubId}.{playerId}` | 사람 선수 1명분, 양 팀 모두 | 키가 **고정 선수 ID**(예: lanil = 172777124). `playername`은 바뀔 수 있음 |
| `players.*.*` 기본 필드 | `secondsPlayed`, `rating`, `mom`, `pos`, `goals`, `assists`, `shots`, `passesmade`, `passattempts`, `tacklesmade`, `tackleattempts`, `redcards`, `saves` 등 38개 | EA 집계값. 총계는 이걸 기준으로 |
| `players.*.*.match_event_aggregate_0~3` | `"215:21,216:5,24:17,…"` 형태의 `이벤트ID:횟수` 목록 | 세부 지표의 원천. 4개 버킷을 합쳐 해석 |
| `aggregate.{clubId}` | 위 기본 필드의 팀 합계 | 사람 선수 합계일 뿐, AI 제외 |

### 2.4 기타 응답 필드

- `overallStats`: `gamesPlayed`, `wins/ties/losses`, `goals/goalsAgainst`, `skillRating`, `promotions/relegations`, `lastMatch0~9`, `lastOpponent0~9`. `bestDivision`은 여기서는 `null`로 온 적 있음 → leaderboard 값 사용.
- `allTimeLeaderboard/search`: 위 + `currentDivision`, `bestDivision`, `points`, `cleanSheets`, `clubInfo`. 클럽명 검색이라 결과에서 `clubId`로 골라야 함.
- `members/stats`: 멤버당 `name`, `gamesPlayed`, `goals`, `assists`, `passSuccessRate`, `tackleSuccessRate`, `ratingAve`, `manOfTheMatch`, `proOverall`, `proPos`, `favoritePosition`, `prevGoals~prevGoals10` 등 34개. **선수 ID 없음**(이름만) → 경기 데이터로 ID↔이름 매핑 필요.

### 2.5 호출에 필요한 헤더

EA는 Akamai 봇 차단 뒤에 있어 브라우저처럼 보이지 않으면 403 또는 무응답이다. 커뮤니티에서 통과한 조합:

```
Accept: application/json
Referer: https://www.ea.com/
Origin: https://www.ea.com
sec-fetch-site: same-origin
User-Agent: (최신 Chrome UA)
```

커뮤니티 보고는 "헤더를 넣어도 일반 `curl`은 막히고 Python `curl_cffi`의 `impersonate="chrome"`(크롬 TLS 지문 모방)은 통과한다"였다. **2026-10-08 검증 결과는 다르다.** GitHub 호스팅 러너 5대에서 `sec-fetch-site: same-origin`을 포함한 브라우저 헤더만 붙이면 curl\_cffi, 표준 urllib, 일반 curl CLI 세 가지 모두 200을 받았다. 즉 현재 시점의 관건은 TLS 지문이 아니라 헤더다. 수집기는 의존성 없는 urllib을 기본으로 하고, 403이 나타나면 curl\_cffi로 전환할 수 있게 클라이언트를 분리해 둔다.

## 3. API 제약 요약

설계를 결정하는 제약은 다섯 개다: 10경기 한도, 클라우드 IP 차단, CORS 불가, 사람 선수만 기록, 이벤트 ID 비공식. 나머지는 구현에서 처리하면 된다.

| 제약 | 확인 방법 | 설계에 미치는 영향 |
| --- | --- | --- |
| **경기 종류별 최근 10경기만 제공**, 페이지네이션 없음 | 10/06 파라미터 테스트. 10/02→10/03 사이 11경기를 치러 1경기를 영구 유실한 사례 있음 | 주기 수집이 유일한 누적 수단. 1시간 간격, 예약 누락 2회 연속이면 유실 가능 |
| **클라우드 IP 차단** (Akamai) | Vercel IP에서 403 확인된 사례, GitHub Actions는 "가끔 막힘" 보고. AWS·GCP는 미확인(데이터센터 IP라 같은 위험) | **Phase 0에서 GitHub 러너로 200이 오는지 먼저 확인**. 집 IP(Mac) 대비책 필수 |
| **브라우저 직접 호출 불가** (CORS 헤더 없음) | example.com에서 fetch → 차단 확인 | 페이지는 EA를 부르지 않고 우리 저장소의 정리 데이터만 읽음 |
| **사람 선수만 기록** | 양 팀 모두 AI 동료·AI 골키퍼 기록 없음 | 팀 슬팅·패스·태클은 사람 합계. 팀 득점 ≠ 선수 골 합 가능. "패스 점유율"은 실제 점유율 아님 |
| **이벤트 ID 해석은 커뮤니티 추정** | 골·도움·슈팅은 EA 집계값과 100% 일치 확인(59건). 나머지는 검증 불가 | 원본 보존 필수. 지표 정의에 버전을 붙이고 재계산 가능하게 |
| EA 집계 `passesmade`가 오프사이드 패스를 성공으로 셀 | 59건 중 56건에서 `passesmade = event_215 + event_153` | 성공 패스 = EA값 − event\_153 (정의 v2) |
| 일부 기록은 이벤트가 경기 전체를 덮지 않음 | 커뮤니티 문서: 59건 중 4건에서 EA 집계값 ≫ 이벤트 합(패스 99 vs 11). 우리 데이터에서는 아직 0건 | "이벤트 불완전" 플래그를 두고 이벤트 기반 집계에서 제외 |
| `timeAgo` 필드가 조회마다 바뀜 | 같은 경기 2회 조회 비교 | 저장 전 제거. 안 그러면 매 실행 "변경"으로 보임 |
| **시즌 구분 값 없음** (`season_id`는 항상 `"0"`) | 응답 확인 | 시즌 경계는 `gamesPlayed`가 줄어드는 시점으로 직접 판정 |
| 선수 시즌 기록에 선수 ID 없음, 현 멤버만 | `members/stats` 응답 | 경기 데이터의 playerId↔이름 매핑 표 유지. 게이머태그 변경 대비 |
| 결과 코드 의미 미문서화 | 1·2·4는 스코어와 일치, 10·16385는 이탈 경기에서만 관측 | 승패는 스코어로 판정, 코드는 보조 |
| 상대 스킬 레이팅은 조회 시점 값 | 경기 당시 값은 어디에도 없음 | 경기를 처음 본 실행에서 바로 받아 경기와 묶어 저장(1시간 안 값 = 사실상 당시 값) |
| 가동 불안정 | 2026-06-19부터 약 3개월간 인증서 만료로 일부 엔드포인트 장애(EA 포럼) | 실패를 정상 상황으로 설계. 연속 실패 알림 |

## 4. 전체 아키텍처

서버 없는 3단 구조다. EA API를 부르는 것은 수집기 하나뿐이고, 페이지는 저장소에 커밋된 정리 데이터만 읽는다.

&#91;embedded content: 전체 아키텍처 · 수집 → 원본 → 빌드 → 정리 데이터 → 정적 페이지\]

수집기는 새 경기만 원본으로 저장하고, 빌드가 원본 전체에서 정리 데이터를 다시 만든 뒤 함께 커밋한다. GitHub 러너가 EA에 막히면 같은 수집기를 Mac에서 돌린다.

**역할 분리 원칙**

1. **원본은 불변.** `raw/`는 추가만 하고 수정하지 않는다. EA가 기록을 나중에 고치면 변경으로 커밋되어 git 이력에 남는다.
2. **지표 계산은 `build.py` 한 곳에서만.** 페이지 JavaScript는 값을 합산·필터링만 하고 이벤트 ID를 해석하지 않는다. 정의 변경 = 빌드 1회.
3. **증분 갱신 없음.** 수천 경기도 1초 안에 전부 재계산되므로 캐시·부분 갱신 로직을 두지 않는다.
4. **쓰는 주체는 항상 하나.** GitHub 러너와 Mac 러너가 동시에 커밋하지 않도록 워크플로의 `concurrency` 그룹으로 직렬화한다.

## 5. 데이터 모델과 저장 레이아웃

저장소는 \*\*원본(`raw/`)과 정리 데이터(`docs/data/`)\*\*로 나뉘고, 페이지는 `docs/`만 배포한다. 경기 1건 = 파일 1개가 기본 단위다.

### 5.1 디렉터리 구조

```
raw/
  matches/{matchType}/{matchId}.json    EA 응답 그대로 (timeAgo만 제거) + _meta{fetchedAt, oppSR, oppGP}
  club/overall/{YYYY-MM-DD}.json        값이 바뀐 날만 저장
  club/leaderboard/{YYYY-MM-DD}.json
  members/{YYYY-MM-DD}.json
  state.json                            마지막 성공 시각, 마지막 gamesPlayed, 연속 실패 수
meta/
  players.json                          playerId → {names:[…], firstSeen, lastSeen}
  seasons.json                          시즌 경계 (수동 + 자동 감지)
  metrics.json                          지표 정의 버전·변경 이력
docs/                                   GitHub Pages 배포 루트
  index.html, app.js, style.css
  data/
    index.json                          시즌 누적, 선수 집계(시즌·최근 10), 세션 목록, 빌드 시각, 버전
    matches/{season}.json               경기당 1줄 요약 (검색·목록용)
    match/{matchId}.json                해석 완료된 경기 1건 (팀 비교 + 선수별 지표)
    player/{playerId}/{season}.json     선수 × 경기 1줄
```

### 5.2 파일 단위와 크기

| 파일 | 행 단위 | 크기 | 로딩 시점 |
| --- | --- | --- | --- |
| `raw/matches/*.json` | 경기 | 약 11 KB | 페이지는 읽지 않음 |
| `data/index.json` | — | 수십 KB (고정) | 첫 화면 |
| `data/matches/{season}.json` | 경기 | 약 150 B × 경기 수 (시즌 1,000경기 = 150 KB) | 경기 이력·검색 |
| `data/match/{id}.json` | 경기 | 1\~3 KB | 경기 펼치기, 선수 세부 |
| `data/player/{id}/{season}.json` | 선수 × 경기 | 경기당 약 300 B | 선수 추이, 임의 기간 집계 |

### 5.3 경기 요약 행 (`matches/{season}.json`)

`matchId`, `ts`, `matchType`, `oppId`, `oppName`, `gf`, `ga`, `result`(W/D/L), `dnf`, `dnfReason`, `oppSR`, `oppGP`, `scorers[{pid, g}]`, `nHuman`, `nOppHuman`, `sessionId`, `eventsIncomplete`. 상대팀 검색과 상대별 전적은 이 파일 하나로 브라우저에서 처리한다.

### 5.4 선수 식별

- 키는 EA의 **고정 playerId**(`players.{clubId}.{playerId}`)다. 게이머태그(`playername`)는 표시용.
- `meta/players.json`에 ID별 이름 이력을 쌓는다. `members/stats`는 이름만 주므로 이 표로 ID에 연결한다. 연결 안 되는 이름은 경고 로그.
- 상대팀 선수는 개인 식별 없이 팀 합계만 정리 데이터에 남긴다(원본에는 그대로 있음).

### 5.5 세션 묶기

경기 간격이 **60분 이하**면 같은 세션. 세션 ID = 첫 경기 시각(KST) `2026-10-07_2244`. 관측된 세션 내 최대 간격은 29분, 세션 간 간격은 최소 20시간이라 여유가 크다. 세션은 빌드 때 계산하는 파생값이라 기준을 바꿔도 원본은 그대로다. 하루 두 세션도 시각이 붙어 있어 충돌하지 않는다.

### 5.6 지표 정의 버전

`meta/metrics.json`에 현재 버전과 변경 이력을 두고, 모든 `data/` 파일에 `metricsVersion`을 박는다. 예: v1 "패스 성공 = EA passesmade", v2(2026-10-07) "패스 성공 = passesmade − event\_153". 페이지는 버전을 하단에 표시한다.

## 6. 수집기 설계

`collect.py`는 매시 17분에 돌아 **새 경기만 원본으로 저장**하고, 아무것도 해석하지 않는다. 한 번 실행에 EA 호출 5\~10번, 30\~45초를 목표로 한다.

### 6.1 실행 순서

1. `raw/state.json` 읽기 (마지막 `gamesPlayed`, 연속 실패 수).
2. `matchType` 3종을 각각 `maxResultCount=10`으로 호출. 호출 사이 1초 대기.
3. 경기별로 `raw/matches/{type}/{matchId}.json`이 **없으면** 저장. 있으면 내용을 비교해 달라졌을 때만 덮어쓰고 로그에 남긴다(저장 전 `timeAgo` 제거).
4. 새 경기의 상대 중 이번 실행에서 처음 보는 클럽만 `clubs/overallStats` 호출 → 경기 파일의 `_meta.oppSR`, `_meta.oppGP`에 기록. 원본 응답은 건드리지 않고 `_meta` 키만 덧붙인다.
5. 우리 `overallStats`, `allTimeLeaderboard`, `members/stats` 호출 → 전날 파일과 다를 때만 오늘 날짜 파일로 저장.
6. 누락 감지(6.3) 후 `state.json` 갱신, `build.py` 실행, 변경이 있을 때만 커밋·푸시.

### 6.2 HTTP 클라이언트

| 항목 | 값 | 이유 |
| --- | --- | --- |
| 라이브러리 | Python 3.12 표준 라이브러리 urllib + 브라우저 헤더 (기본). 403 시 대체: `curl_cffi` (`impersonate="chrome"`) | 크롬 TLS 지문으로 Akamai 통과. `requests`는 차단 보고 |
| 헤더 | 2.5절의 5개 | 없으면 403 |
| 요청 대기 | 10초 | 2분 작업 제한 안에 들어오게 |
| 재시도 | 2회, 2·4초 간격. **403은 재시도 안 함** | 403은 IP 차단이라 반복해도 같고 차단만 강화 |
| 호출 간격 | 1초 | 커뮤니티 권장 |
| 응답 검증 | JSON 파싱 + `matchId` 존재 + 필수 키 | 200이어도 HTML 오류 페이지가 올 수 있음 |

### 6.3 누락 감지

- **정확한 방법(리그)**: `gamesPlayed` 증가분 − 이번에 새로 저장한 리그 경기 수 = 놀친 경기 수. 0이 아니면 `state.json`에 누적하고 알림.
- **보조 방법(전체)**: 받은 10경기가 기존 저장분과 하나도 겹치지 않으면 경고. (실제로 10/06→10/08 사이 9경기가 새로 생겨 1경기만 겹쳐서 간신히 유실을 피했다.)
- 유실은 복구할 수 없다. 감지의 목적은 집계에 "결측 있음" 표시를 남기는 것이다.

### 6.4 실패 처리

| 상황 | 동작 |
| --- | --- |
| 403 | 즉시 중단, 아무것도 저장 안 함, `state.json` 연속 실패 +1 |
| 타임아웃·5xx | 재시도 2회 후 중단. 부분 성공(리그만 받음)은 받은 것만 저장 |
| 응답 구조 변경(필수 키 없음) | 원본은 `raw/quarantine/`에 저장, 빌드에서 제외, 알림 |
| 연속 실패 3회 | GitHub Actions 실패 메일(기본 제공) + 페이지 배지 "수집 중단 n시간" |
| 작업 2분 초과 | `timeout-minutes: 2`로 강제 종료. 다음 실행이 이어받음 |

매 실행은 성공·실패와 관계없이 `state.json`의 `lastRunAt`을 갱신하고 하루 1회는 반드시 커밋한다(60일 비활성 규칙 방어, 11절).

## 7. 빌드·집계 설계

`build.py`는 매 실행 **원본 전체를 읽어 `docs/data/`를 통째로 다시 만든다.** 지표 계산은 이 스크립트 한 곳에만 있다.

### 7.1 지표 정의 규칙 (현재 v2, 2026-10-07)

| 지표 | 정의 | 비고 |
| --- | --- | --- |
| 출전 | `secondsPlayed ≥ 600` | 평균 평점은 출전 경기만. 누적 수치는 전부 포함 |
| 승패 | 스코어로 판정 | 결과 코드는 보조 |
| DNF | 결과 코드가 1·2·4가 아님, 또는 `winnerByDnf=1`, 또는 사람 선수 전원 10분 미만 | 집계에서 제외 가능하게 플래그 |
| 패스 성공 | `passesmade − event_153` | EA값은 오프사이드 패스를 성공으로 셀 |
| 패스 시도 | `passattempts` | EA값 그대로 |
| 전진 패스 성공 | `event_30 − event_153` | 오프사이드는 항상 전진 |
| 패스 점유율 | 우리 성공 패스 ÷ 양 팀 성공 패스 합 | 사람 선수만. 반올림 후 합 100% 보정 |
| 슈팅·골·도움 | EA 집계값 | 이벤트와 100% 일치 확인 |
| 크로스 시도 | `event_36 + event_37` | 성공에는 세트피스 포함, 실패는 오픈플레이만 → 성공률 과대 가능 |
| 그 외 이벤트 지표 | 커뮤니티 확정표 그대로 | `event_104`(오프사이드 걸림)만 "참고" 표시 |

정의를 바꿀 때는 `meta/metrics.json`에 버전·날짜·이유를 추가하고 빌드만 다시 돌린다.

### 7.2 이벤트 불완전 기록

`passesmade`가 `event_215 + event_153`보다 **5 이상** 크거나, `shots`가 `event_217 + event_218`과 다르면 `eventsIncomplete=true`. 이 기록은 EA 집계값 기반 지표(골·도움·슈팅·패스 총계·평점)에만 들어가고 이벤트 기반 집계(방향·거리·위치·태클 유형 등)에서는 빠진다. 지금까지 우리 데이터에서는 0건, 커뮤니티 보고는 59건 중 4건.

### 7.3 사전 집계 묶음 (`index.json`에 포함)

| 묶음 | 단위 | 용도 |
| --- | --- | --- |
| 시즌 누적 | 선수 × 시즌 | 선수별 집계 표 기본 |
| 최근 10경기 (팀 기준) | 선수 × 팀의 최근 10경기 | 지금 페이지와 같은 기준 |
| 최근 10경기 (선수 기준) | 그 선수가 10분 이상 뛴 최근 10경기 | 적게 나온 선수 평가용 |
| 세션별 | 선수 × 세션, 팀 × 세션 | "그날 밤" 결산 |
| 월별 | 선수 × 월 | 추이 |
| 상대별 전적 | 상대 클럽 × 시즌 | 검색 결과 요약 |

**모든 비율은 분자·분모를 따로 저장한다.** 성공률 72%가 아니라 `{pc:134, pa:192}`, 평균 평점이 아니라 `{rtSum:62.6, rtN:8}`. 그래야 브라우저에서 기간을 합치거나 줄여도 값이 맞는다. 임의 기간 집계는 `player/{id}/{season}.json`을 받아 브라우저가 합산한다.

### 7.4 시즌 경계

`overallStats.gamesPlayed`가 직전 값보다 작아지면 새 시즌으로 보고 `meta/seasons.json`에 경계 시각을 기록한다. 자동 감지가 틀리면 수동으로 고칠 수 있게 파일로 둔다. 시즌이 바뀜 때 `members/stats`의 누적값도 초기화되므로 직전 시즌의 마지막 파일을 시즌 최종값으로 보관한다.

## 8. 프론트엔드 설계

지금의 단일 HTML 페이지를 그대로 쓰되, 데이터를 HTML에 박지 않고 **`data/` 파일을 필요할 때 fetch**하도록 바꾼다. 프레임워크·빌드 도구 없이 정적 HTML + JS다.

### 8.1 화면과 로딩

| 화면 | URL | 읽는 파일 |
| --- | --- | --- |
| 홈 = 최근 세션 (시즌 누적 · 세션 요약 · 세션 경기 이력 · 세션 선수 집계 · 세션 목록 · 수집 누적 선수 집계) | `#/` | `meta.json`, `sessions.json`, `sessions/{최신}.json`, `players.json` |
| 과거 세션 (홈과 같은 레이아웃, 선택 세션만 바뀜) | `#/s/{sessionId}` | `sessions/{sessionId}.json` |
| 경기 펼치기 (속한 세션 화면으로 이동 후 펼침) | `#/m/{matchId}` | 같은 세션 파일 |
| 선수 세부 (경기 1건) | `#/m/{matchId}/p/{playerId}` | 같은 파일 |
| 상대팀 검색 (세션 목록 카드 안 검색창, 결과는 세션 횡단 경기 행) | `#/?q=이름` | `matches.json` (첫 입력 시 지연 로딩) |
| 선수 추이 (세션별 시계열) | `#/p/{playerId}` | `players.json` + `sessions.json` — 2차 |

**2026-10-09 UX 결정 (시안 `fc_ingyeo_tracker_proto.html` 기준).** 기본 탐색 단위는 "최근 10경기"가 아니라 **세션**(60분 안에 이어진 경기 묶음, ID = 첫 경기 matchId)이다. 선수 집계 표는 두 개를 동시에 둔다: 경기 이력 바로 아래의 **세션 선수 집계**와 페이지 하단의 **수집 누적 집계**(수집 누적 | 최근 5세션). 두 표는 같은 렌더러에 범위만 다르고 각자 탭·정렬·합계/90분당 상태를 가진다. **포지셔닝·판단 탭(event\_219/111/177/182)은 노출하지 않는다**(원본에는 보존). 초기 수집본처럼 이벤트가 일부만 기록된 경기는 해당 항목을 0이 아니라 \*\*미기록(null)\*\*로 다루고, 집계에서는 "n경기 제외" 태그와 함께 기록된 경기만 분모로 쓴다(90분당도 동일). 페이지 하단의 "이벤트 ID 해석: github.com/Interactive-63/…" 문구는 삭제한다(제약사항 접이기 안의 출처 언급은 유지). 상대팀 검색은 이름 부분일치이며, 클럽명 변경에 대비해 `matches.json`에 `oppId`를 함께 두고 같은 클럽을 묶어 보여줄 수 있게 한다.

**2026-10-10 UX 추가 결정.** 선수 세부 화면에서 다른 선수 칩이나 이전/다음 경기를 눌러도 스크롤 위치를 유지한다(홈 → 선수 진입만 최상단). 상대·우리 클럽 엠블럼은 EA 콘텐츠 CDN 이미지를 **핫링크**로 보여 주고 저장소에 내려받지 않는다(경기 응답 `TEAM` → 커스텀 크레스트 → 실제 배지 순 폴백, 전부 실패 시 이니셜; 초기 임포트 상대는 실행당 5클럽씩 `clubs/info`로 보강). EA `members/stats`의 선수별 시즌 누적(퇴장 포함)은 `meta/members.json`에 두고 하단 "시즌 선수 누적 · EA 집계" 표로 따로 보여 주되, 정의가 EA 기준이라 수집 집계와 섞지 않는다. 세션 목록은 **최근 10개씩** 보여 주고 "이전 세션 더 보기"로 펼친다(세션 딥링크는 해당 페이지까지 자동 펼침, 검색 결과는 30개씩). **"진행 중" 태그는 조회 시점 기준 마지막 경기가 1시간 이내일 때만** 붙이고, 1시간 동안 새 경기가 없으면 게임을 마친 것으로 보아 표시를 지운다(페이지가 1분마다 다시 판단하므로 새로고침 불필요). README는 존댓말로 다시 썼다.

검색은 요약 파일을 받은 뒤 브라우저에서 문자열 필터링한다. 상대팀 이름은 바뀔 수 있으므로 표시는 이름, 키는 `oppId`.

### 8.2 캐시 갱신

GitHub Pages는 파일을 약 10분 캐시한다. `index.json`은 매 방문 `?v=빌드시각`을 붙여 받고, 나머지 파일은 `index.json`이 알려 주는 `buildId`를 쿼리에 붙여 받는다. 경기 상세 파일은 내용이 바뀌지 않으므로 버전 없이 두어도 된다(지표 정의가 바뀜 때만 예외 → `metricsVersion`을 쿼리에 포함).

### 8.3 지금 페이지에서 바뀜 것

- `const DATA = __DATA__` → `fetch('data/index.json')`. `file://`로는 열리지 않으므로 로컬 확인은 `python -m http.server`.
- 지표 계산 코드(오프사이드 차감, 세션 묶기, DNF 판정 등)는 `build.py`로 옮기고, 페이지는 미리 계산된 값을 표시·합산만 한다.
- "최근 10경기" 고정 대신 기간·시즌·세션 선택기.
- 데이터 기준 배지에 "마지막 수집 성공 n분 전"과 결측 경고.

### 8.4 유지할 것

펼치기·선수 추이·해시 라우팅·다크 모드·모바일 카드 레이아웃·ⓘ 설명·0 값 숨김·지표 표 구성은 지금 페이지 그대로.

## 9. 퍼블리싱에 필요한 작업

**공개 저장소 + GitHub Actions(1시간) + GitHub Pages** 조합으로 간다. 설정은 반나절이면 끝나고, 비용은 0원이다.

### 9.1 저장소

- 개인 GitHub 계정에 **공개** 저장소 생성(회사 계정 사용 금지). 비공개는 10절 비교 참고.
- 기본 브랜치 `main` 하나만 사용. 예약 실행은 기본 브랜치에서만 동작한다.
- 팀원 게이머태그와 기록이 영구 공개되므로 **팀원 동의를 먼저** 받는다.

### 9.2 GitHub Actions 워크플로 (`.github/workflows/collect.yml`)

```yaml
# 2026-10-10 확정: KST 21:00~03:00 에만 수집. 낮에는 경기가 없어 스케줄 없음 (필요 시 수동 실행)
on:
  schedule:
    - cron: '3,23,43 12-17 * * *'       # UTC 12:03~17:43 = KST 21:03~02:43, 20분 간격 (하루 18회)
    - cron: '3 18 * * *'                # UTC 18:03 = KST 03:03, 밤 세션 마감 1회
  workflow_dispatch:                     # 수동 실행 버튼 (Cloudflare 우회로도 이 이벤트를 사용)
concurrency: { group: collect, cancel-in-progress: false }   # 겹쳐 돌지 않게
permissions: { contents: write, pages: write, id-token: write }
jobs:
  collect:
    runs-on: ubuntu-latest
    timeout-minutes: 5
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.12' }
      - run: python collect.py           # 표준 라이브러리만 사용 (pip install 없음)
      - run: python build.py
      - run: |                             # 변경이 있을 때만 커밋, docs/ 가 바뀌었을 때만 배포 (실제 파일은 keepalive 단계 포함)
          git add raw meta docs
          git diff --cached --quiet && exit 0
          git commit -m "collect $(date -u +%FT%TZ)" && git push
      - uses: actions/upload-pages-artifact@v3   # docs/ 변경 시
  deploy:                                # needs: collect, docs 변경 시에만 → actions/deploy-pages@v4
```

**배포 방식 (2026-10-09 확정).** Pages 소스는 "Deploy from a branch"가 아니라 **GitHub Actions**로 둔다. `GITHUB_TOKEN`으로 만든 커밋은 다른 워크플로를 트리거하지 않기 때문에, 수집 워크플로(`collect.yml`)가 커밋 뒤 `upload-pages-artifact` → `deploy-pages`로 직접 배포한다. 사람이 `docs/`를 고쳐 push한 경우는 별도 `pages.yml`이 배포한다. 저장소 설정에서 Workflow permissions를 **Read and write**로 바꿔야 push가 된다(기본값 read-only). 수집기는 pip install 없이 표준 라이브러리만 쓴다. 상대 선수 게이머태그는 저장 시점에 첫/끝 글자만 남기고 `*`로 치환한다(원복 불가).

EA 호출에 인증이 없어 **저장할 비밀값이 없다.** 커밋은 워크플로 기본 토큰으로 한다.

### 9.3 GitHub Pages

- Settings → Pages → Deploy from a branch → `main` / `/docs`. 푸시마다 자동 배포, 보통 1분 안.
- URL: `https://<계정>.github.io/<저장소>/`. 사용자 정의 도메인은 선택.
- 한도: 사이트 1 GB, 월 100 GB 전송, 시간당 10회 빌드(소프트 한도). 1시간 주기면 빌드는 시간당 최대 1회.

### 9.4 체크리스트

- [ ] 개인 계정에 공개 저장소 생성, 팀원 동의
- [ ] **Phase 0**: `workflow_dispatch`로 1회 실행해 GitHub 러너에서 EA API 200 확인 (403이면 10.2절로)
- [ ] `collect.py`, `build.py` 커밋 (기본 의존성 없음. `requirements.txt`는 curl\_cffi 전환 시에만)
- [ ] 워크플로 커밋 후 수동 실행으로 커밋·푸시 권한 확인
- [ ] Pages 활성화, URL 접속 확인
- [ ] Settings → Notifications에서 워크플로 실패 메일 켜기
- [ ] 1일 뒤 Actions 사용량 페이지에서 실행당 시간 확인 (목표 1분 이내)
- [ ] 지금 스냅샷에 있는 39경기 원본을 `raw/`에 초기 적재 (9/29\~10/08 데이터 보존)

## 10. 대안 비교

선택을 가른 기준은 비용이 아니라 **EA가 그 서버의 요청을 받아 주느냐**였다. 무료 범위는 대부분 충분하다.

### 10.1 실행 환경

| 후보 | 무료 범위 | EA 차단 위험 | 판단 |
| --- | --- | --- | --- |
| **GitHub Actions (GitHub 호스팅 러너)** | 공개 저장소 무제한, 비공개 월 2,000분 | 낮음 (검증됨). 10/08 러너 5대 동시 호출 5/5 성공. 커뮤니티의 "가끔 막힘" 보고는 장기 운영에서 재확인 | **기본** |
| **Mac 자체 러너** (같은 워크플로, 집 IP) | 공개 저장소 무료(유료화 계획 철회됨) | 낮음. 지금까지 성공한 호출은 전부 집 IP | **대비책**. Mac이 켜져 있어야 함 |
| Cloudflare Workers + Cron | 하루 10만 요청, 예약 5개 | 미확인. 10/08 검증에서 일반 curl도 통과해 TLS 지문은 관건이 아닌 듯하나, Cloudflare IP 대역은 미검증 | 비추천 |
| AWS Lambda + EventBridge | Lambda 월 100만 요청 상시 무료. 단 2025-07 이후 신규 "무료 플랜"은 6개월 후 종료, 계속 쓰려면 카드 등록 | 높음. Vercel(AWS 기반) 403 사례 | 비추천 |
| Google Cloud Run + Scheduler | Cloud Run 월 200만 요청, 예약 3개. 결제 계정 필요 | 높음 (데이터센터 IP) | 비추천 |
| Oracle Cloud 상시 무료 VM | VM 2대 무료. 7일 사용률 20% 미만이면 회수 가능 | 높음 | 비추천 |
| Vercel Cron | 무료 플랜은 하루 1회만 | **차단 확인됨** | 부적합 |

### 10.2 GitHub 러너가 403이면

Mac을 저장소의 자체 러너(self-hosted runner)로 등록하고 워크플로의 `runs-on`만 `self-hosted`로 바꾼다. 예약·로그·커밋은 GitHub이 그대로 관리하고 실행만 집에서 된다. 가장 가벼운 전환이지만 Mac이 잠들면 수집이 멈춘다(전원 설정에서 잠자기 끄기). 두 번째 선택은 Mac의 launchd로 독립 실행하고 git push만 하는 방식이다.

### 10.3 호스팅

| 후보 | 비공개 저장소 | 한도 | 판단 |
| --- | --- | --- | --- |
| **GitHub Pages** | 무료 플랜은 공개 저장소만 | 1 GB, 월 100 GB, 시간당 10빌드 | **기본**. 같은 저장소에서 끝남 |
| Cloudflare Workers (정적 에셋) | 가능. Actions에서 `wrangler deploy`로 직접 배포 | 파일 2만 개, 파일당 25 MiB, 정적 요청 무제한. 커스텀 도메인은 DNS가 Cloudflare에 있어야 함 | 비공개·접근제어(Access)가 필요해질 때의 대안. **Cloudflare Pages는 공식 문서가 "새 프로젝트는 Workers로"라고 안내해 제외.** `wrangler deploy` 직접 배포면 Cloudflare 빌드 한도와 무관. 2026-10-09 결정: GitHub Pages로 시작하고 Workers는 추후 검토 |
| Netlify / Vercel 정적 | 가능 | 빌드 분·대역폭 한도 | 장점 없음 |

**Cloudflare Workers 전환 절차 (필요해질 때).** 수집·빌드는 그대로 두고 배포 단계만 바꾼다. (1) Cloudflare 무료 계정 → Workers & Pages에서 `workers.dev` 서브도메인(계정당 1회) 선택, Account ID 확인. (2) API 토큰 생성: 템플릿 "Edit Cloudflare Workers" → GitHub Secrets에 `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID` 저장. (3) 저장소에 `wrangler.jsonc` 추가: `name: fc-ingyeo-tracker`, `compatibility_date`, `assets.directory: ./docs` — Worker 스크립트(`main`) 없이 정적 에셋만. (4) 워크플로 마지막에 `cloudflare/wrangler-action@v3`(또는 `npx wrangler deploy`) 단계 추가, `docs/` 변경이 있을 때만 실행. (5) `docs/_headers`에 `data/*`는 짧은 캐시, `index.html`은 no-cache 지정. (6) 결과 URL `fc-ingyeo-tracker.<계정>.workers.dev` 확인 후 GitHub Pages 끄고 저장소 비공개 전환. 커스텀 도메인은 Cloudflare zone 필요, 접근 제어는 Zero Trust Access(무료 50명). 추가 비용: Node 실행으로 작업 시간 20\~40초 증가, 토큰 관리 대상 1개.

### 10.4 공개 vs 비공개 저장소

|  | 공개 | 비공개 |
| --- | --- | --- |
| Actions | 무제한 | 월 2,000분. 하루 18회 × 1분 = 약 540분(27%). 경기 시간대를 15분으로 좁혀도 약 900분(45%) |
| Pages | 가능 | 불가 → Cloudflare Pages 필요 |
| 60일 비활성 시 예약 중단 | 적용 | 미적용 |
| 데이터 노출 | 원본·게이머태그 전부 공개 | 저장소는 비공개. 단 페이지 URL은 어차피 공개 |
| 판단 | **기본** | 게이머태그 공개가 부담스러울 때 |

"데이터는 숨기고 페이지만 공개"는 성립하지 않는다. 페이지가 읽는 `data/` 파일도 같은 URL 아래 공개되기 때문이다. 비공개로 얻는 건 원본 JSON과 코드를 검색에서 숨기는 정도다.

## 11. 운영

### 11.1 모니터링

(2026-10-09 개정) 수집기는 `meta/state.json`에 시즌 누적, 마지막으로 본 `gamesPlayed`, 연속 실패 횟수만 기록하고 실행 시각은 쓰지 않는다. 빌드 산출물도 시각 없이 결정적이라, 새 경기·시즌 누적 변화·실패 상태 변화가 없으면 git diff가 비어 커밋도 배포도 생기지 않는다. 워크플로는 **연속 3회 실패**(403 포함)에 한해 `::error::`로 실행을 실패 처리하여 GitHub 기본 알림(이메일)이 가도록 한다. 1\~2회 실패는 로그만 남기고 성공으로 끝낸다. 하루 종일 경기가 없는 날이 대부분이므로 "새 경기 0건"은 정상이다.

페이지 상단의 "마지막 수집" 시각은 저장소가 아니라 GitHub Actions 실행 기록(공개 API, 비인증 60회/시)에서 읽으므로 커밋 없이도 갱신된다. 기존 설계(페이지 상단에는 `meta/state.json`의 마지막 성공 시각을 표시하고, 24시간을 넘기면 경고 띠를 보여 준다. 수집이 멈춘 것을 사람이 가장 먼저 알아채는 경로가 이 띠다.

커밋은 변경이 있을 때만 생기므로 Pages 빌드 한도는 고려 대상이 아니다. 60일 비활성 규칙은 마지막 커밋 후 50일이 지나면 워크플로가 meta/keepalive.txt를 갱신해 피한다. 나중에 Cloudflare Workers로 옮길 때도 Actions에서 `wrangler deploy`를 직접 호출하면 Cloudflare 빌드 한도를 쓰지 않으므로 이 커밋 정책을 바꿀 필요가 없다.

### 11.2 수집 누락 복구

`gamesPlayed` 증가분이 새로 받은 경기 수보다 크면 누락이 확정이다. API는 과거를 돌려주지 않으므로 복구 수단은 없다. `meta/gaps.json`에 "언제, 몇 경기"만 기록하고 페이지의 시즌 누적 섹션에 "수집 누락 N경기" 주석을 붙인다. 누락은 한 세션에 10경기 넘게 치르고 그 사이 수집이 모두 실패했을 때만 발생하므로, 실패가 겹치지 않는 한 발생 빈도는 낮다.

### 11.3 재빌드

집계 규칙(지표 버전)을 바꾸면 `build.py`를 로컬에서 돌려 `docs/data/`를 다시 생성하고 커밋한다. 원본은 손대지 않는다. 지표 버전 번호를 `docs/data/meta.json`에 기록하고 페이지가 이를 표시한다. 과거 경기의 이벤트 매핑 해석이 바뀌어도 원본에서 다시 계산하면 되므로 소급 적용이 자유롭다.

### 11.4 비용 감시

공개 저장소면 볼 것이 없다. 비공개로 갈 경우 Settings → Billing의 Actions 사용량을 월 1회 확인한다. 실행 시간이 1분 30초를 넘기 시작하면 `timeout-minutes`보다 먼저 원인을 본다. 저장소 용량은 연 100 MB 미만이라 수년간 문제없다.

### 11.5 EA 측 변경 대응

응답 스키마가 바뀌거나(필드 추가·삭제) 이벤트 코드 의미가 바뀌면 수집기는 원본을 그대로 저장하고 빌드만 실패한다. 수집기에는 스키마 검증을 두지 않는다. 원본만 확보되면 나중에 빌드를 고치면 된다. 다만 `matchId`·`timestamp`·`clubs` 키가 없으면 그 응답은 저장하지 않고 실패로 기록한다.

## 12. 제약사항 · 리스크 · 추가 검토

### 12.1 확정된 제약

| 제약 | 영향 | 대응 |
| --- | --- | --- |
| 비공식 API. 예고 없이 변경·종료 가능 | 전체 서비스 중단 | 원본 보존으로 누적분은 지킨다. 그 이상은 없음 |
| 매치타입당 최근 10경기, 과거 조회 불가 | 시작 이전 기록은 영원히 없음 | 현재 확보한 39경기 스냅샷을 초기 원본으로 임포트 |
| Akamai 봇 차단 | GitHub 러너 403 가능성 | 브라우저 헤더(urllib) + Mac 러너 대비 (§10.2). 10/08 GitHub 러너 5/5 통과 |
| CORS 미허용 | 브라우저가 EA를 직접 못 부름 | 페이지는 저장소 데이터만 읽음. "지금 새로고침" 기능 불가 |
| 최대 1시간 지연 | 경기 직후 확인 불가 | 상단에 마지막 수집 시각 표시 |
| 이벤트 코드 매핑은 커뮤니티 역추적 | 일부 지표 해석이 틀릴 수 있음 | 직접 검증한 항목(패스·골·도움·슈팅)과 미검증 항목을 페이지 주석에서 구분 |
| 오프사이드 걸림(104) 매핑 부분적 | 과소 집계 | "참고" 태그 유지 |
| 게이머태그가 공개 페이지에 노출 | 상대팀 선수 포함 | 원본은 저장, 페이지에는 우리 팀만 표시하는 옵션 검토 (§14) |
| 선수 이름 변경 | 이름 기준 집계가 갈림 | playerId 기준 집계, 이름은 최신값 표시. 단 members/stats에는 ID가 없어 이름으로만 연결 |

### 12.2 리스크와 발생 시 행동

| 리스크 | 가능성 | 발생 시 |
| --- | --- | --- |
| GitHub 러너 IP 전면 차단 | 중 | 워크플로 `runs-on` 한 줄 변경으로 Mac 러너 전환. 동일 코드 |
| 집 IP도 차단 | 하 | 브라우저 세션 쿠키 재사용 검토. 이 시점에서 자동화는 사실상 끝 |
| 스케줄 지연·누락 | 상 (GitHub 공식 안내) | 1시간 주기 + 10경기 버퍼로 흡수. 한 세션 10경기 초과 시에만 실제 누락 |
| 60일 비활성 스케줄 자동 중단 | 상 (시즌 오프 시) | 마지막 커밋 50일 경과 시 워크플로가 keepalive 커밋(§11.1). 봇 커밋이 활동으로 인정되는지는 미확인 → §14 |
| EA가 클럽 ID·플랫폼 체계 변경 (신작 출시) | 상 (매년 9월) | FC 28은 별도 저장소·별도 데이터로 본다. 섞지 않음 |
| 동시 실행으로 커밋 충돌 | 하 | `concurrency` 그룹 + push 실패 시 rebase 재시도 1회 |
| 저장소를 실수로 공개→비공개 전환 | 하 | Pages 즉시 중단됨. 전환 전 Cloudflare Pages 연결 선행 |

### 12.3 추가 검토 항목

**수집 주기 단축.** 30분으로 줄이면 공개 저장소에서는 비용 차이가 없고 누락 위험만 줄어든다. 단 비공개로 갈 경우 월 1,500분(75%)으로 여유가 없다. 더 좋은 방법은 **시간대별 주기**이다. 관측된 세션은 저녁\~새벽에 몰려 있으므로 20시\~04시는 30분, 나머지는 2시간으로 하면 실행 횟수는 비슷하고 누락 위험은 더 낮다. cron 두 줄로 표현 가능. 2026-10-09 채택: KST 21\~03시 30분, 그 외 3시간 → 2026-10-10 재조정: 낮에는 경기가 없어 KST 21:03\~03:03만 20분 간격, 그 외 시간은 수집하지 않음 (§9 YAML).

**플레이오프·친선 매치 수집.** 현재 설계는 세 매치타입을 모두 호출하지만 집계는 리그만 기본으로 한다. 플레이오프를 포함할지는 팀이 정할 문제다. 2026-10-09 결정: 세 타입 모두 수집하고 세션·누적 집계에 그대로 포함한다(필터 없음). 경기 행과 세션 요약에서 플레이오프·친선은 상대팀 이름 뒤 태그로만 구분한다. 세 타입은 API에서 각각 최근 10경기가 나오므로 수집 버퍼는 타입별로 따로 잡힌다. 단, 플레이오프는 상대 수준이 리그와 달라 누적 승률·득실에 섮이면 해석이 흐려질 수 있다는 점은 알고 간다.

**상대팀 후속 추적.** 상대 SR은 첫 관측 시점 값만 저장한다. 나중에 상대가 성장해도 반영되지 않는다. 이게 오히려 맞다 (경기 당시 강함). 다만 첫 관측이 경기 후 최대 1시간 뒤여서 그 사이 경기가 섮인다. 오차 범위를 주석에 적는다.

**세션 기준 60분.** 관측된 경기 간 최대 간격 29분 기반. 데이터가 쌓이면 재검토. 빌드 시 파생되므로 바꾸기 쉬움.

**기존 스냅샷 임포트.** data.json\~data9.json의 39경기는 스키마가 축약된 형태라 원본 응답과 다르다. 원본으로 복원할 수 없는 필드(예: `timeAgo`, 상대팀 선수 세부)가 있다. 이 경기들은 `raw/legacy/`에 별도 스키마로 두고 빌드가 두 형식을 모두 읽도록 한다. 아니면 버리고 새로 시작한다. §14에서 결정.

**인증 없는 공개 페이지.** URL을 아는 누구나 볼 수 있다. 팀 내부용이라면 Cloudflare Access(무료 50명) 같은 앞단 인증을 붙일 수 있지만 GitHub Pages에는 직접 못 붙인다. 필요하면 Cloudflare Pages로 옮긴다.

## 13. 단계별 실행 계획

각 단계는 단독으로 가치가 있고, 다음 단계가 막혀도 이전 단계 결과는 남는다. 예상 시간은 개발 기준이고 검증 대기(차단 여부 확인 등)는 별도다.

| 단계 | 할 일 | 완료 기준 | 예상 |
| --- | --- | --- | --- |
| **0. 차단 검증** | 빈 저장소에 urllib·curl\_cffi·curl 세 클라이언트로 `/clubs/info` 한 번 호출하는 워크플로만 만들고 `workflow_dispatch`로 5회 실행 | ✅ 완료 2026-10-08: 러너 5대 5/5 PASS. GitHub 러너 사용 확정. 6시간 간격 schedule 샘플은 24h 더 진행 | 1시간 |
| **1. 수집기** | `collect.py`: 3개 매치타입 호출, `raw/matches/{id}.json` 저장, 중복 스킵, `overallStats` → `meta/state.json`, 상대 SR 첫 관측 저장, gap 기록 | 로컬에서 2회 실행 시 두 번째는 새 파일 0개 | 반나절 |
| **2. 빌드** | `build.py`: raw → `docs/data/` (matches.json, players.json, recent10.json, seasons.json, meta.json). 현재 페이지의 집계 JS를 Python으로 이식. 지표 v2 규칙 그대로 | data9.json 10경기로 돌려 현재 페이지 수치와 일치 (자동 비교 스크립트) | 하루 |
| **3. 워크플로** | §9 YAML 적용, 커밋·push, concurrency, 실패 알림. Pages 설정 | 한 시간 두고 보면 커밋이 쌓이고 Pages URL에서 `data/meta.json` 읽힘 | 반나절 |
| **4. 페이지 전환** | `__DATA__` 인라인 → `fetch('data/*.json')`. 라우터는 유지. 상단에 마지막 수집 시각 | 모바일·데스크탑에서 현재 페이지와 동일하게 보임 | 반나절 |
| **5. 초기 데이터** | 기존 39경기 임포트 여부 결정 후 실행 (§14) | 시즌 누적 섹션에 반영 | 반나절 |
| **6. 누적 화면** | 시즌 누적·선수별 누적·상대팀 검색·세션 목록. 현재 페이지에 없는 새 화면 | 데이터 30경기 이상 쌓인 뒤 설계 | 미정 |

0단계는 2026-10-08에 통과했다. ubuntu-latest 러너 5대에서 동시에 /clubs/info 와 /clubs/matches 를 호출해 5/5 모두 200 + 정상 JSON을 받았다. 단 이 샘플은 같은 시각·같은 리전에서 나온 것이라 시간대·IP 대역 분산은 보여주지 못한다. 6시간 간격 schedule을 하루 더 돌려 보강하고, 운영 단계에서는 §11.1의 연속 실패 알림이 장기 차단을 잡는다. Mac 러너는 대비책으로만 남긴다.

1·2단계는 로컬 Mac에서 개발한다. 현재까지 성공한 호출이 전부 그 환경이라 차단 문제와 코드 문제를 분리할 수 있다. 2단계 완료 기준은 엄격하게 지킨다. 집계 이식에서 숫자가 틀어지면 이후 모든 데이터가 틀린다.

**구현 현황 (2026-10-09).** 1·2·3·4·5단계의 코드를 `fc-ingyeo-tracker` 저장소 뼈대로 작성했다: `collect.py`(세 매치타입 수집, 마스킹, 상대 SR 첫 관측, gap, 연속 실패 3회), `build.py`(세션·선수 집계·검색 인덱스, 지표 v2, 결정적 산출물), `docs/index.html`(data/ fetch, 세션 파일·검색 인덱스 지연 로딩, 수집 시각은 GitHub Actions API), 워크플로 2개, 임포트된 39경기. 검증: (a) 시안 페이지와 새 페이지의 세션 목록·세션 요약·선수 집계(기본·패스) 표시값이 일치, (b) `tools/selftest.py`가 가짜 EA 응답으로 수집·중복 제거·마스킹·gap·403 연속 실패·빌드·무변경 멱등성을 통과, (c) **실제 운영 검증(2026-10-09 22:45 KST)**: github.com/greg82p/fc-ingyeo-tracker 에 push 후 pages → collect 수동 실행 성공. 첫 수집에서 새 경기 10건(10/8 밤 세션 9 + 10/9 1) 저장, 상대 게이머태그 마스킹·상대 SR 첫 관측·팀 경고/오프사이드 집계 모두 정상. gamesPlayed 71→84 대비 수집 10건이라 리그 3경기 영구 누락이 gaps.json에 기록됨(수집 시작 전에 발생한 누락). overallStats의 bestDivision이 null로 와서 리더보드 값으로 대체하는 수정 1건. 6단계(선수 추이 화면)는 미착수.

**운영 이슈 #1 (2026-10-10 01:00 KST): GitHub `schedule` 이벤트가 한 번도 발화하지 않음.** 워크플로 `active`, 기본 브랜치, cron 유효, Actions 허용, 장애 없음 — 문서화된 제약은 모두 충족하는데 수동(`workflow_dispatch`)만 동작. 같은 계정의 `ea-probe`(6시간 간격, 24시간 경과)도 0건이라 계정 단위 현상. 2026년 7\~10월 GitHub 커뮤니티에 동일 증상 보고 다수(미해결). 대응: `tools/cf-dispatcher/` — Cloudflare Worker Cron Trigger(같은 UTC 슬롯 3줄)이 GitHub REST API로 `collect`에 `workflow_dispatch`를 보내는 우회로. fine-grained PAT(Actions RW, 이 저장소만)를 Worker secret에만 보관. GitHub `schedule:`은 관찰용으로 당분간 유지(겹치면 concurrency가 직렬화, 두 번째는 변경 없음). 임시 운영: 세션 종료 후(10경기 넘으면 중간에도) Actions 탭에서 수동 실행.

## 14. 미결 사항

개발 전에 결정해야 하는 것과, 나중에 결정해도 되는 것을 나눐다.

### 시작 전 결정

| 항목 | 선택지 | 제안 |
| --- | --- | --- |
| 저장소 공개 여부 | ✅ 결정 2026-10-09: 공개 + GitHub Pages. Cloudflare Workers 전환은 추후 검토 | **공개**. 페이지가 공개인 이상 비공개로 숨기는 건 원본 JSON 폴더 정도 |
| 기존 39경기 임포트 | ✅ 결정 2026-10-09: 임포트 (raw/legacy/ 별도 스키마, 미기록 항목은 null) | **임포트**. 시즌 누적 71경기 중 39경기는 버리기 아깝다. 단 축약 스키마라 상대팀 선수 세부는 없음을 명시 |
| 수집 주기 | ✅ 결정 2026-10-10: KST 21:00\~03:00에만 20분 간격 + 03:03 마감 (하루 19회). 낮에는 수집 없음, 필요 시 수동 실행 | **매시로 시작**, 누락이 관측되면 시간대별로 |
| 집계 대상 매치타입 | ✅ 결정 2026-10-09: 세 타입 모두 수집하고 세션·누적에 그대로 포함, 필터 없음. 리그가 기본이므로 플레이오프·친선만 상대팀 이름 뒤에 '플레이오프' / '친선' 태그 | **수집은 전부, 기본 표시는 리그**. 필터로 전환 |
| 저장소 이름 | ✅ 결정 2026-10-09: fc-ingyeo-tracker (개인 계정, 공개) | Pages URL은 <계정>.github.io/fc-ingyeo-tracker 로 고정됨. ea-probe 저장소는 검증 후 삭제 |

### 나중에 결정

| 항목 | 비고 |
| --- | --- |
| 상대팀 선수 게이머태그 페이지 표시 여부 | ✅ 2026-10-09: 원본(raw/) 공개 무방. 페이지는 기존대로 상대 선수 수만 표시 |
| 세션 기준 간격 | 60분으로 시작, 파생 데이터라 언제든 변경 |
| 60일 비활성 자동 중단이 봇 커밋으로 해제되는지 | GitHub 문서는 "저장소 활동"이라고만 함. Actions 봇 커밋이 포함되는지 커뮤니티 보고는 엇갈림. 시즌 오프 60일 전에 수동 커밋 한 번이 안전 |
| 팀 내부 인증 | 필요해지면 Cloudflare Pages + Access로 이전 |
| FC 28 전환 시 저장소 분리 방식 | 2027년 9월 전에만 정하면 됨 |
| 다른 클럽(상대팀) 추적 | 현재 설계는 클럽 하나. 확장하면 호출 수가 클럽 수에 비례 |

## 15. 참고 자료

**API 문서 (비공식, 커뮤니티)**

- Interactive-63, EA FC Pro Clubs API Research — https://github.com/Interactive-63/eafc-pro-clubs-api-research (엔드포인트·헤더·이벤트 코드 매핑 원출처)
- 1erkandogan, fc27-clubs-api 매치 이벤트 문서 — https://1erkandogan.github.io/fc27-clubs-api/match-events/ (이벤트 매핑 교차 검증에 사용)
- curl\_cffi — https://github.com/lexiforest/curl\_cffi (크롬 TLS 지문 모방)

**GitHub 공식 문서**

- GitHub Actions 과금 — https://docs.github.com/en/billing/managing-billing-for-your-products/managing-billing-for-github-actions/about-billing-for-github-actions
- 워크플로 스케줄 이벤트 (최소 간격, 지연, 60일 비활성) — https://docs.github.com/en/actions/writing-workflows/choosing-when-your-workflow-runs/events-that-trigger-workflows#schedule
- GitHub Pages 한도 — https://docs.github.com/en/pages/getting-started-with-github-pages/github-pages-limits
- GitHub Pages 요금제별 가용 여부 — https://docs.github.com/en/pages/getting-started-with-github-pages/what-is-github-pages
- 자체 호스팅 러너 — https://docs.github.com/en/actions/hosting-your-own-runners/managing-self-hosted-runners/about-self-hosted-runners

**대안 서비스 무료 한도**

- Cloudflare Pages — https://developers.cloudflare.com/pages/platform/limits/
- Cloudflare Workers Cron — https://developers.cloudflare.com/workers/configuration/cron-triggers/
- Vercel Cron — https://vercel.com/docs/cron-jobs/usage-and-pricing
- AWS 무료 플랜 — https://aws.amazon.com/free/
- Google Cloud 무료 등급 — https://cloud.google.com/free/docs/free-cloud-features
- Oracle Cloud Always Free — https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier\_topic-Always\_Free\_Resources.htm

**이 프로젝트 산출물**

- 현재 스냅샷 페이지 `fc_ingyeo_recent10.html` (template12 + data9.json, 2026-10-08 01:08 KST 수집)
- 수집 스냅샷 data.json \~ data9.json (39경기, 10/02\~10/08)
- 이벤트 매핑 검증: 59개 선수-경기 행에서 `passesmade = e215 + e153` 56건 일치, 골·도움·슈팅 100% 일치
