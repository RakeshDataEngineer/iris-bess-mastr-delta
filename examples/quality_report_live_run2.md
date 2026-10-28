# Quality report - run `20261028T133111Z-4a0cef`

**Data origin: OBSERVED (live MaStR extraction).**

Mode `live`, status **success**, generated 2026-10-28T13:32:27.881900+00:00.

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
| in_scope_projects | 232 |
| total_power_mw | 13381.761 |
| total_capacity_mwh_known | 37077.59798 |
| with_capacity_mwh | 100.0% |
| with_valid_coordinates | 99.1% |
| with_net_power_basis | 100.0% |
| technology_verified_battery | 100.0% |

By status: {'operating': 110, 'planned': 122}

By federal state: {'Baden-Württemberg': 7, 'Bayern': 48, 'Brandenburg': 20, 'Bremen': 2, 'Hessen': 8, 'Mecklenburg-Vorpommern': 13, 'Niedersachsen': 20, 'Nordrhein-Westfalen': 26, 'Rheinland-Pfalz': 10, 'Saarland': 4, 'Sachsen': 21, 'Sachsen-Anhalt': 29, 'Schleswig-Holstein': 13, 'Thüringen': 11}

Quality flags (in-scope): {'DURATION_IMPLAUSIBLE': 1, 'LOCATION_MISSING': 2}

## Acceptance checks

- duplicate delta transitions (must be 0): **0**
- in-scope rows with power <= threshold (must be 0): **0**
- deltas all-time: {'NEW': 232}
- rejections all-time: {}
