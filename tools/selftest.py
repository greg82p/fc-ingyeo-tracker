#!/usr/bin/env python3
"""collect.py 를 가짜 EA 응답으로 돌려 보는 자체 점검 (네트워크 없음).

임시 디렉터리에 raw/meta 를 만들고 ① 첫 실행 ② 같은 응답 재실행(중복 0) ③ gamesPlayed 가 새 경기보다 더 늘어난 실행(누락 기록)
④ 403 연속 실패 를 확인한 뒤, 그 raw 로 build.py 를 돌려 정규화가 깨지지 않는지 본다.

사용:  python tools/selftest.py
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def fake_match(mid: str, ts: int, our: str, opp: str, our_goals=2, opp_goals=1, result="1"):
    def player(pid, name, sec=5400, ev="215:20,153:1,30:8,32:6,34:5,217:2,218:1,13:1,14:1,36:1,37:2,229:2,230:1,95:1"):
        return {"playername": name, "pos": "midfielder", "secondsPlayed": str(sec), "rating": "7.30", "mom": "0",
                "goals": "1", "assists": "0", "shots": "3", "passesmade": "21", "passattempts": "26",
                "tacklesmade": "3", "tackleattempts": "5", "redcards": "0",
                "match_event_aggregate_0": ev, "match_event_aggregate_1": "", "match_event_aggregate_2": "", "match_event_aggregate_3": ""}
    agg = lambda: {"shots": 6, "passesmade": 42, "passattempts": 52, "tacklesmade": 6, "tackleattempts": 10, "redcards": 0}
    return {
        "matchId": mid, "timestamp": ts, "timeAgo": {"number": 1, "unit": "hours"},
        "clubs": {
            our: {"goals": str(our_goals), "goalsAgainst": str(opp_goals), "result": result, "winnerByDnf": "0", "TEAM": "2055",
                  "details": {"name": "FC INGYEO", "clubId": int(our), "teamId": 2055, "customKit": {"crestAssetId": "99160104", "selectedKitType": "0"}}},
            opp: {"goals": str(opp_goals), "goalsAgainst": str(our_goals), "result": "2", "winnerByDnf": "0", "TEAM": "99161026",
                  "details": {"name": "Opponent United", "clubId": int(opp), "teamId": 241, "customKit": {"crestAssetId": "99161026", "selectedKitType": "1"}}},
        },
        "players": {
            our: {"172777124": player("172777124", "lanil"), "226084863": player("226084863", "Aparensis")},
            opp: {"900001": player("900001", "SomeGamer"), "900002": player("900002", "Jo", sec=300), "900003": player("900003", "X")},
        },
        "aggregate": {our: agg(), opp: agg()},
    }


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="fcingyeo-selftest-"))
    try:
        shutil.copy(ROOT / "collect.py", tmp / "collect.py")
        shutil.copy(ROOT / "build.py", tmp / "build.py")
        collect = load_module("collect_t", tmp / "collect.py")
        build = load_module("build_t", tmp / "build.py")
        our = collect.CLUB_ID; opp = "555555"
        base_ts = 1_791_400_000
        matches = {"leagueMatch": [fake_match("1001", base_ts, our, opp), fake_match("1002", base_ts + 900, our, "555556")],
                   "playoffMatch": [fake_match("2001", base_ts + 1800, our, opp)], "friendlyMatch": []}
        overall = {"gamesPlayed": "71", "gamesPlayedPlayoff": "3", "wins": "22", "ties": "14", "losses": "35", "goals": "99",
                   "goalsAgainst": "117", "skillRating": "1373", "bestDivision": "4", "wstreak": "0", "unbeatenstreak": "1"}
        calls = []

        def fake_get(endpoint, **params):
            calls.append(endpoint)
            if fake_get.fail_403:
                raise collect.ApiError("HTTP 403 " + endpoint)
            if endpoint == "clubs/matches":
                return matches[params["matchType"]]
            if endpoint == "clubs/overallStats":
                if params["clubIds"] == our:
                    return [dict(overall)]
                return [{"skillRating": "1450", "gamesPlayed": "40"}]
            if endpoint == "members/stats":
                return {"members": [
                    {"name": "lanil", "gamesPlayed": "83", "winRate": "31", "goals": "55", "assists": "12", "cleanSheetsDef": "12", "cleanSheetsGK": "0",
                     "shotSuccessRate": "41", "passesMade": "1300", "passSuccessRate": "72", "ratingAve": "7.3", "tacklesMade": "60", "tackleSuccessRate": "30",
                     "manOfTheMatch": "9", "redCards": "1", "proOverall": "85", "favoritePosition": "forward", "proName": ""},
                    {"name": "nobody", "gamesPlayed": "0", "winRate": "0", "goals": "0", "assists": "0", "cleanSheetsDef": "0", "cleanSheetsGK": "0",
                     "shotSuccessRate": "0", "passesMade": "0", "passSuccessRate": "0", "ratingAve": "", "tacklesMade": "0", "tackleSuccessRate": "0",
                     "manOfTheMatch": "0", "redCards": "0", "proOverall": "", "favoritePosition": "", "proName": ""}], "positionCount": {}}
            if endpoint == "clubs/info":
                return {params["clubIds"]: {"name": "Legacy Club", "clubId": int(params["clubIds"]), "teamId": 5, "customKit": {"crestAssetId": "99000001", "selectedKitType": "0"}}}
            if endpoint == "currentSeasonLeaderboard/search":
                if params.get("clubName") == "Opponent United":                      # 상대 디비전 조회 (clubId 로 매칭)
                    return [{"clubId": "999", "currentDivision": "1"}, {"clubId": opp, "currentDivision": "3"}]
                if params.get("clubName") not in (collect.CLUB_NAME_HINT,):
                    return []                                                        # 리더보드에 없는 클럽
                return [{"clubId": our, "currentDivision": "5", "points": "47", "cleanSheets": "28", "clubInfo": {"name": "FC INGYEO", "teamId": 2055, "customKit": {"crestAssetId": "99160104", "selectedKitType": "0"}}}]
            raise AssertionError(endpoint)
        fake_get.fail_403 = False
        collect.get_json = fake_get

        # ① 첫 실행
        assert collect.main() == 0
        raw = sorted(p.name for p in (tmp / "raw" / "matches").glob("*.json"))
        assert raw == ["1001.json", "1002.json", "2001.json"], raw
        rec = json.loads((tmp / "raw" / "matches" / "1001.json").read_text())
        names = [p["playername"] for p in rec["match"]["players"][opp].values()]
        assert names == ["S*******r", "J*", "X"], names                       # 마스킹: 첫/끝 유지, 2글자는 첫+*, 1글자 그대로
        ours = [p["playername"] for p in rec["match"]["players"][our].values()]
        assert ours == ["lanil", "Aparensis"], ours                          # 우리 선수는 그대로
        assert rec["matchType"] == "leagueMatch" and rec["v"] == 1
        state = json.loads((tmp / "meta" / "state.json").read_text())
        assert state["club"]["gp"] == 71 and state["club"]["div"] == 5 and state["club"]["pts"] == 47, state["club"]
        assert state["rawCount"] == 3 and "lastRunAt" not in state and "lastSuccessAt" not in state, state
        state_bytes_1 = (tmp / "meta" / "state.json").read_bytes()
        # 초기 임포트 상대(엠블럼 정보 없음)를 미리 하나 심어 백필을 확인
        opps_path = tmp / "meta" / "opponents.json"
        opps = json.loads(opps_path.read_text())
        assert opps[opp]["crest"] == {"team": 99161026, "teamId": 241, "crestAssetId": 99161026, "kitType": 1}, opps[opp]
        opps["777777"] = {"name": "Legacy Club", "sr": 1500, "gp": 10, "seenAt": "2026-10-08", "firstMatchId": None, "source": "legacy"}
        opps_path.write_text(json.dumps(opps))
        mem = json.loads((tmp / "meta" / "members.json").read_text())
        assert mem["members"][0]["name"] == "lanil" and mem["members"][0]["red"] == 1 and mem["members"][1]["rt"] is None, mem
        assert state["club"]["crest"]["team"] == 2055 and state["club"]["crest"]["crestAssetId"] == 99160104, state["club"]
        mem_bytes_1 = (tmp / "meta" / "members.json").read_bytes()
        assert set(opps) == {opp, "555556", "777777"} and opps[opp]["sr"] == 1450, opps
        print("① first run OK:", raw, "masked:", names, "crest+members stored")

        # ② 같은 응답으로 재실행 → 새 파일 0, 상대 조회 없음
        calls.clear(); assert collect.main() == 0
        state = json.loads((tmp / "meta" / "state.json").read_text())
        assert calls.count("clubs/overallStats") == 1, calls
        assert json.loads((tmp / "meta" / "gaps.json").read_text()) == []
        # 변경 없는 실행: state.json 이 바이트 단위로 동일해야 커밋이 생기지 않는다
        assert (tmp / "meta" / "state.json").read_bytes() == state_bytes_1, "state.json changed on a no-op run"
        assert (tmp / "meta" / "members.json").read_bytes() == mem_bytes_1, "members.json changed on a no-op run"
        opps = json.loads(opps_path.read_text())
        assert opps["777777"]["crest"] == {"team": None, "teamId": 5, "crestAssetId": 99000001, "kitType": 0}, opps["777777"]   # clubs/info 백필
        assert opps[opp]["div"] == 3 and "divAt" in opps[opp], opps[opp]                       # 리더보드에서 clubId 일치 행의 디비전
        assert opps["555556"]["div"] is None and "divAt" in opps["555556"], opps["555556"]    # 리더보드에 없음 → None, 재시도 안 함
        hist_path = tmp / "meta" / "club_history.json"
        hist = json.loads(hist_path.read_text())
        assert len(hist) == 1 and hist[0]["gp"] == 71 and hist[0]["sr"] == 1373 and hist[0]["div"] == 5 and hist[0]["pts"] == 47, hist
        hist_bytes_1 = hist_path.read_bytes()
        # ②-b 한 번 더: 백필까지 끝난 뒤의 진짜 무변경 실행 → meta/ 전부 바이트 동일, 리더보드 검색은 우리 클럽 1회뿐
        meta_bytes = {q.name: q.read_bytes() for q in (tmp / "meta").glob("*.json")}
        calls.clear(); assert collect.main() == 0
        assert {q.name: q.read_bytes() for q in (tmp / "meta").glob("*.json")} == meta_bytes, "meta/ changed on a no-op run"
        assert calls.count("currentSeasonLeaderboard/search") == 1 and calls.count("clubs/info") == 0, calls
        print("② rerun OK: 0 new, no duplicate opponent lookups, meta/ unchanged on no-op, history 1 row")

        # ③ 리그 gamesPlayed 가 74 로 늘었는데 새 리그 경기는 1건 → 누락 2건 기록
        overall["gamesPlayed"] = "74"
        matches["leagueMatch"] = [fake_match("1003", base_ts + 7200, our, opp)]
        assert collect.main() == 0
        gaps = json.loads((tmp / "meta" / "gaps.json").read_text())
        assert len(gaps) == 1 and gaps[0]["missedLeagueMatches"] == 2, gaps
        hist = json.loads(hist_path.read_text())
        assert len(hist) == 2 and hist[1]["gp"] == 74 and hist_path.read_bytes() != hist_bytes_1, hist   # 값이 바뀐 실행에서만 한 줄 추가
        print("③ gap detection OK:", gaps[0], "· history 2 rows")

        # ④ 403 연속 3회 → 세 번째에 종료코드 1
        fake_get.fail_403 = True
        codes = [collect.main() for _ in range(3)]
        assert codes == [0, 0, 1], codes
        state = json.loads((tmp / "meta" / "state.json").read_text())
        assert state["consecutiveFailures"] == 3 and state["lastError"]["msg"].startswith("HTTP 403")
        print("④ failure handling OK: exit codes", codes)

        # ⑤ 그 raw 로 build
        fake_get.fail_403 = False
        (tmp / "raw" / "legacy").mkdir(exist_ok=True)
        assert build.main() == 0
        sess = json.loads((tmp / "docs" / "data" / "sessions.json").read_text())
        # 1003 은 2시간 뒤라 별도 세션 → 세션 2개 (60분 규칙 확인)
        assert len(sess) == 2 and sess[1]["id"] == "1001" and sess[1]["n"] == 3 and sess[1]["types"] == {"league": 2, "playoff": 1, "friendly": 0}, sess
        sfile = json.loads((tmp / "docs" / "data" / "sessions" / "1001.json").read_text())
        m = sfile["matches"][0]
        assert m["oppN"] == 2 and m["us"]["yc"] == 2 and m["us"]["off"] == 2 and m["us"]["pc"] == 40, (m["oppN"], m["us"])
        assert m["crest"] == [99161026, 241], m["crest"]                     # TEAM 우선, 커스텀 크레스트 → teamId
        rows = json.loads((tmp / "docs" / "data" / "matches.json").read_text()); assert rows[0][10] == [99161026, 241], rows[0]
        p = m["players"][0]
        assert p["pc"] == 20 and p["fwd"] == 7 and p["ev"]["215"] == 20 and p["id"] == "172777124", p
        players = json.loads((tmp / "docs" / "data" / "players.json").read_text())["all"]["players"]
        assert players["172777124"]["listed"] == 4 and players["172777124"]["miss"] == {}, players["172777124"]
        meta = json.loads((tmp / "docs" / "data" / "meta.json").read_text())
        assert meta["counts"] == {"matches": 4, "sessions": 2, "imported": 0, "raw": 4, "legacy": 0} and len(meta["gaps"]) == 1
        assert "builtAt" not in meta and len(meta["dataHash"]) == 12
        # 빌드 재실행 → 산출물 바이트 동일 (결정적)
        before = {p.name: p.read_bytes() for p in (tmp / "docs" / "data").rglob("*.json")}
        assert build.main() == 0
        after = {p.name: p.read_bytes() for p in (tmp / "docs" / "data").rglob("*.json")}
        assert before == after, "build output is not deterministic"
        assert meta["clubCrest"] == [2055, 99160104], meta["clubCrest"]      # 실제 배지(kitType 0) → teamId 먼저
        idx = json.loads((tmp / "docs" / "data" / "matches.json").read_text())
        assert len(idx[0]) == 13 and idx[0][11] == 1450 and idx[0][12] == 3, idx[0]              # oppSR, oppDiv
        row_1002 = next(r for r in idx if r[0] == "1002"); assert row_1002[12] is None, row_1002
        hist_out = json.loads((tmp / "docs" / "data" / "history.json").read_text())
        assert [h["gp"] for h in hist_out] == [71, 74] and set(hist_out[0]) == {"at", "gp", "sr", "div", "pts", "w", "d", "l"}, hist_out
        sfile0 = json.loads((tmp / "docs" / "data" / "sessions" / "1001.json").read_text())
        assert sfile0["matches"][0]["oppDiv"] == 3, sfile0["matches"][0].get("oppDiv")
        season = json.loads((tmp / "docs" / "data" / "players.json").read_text())["season"]
        assert season["players"][0]["id"] == "172777124" and season["players"][0]["red"] == 1 and season["players"][1]["id"] == "name:nobody", season
        print("⑤ build OK (deterministic):", meta["counts"], "hash", meta["dataHash"], "crest", meta["clubCrest"], "season rows", len(season["players"]))
        print("ALL OK")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
