import sqlite3
import itertools
from cricket_agent.simulation import simulate_match
from cricket_agent.agents.selector import recommend_lineup
from cricket_agent.squads import current_squad, feed_formats, feed_team, team_gender

def simulate_tournament(conn: sqlite3.Connection, teams: list[str], year: str, format: str = "T20"):
    # 1. Fetch squads. Names may come from a fixture feed ("Australia Women").
    squads = {}
    for name in teams:
        team, gender, impossible = feed_team(name)
        if impossible:
            squads[name] = []
            continue
        gender = gender or team_gender(conn, team)
        squad = []
        # That season in this format, else the current squad in this format, else any format.
        for fmts, season in ((feed_formats(format), year or ""), (feed_formats(format), ""), ((), "")):
            squad = current_squad(conn, team, gender=gender, formats=fmts, year=season)
            if len(squad) >= 11:
                break
        squads[name] = squad

    lineups = {}
    skipped = []
    for team, squad in squads.items():
        if len(squad) < 11:
            skipped.append(team)
            continue
        # Use selector to get ordered XI
        rec = recommend_lineup(conn, squad=squad, xi_size=11)
        
        # Apply the exact same batting order logic we just added to selector
        def batting_order_key(player_case):
            role_report = player_case.get("role_report") or {}
            primary = role_report.get("primary") or {}
            metrics = primary.get("supporting_metrics") or {}
            avg_over = metrics.get("average_first_over_0based")
            if avg_over is None:
                wickets = (player_case.get("bowling") or {}).get("wickets") or 0
                return (100, -wickets)
            return (avg_over, 0)
            
        selected = sorted(rec["recommended_xi"], key=batting_order_key)
        batting = [p["player"] for p in selected]
        # A player with no bowling record has bowling=None, not a missing key.
        bowlers = [x for x in selected if ((x.get("bowling") or {}).get("legal_deliveries") or 0) > 0]
        bowlers.sort(key=lambda x: -((x.get("bowling") or {}).get("wickets") or 0))
        bowling = [p["player"] for p in bowlers][:6]
        if len(bowling) < 2:
            bowling = batting[-5:]
        lineups[team] = {"batting": batting, "bowling": bowling}

    valid_teams = list(lineups.keys())
    
    standings = {t: {"played": 0, "won": 0, "lost": 0, "points": 0, "runs_scored": 0, "overs_faced": 0, "runs_conceded": 0, "overs_bowled": 0} for t in valid_teams}
    
    match_results = []
    overs = 20 if format == "T20" else 50
    
    for team_a, team_b in itertools.combinations(valid_teams, 2):
        res = simulate_match(
            conn, team_a=team_a, team_b=team_b,
            batting_lineup_a=lineups[team_a]["batting"],
            bowling_lineup_a=lineups[team_a]["bowling"],
            batting_lineup_b=lineups[team_b]["batting"],
            bowling_lineup_b=lineups[team_b]["bowling"],
            overs=overs,
            match_type=format,
            date_limit=None,
            seed=None
        )
        
        if not res["available"]: continue
        
        i1 = res["innings"][0]
        i2 = res["innings"][1]
        
        # Calculate overs
        o1 = i1["legal_deliveries_faced"] / 6.0
        o2 = i2["legal_deliveries_faced"] / 6.0
        
        standings[team_a]["played"] += 1
        standings[team_b]["played"] += 1
        
        standings[team_a]["runs_scored"] += i1["total_runs"]
        standings[team_a]["overs_faced"] += overs if i1["wickets_lost"] == 10 else o1
        standings[team_a]["runs_conceded"] += i2["total_runs"]
        standings[team_a]["overs_bowled"] += overs if i2["wickets_lost"] == 10 else o2
        
        standings[team_b]["runs_scored"] += i2["total_runs"]
        standings[team_b]["overs_faced"] += overs if i2["wickets_lost"] == 10 else o2
        standings[team_b]["runs_conceded"] += i1["total_runs"]
        standings[team_b]["overs_bowled"] += overs if i1["wickets_lost"] == 10 else o1
        
        winner = res["winner"] or "Tie"
        if winner == team_a:
            standings[team_a]["won"] += 1
            standings[team_a]["points"] += 2
            standings[team_b]["lost"] += 1
        elif winner == team_b:
            standings[team_b]["won"] += 1
            standings[team_b]["points"] += 2
            standings[team_a]["lost"] += 1
            
        match_results.append({
            "team_a": team_a,
            "team_b": team_b,
            "score_a": f"{i1['total_runs']}/{i1['wickets_lost']}",
            "score_b": f"{i2['total_runs']}/{i2['wickets_lost']}",
            "winner": winner
        })
        
    for t in valid_teams:
        rs = standings[t]["runs_scored"]
        of = standings[t]["overs_faced"]
        rc = standings[t]["runs_conceded"]
        ob = standings[t]["overs_bowled"]
        standings[t]["nrr"] = (rs/of if of > 0 else 0) - (rc/ob if ob > 0 else 0)
        
    ranked = sorted(valid_teams, key=lambda x: (standings[x]["points"], standings[x]["nrr"]), reverse=True)
    
    return {
        "standings": [{"team": t, **standings[t]} for t in ranked],
        "matches": match_results,
        # Teams without 11 recorded players cannot field a side and are left out.
        "skipped_teams": skipped,
    }
