"""Mirrors the BrokerStatus slice of dashboard/src/types/index.ts."""

from pydantic import BaseModel


class BrokerStatus(BaseModel):
    connected: bool
    name: str
    latency: float
    lastPing: str
    lastSync: str
    ordersToday: int
    apiCalls: int
