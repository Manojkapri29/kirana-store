from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SyncOperationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_op_id: str = Field(min_length=8, max_length=64)
    type: str = Field(max_length=20)
    created_at: datetime | None = None
    payload: dict[str, Any]


class SyncBatchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device_id: str | None = Field(default=None, max_length=64)
    operations: list[SyncOperationIn] = Field(min_length=1, max_length=50)
