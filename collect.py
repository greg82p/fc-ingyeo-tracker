#!/usr/bin/env python3
"""FC INGYEO 수집기 — EA Pro Clubs API에서 새 경기를 받아 raw/ 에 저장한다.

표준 라이브러리만 사용한다 (2026-10-08 검증: 브라우저 헤더만 있으면 urllib 로 통과).
실행 1회 = 다음 순서.

  1. 세 매치타입(league / playoff / friendly)의 최근 10경기 조회
  2. raw/matches/{matchId}.json 이 없는 경기만 저장 (상대 선수 게이머태그는 마스킹)
  3. 우리 클럽 overallStats + currentSeasonLeaderboard 로 시즌 누적 갱신, gamesPlayed 증가분으로 누락 감지
  4. 새 상대 클럽의 스킬 레이팅을 첫 관측 시점에 meta/opponents.json 에 저장
  5. meta/state.json 갱신. 실패는 연속 횟수만 세고, 3회 연속이면 종료코드 1

설계 문서: §6 수집기 설계, §11 운영.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# ---------- 설정 ----------
CLUB_ID = os.environ.get("CLUB_ID", "829628")
CLUB_NAME_HINT = os.environ.get("CLUB_NAME", "FC INGYEO")        # 리더보드 검색어
PLATFORM = os.environ.get("PLATFORM", "common-gen5")
BASE = "https://proclubs.ea.com/api/fc"
MATCH_TYPES = ["leagueMatch", "playoffMatch", "friendlyMatch"]
TIMEOUT = 20
RETRIES = 2                      # 요청당 재시도 (총 3회)
FAIL_LIMIT = 3                   # 연속 실패 n회면 종료코드 1 → GitHub 알림

ROOT = Path(__file__).resolve().parent
RAW_DIR = ROOT / "raw" / "matches"
META_DIR = ROOT / "meta"
STATE_PATH = META_DIR / "state.json"
OPP_PATH = META_DIR / "opponents.json"
GAPS_PATH = META_DIR / "gaps.json"
MEMBERS_PATH = META_DIR / "members.json"   # EA members/stats (선수별 시즌 누적, EA 집계)
HISTORY_PATH = META_DIR / "club_history.json"  # 우리 클럽 SR·디비전·승점 추이 (값이 바뀐 실행에서만 한 줄 추가)
CREST_BACKFILL_PER_RUN = 5                 # 엠블럼 정보가 없는 기존 상대를 실행당 몇 클럽까지 clubs/info 로 보강할지
DIV_BACKFILL_PER_RUN = 5                   # 디비전을 아직 모르는 기존 상대를 실행당 몇 클럽까지 리더보드 검색으로 보강할지

HEADERS = {
    "accept": "application/json",
    "accept-language": "en-US,en;q=0.9",
    "sec-ch-ua": '"Google Chrome";v="141", "Not?A_Brand";v="8", "Chromium";v="141"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
    "referer": "https://proclubs.ea.com/",
    "user-agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"),
}


class ApiError(Exception):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(msg: str) -> None:
    print(f"[{now_iso()}] {msg}", flush=True)


def load_json(path: Path, default):
    if path.exists():
        with path.open(encoding="utf-8") as f:
            return json.load(f)
    return default


def save_json(path: Path, data, compact: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        if compact:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
        else:
            json.dump(data, f, ensure_ascii=False, indent=1)
        f.write("\n")
    tmp.replace(path)


# ---------- HTTP ----------
def get_json(endpoint: str, **params):
    """GET {BASE}/{endpoint}?... → JSON. 403/5xx/타임아웃은 재시도 후 ApiError."""
    params.setdefault("platform", PLATFORM)
    url = f"{BASE}/{endpoint}?{urllib.parse.urlencode(params)}"
    last = None
    for attempt in range(RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                body = r.read()
            try:
                return json.loads(body)
            except json.JSONDecodeError:
                raise ApiError(f"non-JSON body from {endpoint}: {body[:80]!r}")
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code} {endpoint}"
            if 400 <= e.code < 500 and e.code != 403 and e.code != 429:
                raise ApiError(last)                 # 잘못된 요청은 재시도 의미 없음
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = f"{type(e).__name__}: {e} ({endpoint})"
        if attempt < RETRIES:
            time.sleep(3 * (attempt + 1))
    raise ApiError(last or f"unknown error {endpoint}")


# ---------- 마스킹 ----------
def mask_name(name: str) -> str:
    """상대 선수 게이머태그: 첫/끝 글자만 남기고 * 로 치환. 2글자 이하면 첫 글자 + *."""
    s = str(name or "")
    if len(s) <= 1:
        return s
    if len(s) == 2:
        return s[0] + "*"
    return s[0] + "*" * (len(s) - 2) + s[-1]


def mask_opponents(match: dict) -> dict:
    players = match.get("players") or {}
    for club_id, plist in players.items():
        if str(club_id) == str(CLUB_ID):
            continue
        for p in (plist or {}).values():
            if isinstance(p, dict) and "playername" in p:
                p["playername"] = mask_name(p["playername"])
    return match


def crest_of(club: dict) -> dict:
    """clubs[id] (matches) 또는 clubs/info 항목에서 엠블럼 후보 id. TEAM(경기에서 실제 쓴 크레스트) > crestAssetId/teamId.
    규칙 출처: fc27-clubs-api docs/assets.md (selectedKitType 1 = 커스텀 크레스트, 0 = 실제 팀 배지)."""
    d = club.get("details") or club
    kit = d.get("customKit") or {}
    return {"team": _int_or_none(club.get("TEAM")), "teamId": _int_or_none(d.get("teamId")),
            "crestAssetId": _int_or_none(kit.get("crestAssetId")), "kitType": _int_or_none(kit.get("selectedKitType"))}


# ---------- 수집 ----------
def collect_matches(state: dict) -> tuple[dict[str, int], list[dict]]:
    """세 타입 조회 → 새 파일 저장. (타입별 새 경기 수, 새 경기 요약 리스트)"""
    new_counts: dict[str, int] = {}
    new_matches: list[dict] = []
    for mt in MATCH_TYPES:
        data = get_json("clubs/matches", clubIds=CLUB_ID, matchType=mt, maxResultCount=10)
        if not isinstance(data, list):
            raise ApiError(f"unexpected matches payload for {mt}: {type(data).__name__}")
        n = 0
        for m in data:
            mid = str(m.get("matchId", ""))
            if not mid or "clubs" not in m or "timestamp" not in m:
                log(f"  skip malformed match in {mt}: keys={list(m)[:5]}")
                continue
            path = RAW_DIR / f"{mid}.json"
            if path.exists():
                continue
            rec = {"v": 1, "matchType": mt, "collectedAt": now_iso(), "match": mask_opponents(m)}
            save_json(path, rec, compact=True)
            n += 1
            opp = next((c for cid, c in m["clubs"].items() if str(cid) != str(CLUB_ID)), None)
            ours = m["clubs"].get(str(CLUB_ID)) or {}
            new_matches.append({
                "id": mid, "type": mt, "ts": int(m["timestamp"]),
                "oppId": str(opp["details"]["clubId"]) if opp and opp.get("details") else None,
                "oppName": (opp.get("details") or {}).get("name") if opp else None,
                "oppCrest": crest_of(opp) if opp else None,
                "ourCrest": crest_of(ours) if ours else None,
            })
        new_counts[mt] = n
        log(f"  {mt}: {len(data)} returned, {n} new")
        time.sleep(1)
    return new_counts, new_matches


def fetch_club_summary(state: dict, changed: bool = False, our_crest: dict | None = None) -> dict:
    """overallStats(+ 현재 시즌 리더보드)로 시즌 누적 갱신. 실패해도 수집은 성공으로 본다.
    값이 하나라도 바뀌었을 때만 updatedAt 을 갱신한다 (그래야 변경 없는 실행에서 state.json 이 안 바뀜)."""
    prev = dict(state.get("club") or {})
    club = dict(prev)
    try:
        ov = get_json("clubs/overallStats", clubIds=CLUB_ID)
        row = ov[0] if isinstance(ov, list) and ov else None
        if row:
            club.update({
                "gp": int(row.get("gamesPlayed") or 0), "gpPlayoff": int(row.get("gamesPlayedPlayoff") or 0),
                "w": int(row.get("wins") or 0), "d": int(row.get("ties") or 0), "l": int(row.get("losses") or 0),
                "gf": int(row.get("goals") or 0), "ga": int(row.get("goalsAgainst") or 0),
                "sr": int(row.get("skillRating") or 0), "best": _int_or_none(row.get("bestDivision")),
                "wstreak": int(row.get("wstreak") or 0), "unbeaten": int(row.get("unbeatenstreak") or 0),
            })
    except ApiError as e:
        log(f"  overallStats failed (non-fatal): {e}")
    try:
        lb = get_json("currentSeasonLeaderboard/search", clubName=CLUB_NAME_HINT)
        row = next((x for x in lb if str(x.get("clubId")) == str(CLUB_ID)), None) if isinstance(lb, list) else None
        if row:
            club.update({
                "name": (row.get("clubInfo") or {}).get("name") or row.get("clubName") or club.get("name"),
                "div": _int_or_none(row.get("currentDivision")), "pts": int(row.get("points") or 0),
                "cs": int(row.get("cleanSheets") or 0), "team": (row.get("clubInfo") or {}).get("teamId"),
            })
            # 엠블럼: 리더보드 clubInfo 값을 기본으로, 비어 있는 항목은 최신 경기(TEAM 포함)·이전 값 순으로 채운다
            info_crest = crest_of({"details": row.get("clubInfo") or {}})
            fallback = {**(club.get("crest") or {}), **{k: v for k, v in (our_crest or {}).items() if v is not None}}
            club["crest"] = {k: (info_crest.get(k) if info_crest.get(k) is not None else fallback.get(k)) for k in ("team", "teamId", "crestAssetId", "kitType")}
            if club.get("best") is None:                       # overallStats 의 bestDivision 은 null 로 오는 경우가 있음 (실측 2026-10-09)
                club["best"] = _int_or_none(row.get("bestDivision"))
    except ApiError as e:
        log(f"  leaderboard search failed (non-fatal): {e}")
    if {k: v for k, v in club.items() if k != "updatedAt"} != {k: v for k, v in prev.items() if k != "updatedAt"} or changed:
        club["updatedAt"] = now_iso()
    club.pop("source", None)
    return club


def _int_or_none(v):
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def record_gap(state: dict, club: dict, new_counts: dict[str, int], gaps: list) -> None:
    """리그 gamesPlayed 증가분 > 새로 받은 리그 경기 수 → 그 차이만큼 영구 누락."""
    prev_gp = (state.get("club") or {}).get("gp")
    cur_gp = club.get("gp")
    if prev_gp is None or cur_gp is None:
        return
    delta = cur_gp - prev_gp
    missed = delta - new_counts.get("leagueMatch", 0)
    if missed > 0:
        gaps.append({"detectedAt": now_iso(), "missedLeagueMatches": missed,
                     "gamesPlayedBefore": prev_gp, "gamesPlayedAfter": cur_gp})
        log(f"  GAP: {missed} league match(es) missed (gp {prev_gp} → {cur_gp})")


def fetch_division(oid: str, name: str | None) -> int | None:
    """상대 클럽의 현재 디비전. overallStats 에는 현재 디비전이 없어 현재 시즌 리더보드를 클럽명으로 검색해 clubId 가 같은 행을 쓴다.
    리더보드에 없는 클럽(이름 불일치·미집계)은 None. 호출 실패는 ApiError 로 올린다."""
    if not name:
        return None
    lb = get_json("currentSeasonLeaderboard/search", clubName=name)
    row = next((x for x in lb if str(x.get("clubId")) == str(oid)), None) if isinstance(lb, list) else None
    return _int_or_none(row.get("currentDivision")) if row else None


def update_opponents(new_matches: list[dict], opponents: dict) -> None:
    """새 상대 클럽은 overallStats 1건씩 조회해 첫 관측 SR 저장. 엠블럼 정보는 경기 응답에서 바로 저장(추가 요청 없음).
    새 경기의 상대는 처음 보는 클럽이 아니어도 디비전을 다시 조회해 경기 시점 값에 가깝게 유지한다."""
    seen = set()
    for nm in new_matches:
        oid = nm.get("oppId")
        if not oid or oid in seen:
            continue
        seen.add(oid)
        if oid in opponents:
            if nm.get("oppCrest") and not opponents[oid].get("crest"):
                opponents[oid]["crest"] = nm["oppCrest"]              # 초기 임포트 상대: 다시 만나면 엠블럼 채움
            opponents[oid].setdefault("name", nm.get("oppName"))
        else:
            entry = {"name": nm.get("oppName"), "sr": None, "gp": None, "seenAt": now_iso(), "firstMatchId": nm["id"],
                     "crest": nm.get("oppCrest")}
            try:
                ov = get_json("clubs/overallStats", clubIds=oid)
                row = ov[0] if isinstance(ov, list) and ov else {}
                entry.update({"sr": _int_or_none(row.get("skillRating")), "gp": _int_or_none(row.get("gamesPlayed"))})
            except ApiError as e:
                log(f"  opponent {oid} SR failed (non-fatal): {e}")
                entry["error"] = str(e)[:80]
            opponents[oid] = entry
            time.sleep(1)
        try:
            div = fetch_division(oid, opponents[oid].get("name") or nm.get("oppName"))
            opponents[oid]["div"] = div
            opponents[oid]["divAt"] = now_iso()                       # 조회 시각 — 새 경기가 있을 때만 바뀌므로 무변경 실행에는 영향 없음
        except ApiError as e:
            log(f"  opponent {oid} division failed (non-fatal): {e}")
        time.sleep(1)


def backfill_divisions(opponents: dict, limit: int = DIV_BACKFILL_PER_RUN) -> int:
    """디비전을 한 번도 조회하지 않은 기존 상대(초기 임포트분)를 실행당 limit 개까지 리더보드 검색으로 채운다.
    조회했는데 리더보드에 없으면 div=None 에 divAt 만 남겨 다시 시도하지 않는다."""
    todo = [oid for oid, o in opponents.items() if "divAt" not in o and o.get("name")][:limit]
    done = 0
    for oid in todo:
        try:
            opponents[oid]["div"] = fetch_division(oid, opponents[oid].get("name"))
            opponents[oid]["divAt"] = now_iso()
            done += 1
        except ApiError as e:
            log(f"  division backfill {oid} failed (non-fatal): {e}")
        time.sleep(1)
    if todo:
        log(f"  division backfill: {done}/{len(todo)} (remaining {sum(1 for o in opponents.values() if 'divAt' not in o)})")
    return done


HISTORY_KEYS = ("gp", "sr", "div", "pts", "w", "d", "l")


def update_history(history: list, club: dict) -> bool:
    """우리 클럽 요약이 직전 기록과 다를 때만 한 줄 추가 → 변경 없는 실행에서는 파일이 그대로다."""
    if not club or club.get("gp") is None:
        return False
    row = {k: club.get(k) for k in HISTORY_KEYS}
    last = history[-1] if history else None
    if last and all(last.get(k) == row[k] for k in HISTORY_KEYS):
        return False
    history.append({"at": now_iso(), **row})
    return True


def backfill_crests(opponents: dict, limit: int = CREST_BACKFILL_PER_RUN) -> int:
    """엠블럼 정보가 없는 기존 상대(초기 임포트분)를 실행당 limit 개까지 clubs/info 로 채운다. 실패해도 무시."""
    todo = [oid for oid, o in opponents.items() if not o.get("crest")][:limit]
    done = 0
    for oid in todo:
        try:
            info = get_json("clubs/info", clubIds=oid)
            row = info.get(str(oid)) if isinstance(info, dict) else None
            if row:
                opponents[oid]["crest"] = crest_of({"details": row})
                opponents[oid].setdefault("name", row.get("name"))
                done += 1
        except ApiError as e:
            log(f"  clubs/info {oid} failed (non-fatal): {e}")
        time.sleep(1)
    if todo:
        log(f"  crest backfill: {done}/{len(todo)} (remaining {sum(1 for o in opponents.values() if not o.get('crest'))})")
    return done


MEMBER_FIELDS = {  # EA members/stats → 저장 키 (시즌 누적, EA 집계값 그대로)
    "gamesPlayed": "gp", "winRate": "winRate", "goals": "g", "assists": "a", "cleanSheetsDef": "csDef", "cleanSheetsGK": "csGk",
    "shotSuccessRate": "shotPct", "passesMade": "pm", "passSuccessRate": "passPct", "ratingAve": "rt",
    "tacklesMade": "tm", "tackleSuccessRate": "tacklePct", "manOfTheMatch": "mom", "redCards": "red",
    "proOverall": "ovr", "favoritePosition": "pos", "proName": "proName",
}


def fetch_members(prev: dict | None) -> dict | None:
    """members/stats → 선수별 시즌 누적(EA 집계). 값이 안 바뀌면 이전 내용을 그대로 돌려 파일 변경이 없게 한다."""
    try:
        data = get_json("members/stats", clubId=CLUB_ID)
    except ApiError as e:
        log(f"  members/stats failed (non-fatal): {e}")
        return prev
    members = []
    for m in (data.get("members") or []) if isinstance(data, dict) else []:
        row = {"name": m.get("name", "")}
        for src, dst in MEMBER_FIELDS.items():
            v = m.get(src)
            if dst in ("rt",):
                row[dst] = float(v) if v not in (None, "") else None
            elif dst in ("pos", "proName"):
                row[dst] = v or ""
            else:
                row[dst] = _int_or_none(v)
        members.append(row)
    members.sort(key=lambda r: r["name"].lower())
    if not members and prev:
        log("  members/stats returned no members; keeping previous file")
        return prev
    out = {"source": "members/stats", "members": members}
    return prev if prev and prev.get("members") == members else out


def write_summary(lines: list[str]) -> None:
    p = os.environ.get("GITHUB_STEP_SUMMARY")
    if p:
        with open(p, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")


def main() -> int:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    state = load_json(STATE_PATH, {"consecutiveFailures": 0})
    opponents = load_json(OPP_PATH, {})
    gaps = load_json(GAPS_PATH, [])
    # 주의: state.json 에는 실행 시각을 쓰지 않는다. 새 경기·시즌 누적·실패 상태가 바뀔 때만 파일이 바뀌어야
    # "변경 없으면 커밋 없음"이 성립한다. 마지막 수집 시각은 페이지가 GitHub Actions 실행 기록에서 읽는다.

    try:
        log("collecting matches")
        new_counts, new_matches = collect_matches(state)
    except ApiError as e:
        state["consecutiveFailures"] = int(state.get("consecutiveFailures") or 0) + 1
        state["lastError"] = {"at": now_iso(), "msg": str(e)[:200]}
        save_json(STATE_PATH, state)
        n = state["consecutiveFailures"]
        log(f"FAILED ({n} consecutive): {e}")
        write_summary([f"### collect: FAIL ({n} consecutive)", f"`{e}`"])
        if n >= FAIL_LIMIT:
            print(f"::error::collect failed {n} times in a row: {e}")
            return 1
        return 0

    our_crest = next((nm["ourCrest"] for nm in sorted(new_matches, key=lambda x: -x["ts"]) if nm.get("ourCrest")), None)
    club = fetch_club_summary(state, changed=bool(new_matches), our_crest=our_crest)
    record_gap(state, club, new_counts, gaps)
    update_opponents(new_matches, opponents)
    backfill_crests(opponents)
    backfill_divisions(opponents)
    history = load_json(HISTORY_PATH, [])
    history_changed = update_history(history, club)
    members = fetch_members(load_json(MEMBERS_PATH, None))

    total_new = sum(new_counts.values())
    state.update({"consecutiveFailures": 0, "club": club, "rawCount": len(list(RAW_DIR.glob("*.json")))})
    state.pop("lastError", None)
    save_json(STATE_PATH, state)
    save_json(OPP_PATH, opponents)
    save_json(GAPS_PATH, gaps)
    if history_changed:
        save_json(HISTORY_PATH, history)
    if members:
        save_json(MEMBERS_PATH, members)

    # 워크플로가 읽는 출력: 새 경기가 있었는지
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as f:
            f.write(f"new_matches={total_new}\n")
    log(f"done: {total_new} new match(es) {new_counts}, gp={club.get('gp')} sr={club.get('sr')}")
    write_summary([f"### collect: OK · new {total_new} {new_counts}",
                   f"season gp {club.get('gp')} · SR {club.get('sr')} · raw files {state['rawCount']}"])
    return 0


if __name__ == "__main__":
    sys.exit(main())

