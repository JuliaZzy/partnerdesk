"""One brand–distributor relationship, with the names the desk shows."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class Partnership(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    brand_id: str
    distributor_id: str
    brand_name: str
    distributor_name: str
    ship_to_country: str | None = None
    currency: str = "USD"
