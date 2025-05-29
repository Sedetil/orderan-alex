from flask import Flask, jsonify
from flask_restx import Api, Resource, Namespace
import cloudscraper
from bs4 import BeautifulSoup
import re
import logging
import time
from fake_useragent import UserAgent
from cachetools import TTLCache
import requests
from datetime import datetime
import dateutil.parser

# Configure logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)
api = Api(app, version='1.3', title='Grow A Garden Stock API',
          description='API to scrape stock data from VulcanValues Grow A Garden page')

# Define namespaces
ns = api.namespace('stocks', description='Stock operations')

# Initialize cache (TTL 5 minutes)
cache = TTLCache(maxsize=100, ttl=300)

def calculate_countdown(server_time):
    """Calculate countdown for gear, egg, and seeds stock based on server time."""
    # Parse server time from HTTP Date header
    try:
        now = dateutil.parser.parse(server_time)
    except (ValueError, TypeError) as e:
        logger.warning(f"Failed to parse server time: {e}. Falling back to local time.")
        now = datetime.now()

    # Gear and Seeds: Next 5-minute interval
    minutes = now.minute
    seconds = now.second
    next_5_min = now.replace(second=0, microsecond=0)
    next_5_min += timedelta(minutes=(5 - minutes % 5) % 5)
    if minutes % 5 == 0 and seconds == 0:
        next_5_min += timedelta(minutes=5)
    
    dist_5 = next_5_min - now
    m5 = dist_5.seconds // 60
    s5 = dist_5.seconds % 60
    gear_seeds_countdown = f"{m5:02d}m {s5:02d}s"
    
    # Egg: Next 30-minute mark
    next_half_hour = now.replace(second=0, microsecond=0)
    if now.minute < 30:
        next_half_hour = next_half_hour.replace(minute=30)
    else:
        next_half_hour = next_half_hour.replace(hour=now.hour + 1, minute=0)
    
    dist_egg = next_half_hour - now
    h_egg = dist_egg.seconds // 3600
    m_egg = (dist_egg.seconds % 3600) // 60
    s_egg = dist_egg.seconds % 60
    egg_countdown = f"{h_egg:02d}h {m_egg:02d}m {s_egg:02d}s"
    
    return gear_seeds_countdown, egg_countdown

def scrape_stock_data():
    """Scrape stock data from VulcanValues with retry, caching, and robust grid detection."""
    cache_key = f"stock_data_{int(time.time() // 300)}"
    if cache_key in cache:
        logger.info("Returning cached stock data")
        return cache[cache_key]

    url = f"https://vulcanvalues.com/grow-a-garden/stock?_={int(time.time())}"
    ua = UserAgent()
    headers = {
        'User-Agent': ua.random,
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
        'Accept-Encoding': 'gzip, deflate',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Cache-Control': 'no-cache',
        'Pragma': 'no-cache',
        'Referer': 'https://vulcanvalues.com/',
        'DNT': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'same-origin',
        'Sec-Fetch-User': '?1',
        'Sec-CH-UA': '"Chromium";v="128", "Not;A=Brand";v="24", "Google Chrome";v="128"',
        'Sec-CH-UA-Mobile': '?0',
        'Sec-CH-UA-Platform': '"Windows"'
    }

    scraper = cloudscraper.create_scraper()
    max_retries = 3
    retry_delay = 5

    for attempt in range(max_retries):
        try:
            logger.info(f"Attempt {attempt + 1}/{max_retries}: Fetching data from {url} with User-Agent: {headers['User-Agent']}")
            time.sleep(2)
            response = scraper.get(url, headers=headers, timeout=15)
            response.raise_for_status()
            logger.info(f"Successfully fetched webpage, Status Code: {response.status_code}")
            logger.debug(f"Response headers: {response.headers}")

            # Get server time from Date header
            server_time = response.headers.get('Date')
            logger.debug(f"Server time: {server_time}")
            gear_seeds_countdown, egg_countdown = calculate_countdown(server_time)

            content_type = response.headers.get('Content-Type', '')
            if 'text/html' not in content_type.lower():
                logger.error(f"Non-HTML response received, Content-Type: {content_type}")
                return {
                    'error': 'Invalid response from server.',
                    'details': f'Received non-HTML content (Content-Type: {content_type}).',
                    'suggestion': 'The server may be blocking access or returning unexpected data. Contact the server administrator via Discord (https://discord.gg/kEpjAJQdPH).'
                }

            logger.debug(f"HTML received: {response.text[:2000]}")
            soup = BeautifulSoup(response.text, 'lxml')  # Use lxml parser

            if 'cf-browser-verification' in response.text or 'checking your browser' in response.text.lower():
                logger.error("Cloudflare verification page detected")
                return {
                    'error': 'Blocked by Cloudflare verification.',
                    'details': 'The server requires browser verification, which cannot be bypassed with current setup.',
                    'suggestion': 'Try using a proxy or contact the server administrator via Discord (https://discord.gg/kEpjAJQdPH).'
                }

            stock_data = {
                'gear_stock': {'items': [], 'updates_in': gear_seeds_countdown},
                'egg_stock': {'items': [], 'updates_in': egg_countdown},
                'seeds_stock': {'items': [], 'updates_in': gear_seeds_countdown}
            }

            stock_grid = soup.find('div', class_=re.compile(r'grid.*grid-cols'))
            if not stock_grid:
                stock_grid = None
                for div in soup.find_all('div'):
                    if div.find('h2', text=re.compile(r'GEAR STOCK|EGG STOCK|SEEDS STOCK', re.I)):
                        stock_grid = div
                        break
                if not stock_grid:
                    logger.error("Stock grid not found, even with fallback")
                    return {
                        'error': 'Stock grid not found on the page.',
                        'details': 'The page structure may differ, or data is loaded dynamically.',
                        'suggestion': 'Verify the website content in a browser or contact the server administrator via Discord (https://discord.gg/kEpjAJQdPH).'
                    }

            logger.info("Found stock grid")
            stock_sections = stock_grid.find_all('div', recursive=False)
            if not stock_sections:
                logger.error("No stock sections found in grid")
                return {
                    'error': 'No stock sections found in grid.',
                    'details': 'The page structure may have changed.',
                    'suggestion': 'Verify the website content or contact the server administrator.'
                }

            for section in stock_sections:
                title_tag = section.find('h2')
                if not title_tag:
                    logger.warning("Section title not found")
                    continue
                title = title_tag.text.strip().upper()

                items_list = section.find('ul', class_=re.compile(r'space-y-\d+'))
                if not items_list:
                    logger.warning(f"No items list found for {title}")
                    continue

                items = items_list.find_all('li', class_=re.compile(r'bg-gray-\d+'))
                stock_items = []

                # Aggregate all items (including eggs) to handle duplicates
                item_dict = {}
                for item in items:
                    try:
                        name_span = item.find('span')
                        if not name_span:
                            logger.warning("Item name span not found")
                            continue
                        name = name_span.contents[0].strip()

                        quantity_span = name_span.find('span', class_=re.compile(r'text-gray'))
                        if not quantity_span:
                            logger.warning(f"Quantity not found for {name}")
                            continue
                        quantity_text = quantity_span.text.strip()
                        quantity_match = re.search(r'\d+', quantity_text)
                        if not quantity_match:
                            logger.warning(f"Invalid quantity text for {name}: '{quantity_text}'")
                            continue
                        quantity = int(quantity_match.group())

                        if name in item_dict:
                            item_dict[name]['quantity'] += quantity
                        else:
                            item_dict[name] = {'name': name, 'quantity': quantity}
                    except Exception as e:
                        logger.error(f"Error processing item: {str(e)}")
                        continue
                stock_items = list(item_dict.values())

                if 'GEAR' in title:
                    stock_data['gear_stock'] = {'items': stock_items, 'updates_in': gear_seeds_countdown}
                elif 'EGG' in title:
                    stock_data['egg_stock'] = {'items': stock_items, 'updates_in': egg_countdown}
                elif 'SEEDS' in title:
                    stock_data['seeds_stock'] = {'items': stock_items, 'updates_in': gear_seeds_countdown}
                else:
                    logger.warning(f"Unknown stock section: {title}")

            if not any(stock_data[stock]['items'] for stock in stock_data):
                logger.error("All stock sections are empty")
                return {
                    'error': 'No stock data found.',
                    'details': 'The page structure may have changed or data is not available.',
                    'suggestion': 'Verify the website content or contact the server administrator via Discord (https://discord.gg/kEpjAJQdPH).'
                }

            logger.info("Successfully scraped stock data")
            cache[cache_key] = stock_data
            return stock_data

        except Exception as e:
            logger.error(f"Attempt {attempt + 1}/{max_retries} failed: {str(e)}, Status Code: {response.status_code if 'response' in locals() else 'N/A'}")
            logger.debug(f"Response content (if available): {response.text[:2000] if 'response' in locals() else 'N/A'}")
            if attempt < max_retries - 1:
                headers['User-Agent'] = ua.random
                logger.info(f"Retrying after {retry_delay} seconds with new User-Agent: {headers['User-Agent']}")
                time.sleep(retry_delay)
            else:
                error_msg = 'Failed to fetch or parse data after multiple attempts.'
                logger.error(error_msg)
                return {
                    'error': error_msg,
                    'details': f'{str(e)}. The server may have returned invalid or non-HTML data.',
                    'suggestion': 'Try using a proxy, verify the website content in a browser, or contact the server administrator via Discord (https://discord.gg/kEpjAJQdPH).'
                }

# Flask endpoints
@ns.route('/all')
class AllStocks(Resource):
    @ns.doc('get_all_stocks', description='Retrieve all stock data (gear, egg, and seeds) from VulcanValues')
    def get(self):
        """Get all stock data"""
        data = scrape_stock_data()
        return jsonify(data)

@ns.route('/gear')
class GearStock(Resource):
    @ns.doc('get_gear_stock', description='Retrieve gear stock data from VulcanValues')
    def get(self):
        """Get gear stock data"""
        data = scrape_stock_data()
        return jsonify(data.get('gear_stock', {'items': [], 'updates_in': 'Unknown'}))

@ns.route('/egg')
class EggStock(Resource):
    @ns.doc('get_egg_stock', description='Retrieve egg stock data from VulcanValues')
    def get(self):
        """Get egg stock data"""
        data = scrape_stock_data()
        return jsonify(data.get('egg_stock', {'items': [], 'updates_in': 'Unknown'}))

@ns.route('/seeds')
class SeedsStock(Resource):
    @ns.doc('get_seeds_stock', description='Retrieve seeds stock data from VulcanValues')
    def get(self):
        """Get seeds stock data"""
        data = scrape_stock_data()
        return jsonify(data.get('seeds_stock', {'items': [], 'updates_in': 'Unknown'}))

if __name__ == '__main__':
    app.run(debug=True)
