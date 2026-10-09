#!/usr/bin/env python3
"""초기 수동 수집 스냅샷(data.json ~ data9.json, 2026-10-02~10-08) → raw/legacy/{matchId}.json

스냅샷은 EA 원본이 아니라 페이지용으로 축약된 형태라 raw/matches/ 와 스키마가 다르다.
그래서 별도 폴더(raw/legacy/)에 "정규화 포맷"으로 저장하고 build.py 가 두 포맷을 모두 읽는다.
같은 경기가 여러 스냅샷에 있으면 이벤트 수가 많은(나중) 스냅샷을 쓴다.

사용:  python tools/legacy_import.py tools/snapshots/data*.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "raw" / "legacy"
OPP_PATH = ROOT / "meta" / "opponents.json"

F = ["sec", "rt", "mom", "g", "a", "sh", "pm", "pa", "tm", "ta", "red"]
# 스냅샷 버전별 이벤트 ID 목록 (앞부분이 서로 접두어 관계)
EV_FULL = [217, 218, 13, 18, 153, 30, 32, 34, 24, 26, 28, 143, 152, 115, 174, 112, 0, 1, 6, 158, 108, 109, 110, 105,
           106, 107, 2, 4, 95, 213, 265, 266, 219, 111, 177, 182, 31, 33, 35, 25, 27, 29, 104, 214, 11, 3, 36, 37, 157,
           145, 14, 19, 38, 97, 164, 163, 156, 147, 144, 229, 230]
POS = {"f": "forward", "m": "midfielder", "d": "defender", "g": "goalkeeper"}
TEAM_KEYS = ["sh", "pm", "pa", "tm", "ta", "red", "yc", "off"]


def team(a: list) -> dict:
    # 초기 스냅샷은 6필드(yc·off 없음) → 없는 값은 null (0 아님)
    return {k: (a[i] if i < len(a) else None) for i, k in enumerate(TEAM_KEYS)}


def convert(row: list, src: str, fetched_at: str) -> dict:
    mid, ts, opp, opp_id, gf, ga, code, dnf_flag, agg, oagg, opp_n, prow = row
    ev_n = len(prow[0]) - 2 - len(F) if prow else len(EV_FULL)
    ev_ids = EV_FULL[:ev_n]
    players = []
    for r in prow:
        p = {"name": r[0], "pos": POS.get(r[1], r[1])}
        for i, k in enumerate(F):
            p[k] = r[2 + i]
        p["ev"] = {str(e): r[2 + len(F) + i] for i, e in enumerate(ev_ids) if r[2 + len(F) + i]}
        players.append(p)
    return {
        "v": "legacy", "matchType": "leagueMatch", "source": src, "snapshotAt": fetched_at, "evIds": ev_ids,
        "match": {"id": str(mid), "ts": int(ts), "opp": opp, "oppId": str(opp_id), "gf": int(gf), "ga": int(ga),
                  "code": int(code), "dnfFlag": int(dnf_flag), "oppN": int(opp_n),
                  "us": team(agg), "them": team(oagg), "players": players},
    }


def main(paths: list[str]) -> int:
    best: dict[str, tuple[int, dict]] = {}
    opp_sr: dict[str, dict] = {}
    for path in sorted(paths):
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        for row in d["M"]:
            rec = convert(row, Path(path).name, d.get("fetchedAt", ""))
            n_ev = len(rec["evIds"])
            if rec["match"]["id"] not in best or n_ev >= best[rec["match"]["id"]][0]:
                best[rec["match"]["id"]] = (n_ev, rec)
        for oid, v in (d.get("oppSR") or {}).items():
            opp_sr.setdefault(str(oid), {"sr": v[0], "gp": v[1], "seenAt": d.get("oppSRAt") or d.get("fetchedAt")})
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for mid, (_, rec) in best.items():
        (OUT_DIR / f"{mid}.json").write_text(json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    # 상대 SR 시드: 이미 있는 항목은 건드리지 않는다
    opponents = json.loads(OPP_PATH.read_text(encoding="utf-8")) if OPP_PATH.exists() else {}
    names = {rec["match"]["oppId"]: rec["match"]["opp"] for _, rec in best.values()}
    for oid, v in opp_sr.items():
        opponents.setdefault(oid, {"name": names.get(oid), "sr": v["sr"], "gp": v["gp"], "seenAt": v["seenAt"],
                                   "firstMatchId": None, "source": "legacy"})
    OPP_PATH.parent.mkdir(parents=True, exist_ok=True)
    OPP_PATH.write_text(json.dumps(opponents, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    # 시즌 누적 시드: state.json 에 club 이 없으면 마지막 스냅샷 값으로 채운다 (collect.py 첫 실행 때 덮어씀)
    state_path = ROOT / "meta" / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {"consecutiveFailures": 0}
    if "club" not in state:
        last = json.loads(Path(sorted(paths)[-1]).read_text(encoding="utf-8"))
        c = last.get("club") or {}
        state["club"] = {"name": c.get("name"), "div": c.get("div"), "best": c.get("best"), "gp": c.get("gp"),
                         "w": c.get("w"), "d": c.get("d"), "l": c.get("l"), "gf": c.get("gf"), "ga": c.get("ga"),
                         "pts": c.get("pts"), "sr": c.get("sr"), "cs": c.get("cs"), "team": c.get("team"),
                         "updatedAt": last.get("fetchedAt"), "source": "legacy"}
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    ev_hist: dict[int, int] = {}
    for n, _ in best.values():
        ev_hist[n] = ev_hist.get(n, 0) + 1
    print(f"legacy matches written: {len(best)}  (events per match: {dict(sorted(ev_hist.items()))})  opponents seeded: {len(opp_sr)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
