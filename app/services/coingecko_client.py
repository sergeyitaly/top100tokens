import aiohttp
import asyncio
import json
import logging
from typing import List, Dict, Any, Optional
from app.config import settings

logger = logging.getLogger(__name__)

class CoinGeckoClient:
    def __init__(self):
        self.base_url = settings.COINGECKO_API_BASE
        self.session = None

    async def __aenter__(self):
        self.session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()

    async def get_solana_tokens(self, per_page: int = 100) -> List[Dict[str, Any]]:
        """Get top Solana ecosystem tokens from CoinGecko API"""
        try:
            url = f"{self.base_url}/coins/markets"
            params = {
                'vs_currency': 'usd',
                'category': 'solana-ecosystem',
                'order': 'market_cap_desc',
                'per_page': per_page,
                'page': 1,
                'sparkline': 'false',  # Changed from False to 'false'
                'price_change_percentage': '24h'
            }
            
            logger.info(f"Fetching tokens from CoinGecko with params: {params}")
            
            async with self.session.get(url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    logger.info(f"Fetched {len(data)} tokens from CoinGecko")
                    return data
                else:
                    error_text = await response.text()
                    logger.error(f"CoinGecko API error: {response.status} - {error_text}")
                    return []
                    
        except Exception as e:
            logger.error(f"Error fetching from CoinGecko: {str(e)}")
            return []

    async def get_token_info(self, coin_id: str) -> Optional[Dict[str, Any]]:
        """Get detailed information for a specific token"""
        try:
            url = f"{self.base_url}/coins/{coin_id}"
            params = {
                'localization': 'false',  # Changed from False to 'false'
                'tickers': 'true',        # Changed from True to 'true'
                'market_data': 'true',    # Changed from True to 'true'
                'community_data': 'false', # Changed from False to 'false'
                'developer_data': 'false', # Changed from False to 'false'
                'sparkline': 'false'      # Changed from False to 'false'
            }
            
            async with self.session.get(url, params=params) as response:
                if response.status == 200:
                    return await response.json()
                return None
                
        except Exception as e:
            logger.error(f"Error fetching token info for {coin_id}: {str(e)}")
            return None