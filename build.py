#!/usr/bin/env python3
"""FC INGYEO 빌드 — raw/ 전체를 읽어 docs/data/ 를 통째로 다시 만든다.

지표 계산은 이 파일 한 곳에만 있다 (설계 문서 §7 지표 v2).
입력:  raw/matches/*.json (EA 원본, collect.py)  +  raw/legacy/*.json (초기 스냅샷, tools/legacy_import.py)
       meta/state.json, meta/opponents.json, meta/gaps.json
출력:  docs/data/meta.json, sessions.json, sessions/{sessionId}.json, matches.json, players.json

표준 라이브러리만 사용한다.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

CLUB_ID = os.environ.get("CLUB_ID", "829628")
ROOT = Path(__file__).resolve().parent
RAW_DIR = ROOT / "raw" / "matches"
LEGACY_DIR = ROOT / "raw" / "legacy"
META_DIR = ROOT / "meta"
OUT_DIR = ROOT / "docs" / "data"

METRICS_VERSION = 2
SESSION_GAP_SEC = 3600          # 같은 세션으로 묶는 최대 간격
MIN_APP_SEC = 600               # "출전"으로 치는 최소 경기 시간(초)
LAST_N_SESSIONS = 5
RES = {1: "W", 2: "L", 4: "D"}
TYPE = {"leagueMatch": "league", "playoffMatch": "playoff", "friendlyMatch": "friendly"}
F = ["sec", "rt", "mom", "g", "a", "sh", "pm", "pa", "tm", "ta", "red"]
EV_FULL = [217, 218, 13, 18, 153, 30, 32, 34, 24, 26, 28, 143, 152, 115, 174, 112, 0, 1, 6, 158, 108, 109, 110, 105,
           106, 107, 2, 4, 95, 213, 265, 266, 219, 111, 177, 182, 31, 33, 35, 25, 27, 29, 104, 214, 11, 3, 36, 37, 157,
           145, 14, 19, 38, 97, 164, 163, 156, 147, 144, 229, 230]
SUM_KEYS = ["g", "a", "sh", "pm", "pa", "pc", "fwd", "tm", "ta", "red"] + [f"e{e}" for e in EV_FULL]
# 초기 스냅샷은 이름만 있어서 알려진 playerId 로 연결한다. 모르는 이름은 "name:<이름>" 을 ID 로 쓴다.
NAME_TO_ID = {"lanil": "172777124", "Aparensis": "226084863", "hoyeol1107": "1004841999062", "raoshuant": "1005905331121"}


def load_json(path: Path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def dump(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")


def i(v, default=0):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def f(v, default=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


# ---------- 정규화: 두 원본 포맷 → 공통 Match ----------
def parse_events(p: dict) -> dict[str, int]:
    ev: dict[str, int] = {}
    for k in ("match_event_aggregate_0", "match_event_aggregate_1", "match_event_aggregate_2", "match_event_aggregate_3"):
        s = p.get(k) or ""
        if not isinstance(s, str):
            continue
        for part in s.split(","):
            if ":" in part:
                a, b = part.split(":", 1)
                ev[a.strip()] = ev.get(a.strip(), 0) + i(b)
    return ev


def player_from_ea(pid: str, p: dict) -> dict:
    return {"id": str(pid), "name": p.get("playername", ""), "pos": p.get("pos", ""),
            "sec": i(p.get("secondsPlayed")), "rt": f(p.get("rating")), "mom": i(p.get("mom")),
            "g": i(p.get("goals")), "a": i(p.get("assists")), "sh": i(p.get("shots")),
            "pm": i(p.get("passesmade")), "pa": i(p.get("passattempts")),
            "tm": i(p.get("tacklesmade")), "ta": i(p.get("tackleattempts")), "red": i(p.get("redcards")),
            "ev": parse_events(p)}


def team_from_ea(agg: dict, players: list[dict]) -> dict:
    return {"sh": i(agg.get("shots")), "pm": i(agg.get("passesmade")), "pa": i(agg.get("passattempts")),
            "tm": i(agg.get("tacklesmade")), "ta": i(agg.get("tackleattempts")), "red": i(agg.get("redcards")),
            "yc": sum(p["ev"].get("95", 0) + p["ev"].get("213", 0) for p in players),
            "off": sum(p["ev"].get("153", 0) for p in players)}


def normalize_ea(rec: dict) -> dict:
    m = rec["match"]
    clubs = m["clubs"]
    ours = clubs.get(str(CLUB_ID)) or next((c for cid, c in clubs.items() if str(cid) == str(CLUB_ID)), None)
    opp_id = next((str(cid) for cid in clubs if str(cid) != str(CLUB_ID)), None)
    opp = clubs.get(opp_id) or {}
    pl = m.get("players") or {}
    our_players = [player_from_ea(pid, p) for pid, p in (pl.get(str(CLUB_ID)) or {}).items()]
    opp_players = [player_from_ea(pid, p) for pid, p in (pl.get(opp_id) or {}).items()] if opp_id else []
    agg = m.get("aggregate") or {}
    return {
        "id": str(m["matchId"]), "ts": i(m["timestamp"]), "type": TYPE.get(rec.get("matchType"), "league"),
        "opp": (opp.get("details") or {}).get("name") or "?", "oppId": opp_id,
        "gf": i(ours.get("goals")), "ga": i(ours.get("goalsAgainst")), "code": i(ours.get("result")),
        "dnfFlag": i(ours.get("winnerByDnf")),
        "oppN": sum(1 for p in opp_players if p["sec"] >= MIN_APP_SEC),
        "us": team_from_ea(agg.get(str(CLUB_ID)) or {}, our_players),
        "them": team_from_ea(agg.get(opp_id) or {}, opp_players),
        "evIds": EV_FULL, "imported": False, "players": our_players,
    }


def normalize_legacy(rec: dict) -> dict:
    m = rec["match"]
    players = []
    for p in m["players"]:
        q = dict(p)
        q["id"] = NAME_TO_ID.get(p["name"], f"name:{p['name']}")
        q["ev"] = {str(k): v for k, v in (p.get("ev") or {}).items()}
        players.append(q)
    return {"id": str(m["id"]), "ts": i(m["ts"]), "type": TYPE.get(rec.get("matchType"), "league"),
            "opp": m["opp"], "oppId": str(m["oppId"]), "gf": i(m["gf"]), "ga": i(m["ga"]), "code": i(m["code"]),
            "dnfFlag": i(m.get("dnfFlag")), "oppN": i(m.get("oppN")), "us": m["us"], "them": m["them"],
            "evIds": rec["evIds"], "imported": True, "players": players}


# ---------- 지표 v2 (페이지 JS 와 1:1) ----------
def enrich(m: dict, opponents: dict) -> dict:
    ev_set = {str(e) for e in m["evIds"]}
    for p in m["players"]:
        e153 = p["ev"].get("153", 0)
        p["pc"] = max(0, p["pm"] - e153)                     # 성공 패스 = EA 집계 − 오프사이드 패스
        p["fwd"] = max(0, p["ev"].get("30", 0) - e153)       # 전진 성공 = event_30 − 오프사이드(항상 전진)
        p["played"] = p["sec"] >= MIN_APP_SEC
    for side in ("us", "them"):
        t = m[side]
        t["pc"] = t["pm"] if t.get("off") is None else max(0, t["pm"] - t["off"])
    m["res"] = RES.get(m["code"]) or ("W" if m["gf"] > m["ga"] else "L" if m["gf"] < m["ga"] else "D")
    max_sec = max((p["sec"] for p in m["players"]), default=0)
    m["zeroEvt"] = max_sec < MIN_APP_SEC
    m["dnf"] = (m["code"] not in RES) or m["dnfFlag"] == 1 or m["zeroEvt"]
    m["nPlayed"] = sum(1 for p in m["players"] if p["played"])
    o = opponents.get(str(m["oppId"])) or {}
    m["oppSR"] = {"sr": o.get("sr"), "gp": o.get("gp")} if o.get("sr") is not None else None
    m["evN"] = len(m["evIds"])
    m["legacy"] = len(m["evIds"]) < len(EV_FULL)                 # 일부 이벤트만 기록된 경기(초기 스냅샷 20경기)
    m["evMissing"] = sorted(int(e) for e in EV_FULL if str(e) not in ev_set)   # 이 경기에 기록되지 않은 이벤트
    return m


def group_sessions(matches: list[dict]) -> list[dict]:
    asc = sorted(matches, key=lambda m: m["ts"])
    groups: list[list[dict]] = []
    for m in asc:
        if groups and m["ts"] - groups[-1][-1]["ts"] <= SESSION_GAP_SEC:
            groups[-1].append(m)
        else:
            groups.append([m])
    out = []
    for g in groups:
        ms = sorted(g, key=lambda m: -m["ts"])
        sid = g[0]["id"]
        for m in ms:
            m["sid"] = sid
        types = {"league": 0, "playoff": 0, "friendly": 0}
        for m in ms:
            types[m["type"]] = types.get(m["type"], 0) + 1
        out.append({"id": sid, "start": g[0]["ts"], "end": g[-1]["ts"], "n": len(ms),
                    "w": sum(m["res"] == "W" for m in ms), "d": sum(m["res"] == "D" for m in ms), "l": sum(m["res"] == "L" for m in ms),
                    "gf": sum(m["gf"] for m in ms), "ga": sum(m["ga"] for m in ms), "dnfN": sum(m["dnf"] for m in ms),
                    "legacy": any(m["legacy"] for m in ms), "types": types,
                    "form": [m["res"] for m in ms], "matches": ms})
    return sorted(out, key=lambda s: -s["start"])


def aggregate_players(matches: list[dict]) -> dict:
    """선수별 합계. 미기록(null) 항목은 합계에서 빼고 건수·출전시간만 센다 — 페이지 JS 와 같은 규칙."""
    A: dict[str, dict] = {}
    for m in matches:
        ev_set = {str(e) for e in m["evIds"]}
        for p in m["players"]:
            a = A.setdefault(p["id"], {"id": p["id"], "name": p["name"], "pos": p["pos"], "listed": 0, "appsN": 0,
                                       "secT": 0, "rtSum": 0.0, "rtN": 0, "mom": 0, "miss": {}, "missSec": {},
                                       **{k: 0 for k in SUM_KEYS}})
            a["name"] = p["name"]; a["pos"] = p["pos"]           # 최신 이름·포지션
            a["listed"] += 1; a["secT"] += p["sec"]; a["mom"] += p["mom"]
            if p["played"]:
                a["appsN"] += 1
                if p["rt"] is not None:
                    a["rtSum"] += p["rt"]; a["rtN"] += 1
            for k in ("g", "a", "sh", "pm", "pa", "pc", "fwd", "tm", "ta", "red"):
                a[k] += p[k]
            for e in EV_FULL:
                k = f"e{e}"
                if str(e) in ev_set:
                    a[k] += p["ev"].get(str(e), 0)
                else:
                    a["miss"][k] = a["miss"].get(k, 0) + 1
                    a["missSec"][k] = a["missSec"].get(k, 0) + p["sec"]
    return A


def match_public(m: dict) -> dict:
    """세션 파일에 넣는 경기 객체 (내부 전용 필드 제거)."""
    out = {k: v for k, v in m.items() if k not in ("evIds", "evMissing")}
    out["evMissing"] = m["evMissing"] if m["legacy"] else []
    out["players"] = [{k: v for k, v in p.items() if k != "played"} for p in m["players"]]
    return out


def main() -> int:
    state = load_json(META_DIR / "state.json", {})
    opponents = load_json(META_DIR / "opponents.json", {})
    gaps = load_json(META_DIR / "gaps.json", [])

    by_id: dict[str, dict] = {}
    for path in sorted(LEGACY_DIR.glob("*.json")):
        rec = load_json(path, None)
        if rec:
            m = normalize_legacy(rec); by_id[m["id"]] = m
    n_legacy = len(by_id)
    for path in sorted(RAW_DIR.glob("*.json")):
        rec = load_json(path, None)
        if rec:
            m = normalize_ea(rec); by_id[m["id"]] = m           # 같은 경기면 EA 원본이 우선
    matches = [enrich(m, opponents) for m in by_id.values()]
    if not matches:
        print("no matches; nothing to build"); return 0
    sessions = group_sessions(matches)
    matches_desc = sorted(matches, key=lambda m: -m["ts"])

    # 출력 디렉터리 통째로 재생성
    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)
    (OUT_DIR / "sessions").mkdir(parents=True)

    for s in sessions:
        dump(OUT_DIR / "sessions" / f"{s['id']}.json", {"id": s["id"], "matches": [match_public(m) for m in s["matches"]]})
    dump(OUT_DIR / "sessions.json", [{k: v for k, v in s.items() if k != "matches"} for s in sessions])
    dump(OUT_DIR / "matches.json", [[m["id"], m["ts"], m["type"], m["opp"], m["oppId"], m["gf"], m["ga"], m["res"],
                                     1 if m["dnf"] else 0, m["sid"]] for m in matches_desc])
    last5 = [m for s in sessions[:LAST_N_SESSIONS] for m in s["matches"]]
    dump(OUT_DIR / "players.json", {
        "all": {"n": len(matches), "from": matches_desc[-1]["ts"], "to": matches_desc[0]["ts"], "players": aggregate_players(matches)},
        "last5": {"n": len(last5), "sessions": min(LAST_N_SESSIONS, len(sessions)), "players": aggregate_players(last5)},
    })
    # meta.json 에는 시각을 넣지 않는다 — 데이터가 같으면 바이트까지 같아야 "변경 없으면 커밋 없음"이 성립한다.
    # dataHash 는 다른 산출물 내용의 해시: 페이지가 캐시 버스팅(?v=)에 쓴다.
    h = hashlib.sha1()
    for path in sorted(OUT_DIR.rglob("*.json")):
        h.update(path.relative_to(OUT_DIR).as_posix().encode()); h.update(path.read_bytes())
    dump(OUT_DIR / "meta.json", {
        "dataHash": h.hexdigest()[:12], "metricsVersion": METRICS_VERSION, "clubId": CLUB_ID,
        "club": state.get("club") or {},
        "consecutiveFailures": state.get("consecutiveFailures", 0), "lastError": state.get("lastError"),
        "counts": {"matches": len(matches), "sessions": len(sessions), "imported": n_legacy, "raw": len(matches) - n_legacy,
                   "legacy": sum(1 for m in matches if m["legacy"])},
        "range": {"first": matches_desc[-1]["ts"], "last": matches_desc[0]["ts"]},
        "gaps": gaps, "sessionGapSec": SESSION_GAP_SEC, "minAppSec": MIN_APP_SEC, "evFull": EV_FULL,
        "opponentsAt": max((o.get("seenAt") or "" for o in opponents.values()), default=None),
    })
    size = sum(p.stat().st_size for p in OUT_DIR.rglob("*.json"))
    n_partial = sum(1 for m in matches if m["legacy"])
    print(f"built {len(matches)} matches ({n_legacy} imported, {n_partial} partial-event) → {len(sessions)} sessions · docs/data {size/1024:.0f} KB · metrics v{METRICS_VERSION}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
