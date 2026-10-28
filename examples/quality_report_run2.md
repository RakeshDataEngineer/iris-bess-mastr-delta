# Quality report - run `ex-r2`

**Data origin: FIXTURE (synthetic, illustrative - not observational MaStR data).**

Mode `replay`, status **success**, generated 2026-10-28T10:22:18.904520+00:00.

## This run

| metric | value |
|---|---|
| pages | 1 |
| records seen | 8 |
| valid (after de-dup) | 7 |
| rejected | 0 |
| exact duplicates dropped | 0 |
| conflicting duplicates superseded | 1 |
| stale (older than stored) skipped | 1 |
| unchanged | 1 |
| deltas written | 5 {'CHANGED': 2, 'ENTERED_SCOPE': 1, 'LEFT_SCOPE': 1, 'NEW': 1} |
| since filter / watermark after | None / 2026-10-07T00:00:00+00:00 |

Rejections this run: DUPLICATE_SUPERSEDED=1

## Coverage of staged >10 MW battery population

| metric | value |
|---|---|
| in_scope_projects | 8 |
| total_power_mw | 188.5005 |
| total_capacity_mwh_known | 397.001 |
| with_capacity_mwh | 100.0% |
| with_valid_coordinates | 87.5% |
| with_net_power_basis | 87.5% |
| technology_verified_battery | 87.5% |

By status: {'operating': 7, 'planned': 1}

By federal state: {'Bayern': 1, 'Brandenburg': 7}

Quality flags (in-scope): {'LOCATION_OUT_OF_BOUNDS': 1, 'POWER_FROM_GROSS': 1, 'TECHNOLOGY_UNKNOWN': 1}

## Acceptance checks

- duplicate delta transitions (must be 0): **0**
- in-scope rows with power <= threshold (must be 0): **0**
- deltas all-time: {'CHANGED': 2, 'ENTERED_SCOPE': 1, 'LEFT_SCOPE': 1, 'NEW': 8}
- rejections all-time: {'DUPLICATE_SUPERSEDED': 1, 'IMPLAUSIBLE_POWER': 1, 'INVALID_MASTR_ID': 1, 'MALFORMED_POWER': 1, 'MISSING_MASTR_ID': 1}
