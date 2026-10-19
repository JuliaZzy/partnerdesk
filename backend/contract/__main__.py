"""JSON extraction CLI: python -m backend.contract <file> --brand … --partnership …"""

from __future__ import annotations

from .ingest import main

raise SystemExit(main())
