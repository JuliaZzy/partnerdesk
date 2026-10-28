"""Column types shared by every desk table."""

from __future__ import annotations

from datetime import datetime
from typing import Any, ClassVar

from sqlalchemy import DateTime
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase

# JSONB, not JSON: it is stored parsed, so it can be indexed and queried by key.
JSONCol = JSONB()
# `timezone=True` makes the column `timestamptz` rather than a wall-clock `timestamp`,
# so a value carries the offset it was written with.
TimestampCol = DateTime(timezone=True)


class Base(DeclarativeBase):
    type_annotation_map: ClassVar = {
        dict[str, Any]: JSONCol, list[str]: JSONCol, list[dict[str, Any]]: JSONCol, datetime: TimestampCol,
    }
