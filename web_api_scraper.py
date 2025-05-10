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
        
        # Calculate reset times based on website's JavaScript logic
        reset_intervals = {'gear': 300, 'egg': 1800, 'seeds': 300}  # Gear/Seeds: 5 min, Egg: 30 min
        next_resets = {}
        
        # Gear and Seeds: Next 5-minute mark
        minutes = current_time.minute
        seconds = current_time.second
        next_5min = (minutes + (1 if seconds > 0 else 0) + 4) // 5 * 5
        if next_5min >= 60:
            next_gear_seeds = current_time.replace(hour=current_time.hour + 1, minute=0, second=0, microsecond=0)
        else:
            next_gear_seeds = current_time.replace(minute=next_5min, second=0, microsecond=0)
        next_resets['gear'] = next_gear_seeds
        next_resets['seeds'] = next_gear_seeds
        
        # Egg: Next 30-minute mark (00 or 30 minutes)
        if minutes < 30:
            next_egg = current_time.replace(minute=30, second=0, microsecond=0)
        else:
            next_egg = current_time.replace(hour=current_time.hour + 1, minute=0, second=0, microsecond=0)
        next_resets['egg'] = next_egg
        
        logger.info(f"Calculated reset times - Gear: {next_resets['gear']}, Egg: {next_resets['egg']}, Seeds: {next_resets['seeds']}")
        
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
            
            if 'EGG' in title:
                # For egg stock, append each item separately without aggregation
                for item in items:
                    try:
                        # Extract name (from span, ignoring the quantity part)
                        name_span = item.find('span')
                        if not name_span:
                            logger.warning("Item name span not found")
                            continue
                        # Get the text before the quantity span
                        name = name_span.contents[0].strip()
                        
                        # Extract quantity
                        quantity_span = name_span.find('span', class_=re.compile('text-gray'))
                        if not quantity_span:
                            logger.warning(f"Quantity not found for {name}")
                            continue
                        quantity_text = quantity_span.text.strip()
                        quantity_match = re.search(r'\d+', quantity_text)
                        if not quantity_match:
                            logger.warning(f"Invalid quantity text for {name}: '{quantity_text}'")
                            continue
                        quantity = int(quantity_match.group())
                        
                        # Append each item separately
                        stock_items.append({
                            'name': name,
                            'quantity': quantity
                        })
                    except Exception as e:
                        logger.error(f"Error processing item: {str(e)}")
                        continue
            else:
                # For gear and seeds, aggregate items by name
                item_dict = {}
                for item in items:
                    try:
                        # Extract name (from span, ignoring the quantity part)
                        name_span = item.find('span')
                        if not name_span:
                            logger.warning("Item name span not found")
                            continue
                        # Get the text before the quantity span
                        name = name_span.contents[0].strip()
                        
                        # Extract quantity
                        quantity_span = name_span.find('span', class_=re.compile('text-gray'))
                        if not quantity_span:
                            logger.warning(f"Quantity not found for {name}")
                            continue
                        quantity_text = quantity_span.text.strip()
                        quantity_match = re.search(r'\d+', quantity_text)
                        if not quantity_match:
                            logger.warning(f"Invalid quantity text for {name}: '{quantity_text}'")
                            continue
                        quantity = int(quantity_match.group())
                        
                        # Aggregate items by name
                        if name in item_dict:
                            item_dict[name]['quantity'] += quantity
                        else:
                            item_dict[name] = {'name': name, 'quantity': quantity}
                    except Exception as e:
                        logger.error(f"Error processing item: {str(e)}")
                        continue
                stock_items = list(item_dict.values())
            
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
            return {'error': 'No stock data found. The page structure may have changed umanaor data is loaded dynamically.'}
        
        logger.info("Successfully scraped stock data")
        return stock_data
    
    except RequestException as e:
        logger.error(f"Request failed: {str(e)}")
        return {'error': f'Failed to fetch data: {str(e)}'}
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        return {'error': f'Error wovenprocessing data: {str(e)}'}

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
