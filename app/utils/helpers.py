import asyncio
import aiohttp
import logging
from typing import List, Dict, Any
from datetime import datetime

logger = logging.getLogger(__name__)

async def send_webhook(url: str, payload: Dict[str, Any], timeout: int = 30) -> bool:
    """Send data to webhook URL"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, 
                json=payload, 
                timeout=timeout,
                headers={'Content-Type': 'application/json'}
            ) as response:
                if response.status in [200, 201, 202]:
                    logger.info(f"Webhook sent successfully to {url}")
                    return True
                else:
                    logger.error(f"Webhook failed with status {response.status} for {url}")
                    return False
    except Exception as e:
        logger.error(f"Error sending webhook to {url}: {str(e)}")
        return False

async def retry_operation(operation, max_retries: int = 3, delay: float = 1.0):
    """Retry an operation with exponential backoff"""
    for attempt in range(max_retries):
        try:
            return await operation()
        except Exception as e:
            if attempt == max_retries - 1:
                raise e
            wait_time = delay * (2 ** attempt)
            logger.warning(f"Operation failed, retrying in {wait_time}s: {str(e)}")
            await asyncio.sleep(wait_time)