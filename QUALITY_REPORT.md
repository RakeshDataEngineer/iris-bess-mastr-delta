# Quality & error report

Scope: evidence that the pipeline meets the acceptance criteria, what it rejects,
which edge cases it handles, and what is still unverified.

> **Data origin.** Everything quantified below was produced from the **synthetic
> fixtures** in `tests/fixtures/` (IDs `SEE9000000000xx`, names prefixed
> `FIXTURE`). It is illustrative evidence of behaviour, **not** a statement about
> the real German battery fleet. Each run writes its own report to
> `reports/<run_id>/quality_report.md` - a live run produces the observational
> equivalent, labelled `Data origin: OBSERVED`.

## 1. Acceptance criteria → evidence

| Criterion | Evidence |
|---|---|
| Repeated ingestion is idempotent | `test_repeated_ingestion_is_idempotent` (3 sequences incl. out-of-order replay): 0 new deltas, identical current state, no new raw/rejected rows. `examples/run_log.txt` runs 3 and 4: `deltas: {}`. Gate `gate_duplicate_transitions` = 0. PostgreSQL: `test_postgres_end_to_end`. |
| Handles MW/kW correctly | All arithmetic in `Decimal`; `test_to_mw` (W, kW, MW, GW, German/English strings, embedded units), `test_threshold_is_exact_not_float`, `test_equal_values_in_different_units_hash_equal`; bulk `"10.000"` read as 10 kW, not 10 MW (`test_bulk_xml_mode`). |
| Filters projects over the threshold | Strict `>` 10 MW: 10 000 kW and 9 999 kW excluded, 10 000.5 kW included (`test_threshold_strictly_greater`, fixture units 02/05/12). Gates `gate_in_scope_at_or_below_threshold` = 0. |
| Deterministic / auditable | Deterministic paging (`sort=MaStRNummer-asc`), deterministic duplicate resolution, raw pages + sha256 manifest in the landing zone, every payload in `bess_raw_records`, config fingerprint + code version on every run, `--mode replay` reproduces a run (`test_replay_of_landed_live_run_matches`). |
| Uses the documented source | MaStR public JSON unit table (same family as the IRIS base repo) and the official bulk export; README §2. |
| Staging only, never `iris_core` | Config and `Database` refuse `iris_core`/`public` (`test_refuses_iris_core`, `test_config_guards`); compose init creates only `iris_staging`. |
| Observational vs illustrative separated | `data_origin` column on runs, current state and every delta; report banner (`test_every_row_is_labelled_with_origin`). |

## 2. Fixture scenario and outcome

Run 1 = `snapshot_1` (15 records, 2 pages), run 2 = `snapshot_2` (8 records), runs 3-4 = repeats.

| Unit | Situation | Run 1 | Run 2 |
|---|---|---|---|
| 01 Alpha | 50 MW / 100 MWh; capacity revised to 120 MWh in run 2 | NEW | CHANGED (`usable_capacity_mwh` 100 → 120) |
| 01 Alpha (copy) | identical payload repeated on page 2 | exact duplicate dropped | - |
| 02 Beta | exactly 10 000 kW; upgraded to 25 MW | out of scope (staged) | ENTERED_SCOPE |
| 03 Gamma | 12.5 MW planned, no MWh; then commissioned | NEW (`CAPACITY_MISSING`) | CHANGED (status, commissioning date, MWh) |
| 04 Delta | 200 MW **pumped hydro** | out of scope (`not_battery_technology`) | - |
| 05 Epsilon | power as German string `"10.000,5"` kW | NEW at 10.0005 MW | - |
| 06 | `MaStRNummer` null | rejected `MISSING_MASTR_ID` | - |
| 07 | `MaStRNummer = "ABC123"` | rejected `INVALID_MASTR_ID` | - |
| 08 | power `"n/a"` | rejected `MALFORMED_POWER` | - |
| 10 Juliet | net missing, gross 15 MW | NEW (`POWER_FROM_GROSS`) | - |
| 11 Kilo | no coordinates; downsized to 8 MW | NEW (`LOCATION_MISSING`) | LEFT_SCOPE |
| 12 Lima | 9 999 kW | out of scope | - |
| 13 Mike | no technology info; older version arrives in run 2 | NEW (`TECHNOLOGY_UNKNOWN`) | stale → skipped, state kept |
| 14 November | coordinates 0/0; only timestamp bumped in run 2 | NEW (`LOCATION_OUT_OF_BOUNDS`) | unchanged, no delta |
| 15 Oscar | 5 000 000 000 "kW" (W-vs-kW error) | rejected `IMPLAUSIBLE_POWER` | - |
| 16 Papa | new 40 MW, then 45 MW, both in one batch | - | NEW at 45 MW; older copy `DUPLICATE_SUPERSEDED` |

Totals (from `examples/run_log.txt` and `examples/validation_query_results.txt`):

| Run | seen | valid | rejected | dup | stale | unchanged | deltas |
|---|---|---|---|---|---|---|---|
| 1 | 15 | 10 | 4 | 1 | 0 | 0 | 7 NEW |
| 2 | 8 | 7 | 0 | 1 | 1 | 1 | 2 CHANGED, 1 ENTERED, 1 LEFT, 1 NEW |
| 3 (= run 2 again) | 8 | 7 | 0 | 1 | 1 | 6 | **0** |
| 4 (= run 1 again) | 15 | 10 | 4 | 1 | 5 | 5 | **0** |

End state: 8 in-scope projects, 188.5005 MW; all 5 validation gates pass.

## 3. Rejected data and fault handling

| Reason | Trigger | Handling |
|---|---|---|
| `MISSING_MASTR_ID`, `INVALID_MASTR_ID` | no / malformed unit ID | rejected; raw payload kept |
| `MALFORMED_POWER`, `MISSING_POWER`, `NEGATIVE_POWER` | power unusable | rejected; scope cannot be decided |
| `IMPLAUSIBLE_POWER` | > 2 000 MW (configurable) | rejected; most likely a unit error |
| `DUPLICATE_SUPERSEDED` | same ID, different content in one batch | older version recorded, newer one staged |
| HTTP 429/5xx, network | transient | retried with back-off (`test_retries_transient_errors`) |
| repeated 5xx, 4xx, HTML/invalid JSON, `Errors` payload, missing `Data` | source failure | run `failed`, nothing staged, watermark unchanged, body kept (`test_gives_up…`, `test_malformed_response_fails_safely`) |
| exception during staging | DB/disk failure | transaction rolled back; clean retry works (`test_failed_run_does_not_leave_partial_deltas`) |

## 4. Known gaps

1. **Incremental date filter not honoured by the server** (see §5); runs are correct but pull the full >1 MW storage set.
2. **MWh completeness** depends on the source; use `--mode bulk` for authoritative capacity.
3. **Disappearing units** are only detectable with a full refresh / bulk run.
4. **Bundesland codes** from the bulk export are not decoded.
5. **PostgreSQL path** is covered by `tests/test_postgres_integration.py`, which runs in `docker compose run --rm tests`.

## 5. Observed live runs (MaStR, 2026-10-28)

**First attempt - and what it caught.** The first live run (3 pages, 1,337 storage units > 1 MW, 0 rejected, all validation gates passed) flagged **all 334** in-scope units `TECHNOLOGY_UNKNOWN`. The quality report made the cause visible: the public table carries technology in `Stromspeichertechnologie` / `StromspeichertechnologieBezeichnung` (e.g. "Pumpspeicher" for *PSW Hohenwarte 2*), which the mapping did not yet read, so pumped hydro was being counted as battery storage (inflated MW totals, 95 `DURATION_IMPLAUSIBLE` flags). The mapping was corrected (plus `SpeicherEinheitMastrNummer`, `EinheitRegistrierungsdatum`, `GeplantesInbetriebsnahmeDatum`), live-shaped tests were added (`tests/test_live_shape.py`), and the observed evidence below was regenerated on a fresh staging DB with `scripts/live_evidence.sh`.

**Regenerated evidence:** `examples/quality_report_live_run1.md` (initial run), `examples/quality_report_live_run2.md` (incremental re-run), `examples/live_bess_pipeline_deltas.csv`, `examples/live_validation_query_results.txt`.

**Finding - incremental date filter:** the incremental run still returned `Total` = 1,337, so the server ignores the `Datum der letzten Aktualisierung` clause (label mismatch). Correctness is unaffected because unchanged records are detected by hash and skipped (the re-run produced 0 deltas); the cost is a full pull of ~1.3k records (~20 s). Fix: confirm the UI column label and set `source.updated_filter_field`.
