from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import datetime

class TokenData(BaseModel):
    rank: int
    name: str
    symbol: str
    mint_address: Optional[str] = None
    market_cap: float = Field(..., description="Market cap in USD")
    volume_24h: float = Field(..., description="24h volume in USD")
    price: Optional[float] = None
    price_change_24h: Optional[float] = None
    solscan_url: Optional[str] = None
    coingecko_url: Optional[str] = None
    coingecko_id: Optional[str] = None
    birdeye_url: Optional[str] = None

class WebhookPayload(BaseModel):
    timestamp: datetime
    update_interval: int = Field(..., description="Update interval in seconds")
    total_tokens: int
    total_market_cap: float
    total_volume_24h: float
    tokens: List[TokenData]

class WebhookResponse(BaseModel):
    status: str
    message: str
    tokens_count: int
    last_updated: datetime