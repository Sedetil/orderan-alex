from flask import Flask, jsonify
from flask_restx import Api, Resource, Namespace
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

# Define namespaces
ns = api.namespace('stocks', description='Stock operations')

def calculate_countdown(next_reset, current_time):
    """Calculate the countdown until the next reset time in the format 'XXh YYm ZZs' or 'XXm YYs'"""
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
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8'
    }
    
    try:
        logger.info(f"Fetching data from {url}")
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        logger.info("Successfully fetched webpage")
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Initialize stock data
        stock_data = {
            'gear_stock': {'items': [], 'updates_in': 'Unknown'},
            'egg_stock': {'items': [], 'updates_in': 'Unknown'},
            'seeds_stock': {'items': [], 'updates_in': 'Unknown'}
        }
        
        # Current time for countdown calculation
        current_time = datetime.now()
        
        # Extract reset intervals from JavaScript (if available)
        script_tag = soup.find('script', string=re.compile(r'updateCountdowns'))
        reset_intervals = {'gear': 300, 'egg': 43200, 'seeds': 300}  # Default: Gear/Seeds 5 min, Egg 12 hours
        next_resets = {}
        
        if script_tag:
            script_content = script_tag.string
            # Look for reset intervals or timestamps (e.g., gearReset = 300000; for 5 minutes in milliseconds)
            reset_patterns = {
                'gear': r'gearReset\s*=\s*(\d+)\s*;',  # In milliseconds
                'egg': r'eggReset\s*=\s*(\d+)\s*;',   # In milliseconds
                'seeds': r'seedsReset\s*=\s*(\d+)\s*;' # In milliseconds
            }
            
            for stock_type, pattern in reset_patterns.items():
                match = re.search(pattern, script_content)
                if match:
                    reset_ms = int(match.group(1))
                    reset_intervals[stock_type] = reset_ms // 1000  # Convert to seconds
                    logger.info(f"Extracted {stock_type} reset interval: {reset_intervals[stock_type]} seconds")
                else:
                    logger.warning(f"Could not find reset interval for {stock_type}, using default: {reset_intervals[stock_type]} seconds")
            
            # Look for next reset timestamps (e.g., gearNextReset = 1623079200;)
            timestamp_patterns = {
                'gear': r'gearNextReset\s*=\s*(\d+)\s*;',  # Unix timestamp in seconds
                'egg': r'eggNextReset\s*=\s*(\d+)\s*;',   # Unix timestamp in seconds
                'seeds': r'seedsNextReset\s*=\s*(\d+)\s*;' # Unix timestamp in seconds
            }
            
            for stock_type, pattern in timestamp_patterns.items():
                match = re.search(pattern, script_content)
                if match:
                    timestamp = int(match.group(1))
                    next_resets[stock_type] = datetime.fromtimestamp(timestamp)
                    logger.info(f"Extracted {stock_type} next reset: {next_resets[stock_type]}")
                else:
                    # Calculate next reset based on interval
                    interval_seconds = reset_intervals[stock_type]
                    seconds_since_epoch = int(current_time.timestamp())
                    elapsed = seconds_since_epoch % interval_seconds
                    seconds_until_next = interval_seconds - elapsed
                    next_resets[stock_type] = current_time + timedelta(seconds=seconds_until_next)
                    logger.info(f"Calculated {stock_type} next reset: {next_resets[stock_type]}")
        else:
            logger.warning("Could not find script tag with updateCountdowns, using default intervals")
            # Calculate next resets using default intervals
            for stock_type, interval_seconds in reset_intervals.items():
                seconds_since_epoch = int(current_time.timestamp())
                elapsed = seconds_since_epoch % interval_seconds
                seconds_until_next = interval_seconds - elapsed
                next_resets[stock_type] = current_time + timedelta(seconds=seconds_until_next)
        
        # Find the grid containing stock sections
        stock_grid = soup.find('div', class_=re.compile('grid.*grid-cols'))
        
        if not stock_grid:
            logger.error("Stock grid not found")
            return {'error': 'Stock grid not found on the page'}
        
        logger.info("Found stock grid")
        # Get all stock sections
        stock_sections = stock_grid.find_all('div', recursive=False)
        
        if not stock_sections:
            logger.error("No stock sections found in grid")
            return {'error': 'No stock sections found in grid'}
        
        for section in stock_sections:
            # Get section title
            title_tag = section.find('h2')
            if not title_tag:
                logger.warning("Section title not found")
                continue
            title = title_tag.text.strip().upper()
            
            # Calculate countdown based on stock type
            countdown = 'Unknown'
            if 'GEAR' in title and 'gear' in next_resets:
                countdown = calculate_countdown(next_resets['gear'], current_time)
            elif 'EGG' in title and 'egg' in next_resets:
                countdown = calculate_countdown(next_resets['egg'], current_time)
            elif 'SEEDS' in title and 'seeds' in next_resets:
                countdown = calculate_countdown(next_resets['seeds'], current_time)
            
            # Get stock items
            items_list = section.find('ul')
            if not items_list:
                logger.warning(f"No items list found for {title}")
                continue
                
            items = items_list.find_all('li')
            stock_items = []
            
            for item in items:
                try:
                    # Extract name (first span's text content)
                    name_span = item.find('span')
                    if not name_span:
                        logger.warning("Item name span not found")
                        continue
                    name = name_span.contents[0].strip()
                    
                    # Extract quantity
                    quantity_span = item.find('span', class_=re.compile('text-gray'))
                    if not quantity_span:
                        logger.warning(f"Quantity not found for {name}")
                        continue
                    quantity_text = quantity_span.text.strip()
                    quantity_match = re.search(r'\d+', quantity_text)
                    if not quantity_match:
                        logger.warning(f"Invalid quantity text for {name}: '{quantity_text}'")
                        continue
                    quantity = int(quantity_match.group())
                    
                    stock_items.append({
                        'name': name,
                        'quantity': quantity
                    })
                except Exception as e:
                    logger.error(f"Error processing item: {str(e)}")
                    continue
            
            # Assign to appropriate stock type
            if 'GEAR' in title:
                stock_data['gear_stock'] = {
                    'items': stock_items,
                    'updates_in': countdown
                }
            elif 'EGG' in title:
                stock_data['egg_stock'] = {
                    'items': stock_items,
                    'updates_in': countdown
                }
            elif 'SEEDS' in title:
                stock_data['seeds_stock'] = {
                    'items': stock_items,
                    'updates_in': countdown
                }
            else:
                logger.warning(f"Unknown stock section: {title}")
        
        # Check if all stocks are empty
        if not any(stock_data[stock]['items'] for stock in stock_data):
            logger.error("All stock sections are empty")
            return {'error': 'No stock data found. The page structure may have changed or data is loaded dynamically.'}
        
        logger.info("Successfully scraped stock data")
        return stock_data
    
    except RequestException as e:
        logger.error(f"Request failed: {str(e)}")
        return {'error': f'Failed to fetch data: {str(e)}'}
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        return {'error': f'Error processing data: {str(e)}'}

@ns.route('/all')
class AllStocks(Resource):
    @ns.doc('get_all_stocks',
            description='Retrieve all stock data (gear, egg, and seeds) from VulcanValues')
    def get(self):
        """Get all stock data"""
        data = scrape_stock_data()
        return jsonify(data)

@ns.route('/gear')
class GearStock(Resource):
    @ns.doc('get_gear_stock',
            description='Retrieve gear stock data from VulcanValues')
    def get(self):
        """Get gear stock data"""
        data = scrape_stock_data()
        return jsonify(data.get('gear_stock', {'items': [], 'updates_in': 'Unknown'}))

@ns.route('/egg')
class EggStock(Resource):
    @ns.doc('get_egg_stock',
            description='Retrieve egg stock data from VulcanValues')
    def get(self):
        """Get egg stock data"""
        data = scrape_stock_data()
        return jsonify(data.get('egg_stock', {'items': [], 'updates_in': 'Unknown'}))

@ns.route('/seeds')
class SeedsStock(Resource):
    @ns.doc('get_seeds_stock',
            description='Retrieve seeds stock data from VulcanValues')
    def get(self):
        """Get seeds stock data"""
        data = scrape_stock_data()
        return jsonify(data.get('seeds_stock', {'items': [], 'updates_in': 'Unknown'}))

if __name__ == '__main__':
    app.run(debug=True)
