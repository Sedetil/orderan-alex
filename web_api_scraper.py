from flask import Flask, jsonify
from flask_restx import Api, Resource, Namespace
from flask_caching import Cache
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import requests
from bs4 import BeautifulSoup
import re
import logging
from requests.exceptions import RequestException
from datetime import datetime, timedelta

# Configure logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)
api = Api(app, version='1.1', title='Grow A Garden Stock API',
          description='API to scrape stock data from VulcanValues Grow A Garden page')

# Configure cache
cache = Cache(app, config={'CACHE_TYPE': 'SimpleCache'})  # Ganti dengan Redis di produksi

# Configure rate limiter
limiter = Limiter(app, key_func=get_remote_address, default_limits=["200 per day", "50 per hour"])

# Define namespaces
ns = api.namespace('stocks', description='Stock operations')

def calculate_countdown(next_reset, current_time):
    delta = next_reset - current_time
    seconds = int(delta.total_seconds())
    if seconds <= 0:
        return "00m 00s"
    hours = seconds // 3600
    seconds %= 3600
    minutes = seconds // 60
    seconds %= 60
    if hours > 0:
        return f"{hours:02d}h {minutes:02d}m {seconds:02d}s"
    return f"{minutes:02d}m {seconds:02d}s"

def scrape_stock_data():
    url = "https://vulcanvalues.com/grow-a-garden/stock"
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/91.0.4472.124 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8'
    }
    try:
        logger.info(f"Fetching data from {url}")
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        logger.info("Successfully fetched webpage")
        
        soup = BeautifulSoup(response.text, 'html.parser')
        stock_data = {
            'gear_stock': {'items': [], 'updates_in': 'Unknown'},
            'egg_stock': {'items': [], 'updates_in': 'Unknown'},
            'seeds_stock': {'items': [], 'updates_in': 'Unknown'}
        }
        current_time = datetime.now()
        
        # Calculate reset times
        reset_intervals = {'gear': 300, 'egg': 1800, 'seeds': 300}
        next_resets = {}
        minutes = current_time.minute
        seconds = current_time.second
        next_5min = (minutes + (1 if seconds > 0 else 0) + 4) // 5 * 5
        if next_5min >= 60:
            next_gear_seeds = current_time.replace(hour=current_time.hour + 1, minute=0, second=0, microsecond=0)
        else:
            next_gear_seeds = current_time.replace(minute=next_5min, second=0, microsecond=0)
        next_resets['gear'] = next_gear_seeds
        next_resets['seeds'] = next_gear_seeds
        if minutes < 30:
            next_egg = current_time.replace(minute=30, second=0, microsecond=0)
        else:
            next_egg = current_time.replace(hour=current_time.hour + 1, minute=0, second=0, microsecond=0)
        next_resets['egg'] = next_egg
        
        stock_grid = soup.find('div', class_=re.compile('grid.*grid-cols'))
        if not stock_grid:
            logger.error("Stock grid not found")
            return {'error': 'Stock grid not found on the page'}
        
        stock_sections = stock_grid.find_all('div', recursive=False)
        if not stock_sections:
            logger.error("No stock sections found in grid")
            return {'error': 'No stock sections found in grid'}
        
        for section in stock_sections:
            title_tag = section.find('h2')
            if not title_tag:
                continue
            title = title_tag.text.strip().upper()
            countdown = 'Unknown'
            if 'GEAR' in title:
                countdown = calculate_countdown(next_resets['gear'], current_time)
            elif 'EGG' in title:
                countdown = calculate_countdown(next_resets['egg'], current_time)
            elif 'SEEDS' in title:
                countdown = calculate_countdown(next_resets['seeds'], current_time)
            
            items_list = section.find('ul')
            if not items_list:
                continue
            items = items_list.find_all('li')
            stock_items = []
            
            if 'EGG' in title:
                for item in items:
                    name_span = item.find('span')
                    if not name_span:
                        continue
                    name = name_span.contents[0].strip()
                    quantity_span = name_span.find('span', class_=re.compile('text-gray'))
                    if not quantity_span:
                        continue
                    quantity_text = quantity_span.text.strip()
                    quantity_match = re.search(r'\d+', quantity_text)
                    if not quantity_match:
                        continue
                    quantity = int(quantity_match.group())
                    stock_items.append({'name': name, 'quantity': quantity})
            else:
                item_dict = {}
                for item in items:
                    name_span = item.find('span')
                    if not name_span:
                        continue
                    name = name_span.contents[0].strip()
                    quantity_span = name_span.find('span', class_=re.compile('text-gray'))
                    if not quantity_span:
                        continue
                    quantity_text = quantity_span.text.strip()
                    quantity_match = re.search(r'\d+', quantity_text)
                    if not quantity_match:
                        continue
                    quantity = int(quantity_match.group())
                    if name in item_dict:
                        item_dict[name]['quantity'] += quantity
                    else:
                        item_dict[name] = {'name': name, 'quantity': quantity}
                stock_items = list(item_dict.values())
            
            if 'GEAR' in title:
                stock_data['gear_stock'] = {'items': stock_items, 'updates_in': countdown}
            elif 'EGG' in title:
                stock_data['egg_stock'] = {'items': stock_items, 'updates_in': countdown}
            elif 'SEEDS' in title:
                stock_data['seeds_stock'] = {'items': stock_items, 'updates_in': countdown}
        
        if not any(stock_data[stock]['items'] for stock in stock_data):
            return {'error': 'No stock data found.'}
        
        return stock_data
    
    except RequestException as e:
        return {'error': f'Failed to fetch data: {str(e)}'}
    except Exception as e:
        return {'error': f'Error processing data: {str(e)}'}

@ns.route('/all')
class AllStocks(Resource):
    @ns.doc('get_all_stocks')
    @cache.cached(timeout=60)
    @limiter.limit("10 per minute")
    def get(self):
        data = scrape_stock_data()
        return jsonify(data)

@ns.route('/gear')
class GearStock(Resource):
    @ns.doc('get_gear_stock')
    @cache.cached(timeout=60)
    @limiter.limit("10 per minute")
    def get(self):
        data = scrape_stock_data()
        return jsonify(data.get('gear_stock', {'items': [], 'updates_in': 'Unknown'}))

@ns.route('/egg')
class EggStock(Resource):
    @ns.doc('get_egg_stock')
    @cache.cached(timeout=60)
    @limiter.limit("10 per minute")
    def get(self):
        data = scrape_stock_data()
        return jsonify(data.get('egg_stock', {'items': [], 'updates_in': 'Unknown'}))

@ns.route('/seeds')
class SeedsStock(Resource):
    @ns.doc('get_seeds_stock')
    @cache.cached(timeout=60)
    @limiter.limit("10 per minute")
    def get(self):
        data = scrape_stock_data()
        return jsonify(data.get('seeds_stock', {'items': [], 'updates_in': 'Unknown'}))

if __name__ == '__main__':
    app.run(debug=False)
