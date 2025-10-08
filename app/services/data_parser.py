import aiohttp
import asyncio
import logging
import os
import time
import json
import pickle
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from dotenv import load_dotenv

# Import your existing modules
try:
    from app.models.schemas import TokenData
    from app.services.coingecko_client import CoinGeckoClient
except ImportError:
    # Fallback for standalone testing
    from dataclasses import dataclass
    @dataclass
    class TokenData:
        rank: int
        name: str
        symbol: str
        mint_address: Optional[str]
        market_cap: float
        volume_24h: float
        price: float
        price_change_24h: float
        solscan_url: str
        coingecko_url: str
        coingecko_id: str
        birdeye_url: str
    
    class CoinGeckoClient:
        async def get_solana_tokens(self, per_page: int = 100):
            # Mock implementation for testing
            return []

load_dotenv()

logger = logging.getLogger(__name__)

class EnhancedRateLimiter:
    """Enhanced rate limiter with exponential backoff"""
    def __init__(self, max_requests: int = 35, time_window: int = 60, max_retries: int = 3):
        self.max_requests = max_requests
        self.time_window = time_window
        self.max_retries = max_retries
        self.requests = []
        self.lock = asyncio.Lock()
        self.retry_delays = [1, 5, 15]
    
    async def acquire(self):
        async with self.lock:
            now = time.time()
            self.requests = [req_time for req_time in self.requests 
                           if now - req_time < self.time_window]
            
            if len(self.requests) >= self.max_requests:
                oldest_request = min(self.requests)
                wait_time = self.time_window - (now - oldest_request) + 1
                if wait_time > 0:
                    logger.info(f"Rate limit approaching, waiting {wait_time:.2f}s")
                    await asyncio.sleep(wait_time)
                    now = time.time()
                    self.requests = [req_time for req_time in self.requests 
                                   if now - req_time < self.time_window]
            
            self.requests.append(now)
    
    async def make_request_with_retry(self, session, url, headers, params=None, retry_count=0):
        """Make request with exponential backoff retry logic"""
        try:
            await self.acquire()
            
            async with session.get(url, headers=headers, params=params, timeout=30) as response:
                if response.status == 200:
                    return await response.json()
                elif response.status == 429:
                    if retry_count < self.max_retries:
                        delay = self.retry_delays[retry_count]
                        logger.warning(f"Rate limited, retrying in {delay}s (attempt {retry_count + 1})")
                        await asyncio.sleep(delay)
                        return await self.make_request_with_retry(session, url, headers, params, retry_count + 1)
                    else:
                        logger.error(f"Max retries exceeded for {url}")
                        return None
                elif response.status == 401:
                    logger.error("API key invalid or missing")
                    return {"error": "API key invalid"}
                else:
                    logger.error(f"API request failed with status {response.status}")
                    return None
                    
        except asyncio.TimeoutError:
            logger.error(f"Request timeout for {url}")
            return None
        except Exception as e:
            logger.error(f"Request error: {str(e)}")
            return None

class DataParser:
    def __init__(self, cache_file: str = "token_cache.pkl", cache_ttl: int = 3600):
        self.coingecko_client = CoinGeckoClient()
        self.birdeye_api_key = os.getenv("BIRDEYE_API_KEY", "").strip()
        self.birdeye_base_url = "https://public-api.birdeye.so/defi"
        self.rate_limiter = EnhancedRateLimiter(max_requests=35, time_window=60)
        self.cache_file = cache_file
        self.cache_ttl = cache_ttl
        self.cache = self._load_cache()
        
        # Known token addresses for major tokens
        self.known_token_addresses = {
            "SOL": "So11111111111111111111111111111111111111112",
            "USDC": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
            "USDT": "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
            "BONK": "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
            "JUP": "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN",
            "PYTH": "HZ1JovNiVvGrGNiiYvEozEVgZ58xaU3RKwX8eACQBCt3",
            "JTO": "jtojtomepa8beP8AuQc6eXt5FriJwfFMwQx2v2f9mCL",
            "WIF": "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm",
            "BOME": "ukHH6c7mMyiWCf1b9pnWe25TSpkDDt3H5pQZgZ74J82",
            "POPCAT": "7GCBgCHZQfPwjQ1fjNsWQXUoroRc3bRsnqtQDpV3kzoc",
            "RAY": "4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R",
            "ORCA": "orcaEKTdK7LKz57vaAYr9QeNsVEPfiu6QeMU1kektZE",
            "MSOL": "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So",
            "JITOSOL": "J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kGCPn",
            "RENDER": "rndrizKT3MK1iimdxRdWabcF7Zg7AR5T4nud4EkHBof",
        }
        
        if self.birdeye_api_key:
            logger.info(f"BirdEye API key loaded: {self.birdeye_api_key[:8]}...")
        else:
            logger.warning("BIRDEYE_API_KEY not found")

    def _load_cache(self):
        """Load cache from file"""
        try:
            if os.path.exists(self.cache_file):
                with open(self.cache_file, 'rb') as f:
                    cache = pickle.load(f)
                    if datetime.now().timestamp() - cache.get('timestamp', 0) < self.cache_ttl:
                        logger.info("Loaded valid cache from disk")
                        return cache
                    else:
                        logger.info("Cache expired, will refresh")
        except Exception as e:
            logger.warning(f"Failed to load cache: {str(e)}")
        return {'timestamp': 0, 'mint_mapping': {}, 'tokens': []}
    
    def _save_cache(self):
        """Save cache to file"""
        try:
            with open(self.cache_file, 'wb') as f:
                pickle.dump(self.cache, f)
            logger.info("Cache saved to disk")
        except Exception as e:
            logger.warning(f"Failed to save cache: {str(e)}")

    async def get_top_tokens(self, limit: int = 100, use_cache: bool = True, force_refresh: bool = False) -> List[TokenData]:
        """Get top Solana tokens by parsing mint addresses from BirdEye API"""
        
        if use_cache and self._is_cache_valid() and not force_refresh:
            logger.info("Using cached token data")
            return self.cache.get('tokens', [])
        
        tokens = []
        
        async with self.coingecko_client as client:
            logger.info(f"Fetching top {limit} tokens from CoinGecko...")
            market_data = await client.get_solana_tokens(per_page=limit)
            
            if not market_data:
                logger.warning("No market data received from CoinGecko")
                return tokens
            
            logger.info(f"Fetched {len(market_data)} tokens from CoinGecko")
            
            # Get mint addresses from BirdEye API
            mint_mapping = await self._get_mint_addresses_from_birdeye(market_data)
            
            for idx, token_data in enumerate(market_data, 1):
                try:
                    token = await self._parse_token_data_with_mint(token_data, idx, mint_mapping)
                    if token:
                        tokens.append(token)
                    
                except Exception as e:
                    logger.error(f"Error parsing token {token_data.get('name', 'Unknown')}: {str(e)}")
                    continue
        
        logger.info(f"Successfully parsed {len(tokens)} tokens with mint addresses")
        
        # Update cache
        if use_cache:
            self.cache = {
                'timestamp': datetime.now().timestamp(),
                'mint_mapping': {token.symbol: token.mint_address for token in tokens},
                'tokens': tokens
            }
            self._save_cache()
        
        return tokens

    def _is_cache_valid(self) -> bool:
        """Check if cache is still valid"""
        return datetime.now().timestamp() - self.cache.get('timestamp', 0) < self.cache_ttl

    async def _get_mint_addresses_from_birdeye(self, market_data: List[Dict[str, Any]]) -> Dict[str, str]:
        """Get mint addresses by searching BirdEye API for each token"""
        mint_mapping = {}
        
        for token_data in market_data:
            symbol = token_data.get('symbol', '').upper()
            name = token_data.get('name', '')
            
            logger.info(f"Searching for mint address: {symbol} ({name})")
            
            # Try multiple methods to find mint address
            mint_address = await self._find_mint_address(symbol, name)
            mint_mapping[symbol] = mint_address
            
            if mint_address:
                logger.info(f"✅ Found mint address for {symbol}: {mint_address[:8]}...")
            else:
                logger.warning(f"❌ No mint address found for {symbol}")
        
        return mint_mapping

    async def _find_mint_address(self, symbol: str, name: str) -> Optional[str]:
        """Find mint address using multiple methods"""
        
        # Method 1: Check known addresses first
        if symbol in self.known_token_addresses:
            return self.known_token_addresses[symbol]
        
        # Method 2: Search BirdEye token list by symbol
        mint_address = await self._search_birdeye_by_symbol(symbol)
        if mint_address:
            return mint_address
        
        # Method 3: Search BirdEye token list by name
        if name:
            mint_address = await self._search_birdeye_by_name(name)
            if mint_address:
                return mint_address
        
        # Method 4: Get token metadata from popular tokens and try to match
        popular_tokens = await self._get_popular_tokens()
        for token in popular_tokens:
            token_symbol = token.get("symbol", "").upper()
            token_name = token.get("name", "").upper()
            
            if (token_symbol == symbol or 
                symbol in token_name or 
                token_symbol in name):
                return token.get("address")
        
        return None

    async def _search_birdeye_by_symbol(self, symbol: str) -> Optional[str]:
        """Search BirdEye token list by symbol"""
        if not self.birdeye_api_key:
            return None
            
        try:
            url = f"{self.birdeye_base_url}/v3/token/list"
            params = {
                "sort_by": "liquidity",
                "sort_type": "desc",
                "offset": 0,
                "limit": 10,
                "search": symbol
            }
            
            headers = self._get_headers()
            
            async with aiohttp.ClientSession() as session:
                data = await self.rate_limiter.make_request_with_retry(session, url, headers, params)
                
                if data and data.get("success") and "data" in data and "items" in data["data"]:
                    items = data["data"]["items"]
                    
                    # Try exact symbol match first
                    for item in items:
                        item_symbol = item.get("symbol", "").upper()
                        if item_symbol == symbol:
                            address = item.get("address")
                            if address:
                                return address
                    
                    # Try any match if no exact symbol match
                    if items:
                        address = items[0].get("address")
                        if address:
                            return address
                
                return None
                    
        except Exception as e:
            logger.error(f"Error searching for {symbol}: {str(e)}")
            return None

    async def _search_birdeye_by_name(self, name: str) -> Optional[str]:
        """Search BirdEye token list by name"""
        if not self.birdeye_api_key:
            return None
            
        try:
            url = f"{self.birdeye_base_url}/v3/token/list"
            params = {
                "sort_by": "liquidity",
                "sort_type": "desc",
                "offset": 0,
                "limit": 5,
                "search": name
            }
            
            headers = self._get_headers()
            
            async with aiohttp.ClientSession() as session:
                data = await self.rate_limiter.make_request_with_retry(session, url, headers, params)
                
                if data and data.get("success") and "data" in data and "items" in data["data"]:
                    items = data["data"]["items"]
                    if items:
                        address = items[0].get("address")
                        if address:
                            return address
                
                return None
                    
        except Exception as e:
            logger.error(f"Error searching for {name}: {str(e)}")
            return None

    async def _get_popular_tokens(self) -> List[Dict[str, Any]]:
        """Get popular tokens from BirdEye to use for matching"""
        if not self.birdeye_api_key:
            return []
            
        try:
            url = f"{self.birdeye_base_url}/v3/token/list"
            params = {
                "sort_by": "liquidity",
                "sort_type": "desc",
                "offset": 0,
                "limit": 50  # Get top 50 tokens by liquidity
            }
            
            headers = self._get_headers()
            
            async with aiohttp.ClientSession() as session:
                data = await self.rate_limiter.make_request_with_retry(session, url, headers, params)
                
                if data and data.get("success") and "data" in data and "items" in data["data"]:
                    items = data["data"]["items"]
                    tokens = []
                    
                    for item in items:
                        tokens.append({
                            "address": item.get("address"),
                            "symbol": item.get("symbol", ""),
                            "name": item.get("name", "")
                        })
                    
                    logger.info(f"Fetched {len(tokens)} popular tokens from BirdEye")
                    return tokens
                else:
                    return []
                    
        except Exception as e:
            logger.error(f"Error fetching popular tokens: {str(e)}")
            return []

    async def _get_token_metadata(self, mint_address: str) -> Optional[Dict[str, Any]]:
        """Get token metadata for a mint address using the meta endpoint"""
        if not self.birdeye_api_key or not mint_address:
            return None
            
        try:
            url = f"{self.birdeye_base_url}/v3/token/meta"
            params = {"address": mint_address}
            
            headers = self._get_headers()
            
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, headers=headers, timeout=10) as response:
                    if response.status == 200:
                        data = await response.json()
                        if data.get("success") and "data" in data:
                            return data["data"]
                    return None
                    
        except Exception as e:
            logger.error(f"Error fetching metadata for {mint_address[:8]}: {str(e)}")
            return None

    def _get_headers(self) -> Dict[str, str]:
        """Get the correct headers for BirdEye API"""
        headers = {
            "accept": "application/json",
            "x-chain": "solana"
        }
        
        if self.birdeye_api_key:
            headers["X-API-KEY"] = self.birdeye_api_key
            
        return headers

    async def _parse_token_data_with_mint(self, data: Dict[str, Any], rank: int, mint_mapping: Dict[str, str]) -> Optional[TokenData]:
        """Parse token data with mint address"""
        try:
            name = data.get('name', '')
            symbol = data.get('symbol', '').upper()
            coin_id = data.get('id', '')
            
            # Market data from CoinGecko
            market_cap = data.get('market_cap', 0) or 0
            volume_24h = data.get('total_volume', 0) or 0
            current_price = data.get('current_price', 0) or 0
            price_change_24h = data.get('price_change_percentage_24h', 0) or 0
            
            # Get mint address from mapping
            mint_address = mint_mapping.get(symbol)
            
            # Generate URLs
            solscan_url = self._generate_solscan_url(mint_address, symbol)
            coingecko_url = f"https://www.coingecko.com/en/coins/{coin_id}"
            birdeye_url = self._generate_birdeye_url(mint_address, symbol)
            
            return TokenData(
                rank=rank,
                name=name,
                symbol=symbol,
                mint_address=mint_address,
                market_cap=market_cap,
                volume_24h=volume_24h,
                price=current_price,
                price_change_24h=price_change_24h,
                solscan_url=solscan_url,
                coingecko_url=coingecko_url,
                coingecko_id=coin_id,
                birdeye_url=birdeye_url
            )
            
        except Exception as e:
            logger.error(f"Error in _parse_token_data_with_mint: {str(e)}")
            return None

    def _generate_solscan_url(self, mint_address: str, symbol: str) -> str:
        if mint_address:
            return f"https://solscan.io/token/{mint_address}"
        else:
            return f"https://solscan.io/token/search?keyword={symbol}"

    def _generate_birdeye_url(self, mint_address: str, symbol: str) -> str:
        if mint_address:
            return f"https://birdeye.so/token/{mint_address}?chain=solana"
        else:
            return f"https://birdeye.so/search?q={symbol}"

    def calculate_totals(self, tokens: List[TokenData]) -> Dict[str, float]:
        """Calculate total market cap and volume"""
        if not tokens:
            return {"total_market_cap": 0, "total_volume_24h": 0}
            
        total_market_cap = sum(token.market_cap for token in tokens)
        total_volume = sum(token.volume_24h for token in tokens)
        
        return {
            "total_market_cap": total_market_cap,
            "total_volume_24h": total_volume
        }

    def clear_cache(self):
        """Clear the cache"""
        self.cache = {'timestamp': 0, 'mint_mapping': {}, 'tokens': []}
        try:
            if os.path.exists(self.cache_file):
                os.remove(self.cache_file)
                logger.info("Cache cleared")
        except Exception as e:
            logger.warning(f"Error clearing cache file: {str(e)}")

    async def refresh_cache(self, limit: int = 100):
        """Force refresh the cache"""
        logger.info("Forcing cache refresh")
        self.clear_cache()
        return await self.get_top_tokens(limit=limit, use_cache=True, force_refresh=True)

    async def get_token_holders(self, mint_address: str, limit: int = 50) -> Optional[Dict[str, Any]]:
        """Get token holders from BirdEye API"""
        if not self.birdeye_api_key or not mint_address:
            return None
            
        try:
            await self.rate_limiter.acquire()
            
            url = f"{self.birdeye_base_url}/v3/token/holder"
            params = {
                "address": mint_address,
                "offset": 0,
                "limit": limit,
                "ui_amount_mode": "scaled"
            }
            
            headers = self._get_headers()
            
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, params=params, timeout=30) as response:
                    if response.status == 200:
                        data = await response.json()
                        if data and data.get("success") and "data" in data and "items" in data["data"]:
                            return self._process_holder_data(data["data"]["items"])
                    
                    return None
                    
        except Exception as e:
            logger.error(f"Error fetching holders for {mint_address[:8]}: {str(e)}")
            return None

    def _process_holder_data(self, items: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Process holder data and calculate statistics"""
        if not items:
            return {"holders": [], "stats": {}}
        
        holders = []
        total_supply = 0
        
        for item in items:
            holder_data = {
                "owner": item.get("owner"),
                "ui_amount": item.get("ui_amount", 0),
                "amount": item.get("amount", "0"),
                "decimals": item.get("decimals", 0),
                "token_account": item.get("token_account")
            }
            holders.append(holder_data)
            total_supply += holder_data["ui_amount"]
        
        # Calculate statistics
        stats = {
            "total_holders": len(holders),
            "total_supply": total_supply,
            "largest_balance": max([h["ui_amount"] for h in holders]) if holders else 0,
            "average_balance": total_supply / len(holders) if holders else 0,
        }
        
        # Calculate percentages
        for holder in holders:
            if total_supply > 0:
                holder["percentage"] = (holder["ui_amount"] / total_supply) * 100
            else:
                holder["percentage"] = 0
        
        return {
            "holders": holders,
            "stats": stats
        }

# Test the API endpoints
async def test_birdeye_endpoints():
    """Test the BirdEye API endpoints"""
    parser = DataParser()
    
    print("Testing BirdEye API endpoints...")
    
    # Test token list endpoint
    url = f"{parser.birdeye_base_url}/v3/token/list"
    params = {
        "sort_by": "liquidity",
        "sort_type": "desc",
        "offset": 0,
        "limit": 5
    }
    
    headers = parser._get_headers()
    
    print(f"Using API key: {parser.birdeye_api_key[:8]}...")
    
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers, params=params, timeout=30) as response:
            print(f"Token list endpoint - Status: {response.status}")
            if response.status == 200:
                data = await response.json()
                print("✅ Token list endpoint works!")
                if data.get("success") and "data" in data and "items" in data["data"]:
                    items = data["data"]["items"]
                    for i, item in enumerate(items[:3]):
                        print(f"  {i+1}. {item.get('symbol')} - {item.get('name')} - {item.get('address')[:8]}...")
                return True
            else:
                print(f"❌ Token list endpoint failed. Status: {response.status}")
                print(f"Response: {await response.text()}")
                return False

async def test_mint_parsing():
    """Test the mint address parsing functionality"""
    parser = DataParser()
    
    print("Testing mint address parsing from BirdEye API...")
    
    # Test with force refresh to bypass cache
    tokens = await parser.get_top_tokens(limit=10, force_refresh=True)
    
    print(f"Found {len(tokens)} tokens:")
    for token in tokens:
        status = "✅" if token.mint_address else "❌"
        print(f"  {status} {token.symbol}: {token.mint_address or 'No mint address'}")
    
    return tokens

if __name__ == "__main__":
    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # Test API endpoints first
    asyncio.run(test_birdeye_endpoints())
    
    # Then test mint parsing
    asyncio.run(test_mint_parsing())