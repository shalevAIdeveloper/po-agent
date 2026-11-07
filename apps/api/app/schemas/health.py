from typing import Literal

from pydantic import BaseModel

from app.config import AppEnv


class HealthResponse(BaseModel):
    status: Literal["ok"]
    env: AppEnv
    version: str
