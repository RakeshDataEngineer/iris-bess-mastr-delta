"""Alternative source: the official MaStR bulk export ("Gesamtdatenexport").

Download: https://www.marktstammdatenregister.de/MaStR/Datendownload (daily,
DL-DE-BY-2.0). Unzip and pass the storage files:

* ``EinheitenStromSpeicher*.xml`` - storage *units* (power, status, location)
* ``AnlagenStromSpeicher*.xml``   - storage *installations* (usable capacity kWh)

Units link to installations via ``SpeMastrNummer`` -> ``MaStRNummer``.
Files are large and usually UTF-16; ``iterparse`` streams them and honours
the XML encoding declaration/BOM.

The bulk path is a full snapshot (no incremental filter), useful for
back-filling and for capacity (MWh), which the public table may not expose.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Iterator


def _iter_rows(path: Path, row_tag: str) -> Iterator[dict]:
    for _, elem in ET.iterparse(path, events=("end",)):
        if elem.tag == row_tag:
            yield {child.tag: (child.text or "").strip() for child in elem}
            elem.clear()


def _files(paths: list[Path], prefix: str) -> list[Path]:
    out: list[Path] = []
    for p in paths:
        if p.is_dir():
            out.extend(sorted(p.glob(f"{prefix}*.xml")))
        elif p.name.startswith(prefix):
            out.append(p)
    return sorted(out)


def iter_bulk_storage_units(paths: list[str | Path]) -> Iterator[dict]:
    ps = [Path(p) for p in paths]
    unit_files = _files(ps, "EinheitenStromSpeicher")
    if not unit_files:
        raise FileNotFoundError("no EinheitenStromSpeicher*.xml found in the given paths")
    capacity: dict[str, str] = {}
    for f in _files(ps, "AnlagenStromSpeicher"):
        for row in _iter_rows(f, "AnlageStromSpeicher"):
            if row.get("MaStRNummer"):
                capacity[row["MaStRNummer"]] = row.get("NutzbareSpeicherkapazitaet", "")
    for f in unit_files:
        for row in _iter_rows(f, "EinheitStromSpeicher"):
            spe = row.get("SpeMastrNummer")
            if spe and spe in capacity and "NutzbareSpeicherkapazitaet" not in row:
                row["NutzbareSpeicherkapazitaet"] = capacity[spe]
            yield row
