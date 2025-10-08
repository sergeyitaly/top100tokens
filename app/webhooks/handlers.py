import asyncio
import logging
from typing import List, Dict, Any, Set
from datetime import datetime
from app.models.schemas import WebhookPayload, TokenData
from app.utils.helpers import send_webhook, retry_operation

logger = logging.getLogger(__name__)

class WebhookManager:
    def __init__(self):
        self.registered_webhooks: Set[str] = set()
        self.last_sent_data: Dict[str, Any] = {}

    def register_webhook(self, url: str) -> bool:
        """Register a new webhook URL"""
        if url in self.registered_webhooks:
            logger.warning(f"Webhook already registered: {url}")
            return False
        
        self.registered_webhooks.add(url)
        logger.info(f"Registered new webhook: {url}")
        return True

    def unregister_webhook(self, url: str) -> bool:
        """Remove a webhook URL"""
        if url in self.registered_webhooks:
            self.registered_webhooks.remove(url)
            logger.info(f"Unregistered webhook: {url}")
            return True
        return False

    def get_registered_webhooks(self) -> List[str]:
        """Get all registered webhook URLs"""
        return list(self.registered_webhooks)

    async def broadcast_update(self, tokens: List[TokenData], update_interval: int) -> Dict[str, Any]:
        """Broadcast token data to all registered webhooks"""
        if not self.registered_webhooks:
            logger.info("No webhooks registered, skipping broadcast")
            return {"sent": 0, "failed": 0}

        # Prepare payload
        totals = self._calculate_totals(tokens)
        payload = WebhookPayload(
            timestamp=datetime.now(),
            update_interval=update_interval,
            total_tokens=len(tokens),
            total_market_cap=totals["total_market_cap"],
            total_volume_24h=totals["total_volume_24h"],
            tokens=tokens
        )

        # Convert to dict for JSON serialization
        payload_dict = payload.model_dump()

        # Send to all webhooks
        results = await asyncio.gather(
            *[self._send_to_webhook(url, payload_dict) for url in self.registered_webhooks],
            return_exceptions=True
        )

        # Count results
        successful = sum(1 for result in results if result is True)
        failed = len(results) - successful

        logger.info(f"Webhook broadcast completed: {successful} successful, {failed} failed")
        
        # Store last sent data
        self.last_sent_data = {
            "timestamp": datetime.now(),
            "payload": payload_dict
        }

        return {"sent": successful, "failed": failed}

    def _calculate_totals(self, tokens: List[TokenData]) -> Dict[str, float]:
        """Calculate total market cap and volume"""
        total_market_cap = sum(token.market_cap for token in tokens)
        total_volume = sum(token.volume_24h for token in tokens)
        
        return {
            "total_market_cap": total_market_cap,
            "total_volume_24h": total_volume
        }

    async def _send_to_webhook(self, url: str, payload: Dict[str, Any]) -> bool:
        """Send data to a specific webhook with retry logic"""
        async def send_operation():
            return await send_webhook(url, payload)
        
        try:
            return await retry_operation(send_operation, max_retries=3, delay=2.0)
        except Exception as e:
            logger.error(f"Failed to send webhook to {url} after retries: {str(e)}")
            return False

# Global webhook manager instance
webhook_manager = WebhookManager()