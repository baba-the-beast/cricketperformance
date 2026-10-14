# Simulation Methodology

This document outlines the architecture and statistical methodology underlying the Cricket Team Performance Agent simulation engine, rebuilt in Phase 14-16 to provide genuine probabilistic modeling rather than naive historical sampling.

## Core Concepts

The system strictly differentiates between:
- **Historical Data**: Empirical facts of what occurred (deliveries, dismissals, runs).
- **Prediction**: Point estimates based on aggregated data (e.g. Expected Value).
- **Simulation**: Stochastically sampled individual events forming a complete timeline.

## Feature Engineering

The simulation engine extracts features dynamically for both batters and bowlers, relying on explicitly limited temporal windows to avoid future data leakage during backtesting. 
- **Batters**: Historical frequencies of ball outcomes (Dots, 1-6s, Dismissals).
- **Bowlers**: Historical frequencies of runs conceded, wickets taken, wides, and no-balls.

## Probability Estimation & Smoothing

Unlike earlier iterations that merely copied historical deliveries intact (`rng.choice`), the new probabilistic simulator generates an outcome distribution array per ball:
1. `_build_distribution()` calculates the raw empirical frequency of 12 distinct outcome keys (`0-6`, `W`, `run_out`, `WD`, `NB`, `B_LB`).
2. The engine combines the batter's historical distribution and the bowler's historical distribution.
3. **Control Weights**:
   - For run-scoring outcomes (0-6), the batter holds 60% of the statistical weight and the bowler holds 40%.
   - For wickets and extras, the bowler holds 80% control and the batter holds 20%.

If empirical data is insufficient (small sample sizes), the engine defaults to a baseline distribution (`_BASELINE_WEIGHTS`) calculated from global ODI and T20 historical averages.

## Context & Monte Carlo Architecture

To support high-performance Monte Carlo tournaments (5,000+ simulations):
1. **Caching Layer**: A wrapper `simulate_monte_carlo()` function extracts the historical distributions for all 22 players *once* before the execution loop begins.
2. **Execution Routing**: These pre-computed histories are passed down to `simulate_match()` and `_simulate_ball_innings()`, preventing N+1 database queries. The simulation executes completely in memory, achieving 100 simulations in under 15 seconds.

## Leakage Prevention

The system enforces rigorous chronologic isolation. The `_delivery_history()` SQL queries explicitly mandate `m.match_date < ?` constraints when `date_limit` is supplied. This prevents historical simulation testing from inadvertently incorporating statistical data that the player generated *after* the simulated date.

## Backtesting & Metrics

The system includes a dedicated backtesting suite (`test_backtest.py`). It fetches the most recent matches in the local dataset, extracts the exact playing XIs, and probabilistically simulates the matches relying entirely on data that existed prior to the first ball being bowled.

## Known Limitations

- **Matchup Sample Sizes**: Granular Head-to-Head batter vs. bowler data is rarely large enough to be statistically robust on its own.
- **Pitch Data**: The engine currently does not degrade or modify probabilities based on pitch deterioration (e.g., Day 5 of a Test Match) or explicit weather events unless available through a live External Provider.
