"""IRIS task 100 - MaStR large battery (>10 MW) delta pipeline.

Staging-only pipeline that extracts battery-storage units from the German
Marktstammdatenregister (MaStR), normalises power/energy/status/location,
and records new or changed projects above a power threshold into
``iris_staging.bess_pipeline_deltas``.
"""

__version__ = "1.0.0"
