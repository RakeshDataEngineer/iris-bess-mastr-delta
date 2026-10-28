"""Generate the synthetic test fixtures (deterministic).

ALL VALUES ARE SYNTHETIC / ILLUSTRATIVE. Unit IDs use the reserved-looking
range SEE9000000000xx and names start with "FIXTURE" so they can never be
confused with real MaStR units. The *shape* mirrors the public MaStR JSON
unit table: {"Data": [...], "Total": n, "AggregateResults": null, "Errors": null}.

Run:  python scripts/make_fixtures.py
"""

import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "mastr_json"


def ms(y, m, d):
    return f"/Date({int(datetime(y, m, d, tzinfo=timezone.utc).timestamp() * 1000)})/"


def unit(n, name, net_kw, *, gross_kw="same", kwh=None, status=35, state="Brandenburg",
         lat=52.40, lon=13.10, updated=(2026, 9, 1), tech="Lithium-Batterie", commissioned=(2025, 6, 1),
         plz="14467", ort="Fixturestadt"):
    rec = {
        "Id": 900000 + n,
        "MaStRNummer": f"SEE9000000000{n:02d}",
        "EinheitName": f"FIXTURE BESS {name}",
        "AnlagenbetreiberName": "FIXTURE Speicher GmbH",
        "EnergietraegerName": "Speicher",
        "TechnologieName": tech,
        "Nettonennleistung": net_kw,
        "Bruttoleistung": net_kw if gross_kw == "same" else gross_kw,
        "NutzbareSpeicherkapazitaet": kwh,
        "BetriebsStatusId": status,
        "BetriebsStatusName": {31: "In Planung", 35: "In Betrieb", 37: "Vorübergehend stillgelegt",
                               38: "Endgültig stillgelegt"}.get(status),
        "InbetriebnahmeDatum": ms(*commissioned) if commissioned else None,
        "EinheitMeldeDatum": ms(2025, 1, 15),
        "DatumLetzteAktualisierung": ms(*updated) if updated else None,
        "BundeslandName": state,
        "Plz": plz,
        "Ort": ort,
        "Breitengrad": lat,
        "Laengengrad": lon,
    }
    return rec


def page(records, total):
    return {"Data": records, "Total": total, "AggregateResults": None, "Errors": None}


def snapshot_1():
    a = unit(1, "Alpha", 50000, kwh=100000)
    b = unit(2, "Beta exactly 10 MW", 10000, kwh=20000)
    c = unit(3, "Gamma planned", 12500, kwh=None, status=31, commissioned=None)
    d = unit(4, "Delta pumped", 200000, kwh=1600000, tech="Pumpspeicher")
    e = unit(5, "Epsilon german string", "10.000,5", kwh="20.001,0")
    g = unit(6, "missing id", 30000, kwh=60000)
    g["MaStRNummer"] = None
    h = unit(7, "bad id", 30000, kwh=60000)
    h["MaStRNummer"] = "ABC123"
    i = unit(8, "bad power", "n/a", gross_kw=None, kwh=1000)
    j = unit(10, "Juliet gross only", None, gross_kw=15000, kwh=30000)
    k = unit(11, "Kilo no coords", 30000, kwh=60000, lat=None, lon=None)
    l = unit(12, "Lima just below", 9999, kwh=19998)
    m = unit(13, "Mike unknown tech", 20000, kwh=40000, tech=None, updated=(2026, 9, 5))
    n = unit(14, "November null island", 11000, kwh=22000, lat=0.0, lon=0.0, state="Bayern")
    o = unit(15, "Oscar watt not kw", 5000000000, kwh=10000000000)
    p1 = [a, b, c, d, e, g, h, i]
    p2 = [dict(a), j, k, l, m, n, o]  # dict(a): exact duplicate of Alpha across pages
    total = len(p1) + len(p2)
    return [page(p1, total), page(p2, total)]


def snapshot_2():
    a = unit(1, "Alpha", 50000, kwh=120000, updated=(2026, 10, 1))  # capacity revised
    c = unit(3, "Gamma planned", 12500, kwh=25000, status=35, commissioned=(2026, 9, 20),
             updated=(2026, 10, 2))  # commissioned
    b = unit(2, "Beta exactly 10 MW", 25000, kwh=50000, updated=(2026, 10, 3))  # upgraded -> enters scope
    k = unit(11, "Kilo no coords", 8000, kwh=16000, lat=None, lon=None, updated=(2026, 10, 4))  # leaves scope
    m_stale = unit(13, "Mike unknown tech", 99000, kwh=1, tech=None, updated=(2026, 8, 1))  # older than stored
    n = unit(14, "November null island", 11000, kwh=22000, lat=0.0, lon=0.0, state="Bayern",
             updated=(2026, 10, 5))  # only timestamp bumped
    p_old = unit(16, "Papa new", 40000, kwh=80000, status=31, commissioned=None, updated=(2026, 10, 6))
    p_new = unit(16, "Papa new", 45000, kwh=90000, status=31, commissioned=None, updated=(2026, 10, 7))
    recs = [a, b, c, k, m_stale, n, p_old, p_new]
    return [page(recs, len(recs))]


def write(name, pages):
    d = ROOT / name
    d.mkdir(parents=True, exist_ok=True)
    for i, pg in enumerate(pages, 1):
        (d / f"page_{i:04d}.json").write_text(json.dumps(pg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    write("snapshot_1", snapshot_1())
    write("snapshot_2", snapshot_2())
    print("fixtures written to", ROOT)
