"""Incremental extractor for the public MaStR unit table (JSON endpoint).

Endpoint (public, no credentials; data licence DL-DE-BY-2.0, (c) Bundesnetzagentur):

    GET https://www.marktstammdatenregister.de/MaStR/Einheit/EinheitJson/
        GetErweiterteOeffentlicheEinheitStromerzeugung
        ?sort=MaStRNummer-asc&page=1&pageSize=500
        &filter=Energieträger~eq~'2496'~and~Nettonennleistung der Einheit~gt~'1000'
               ~and~Datum der letzten Aktualisierung~gt~'01.10.2026'

It returns ``{"Data": [...], "Total": n, "Errors": null}``. This is the same
endpoint family the IRIS base repository already uses
(``GetVerkleinerteOeffentlicheEinheitStromerzeugung``); the *Erweiterte*
variant accepts the same Kendo-grid filter syntax as the public web UI.

Incrementality: the watermark is the max ``DatumLetzteAktualisierung`` seen
in the last successful run, minus ``overlap_days`` to catch late or
back-dated updates. Re-reading the overlap is safe because loading is
idempotent.
"""

from __future__ import annotations

import time
from datetime import date
from decimal import Decimal
from typing import Callable, Iterator

import requests

from ..config import SourceConfig
from ..logging_setup import get_logger
from .landing import LandedPage, LandingError, LandingZone, parse_page_body

RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class SourceError(RuntimeError):
    """Extraction failed; nothing past the failing page was ingested."""


def _fmt_kw(value: Decimal) -> str:
    return format(value.normalize(), "f")


def build_filter(cfg: SourceConfig, since: date | None) -> str:
    parts = [
        f"{cfg.carrier_filter_field}~eq~'{cfg.energietraeger_code}'",
        f"{cfg.power_filter_field}~gt~'{_fmt_kw(cfg.candidate_floor_kw)}'",
    ]
    if since is not None:
        parts.append(f"{cfg.updated_filter_field}~gt~'{since:%d.%m.%Y}'")
    return "~and~".join(parts)


class MastrJsonClient:
    def __init__(self, cfg: SourceConfig, session: requests.Session | None = None,
                 sleep: Callable[[float], None] = time.sleep, run_id: str = "-"):
        self.cfg = cfg
        self.session = session or requests.Session()
        self.session.headers.setdefault("User-Agent", cfg.user_agent)
        self.session.headers.setdefault("Accept", "application/json")
        self.sleep = sleep
        self.log = get_logger(__name__, run_id=run_id, source="mastr_json")

    def _get(self, params: dict) -> requests.Response:
        attempt = 0
        while True:
            attempt += 1
            try:
                resp = self.session.get(self.cfg.base_url, params=params, timeout=self.cfg.timeout_s)
            except requests.RequestException as exc:
                if attempt > self.cfg.max_retries:
                    raise SourceError(f"network error after {attempt} attempts: {exc}") from exc
                self._backoff(attempt, f"network error: {exc}", params)
                continue
            if resp.status_code in RETRYABLE_STATUS:
                if attempt > self.cfg.max_retries:
                    raise SourceError(f"HTTP {resp.status_code} after {attempt} attempts (page {params['page']})")
                self._backoff(attempt, f"HTTP {resp.status_code}", params, resp.headers.get("Retry-After"))
                continue
            if resp.status_code != 200:
                raise SourceError(f"HTTP {resp.status_code} for page {params['page']}: {resp.text[:300]}")
            return resp

    def _backoff(self, attempt: int, reason: str, params: dict, retry_after: str | None = None) -> None:
        delay = self.cfg.backoff_s * (2 ** (attempt - 1))
        if retry_after and retry_after.isdigit():
            delay = max(delay, float(retry_after))
        self.log.warning("retrying request", extra={"reason": reason, "attempt": attempt,
                                                     "page": params.get("page"), "delay_s": delay})
        self.sleep(delay)

    def fetch(self, since: date | None, landing: LandingZone) -> Iterator[LandedPage]:
        """Yield pages, writing each to the landing zone before it is processed."""
        flt = build_filter(self.cfg, since)
        seen = 0
        for page in range(1, self.cfg.max_pages + 1):
            params = {"sort": self.cfg.sort, "page": page, "pageSize": self.cfg.page_size,
                      "group": "", "filter": flt}
            resp = self._get(params)
            body = resp.content
            try:
                records, total = parse_page_body(body, f"page {page}")
            except LandingError as exc:
                # keep the bad body for forensics, then fail the run
                landing.write_page(page, body, url=self.cfg.base_url, params=params,
                                   http_status=resp.status_code, record_count=-1)
                raise SourceError(str(exc)) from exc
            path = landing.write_page(page, body, url=self.cfg.base_url, params=params,
                                      http_status=resp.status_code, record_count=len(records))
            seen += len(records)
            self.log.info("page fetched", extra={"page": page, "records": len(records), "total": total,
                                                  "filter": flt})
            yield LandedPage(str(path), "", records, total)
            if len(records) < self.cfg.page_size or (total is not None and seen >= total):
                return
            if self.cfg.request_interval_s:
                self.sleep(self.cfg.request_interval_s)
        raise SourceError(
            f"max_pages={self.cfg.max_pages} reached before the result set was exhausted; "
            "increase max_pages or narrow the filter (no partial watermark advance)"
        )
