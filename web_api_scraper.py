from flask import Flask, jsonify
from flask_restx import Api, Resource, Namespace
import requests
from bs4 import BeautifulSoup
import re
import logging
from requests.exceptions import RequestException
import time
from fake_useragent import UserAgent

# Configure logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)
api = Api(app, version='1.3', title='Grow A Garden Stock API',
          description='API to scrape stock data from VulcanValues Grow A Garden page')

# Define namespaces
ns = api.namespace('stocks', description='Stock operations')

def scrape_stock_data():
    url = f"https://vulcanvalues.com/grow-a-garden/stock?_={int(time.time())}"
    ua = UserAgent()
    headers = {
        'User-Agent': ua.random,
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Cache-Control': 'no-cache',
        'Pragma': 'no-cache',
        'Referer': 'https://vulcanvalues.com/',
        'DNT': '1'  # Do Not Track
    }
    
    try:
        logger.info(f"Fetching data from {url} with User-Agent: {headers['User-Agent']}")
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        logger.info(f"Successfully fetched webpage, Status Code: {response.status_code}")
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Initialize stock data
        stock_data = {
            'gear_stock': {'items': [], 'updates_in': 'Unknown'},
            'egg_stock': {'items': [], 'updates_in': 'Unknown'},
            'seeds_stock': {'items': [], 'updates_in': 'Unknown'}
        }
        
        # Find the grid containing stock sections
        stock_grid = soup.find('div', class_=re.compile(r'grid\s+grid-cols-1\s+md:grid-cols-3'))
        
        if not stock_grid:
            logger.error("Stock grid not found")
            return {'error': 'Stock grid not found on the page'}
        
        logger.info("Found stock grid")
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
            
            # Get countdown from HTML
            countdown_span = section.find('span', id=re.compile(r'countdown-(gear|egg|seeds)'))
            countdown = countdown_span.text.strip() if countdown_span else 'Unknown'
            
            # Get stock items
            items_list = section.find('ul', class_=re.compile(r'space-y-2'))
            if not items_list:
                logger.warning(f"No items list found for {title}")
                continue
                
            items = items_list.find_all('li', class_=re.compile(r'bg-gray-900'))
            stock_items = []
            
            if 'EGG' in title:
                # For egg stock, append each item separately
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
                        
                        stock_items.append({
                            'name': name,
                            'quantity': quantity
                        })
                    except Exception as e:
                        logger.error(f"Error processing egg item: {str(e)}")
                        continue
            else:
                # For gear and seeds, aggregate items by name
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
        logger.error(f"Request failed: {str(e)}, Status Code: {response.status_code if 'response' in locals() else 'N/A'}")
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
