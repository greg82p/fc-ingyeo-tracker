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
            new_matches.append({
                "id": mid, "type": mt, "ts": int(m["timestamp"]),
                "oppId": str(opp["details"]["clubId"]) if opp and opp.get("details") else None,
                "oppName": (opp.get("details") or {}).get("name") if opp else None,
            })
        new_counts[mt] = n
        log(f"  {mt}: {len(data)} returned, {n} new")
        time.sleep(1)
    return new_counts, new_matches


def fetch_club_summary(state: dict, changed: bool = False) -> dict:
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


def update_opponents(new_matches: list[dict], opponents: dict) -> None:
    """새 상대 클럽만 overallStats 1건씩 조회해 첫 관측 SR 저장."""
    seen = set()
    for nm in new_matches:
        oid = nm.get("oppId")
        if not oid or oid in opponents or oid in seen:
            continue
        seen.add(oid)
        try:
            ov = get_json("clubs/overallStats", clubIds=oid)
            row = ov[0] if isinstance(ov, list) and ov else {}
            opponents[oid] = {
                "name": nm.get("oppName"), "sr": _int_or_none(row.get("skillRating")),
                "gp": _int_or_none(row.get("gamesPlayed")), "seenAt": now_iso(), "firstMatchId": nm["id"],
            }
        except ApiError as e:
            log(f"  opponent {oid} SR failed (non-fatal): {e}")
            opponents[oid] = {"name": nm.get("oppName"), "sr": None, "gp": None, "seenAt": now_iso(),
                              "firstMatchId": nm["id"], "error": str(e)[:80]}
        time.sleep(1)


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

    club = fetch_club_summary(state, changed=bool(new_matches))
    record_gap(state, club, new_counts, gaps)
    update_opponents(new_matches, opponents)

    total_new = sum(new_counts.values())
    state.update({"consecutiveFailures": 0, "club": club, "rawCount": len(list(RAW_DIR.glob("*.json")))})
    state.pop("lastError", None)
    save_json(STATE_PATH, state)
    save_json(OPP_PATH, opponents)
    save_json(GAPS_PATH, gaps)

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
