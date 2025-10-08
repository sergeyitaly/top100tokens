from fastapi import FastAPI, BackgroundTasks, HTTPException, Query
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
import asyncio
import logging
import json
import os
from typing import List, Optional
from datetime import datetime
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.models.schemas import TokenData, WebhookResponse, WebhookPayload
from app.services.data_parser import DataParser
from app.webhooks.handlers import webhook_manager

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# Global variables
token_cache: List[TokenData] = []
last_update: Optional[datetime] = None
update_task: Optional[asyncio.Task] = None
DATA_FILE = "tokens_data.json"

async def update_token_data():
    """Background task to update token data periodically with enhanced error handling"""
    global token_cache, last_update
    
    parser = DataParser()
    consecutive_errors = 0
    max_consecutive_errors = 3
    
    while True:
        try:
            logger.info("🔄 Starting token data update...")
            
            # Get fresh token data with force refresh to bypass cache
            tokens = await parser.get_top_tokens(limit=100, force_refresh=True)
            
            if tokens:
                # Update global cache
                token_cache = tokens
                last_update = datetime.now()
                consecutive_errors = 0  # Reset error counter on success
                
                # Log update statistics
                tokens_with_mint = sum(1 for token in tokens if token.mint_address)
                total_market_cap = sum(token.market_cap for token in tokens)
                
                logger.info(f"✅ Token data updated successfully. "
                          f"{len(tokens)} tokens total, {tokens_with_mint} with mint addresses, "
                          f"${total_market_cap:,.0f} total market cap")
                
                # Save to local JSON file
                try:
                    await save_tokens_to_json(tokens)
                    logger.info("💾 Token data saved to JSON file")
                except Exception as e:
                    logger.error(f"❌ Failed to save tokens to JSON: {str(e)}")
                
                # Broadcast to webhooks
                try:
                    await webhook_manager.broadcast_update(tokens, settings.UPDATE_INTERVAL)
                    logger.info("🌐 Webhook broadcast completed")
                except Exception as e:
                    logger.error(f"❌ Webhook broadcast failed: {str(e)}")
                    
            else:
                consecutive_errors += 1
                logger.warning(f"⚠️ No tokens fetched in update cycle (error {consecutive_errors}/{max_consecutive_errors})")
                
                if consecutive_errors >= max_consecutive_errors:
                    logger.error("🚨 Too many consecutive errors, attempting to clear cache and retry...")
                    parser.clear_cache()
                    consecutive_errors = 0
                
        except asyncio.CancelledError:
            logger.info("🛑 Update task cancelled")
            break
        except Exception as e:
            consecutive_errors += 1
            logger.error(f"❌ Error in update cycle: {str(e)}")
            
            if consecutive_errors >= max_consecutive_errors:
                logger.error("🚨 Too many consecutive errors, attempting to clear cache and retry...")
                parser.clear_cache()
                consecutive_errors = 0
            
            logger.info("🔄 Retrying after error...")
        
        # Wait for next update
        logger.info(f"⏰ Waiting {settings.UPDATE_INTERVAL} seconds until next update...")
        await asyncio.sleep(settings.UPDATE_INTERVAL)
        
async def save_tokens_to_json(tokens: List[TokenData]):
    """Save token data to local JSON file"""
    try:
        data = {
            "last_updated": datetime.now().isoformat(),
            "total_tokens": len(tokens),
            "total_market_cap": sum(token.market_cap for token in tokens),
            "total_volume_24h": sum(token.volume_24h for token in tokens),
            "tokens": [token.model_dump() for token in tokens]
        }
        
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        
        logger.info(f"Token data saved to {DATA_FILE}")
    except Exception as e:
        logger.error(f"Error saving tokens to JSON: {str(e)}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup - fetch data immediately
    logger.info("Starting background update task...")
    global update_task
    
    # Create an immediate first fetch
    async def initial_fetch():
        parser = DataParser()
        tokens = await parser.get_top_tokens(limit=100)
        if tokens:
            global token_cache, last_update
            token_cache = tokens
            last_update = datetime.now()
            await save_tokens_to_json(tokens)
            logger.info(f"Initial data fetch: {len(tokens)} tokens")
    
    # Run initial fetch
    await initial_fetch()
    
    # Start periodic updates
    update_task = asyncio.create_task(update_token_data())
    
    yield
    
    # Shutdown
    if update_task:
        update_task.cancel()
        try:
            await update_task
        except asyncio.CancelledError:
            pass
    logger.info("Background task stopped")

app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    lifespan=lifespan
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins
    allow_credentials=True,
    allow_methods=["*"],  # Allows all methods
    allow_headers=["*"],  # Allows all headers
)
# Mount static files directory
os.makedirs("static", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
async def root():
    return {
        "message": "Solana Top Tokens Webhook Service",
        "version": settings.VERSION,
        "status": "running",
        "last_update": last_update,
        "registered_webhooks": len(webhook_manager.get_registered_webhooks()),
        "endpoints": {
            "ui": "/ui",
            "tokens_json": "/tokens/json",
            "download_json": "/tokens/download",
            "tokens_api": "/tokens",
            "health": "/health"
        }
    }

@app.get("/ui", response_class=HTMLResponse)
async def serve_ui():
    """Serve a simple HTML UI to display the tokens"""
    html_content = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Solana Top 100 Tokens</title>
    <style>
        * { box-sizing: border-box; }
        body { 
            font-family: Arial, sans-serif; 
            margin: 0; 
            padding: 20px; 
            background: #f5f5f5; 
        }
        .container { 
            max-width: 1600px; 
            margin: 0 auto; 
            background: white; 
            padding: 20px; 
            border-radius: 8px; 
            box-shadow: 0 2px 10px rgba(0,0,0,0.1); 
        }
        h1 { 
            color: #333; 
            text-align: center; 
            margin-bottom: 30px;
        }
        h3 {
            color: #333;
            margin-bottom: 15px;
        }
        .status { 
            padding: 15px; 
            margin: 20px 0; 
            border-radius: 5px; 
            text-align: center;
        }
        .status.loading { 
            background: #fff3cd; 
            color: #856404; 
            border: 1px solid #ffeaa7;
        }
        .status.success { 
            background: #d1ecf1; 
            color: #0c5460; 
            border: 1px solid #bee5eb;
            display: none;
        }
        .status.error { 
            background: #f8d7da; 
            color: #721c24; 
            border: 1px solid #f5c6cb;
            display: none;
        }
        .stats { 
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 15px;
            margin: 20px 0; 
        }
        .stat-item { 
            text-align: center; 
            padding: 15px;
            background: #f8f9fa;
            border-radius: 5px;
            border: 1px solid #e9ecef;
        }
        .stat-value { 
            font-size: 1.2em; 
            font-weight: bold; 
            color: #007bff; 
            margin-top: 5px;
        }
        .table-container { 
            overflow-x: auto; 
            margin-top: 20px;
        }
        table { 
            width: 100%; 
            border-collapse: collapse;
            font-size: 14px;
        }
        th, td { 
            padding: 12px; 
            text-align: left; 
            border-bottom: 1px solid #ddd; 
        }
        th { 
            background: #007bff; 
            color: white; 
            position: sticky; 
            top: 0; 
            font-weight: 600;
        }
        tr:hover { 
            background: #f8f9fa; 
        }
        .positive { 
            color: #28a745; 
            font-weight: bold;
        }
        .negative { 
            color: #dc3545; 
            font-weight: bold;
        }
        .rank { 
            font-weight: bold; 
            text-align: center; 
            width: 60px;
        }
        .actions { 
            display: flex;
            gap: 10px;
            justify-content: center;
            margin: 20px 0;
            flex-wrap: wrap;
        }
        .btn { 
            padding: 10px 20px; 
            background: #007bff; 
            color: white; 
            border: none; 
            border-radius: 4px; 
            cursor: pointer; 
            text-decoration: none; 
            display: inline-block;
            font-size: 14px;
        }
        .btn:hover { 
            background: #0056b3; 
        }
        .btn.refresh { 
            background: #28a745; 
        }
        .btn.refresh:hover { 
            background: #1e7e34; 
        }
        .btn.secondary {
            background: #6c757d;
        }
        .btn.secondary:hover {
            background: #545b62;
        }
        .token-symbol {
            background: #e9ecef;
            padding: 2px 6px;
            border-radius: 3px;
            font-family: monospace;
            font-size: 12px;
        }
        .contract-address {
            font-family: monospace;
            font-size: 11px;
            color: #666;
            background: #f8f9fa;
            padding: 4px 6px;
            border-radius: 3px;
            word-break: break-all;
            max-width: 200px;
            cursor: pointer;
        }
        .contract-address:hover {
            background: #e9ecef;
        }
        .links a {
            margin-right: 8px;
            color: #007bff;
            text-decoration: none;
            font-size: 12px;
        }
        .links a:hover {
            text-decoration: underline;
        }
        .copy-btn {
            background: none;
            border: none;
            color: #6c757d;
            cursor: pointer;
            font-size: 12px;
            margin-left: 5px;
        }
        .copy-btn:hover {
            color: #007bff;
        }
        .tooltip {
            position: relative;
            display: inline-block;
        }
        .tooltip .tooltiptext {
            visibility: hidden;
            width: 140px;
            background-color: #555;
            color: #fff;
            text-align: center;
            border-radius: 6px;
            padding: 5px;
            position: absolute;
            z-index: 1;
            bottom: 125%;
            left: 50%;
            margin-left: -70px;
            opacity: 0;
            transition: opacity 0.3s;
            font-size: 12px;
        }
        .tooltip:hover .tooltiptext {
            visibility: visible;
            opacity: 1;
        }
        
        /* Sortable headers */
        th[data-column] {
            cursor: pointer;
            user-select: none;
            position: relative;
            transition: background-color 0.2s ease;
        }
        th[data-column]:hover {
            background: #0056b3 !important;
        }
        .sort-asc::after {
            content: " ↑";
            font-weight: bold;
        }
        .sort-desc::after {
            content: " ↓";
            font-weight: bold;
        }
        th[data-column]::before {
            content: "↕";
            opacity: 0.5;
            margin-right: 5px;
            font-size: 12px;
        }
        th.sort-asc::before,
        th.sort-desc::before {
            opacity: 1;
        }
        
        /* Holder Section Styles */
        .holders-section {
            margin-top: 30px;
            background: #f8f9fa;
            padding: 20px;
            border-radius: 8px;
            border: 1px solid #e9ecef;
        }
        .holder-stats {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 15px;
            margin-bottom: 20px;
        }
        .holders-container {
            margin-top: 15px;
        }
        #holders-table {
            font-size: 13px;
        }
        #holders-table th, #holders-table td {
            padding: 8px 12px;
        }
        .wallet-address {
            font-family: monospace;
            font-size: 11px;
            background: #fff;
            padding: 4px 6px;
            border-radius: 3px;
            cursor: pointer;
            border: 1px solid #dee2e6;
        }
        .wallet-address:hover {
            background: #e9ecef;
        }
        .percentage-bar {
            background: #e9ecef;
            border-radius: 10px;
            height: 8px;
            margin-top: 4px;
            overflow: hidden;
        }
        .percentage-fill {
            background: #007bff;
            height: 100%;
            border-radius: 10px;
            transition: width 0.3s ease;
        }
        .token-row {
            cursor: pointer;
        }
        .token-row:hover {
            background: #f0f8ff !important;
        }
        .holders-loading {
            text-align: center;
            padding: 40px;
            color: #6c757d;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>🚀 Solana Top 100 Tokens</h1>
        
        <div class="actions">
            <button class="btn refresh" onclick="loadTokenData()">🔄 Refresh Data</button>
            <a href="/tokens/download" class="btn">📥 Download JSON</a>
            <a href="/tokens/with-holders/json" target="_blank" class="btn secondary">🔗 View JSON with Holders</a>
            <a href="/tokens/json" target="_blank" class="btn secondary">🔗 View Raw JSON</a>
            <a href="/" class="btn secondary">🏠 API Home</a>
        </div>

        <!-- Status Messages -->
        <div id="status-loading" class="status loading">
            ⏳ Loading token data...
        </div>
        
        <div id="status-success" class="status success">
            ✅ Data loaded successfully! <span id="last-updated-text"></span>
        </div>
        
        <div id="status-error" class="status error">
            ❌ Error loading data. Check console for details.
        </div>

        <!-- Statistics -->
        <div id="stats" class="stats" style="display: none;">
            <div class="stat-item">
                <div>Total Tokens</div>
                <div id="total-tokens" class="stat-value">0</div>
            </div>
            <div class="stat-item">
                <div>Total Market Cap</div>
                <div id="total-market-cap" class="stat-value">$0</div>
            </div>
            <div class="stat-item">
                <div>Total 24h Volume</div>
                <div id="total-volume" class="stat-value">$0</div>
            </div>
            <div class="stat-item">
                <div>Last Updated</div>
                <div id="last-updated" class="stat-value">-</div>
            </div>
        </div>

        <!-- Tokens Table -->
        <div class="table-container">
            <table id="tokens-table">
                <thead>
                    <tr>
                        <th class="rank" data-column="rank">#</th>
                        <th data-column="name">Token</th>
                        <th>Contract Address</th>
                        <th data-column="price">Price</th>
                        <th data-column="price_change_24h">24h Change</th>
                        <th data-column="market_cap">Market Cap</th>
                        <th data-column="volume_24h">24h Volume</th>
                        <th>Links</th>
                    </tr>
                </thead>
                <tbody id="tokens-body">
                    <tr>
                        <td colspan="8" style="text-align: center; padding: 40px;">
                            No token data available. Click "Refresh Data" to load.
                        </td>
                    </tr>
                </tbody>
            </table>
        </div>

        <!-- Holders Section -->
        <div class="holders-section" style="display: none;" id="holders-section">
            <h3>💰 Top Token Holders - <span id="selected-token-name">Select a token</span></h3>
            
            <div class="holder-stats" id="holder-stats" style="display: none;">
                <div class="stat-item">
                    <div>Total Holders</div>
                    <div id="total-holders" class="stat-value">0</div>
                </div>
                <div class="stat-item">
                    <div>Largest Holder</div>
                    <div id="largest-holder" class="stat-value">0</div>
                </div>
                <div class="stat-item">
                    <div>Average Balance</div>
                    <div id="avg-balance" class="stat-value">0</div>
                </div>
            </div>
            
            <div class="holders-container">
                <div class="table-container">
                    <table id="holders-table">
                        <thead>
                            <tr>
                                <th>Rank</th>
                                <th>Wallet Address</th>
                                <th>Balance</th>
                                <th>Percentage</th>
                                <th>Actions</th>
                            </tr>
                        </thead>
                        <tbody id="holders-body">
                            <tr>
                                <td colspan="5" style="text-align: center; padding: 20px;">
                                    Click on a token row to view its top holders
                                </td>
                            </tr>
                        </tbody>
                    </table>
                </div>
            </div>
        </div>
    </div>

    <script>
        let currentSort = { column: 'rank', direction: 'asc' };
        let currentTokens = [];
        let currentTokenAddress = null;
        let currentTokenSymbol = null;

        // Sorting functions
        function sortTokens(column) {
            const direction = currentSort.column === column && currentSort.direction === 'asc' ? 'desc' : 'asc';
            currentSort = { column, direction };
            
            const sortedTokens = [...currentTokens].sort((a, b) => {
                let aValue = a[column];
                let bValue = b[column];
                
                // Handle special cases for different columns
                switch(column) {
                    case 'rank':
                        aValue = a.rank || 999;
                        bValue = b.rank || 999;
                        break;
                    case 'name':
                        aValue = (a.name || '').toLowerCase();
                        bValue = (b.name || '').toLowerCase();
                        break;
                    case 'symbol':
                        aValue = (a.symbol || '').toLowerCase();
                        bValue = (b.symbol || '').toLowerCase();
                        break;
                    case 'price_change_24h':
                        aValue = a.price_change_24h || 0;
                        bValue = b.price_change_24h || 0;
                        break;
                    case 'market_cap':
                        aValue = a.market_cap || 0;
                        bValue = b.market_cap || 0;
                        break;
                    case 'volume_24h':
                        aValue = a.volume_24h || 0;
                        bValue = b.volume_24h || 0;
                        break;
                    default:
                        aValue = aValue || '';
                        bValue = bValue || '';
                }
                
                if (direction === 'asc') {
                    return aValue > bValue ? 1 : -1;
                } else {
                    return aValue < bValue ? 1 : -1;
                }
            });
            
            displayTokensTable(sortedTokens);
            updateSortIndicators(column, direction);
        }

        function updateSortIndicators(column, direction) {
            // Remove all sort indicators
            document.querySelectorAll('th[data-column]').forEach(th => {
                th.classList.remove('sort-asc', 'sort-desc');
                th.innerHTML = th.innerHTML.replace(' ↑', '').replace(' ↓', '');
            });
            
            // Add indicator to current sort column
            const header = document.querySelector(`th[data-column="${column}"]`);
            if (header) {
                const indicator = direction === 'asc' ? ' ↑' : ' ↓';
                header.innerHTML += indicator;
                header.classList.add(direction === 'asc' ? 'sort-asc' : 'sort-desc');
            }
        }

        function setupSortableHeaders() {
            const headers = document.querySelectorAll('#tokens-table th[data-column]');
            headers.forEach(header => {
                header.style.cursor = 'pointer';
                header.title = 'Click to sort';
                header.addEventListener('click', (e) => {
                    e.stopPropagation();
                    const column = header.getAttribute('data-column');
                    sortTokens(column);
                });
            });
        }

        function displayTokensTable(tokens) {
            currentTokens = tokens; // Store tokens for sorting
            
            const tbody = document.getElementById('tokens-body');
            tbody.innerHTML = '';
            
            tokens.forEach(token => {
                const changeClass = token.price_change_24h >= 0 ? 'positive' : 'negative';
                const changeSymbol = token.price_change_24h >= 0 ? '↗' : '↘';
                const changeText = token.price_change_24h ? 
                    `${changeSymbol} ${Math.abs(token.price_change_24h).toFixed(2)}%` : 'N/A';
                
                const contractAddress = token.mint_address || token.contract_address;
                const displayAddress = formatContractAddress(contractAddress);
                
                const row = document.createElement('tr');
                row.className = 'token-row';
                row.setAttribute('data-symbol', token.symbol);
                row.setAttribute('data-address', contractAddress);
                row.setAttribute('data-name', token.name);
                
                row.innerHTML = `
                    <td class="rank">${token.rank || '?'}</td>
                    <td>
                        <strong>${token.name || 'Unknown Token'}</strong><br>
                        <span class="token-symbol">${token.symbol || 'N/A'}</span>
                    </td>
                    <td>
                        ${contractAddress ? `
                            <div class="tooltip">
                                <span class="contract-address" onclick="event.stopPropagation(); copyToClipboard('${contractAddress}')">
                                    ${displayAddress}
                                </span>
                                <span class="tooltiptext">Click to copy full address</span>
                            </div>
                        ` : 'N/A'}
                    </td>
                    <td><strong>${formatPrice(token.price)}</strong></td>
                    <td class="${changeClass}">${changeText}</td>
                    <td>$` + formatNumber(token.market_cap) + `</td>
                    <td>$` + formatNumber(token.volume_24h) + `</td>
                    <td class="links">
                        ${token.solscan_url ? `<a href="${token.solscan_url}" target="_blank" title="View on Solscan" onclick="event.stopPropagation();">Solscan</a>` : ''}
                        ${token.coingecko_url ? `<a href="${token.coingecko_url}" target="_blank" title="View on CoinGecko" onclick="event.stopPropagation();">CoinGecko</a>` : ''}
                        ${token.birdeye_url ? `<a href="${token.birdeye_url}" target="_blank" title="View on Birdeye" onclick="event.stopPropagation();">Birdeye</a>` : ''}
                        ${contractAddress && contractAddress !== 'native' ? 
                            `<a href="https://solscan.io/token/${contractAddress}" target="_blank" title="View token on Solscan" onclick="event.stopPropagation();">🔍</a>` : ''}
                    </td>
                `;
                tbody.appendChild(row);
            });

            // Add click handlers to token rows
            document.querySelectorAll('.token-row').forEach(row => {
                row.addEventListener('click', function() {
                    const symbol = this.getAttribute('data-symbol');
                    const address = this.getAttribute('data-address');
                    const name = this.getAttribute('data-name');
                    
                    if (address && address !== 'null') {
                        loadTokenHolders(address, symbol, name);
                    }
                });
            });
        }

        function showLoading() {
            document.getElementById('status-loading').style.display = 'block';
            document.getElementById('status-success').style.display = 'none';
            document.getElementById('status-error').style.display = 'none';
            document.getElementById('stats').style.display = 'none';
        }

        function showSuccess() {
            document.getElementById('status-loading').style.display = 'none';
            document.getElementById('status-success').style.display = 'block';
            document.getElementById('status-error').style.display = 'none';
            document.getElementById('stats').style.display = 'grid';
        }

        function showError() {
            document.getElementById('status-loading').style.display = 'none';
            document.getElementById('status-success').style.display = 'none';
            document.getElementById('status-error').style.display = 'block';
            document.getElementById('stats').style.display = 'none';
        }

        function formatNumber(num) {
            if (num === null || num === undefined) return 'N/A';
            if (num >= 1e9) return (num / 1e9).toFixed(2) + 'B';
            if (num >= 1e6) return (num / 1e6).toFixed(2) + 'M';
            if (num >= 1e3) return (num / 1e3).toFixed(2) + 'K';
            return num.toFixed(2);
        }

        function formatPrice(price) {
            if (price === null || price === undefined) return 'N/A';
            if (price >= 1000) return '$' + price.toFixed(0);
            if (price >= 1) return '$' + price.toFixed(2);
            if (price >= 0.01) return '$' + price.toFixed(4);
            if (price >= 0.0001) return '$' + price.toFixed(6);
            return '$' + price.toFixed(8);
        }

        function formatDate(dateString) {
            try {
                return new Date(dateString).toLocaleString();
            } catch (e) {
                return 'Unknown';
            }
        }

        function formatContractAddress(address) {
            if (!address) return 'N/A';
            if (address === 'native') return 'Native SOL';
            if (address.length <= 16) return address;
            return address.substring(0, 8) + '...' + address.substring(address.length - 8);
        }

        function formatWalletAddress(address) {
            if (!address) return 'N/A';
            return address.substring(0, 6) + '...' + address.substring(address.length - 4);
        }

        function copyToClipboard(text) {
            navigator.clipboard.writeText(text).then(function() {
                // Show temporary success message
                const originalText = event.target.textContent;
                event.target.textContent = '✓ Copied!';
                setTimeout(() => {
                    event.target.textContent = originalText;
                }, 2000);
            }).catch(function(err) {
                console.error('Failed to copy: ', err);
            });
        }

        async function loadTokenData() {
            console.log('Loading token data...');
            showLoading();
            
            try {
                const response = await fetch('/tokens/json');
                console.log('Response status:', response.status);
                
                if (!response.ok) {
                    throw new Error(`HTTP error! status: ${response.status}`);
                }
                
                const data = await response.json();
                console.log('Data received:', data);
                
                // Update statistics
                document.getElementById('total-tokens').textContent = data.total_tokens || 0;
                document.getElementById('total-market-cap').textContent = '$' + formatNumber(data.total_market_cap);
                document.getElementById('total-volume').textContent = '$' + formatNumber(data.total_volume_24h);
                document.getElementById('last-updated').textContent = formatDate(data.last_updated);
                document.getElementById('last-updated-text').textContent = formatDate(data.last_updated);
                
                // Update table
                if (!data.tokens || data.tokens.length === 0) {
                    const tbody = document.getElementById('tokens-body');
                    tbody.innerHTML = `
                        <tr>
                            <td colspan="8" style="text-align: center; padding: 40px; color: #6c757d;">
                                No token data available. The data might still be loading.
                            </td>
                        </tr>
                    `;
                } else {
                    displayTokensTable(data.tokens);
                    setupSortableHeaders();
                    updateSortIndicators(currentSort.column, currentSort.direction);
                }
                
                showSuccess();
                console.log('Successfully loaded', data.tokens?.length || 0, 'tokens');
                
            } catch (error) {
                console.error('Error loading token data:', error);
                showError();
                
                const tbody = document.getElementById('tokens-body');
                tbody.innerHTML = `
                    <tr>
                        <td colspan="8" style="text-align: center; padding: 40px; color: #dc3545;">
                            Error loading token data: ${error.message}<br>
                            <small>Check the browser console for details.</small>
                        </td>
                    </tr>
                `;
            }
        }

        async function loadTokenHolders(tokenAddress, symbol, name) {
            if (!tokenAddress) {
                console.log('No token address provided');
                return;
            }
            
            currentTokenAddress = tokenAddress;
            currentTokenSymbol = symbol;
            
            console.log(`Loading holders for ${symbol} (${tokenAddress})`);
            showHoldersLoading(name);
            
            try {
                const response = await fetch(`/tokens/${tokenAddress}/holders`);
                if (!response.ok) {
                    throw new Error(`HTTP error! status: ${response.status}`);
                }
                
                const data = await response.json();
                displayHoldersData(data, symbol);
                
            } catch (error) {
                console.error('Error loading holders:', error);
                showHoldersError(error.message);
            }
        }

        function showHoldersLoading(tokenName) {
            const section = document.getElementById('holders-section');
            const tokenNameSpan = document.getElementById('selected-token-name');
            const tbody = document.getElementById('holders-body');
            
            section.style.display = 'block';
            tokenNameSpan.textContent = tokenName || 'Loading...';
            document.getElementById('holder-stats').style.display = 'none';
            
            tbody.innerHTML = `
                <tr>
                    <td colspan="5" style="text-align: center; padding: 40px;">
                        <div style="color: #6c757d;">
                            <div>⏳ Loading holder data...</div>
                            <small>This may take a few seconds</small>
                        </div>
                    </td>
                </tr>
            `;
        }

        function showHoldersError(message) {
            const tbody = document.getElementById('holders-body');
            tbody.innerHTML = `
                <tr>
                    <td colspan="5" style="text-align: center; padding: 40px; color: #dc3545;">
                        <div>❌ Error loading holder data</div>
                        <small>${message}</small>
                    </td>
                </tr>
            `;
        }

        function displayHoldersData(data, symbol) {
            const holders = data.holders || [];
            const stats = data.stats || {};
            
            // Update statistics
            document.getElementById('total-holders').textContent = stats.total_holders || 0;
            document.getElementById('largest-holder').textContent = formatNumber(stats.largest_balance) + ' ' + symbol;
            document.getElementById('avg-balance').textContent = formatNumber(stats.average_balance) + ' ' + symbol;
            document.getElementById('holder-stats').style.display = 'grid';
            
            // Update holders table
            const tbody = document.getElementById('holders-body');
            
            if (holders.length === 0) {
                tbody.innerHTML = `
                    <tr>
                        <td colspan="5" style="text-align: center; padding: 40px; color: #6c757d;">
                            No holder data available for this token
                        </td>
                    </tr>
                `;
                return;
            }
            
            tbody.innerHTML = '';
            
            holders.forEach((holder, index) => {
                const rank = index + 1;
                const balance = holder.ui_amount || 0;
                const percentage = holder.percentage || 0;
                
                const row = document.createElement('tr');
                row.innerHTML = `
                    <td style="text-align: center; font-weight: bold;">${rank}</td>
                    <td>
                        <div class="tooltip">
                            <span class="wallet-address" onclick="event.stopPropagation(); copyToClipboard('${holder.owner}')">
                                ${formatWalletAddress(holder.owner)}
                            </span>
                            <span class="tooltiptext">Click to copy wallet address</span>
                        </div>
                    </td>
                    <td><strong>${formatNumber(balance)} ${symbol}</strong></td>
                    <td>
                        <div>${percentage.toFixed(4)}%</div>
                        <div class="percentage-bar">
                            <div class="percentage-fill" style="width: ${Math.min(percentage * 2, 100)}%"></div>
                        </div>
                    </td>
                    <td>
                        <a href="https://solscan.io/account/${holder.owner}" target="_blank" title="View on Solscan" style="margin-right: 8px;">🔍</a>
                        <a href="https://birdeye.so/address/${holder.owner}?chain=solana" target="_blank" title="View on BirdEye">🦅</a>
                    </td>
                `;
                tbody.appendChild(row);
            });
        }

        // Load data when page loads
        document.addEventListener('DOMContentLoaded', function() {
            console.log('Page loaded, starting initial data load...');
            loadTokenData();
            
            // Auto-refresh every 30 seconds
            setInterval(loadTokenData, 30000);
        });
    </script>
</body>
</html>
    """
    return html_content
@app.get("/tokens/json")
async def get_tokens_json():
    """Get token data as JSON"""
    if not token_cache:
        # Return empty data instead of error for better UI handling
        return {
            "last_updated": datetime.now().isoformat(),
            "total_tokens": 0,
            "total_market_cap": 0,
            "total_volume_24h": 0,
            "tokens": []
        }
    
    try:
        # Calculate totals safely
        total_market_cap = sum(getattr(token, 'market_cap', 0) for token in token_cache)
        total_volume_24h = sum(getattr(token, 'volume_24h', 0) for token in token_cache)
        
        # Convert tokens to dict safely
        tokens_data = []
        for token in token_cache:
            try:
                if hasattr(token, 'model_dump'):
                    tokens_data.append(token.model_dump())
                else:
                    # Fallback: convert to dict manually
                    token_dict = {}
                    for field in ['rank', 'name', 'symbol', 'mint_address', 'market_cap', 
                                 'volume_24h', 'price', 'price_change_24h', 'solscan_url', 
                                 'coingecko_url', 'birdeye_url']:
                        token_dict[field] = getattr(token, field, None)
                    tokens_data.append(token_dict)
            except Exception as e:
                logger.error(f"Error converting token to dict: {e}")
                continue
        
        return {
            "last_updated": last_update.isoformat() if last_update else datetime.now().isoformat(),
            "total_tokens": len(token_cache),
            "total_market_cap": total_market_cap,
            "total_volume_24h": total_volume_24h,
            "tokens": tokens_data
        }
        
    except Exception as e:
        logger.error(f"Error in /tokens/json: {e}")
        raise HTTPException(status_code=500, detail="Error processing token data")
    
@app.get("/tokens/download")
async def download_tokens_json():
    """Download token data as JSON file"""
    if not os.path.exists(DATA_FILE):
        raise HTTPException(status_code=404, detail="Data file not found")
    
    return FileResponse(
        DATA_FILE,
        media_type='application/json',
        filename=f"solana_top_tokens_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
    )

@app.get("/tokens", response_model=List[TokenData])
async def get_tokens(limit: int = Query(100, le=200, ge=1)):
    """Get current top tokens"""
    if not token_cache:
        raise HTTPException(status_code=503, detail="Token data not available yet")
    
    return token_cache[:limit]

@app.get("/tokens/{mint_address}/holders")
async def get_token_holders(mint_address: str, limit: int = Query(50, le=100)):
    """Get token holders from BirdEye API"""
    if not mint_address or mint_address == "null":
        raise HTTPException(status_code=400, detail="Valid mint address required")
    
    parser = DataParser()
    holder_data = await parser.get_token_holders(mint_address, limit)
    
    if not holder_data:
        raise HTTPException(status_code=404, detail="Holder data not available")
    
    return holder_data

@app.get("/tokens/with-holders/json")
async def get_tokens_with_holders_json(include_holders: bool = Query(False)):
    """Get token data as JSON with optional holder information"""
    if not token_cache:
        return {
            "last_updated": datetime.now().isoformat(),
            "total_tokens": 0,
            "total_market_cap": 0,
            "total_volume_24h": 0,
            "tokens": []
        }
    
    try:
        tokens_data = []
        parser = DataParser()
        
        for token in token_cache:
            token_dict = token.model_dump() if hasattr(token, 'model_dump') else {
                'rank': token.rank,
                'name': token.name,
                'symbol': token.symbol,
                'mint_address': token.mint_address,
                'market_cap': token.market_cap,
                'volume_24h': token.volume_24h,
                'price': token.price,
                'price_change_24h': token.price_change_24h,
                'solscan_url': token.solscan_url,
                'coingecko_url': token.coingecko_url,
                'birdeye_url': token.birdeye_url
            }
            
            # Include holder data if requested and mint address exists
            if include_holders and token.mint_address:
                try:
                    holder_data = await parser.get_token_holders(token.mint_address, limit=10)
                    if holder_data:
                        token_dict["top_holders"] = holder_data["holders"][:5]  # Top 5 holders
                        token_dict["holder_stats"] = holder_data["stats"]
                except Exception as e:
                    logger.error(f"Error fetching holders for {token.symbol}: {str(e)}")
                    token_dict["top_holders"] = []
                    token_dict["holder_stats"] = {}
            
            tokens_data.append(token_dict)
        
        return {
            "last_updated": last_update.isoformat() if last_update else datetime.now().isoformat(),
            "total_tokens": len(token_cache),
            "total_market_cap": sum(token.market_cap for token in token_cache),
            "total_volume_24h": sum(token.volume_24h for token in token_cache),
            "tokens": tokens_data
        }
        
    except Exception as e:
        logger.error(f"Error in /tokens/with-holders/json: {e}")
        raise HTTPException(status_code=500, detail="Error processing token data")

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "last_update": last_update,
        "tokens_cached": len(token_cache),
        "data_file_exists": os.path.exists(DATA_FILE),
        "update_interval": settings.UPDATE_INTERVAL
    }

@app.get("/tokens/webhook-payload", response_model=WebhookPayload)
async def get_webhook_payload():
    """Get the payload that would be sent to webhooks"""
    if not token_cache:
        raise HTTPException(status_code=503, detail="Token data not available yet")
    
    totals = webhook_manager._calculate_totals(token_cache)
    
    return WebhookPayload(
        timestamp=last_update or datetime.now(),
        update_interval=settings.UPDATE_INTERVAL,
        total_tokens=len(token_cache),
        total_market_cap=totals["total_market_cap"],
        total_volume_24h=totals["total_volume_24h"],
        tokens=token_cache
    )

@app.post("/webhooks/register", response_model=WebhookResponse)
async def register_webhook(webhook_url: str):
    """Register a new webhook URL"""
    success = webhook_manager.register_webhook(webhook_url)
    
    if success:
        return WebhookResponse(
            status="success",
            message="Webhook registered successfully",
            tokens_count=len(token_cache),
            last_updated=last_update or datetime.now()
        )
    else:
        raise HTTPException(status_code=400, detail="Webhook URL already registered")

@app.delete("/webhooks/unregister", response_model=WebhookResponse)
async def unregister_webhook(webhook_url: str):
    """Unregister a webhook URL"""
    success = webhook_manager.unregister_webhook(webhook_url)
    
    if success:
        return WebhookResponse(
            status="success",
            message="Webhook unregistered successfully",
            tokens_count=len(token_cache),
            last_updated=last_update or datetime.now()
        )
    else:
        raise HTTPException(status_code=404, detail="Webhook URL not found")

@app.get("/webhooks/list")
async def list_webhooks():
    """List all registered webhook URLs"""
    return {
        "webhooks": webhook_manager.get_registered_webhooks(),
        "count": len(webhook_manager.get_registered_webhooks())
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)