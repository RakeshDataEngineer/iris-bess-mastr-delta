# MaStR Large Battery (>10 MW) Delta Pipeline

**IRIS task 100 · `IRIS-GRID-BESS-MASTR` · destination `iris_staging.bess_pipeline_deltas`**

Identifies **new or changed battery-storage projects above 10 MW** in the German
Marktstammdatenregister (MaStR), normalises MW / MWh / status / MaStR ID / location,
and keeps a full change history in a staging-only schema. Repeated ingestion is
idempotent, every raw payload is retained, and every row is labelled as
`observed` (live MaStR) or `fixture` (synthetic test data).

```
MaStR public JSON table ──┐                         ┌─> bess_raw_records        (every distinct payload, sha256)
  (incremental, watermark) ├─> landing zone ─> normalise ─> de-dup ─> diff ─┼─> bess_rejected_records   (reason codes)
MaStR bulk XML export  ───┘   (immutable pages  (Decimal units,  (per        ├─> bess_projects_current   (latest state, all candidates)
  (full snapshot, MWh)          + manifest)      flags/rejects)   MaStR ID)  └─> bess_pipeline_deltas    (NEW / CHANGED /
                                                                                  ENTERED_SCOPE / LEFT_SCOPE)
```

---

## 1. Quick start

### Docker (recommended - PostgreSQL, isolated)

```bash
cp .env.example .env                    # local throw-away password only
docker compose up -d postgres
docker compose run --rm tests           # 82 tests incl. PostgreSQL integration test
docker compose run --rm demo            # replay synthetic fixtures 3x -> shows idempotency
docker compose run --rm pipeline        # LIVE incremental pull from MaStR -> iris_staging
```

Reports land in `./reports/<run_id>/quality_report.{md,json}`; raw pages in `./data/landing/`.

### Local Python 3.11+

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest                                                    # SQLite; set IRIS_TEST_PG_URL to also test PostgreSQL
cp config/config.example.yaml config/config.yaml

python -m bess_mastr run --config config/config.yaml --mode live                  # incremental live pull
python -m bess_mastr run --config config/config.yaml --mode live --full-refresh   # ignore watermark
python -m bess_mastr run --config config/config.yaml --mode replay --input data/landing/mastr_json/<run_id>
python -m bess_mastr run --config config/config.yaml --mode bulk --input /path/to/unzipped/Gesamtdatenexport
python -m bess_mastr export-deltas --config config/config.yaml --out deltas.csv
python scripts/validate.py data/iris_staging.sqlite      # or a postgresql:// URL; exits 1 if a gate fails
```

Database URL: `db.url` in the config, overridden by `IRIS_DB_URL`
(`postgresql://user:pw@host:5432/db` or `sqlite:///path`). The code refuses
`iris_core` and `public` as target schemas.

---

## 2. Data provenance

| | |
|---|---|
| Publisher | Bundesnetzagentur - Marktstammdatenregister (MaStR) |
| Licence | Datenlizenz Deutschland – Namensnennung 2.0 (DL-DE-BY-2.0). Attribution: *© Bundesnetzagentur, Marktstammdatenregister* |
| Primary source (incremental) | Public unit table JSON: `https://www.marktstammdatenregister.de/MaStR/Einheit/EinheitJson/GetErweiterteOeffentlicheEinheitStromerzeugung` - the same endpoint family the IRIS base repo (`iris-sample`, `app/config/data_sources.py`) already uses. No login, no API key. |
| Alternative source (full snapshot) | Official daily bulk export ("Gesamtdatenexport"): `https://www.marktstammdatenregister.de/MaStR/Datendownload` → `EinheitenStromSpeicher*.xml` + `AnlagenStromSpeicher*.xml` |
| Not used | MaStR SOAP web service (needs a registered account + token; avoided so no credentials are involved) |
| Update frequency | Register is updated continuously by operators; the bulk export is regenerated daily. The pipeline is designed for a daily schedule. |
| Access etiquette | Identifying User-Agent, 1 s between pages, exponential back-off honouring `Retry-After`, deterministic paging (`sort=MaStRNummer-asc`), no parallel requests. |

**Request issued in live mode** (server-side filter, Kendo grid syntax used by the public UI):

```
filter = Energieträger~eq~'2496'
         ~and~Nettonennleistung der Einheit~gt~'1000'
         ~and~Datum der letzten Aktualisierung~gt~'<watermark − 2 days, dd.mm.yyyy>'
```

The power pre-filter is deliberately **1 MW, not 10 MW**, so a project that is
downsized from e.g. 30 MW to 8 MW is still returned and recorded as
`LEFT_SCOPE` instead of silently vanishing. The 10 MW rule is applied in Python
with exact `Decimal` arithmetic.

---

## 3. Incremental extraction, idempotency and fault handling

* **Watermark**: max `DatumLetzteAktualisierung` of the last successful run (`pipeline_runs.watermark_after`, taken as `MAX` over successful runs so it never moves backwards). Next run asks for updates since watermark − `overlap_days` (2) to catch late/back-dated edits. Overlap is safe because loading is idempotent.
* **Landing first**: each page is written byte-for-byte to `data/landing/<source>/<run_id>/page_NNNN.json` with a `manifest.jsonl` (URL, params, HTTP status, sha256, record count) *before* it is processed. Any run can be replayed exactly with `--mode replay`.
* **Idempotency** - three layers:
  1. `record_hash` = sha256 of the normalised *business* fields. Unchanged hash ⇒ no delta (only `last_seen_*` is touched). A bumped `DatumLetzteAktualisierung` alone is not a change.
  2. Out-of-order guard: an incoming version older than the stored `source_updated_at` is skipped as *stale* (replaying an old snapshot cannot roll state back).
  3. `delta_id` = sha256(mastr_id, previous hash, new hash, change type, source timestamp) is the primary key, so a transition can be written only once even if the guards were bypassed.
* **Atomicity**: all staging writes of a run happen in one transaction. On any error the batch is rolled back, the run row is marked `failed` with the error, and the watermark does not advance.
* **Retries**: 429/5xx and network errors are retried with exponential back-off; other HTTP errors, non-JSON bodies, `Errors` in the payload or a missing `Data` list fail the run (the bad body is kept in the landing zone for forensics). Hitting `max_pages` fails the run rather than advancing on a partial result.
* **Logging**: one JSON object per line on stderr with `run_id`, `source`, `mode`, page numbers, retry reasons, rejected record IDs and reason codes.

---

## 4. Schema mapping

Units: MaStR reports power in **kW** and usable storage capacity in **kWh**. Both are converted with `Decimal` (`kW × 0.001 → MW`, `kWh × 0.001 → MWh`) and quantised to 6 decimals (1 W / 1 Wh). String numbers in German format (`"12.500,5"`) are understood; values with an embedded unit (`"12,5 MW"`) use that unit.

| Staging column | JSON table field | Bulk XML field | Transform |
|---|---|---|---|
| `mastr_id` | `MaStRNummer` | `EinheitMastrNummer` | trim, upper; must match `^SEE\d{12}$` else rejected |
| `unit_name` | `EinheitName` | `NameStromerzeugungseinheit` | trim |
| `operator_name` | `AnlagenbetreiberName` | - | trim (MaStR anonymises natural persons) |
| `storage_mastr_id` | `SpeMastrNummer` | `SpeMastrNummer` | link unit → storage installation |
| `energy_carrier` | `EnergietraegerName` | `Energietraeger` (code `2496`) | must be "Speicher"/2496 to be in scope |
| `storage_technology`, `is_battery` | `TechnologieName` / `Batterietechnologie*` | `Technologie` (code) | keyword/code classification; pumped hydro, compressed air, flywheel, hydrogen → not battery |
| `net_power_mw` | `Nettonennleistung` (kW) | `Nettonennleistung` (kW) | ÷1000, Decimal |
| `gross_power_mw` | `Bruttoleistung` (kW) | `Bruttoleistung` (kW) | ÷1000, Decimal |
| `scope_power_mw`, `power_basis` | | | net power; falls back to gross (flag `POWER_FROM_GROSS`) |
| `usable_capacity_mwh` | `NutzbareSpeicherkapazitaet` (kWh)* | `AnlagenStromSpeicher.NutzbareSpeicherkapazitaet` via `SpeMastrNummer` | ÷1000 |
| `duration_h` | | | MWh ÷ MW, flagged outside 0.25-12 h |
| `status`, `status_code` | `BetriebsStatusId` / `BetriebsStatusName` | `EinheitBetriebsstatus` | 31 planned · 35 operating · 37 temporarily_shut_down · 38 permanently_shut_down |
| `commissioning_date` / `planned_commissioning_date` / `registration_date` | `InbetriebnahmeDatum` / `GeplantesInbetriebnahmeDatum` / `EinheitMeldeDatum` | `Inbetriebnahmedatum` / … / `Registrierungsdatum` | `/Date(ms)/`, ISO-8601 (incl. 7-digit .NET fractions), `dd.mm.yyyy` → UTC |
| `source_updated_at` | `DatumLetzteAktualisierung` | `DatumLetzteAktualisierung` | → timezone-aware UTC; drives the watermark |
| `postal_code`, `city`, `federal_state`, `municipality_key` | `Plz`, `Ort`, `BundeslandName`, `Gemeindeschluessel` | `Postleitzahl`, `Ort`, `Bundesland` (code), `Gemeindeschluessel` | trim |
| `latitude`, `longitude`, `location_quality`, `geometry_wkt` | `Breitengrad`, `Laengengrad` | same | WGS84 (EPSG:4326), 6 dp; `ok` / `missing` / `out_of_bounds` (outside a DE bounding box); WKT only when `ok` |
| `in_scope`, `scope_reason` | | | storage ∧ battery (or unknown technology, configurable) ∧ `scope_power_mw > 10` |
| `quality_flags` | | | comma-separated, see §5 |
| `data_origin` | | | `observed` / `fixture` |

\* see limitation L3.

### Delta semantics (`iris_staging.bess_pipeline_deltas`)

| `change_type` | Meaning |
|---|---|
| `NEW` | first time this MaStR unit is seen, and it is in scope |
| `CHANGED` | in scope before and now; business fields differ (`changed_fields` = `{field: {old, new}}`) |
| `ENTERED_SCOPE` | known before but out of scope (e.g. upgraded from 10 → 25 MW, technology clarified) |
| `LEFT_SCOPE` | in scope before, now not (downsized ≤ 10 MW, re-classified as non-battery) |

Each delta row stores `previous_state` and `current_state` (JSON), both hashes, headline columns (MW, MWh, status, state, lat/lon), `source_updated_at`, `detected_at`, `run_id`, `data_origin`, `source_system`.

Other tables: `pipeline_runs`, `bess_raw_records`, `bess_rejected_records`, `bess_projects_current`, view `v_bess_large_battery_current`.

---

## 5. Data-quality rules

**Rejected** (stored in `bess_rejected_records` with the raw payload kept in `bess_raw_records`): `NOT_A_RECORD`, `MISSING_MASTR_ID`, `INVALID_MASTR_ID`, `MALFORMED_POWER`, `MISSING_POWER`, `NEGATIVE_POWER`, `IMPLAUSIBLE_POWER` (> 2 000 MW - catches W-vs-kW errors), `DUPLICATE_SUPERSEDED` (same MaStR ID twice in one batch with different content; the newest `DatumLetzteAktualisierung` wins, ties broken by payload hash for determinism). Exact duplicate payloads are counted and dropped.

**Kept but flagged**: `POWER_FROM_GROSS`, `NET_EXCEEDS_GROSS`, `CAPACITY_MISSING`, `CAPACITY_MALFORMED`, `CAPACITY_NEGATIVE`, `DURATION_IMPLAUSIBLE`, `TECHNOLOGY_UNKNOWN`, `NOT_STORAGE_CARRIER`, `CARRIER_MISSING`, `STATUS_UNKNOWN`, `LOCATION_MISSING`, `LOCATION_MALFORMED`, `LOCATION_OUT_OF_BOUNDS`, `UPDATED_AT_MISSING`, `UPDATED_AT_UNPARSEABLE`, `*_UNPARSEABLE` for dates.

Principle: reject only when the record cannot be identified or its scope cannot be decided; everything else is kept so no information is lost.

---

## 6. Tests and evidence

`pytest` → **81 passed, 1 skipped** on SQLite (the skipped one is the PostgreSQL integration test, which runs inside `docker compose run --rm tests`).

| File | Covers |
|---|---|
| `tests/test_units.py` | kW/MW/GW/W, kWh/MWh, German & English number strings, exact threshold (10 000 kW ≠ >10 MW), `/Date()/` + ISO + .NET timestamps |
| `tests/test_normalize.py` | rejections (missing/invalid ID, malformed/negative/implausible power), threshold boundary, gross fallback, non-battery storage, flags for location/capacity/status/timestamps, hash stability across units |
| `tests/test_pipeline.py` | NEW on first load; CHANGED/ENTERED_SCOPE/LEFT_SCOPE/NEW on second; **idempotency over repeated and out-of-order replays**; no data loss; monotonic watermark; origin labels; bulk XML (UTF-8 and UTF-16) with MWh join; `iris_core` refusal; config guards |
| `tests/test_extractor_http.py` | mocked MaStR: filter string, paging, landing + manifest, incremental filter with overlap, replay of a landed live run, retry on 503/429, give-up → failed run with no deltas and no watermark, malformed bodies, 404, crash mid-write → full rollback |
| `tests/test_postgres_integration.py` | same scenario end-to-end on PostgreSQL in a throw-away schema |

Fixtures (`tests/fixtures/`, regenerated by `scripts/make_fixtures.py`) are **synthetic**: IDs `SEE9000000000xx`, names prefixed `FIXTURE`.

`examples/` holds the output of `scripts/generate_examples.sh` on those fixtures: run log for four runs (initial, changes, identical repeat, stale replay), the delta table as CSV, the run-2 quality report and the validation-query results (all gates pass). These are **illustrative, not observational**. See `QUALITY_REPORT.md` for the walkthrough.

---

## 7. Assumptions and limitations

* **L1 - Live source not exercised from the development sandbox.** `www.marktstammdatenregister.de` was not reachable from the environment this was built in, so live mode is verified against a mocked endpoint that reproduces the documented response shape (`Data`/`Total`/`Errors`, `/Date()/` timestamps, kW values). On the first real run, check the items below; all are config values, not code changes.
* **L2 - Catalogue values.** `Energietraeger = 2496` (Speicher) and the bulk technology code `524` (Batterie) come from the MaStR catalogue as I understand it; verify against the "Katalogwerte" in the bulk export. Filter column labels (`Datum der letzten Aktualisierung`, …) must match the public UI's labels.
* **L3 - MWh on the JSON table.** The public table may not expose usable capacity for every unit. The normaliser accepts several field names; if none is present the record is kept with `CAPACITY_MISSING`. The bulk mode joins `AnlagenStromSpeicher` and is the authoritative MWh source.
* **L4 - The JSON endpoint is the backend of the public web UI, not a versioned API.** It can change without notice; the bulk export is the stable, documented fallback and is implemented.
* **L5 - Deletions.** MaStR units are not deleted but decommissioned (status 38, captured as `CHANGED`). Incremental mode cannot see units that stop being returned; a periodic `--full-refresh` or bulk run covers that.
* **L6 - Technology unknown.** Storage units > 10 MW without technology information are included by default (flag `TECHNOLOGY_UNKNOWN`); set `include_unknown_technology: false` to exclude them.
* **L7 - Net vs gross.** Scope uses net nominal power (Nettonennleistung), falling back to gross when net is missing (flagged).
* **L8 - Federal state** in the bulk export is a numeric code and is stored as received (not decoded).
* **L9 - SQLite** is for tests/local runs: it stores NUMERIC as floating point. The scope decision is made in Python `Decimal` before storage; PostgreSQL `NUMERIC(18,6)` is exact.
* **L10 - Single writer.** Runs are assumed not to execute concurrently against the same schema (schedule them serially).

---

## 8. Layout

```
bess_mastr/            config, units, normalize, models, store, pipeline, report, cli
bess_mastr/sources/    mastr_json (live), mastr_bulk_xml, landing (raw page store / replay)
config/                config.example.yaml
sql/                   validation_queries.sql
scripts/               demo.sh, generate_examples.sh, make_fixtures.py, validate.py
tests/                 unit, pipeline, mocked-HTTP and PostgreSQL tests + synthetic fixtures
examples/              illustrative outputs on fixtures
docker/initdb/         creates schema iris_staging only
Dockerfile, docker-compose.yml, requirements*.txt, QUALITY_REPORT.md
```

Data: © Bundesnetzagentur, Marktstammdatenregister - DL-DE-BY-2.0.
