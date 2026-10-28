# Quality report - run `20261028T125010Z-f901b1`

**Data origin: OBSERVED (live MaStR extraction).**

Mode `live`, status **success**, generated 2026-10-28T12:50:30.394138+00:00.

## This run

| metric | value |
|---|---|
| pages | 3 |
| records seen | 1337 |
| valid (after de-dup) | 1337 |
| rejected | 0 |
| exact duplicates dropped | 0 |
| conflicting duplicates superseded | 0 |
| stale (older than stored) skipped | 0 |
| unchanged | 1337 |
| deltas written | 0  |
| since filter / watermark after | 2026-10-25 / 2026-10-27T18:12:29.358000+00:00 |

Rejections this run: none

## Coverage of staged >10 MW battery population

| metric | value |
|---|---|
| in_scope_projects | 334 |
| total_power_mw | 23366.361 |
| total_capacity_mwh_known | 2556973.99798 |
| with_capacity_mwh | 100.0% |
| with_valid_coordinates | 97.9% |
| with_net_power_basis | 100.0% |
| technology_verified_battery | 0.0% |

By status: {'operating': 207, 'permanently_shut_down': 5, 'planned': 122}

By federal state: {'Baden-Württemberg': 30, 'Bayern': 61, 'Brandenburg': 20, 'Bremen': 2, 'Hessen': 13, 'Mecklenburg-Vorpommern': 13, 'Niedersachsen': 21, 'Nordrhein-Westfalen': 29, 'Rheinland-Pfalz': 10, 'Saarland': 4, 'Sachsen': 29, 'Sachsen-Anhalt': 31, 'Schleswig-Holstein': 16, 'Thüringen': 27, 'unknown': 28}

Quality flags (in-scope): {'DURATION_IMPLAUSIBLE': 95, 'LOCATION_MISSING': 2, 'LOCATION_OUT_OF_BOUNDS': 5, 'TECHNOLOGY_UNKNOWN': 334}

## Acceptance checks

- duplicate delta transitions (must be 0): **0**
- in-scope rows with power <= threshold (must be 0): **0**
- deltas all-time: {'NEW': 334}
- rejections all-time: {}
