# Demo Web App

Flask command center for the Cricket Team Performance Agent. It calls the
same `cricket_agent` functions as the MCP server. Dashboard analysis is
**in-process**; it does not claim an MCP round-trip unless `channel=mcp`.

```bash
pip install -r webapp/requirements.txt
PYTHONPATH=src python3 -m cricket_agent.ingest tests/fixtures
python3 webapp/app.py
```

Open http://127.0.0.1:5000

## Live Fixtures

The optional CricketData.org provider requires a server-side API key. Obtain
one from the provider, then export it in the same shell before starting Flask:

```bash
export CRICKETDATA_API_KEY="your-key"
python webapp/app.py
```

The app requests `matches` and `currentMatches` and caches the combined
response for one hour. The provider describes its free data as near-live and
potentially a few minutes delayed. Playing XIs are not guaranteed by these
fixture endpoints. Keep API keys out of source files and browser code.

## Full Match Simulation

Upcoming fixtures can launch a two-innings ODI or T20 scenario. Full-length
simulations use 11 batters and at least five bowling options per side, stop on
10 wickets, the over limit, or a successful chase, and then show a generated
scorecard and charts. Each delivery is sampled from the selected players'
recent matching-format Cricsheet history when available. Missing histories use
a disclosed generic distribution; this is a scenario, not a reliable forecast.
The current local IPL archive is T20-only, so it does not provide
player-specific ODI outcome data or international playing XIs.
