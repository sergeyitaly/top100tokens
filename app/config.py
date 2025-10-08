import os
from dotenv import load_dotenv

load_dotenv()

class Settings:
    PROJECT_NAME = "Solana Top Tokens Webhook"
    VERSION = "1.0.0"
    API_V1_STR = "/api/v1"
    
    # Update interval in seconds (5 minutes)
    UPDATE_INTERVAL = 300
    
    # API endpoints
    COINGECKO_API_BASE = "https://api.coingecko.com/api/v3"
    COINGECKO_SOLANA_ECOSYSTEM_URL = "https://www.coingecko.com/en/categories/solana-ecosystem"
    
    # Rate limiting
    REQUEST_DELAY = 1  # seconds between requests
    
    # Webhook settings
    MAX_WEBHOOK_RETRIES = 3
    WEBHOOK_TIMEOUT = 30

settings = Settings()