from flask import Flask, jsonify
from flask_restx import Api, Resource, Namespace
import cloudscraper
from bs4 import BeautifulSoup
import re
import logging
import time
from fake_useragent import UserAgent
from cachetools import TTLCache
# Pastikan Anda sudah menginstal requests: pip install requests
import requests

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)
# Perbaikan kecil: Menghapus spasi berlebih pada deskripsi
api = Api(app, version='1.3', title='Grow A Garden Stock API',
          description='API to scrape stock data from VulcanValues Grow A Garden page')

# Define namespaces
ns = api.namespace('stocks', description='Stock operations')

# Initialize cache (TTL 5 minutes)
cache = TTLCache(maxsize=100, ttl=300)

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
        'Accept-Encoding': 'gzip, deflate, br', # Menambahkan 'br' untuk kompresi Brotli jika didukung
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
        # Anda bisa mencoba memperbarui header Sec-CH-UA jika diperlukan
        'Sec-CH-UA': '"Chromium";v="128", "Not;A=Brand";v="24", "Google Chrome";v="128"',
        'Sec-CH-UA-Mobile': '?0',
        'Sec-CH-UA-Platform': '"Windows"'
    }

    # Menggunakan session cloudscraper untuk penanganan cookie yang lebih baik
    scraper = cloudscraper.create_scraper(
        browser={
            'browser': 'chrome',
            'platform': 'windows',
            'mobile': False
        }
    )
    max_retries = 3
    retry_delay = 5

    for attempt in range(max_retries):
        try:
            logger.info(f"Attempt {attempt + 1}/{max_retries}: Fetching data from {url} with User-Agent: {headers['User-Agent']}")
            time.sleep(2) # Memberi jeda sebelum request
            response = scraper.get(url, headers=headers, timeout=20) # Menambah timeout sedikit
            response.raise_for_status() # Akan error jika status code bukan 2xx
            logger.info(f"Successfully fetched webpage, Status Code: {response.status_code}")

            content_type = response.headers.get('Content-Type', '')
            if 'text/html' not in content_type.lower():
                logger.error(f"Non-HTML response received, Content-Type: {content_type}")
                return {'error': 'Invalid response type.', 'details': f'Content-Type: {content_type}'}

            # Menyimpan HTML untuk debug (hapus atau komentari di produksi)
            # with open("debug_response.html", "w", encoding="utf-8") as f:
            #     f.write(response.text)

            soup = BeautifulSoup(response.text, 'lxml') # Pastikan lxml terinstal: pip install lxml

            if 'cf-browser-verification' in response.text or 'checking your browser' in response.text.lower() or 'Just a moment...' in response.text:
                logger.error("Cloudflare verification page detected")
                return {'error': 'Blocked by Cloudflare.'}

            stock_data = {
                'gear_stock': {'items': [], 'updates_in': 'Unknown'},
                'egg_stock': {'items': [], 'updates_in': 'Unknown'},
                'seeds_stock': {'items': [], 'updates_in': 'Unknown'}
            }

            # Mencari grid utama
            stock_grid = soup.find('div', class_=re.compile(r'grid.*grid-cols'))
            if not stock_grid:
                logger.warning("Primary stock grid not found, attempting fallback...")
                # Fallback: Cari div mana saja yang mengandung salah satu h2
                all_divs = soup.find_all('div')
                for div in all_divs:
                    if div.find('h2', text=re.compile(r'GEAR STOCK|EGG STOCK|SEEDS STOCK', re.I)):
                        # Cek apakah div ini adalah grid atau parentnya
                        if 'grid' in div.get('class', []):
                           stock_grid = div
                           break
                        elif div.find('div', class_=re.compile(r'grid.*grid-cols')):
                           stock_grid = div.find('div', class_=re.compile(r'grid.*grid-cols'))
                           break
                if not stock_grid:
                     logger.error("Stock grid not found, even with fallback.")
                     return {'error': 'Stock grid not found.'}


            logger.info("Found stock grid. Processing sections...")
            # Mengambil semua div anak langsung dari stock_grid
            stock_sections = stock_grid.find_all('div', recursive=False)

            if not stock_sections:
                 logger.error("No stock sections found within the grid.")
                 # Jika gagal, coba cari semua div di dalam grid, mungkin strukturnya berubah
                 stock_sections = stock_grid.find_all('div')
                 if not stock_sections:
                     return {'error': 'No stock sections found.'}


            found_any_section = False
            for section in stock_sections:
                title_tag = section.find('h2')
                if not title_tag or not title_tag.text.strip():
                    # Lewati div yang tidak memiliki H2 atau H2 kosong
                    continue

                title = title_tag.text.strip().upper()

                # ----> INILAH BAGIAN YANG MENGAMBIL 'UPDATES IN' <----
                # Mencari tag <p> dengan class 'text-yellow...'
                countdown_p = section.find('p', class_=re.compile(r'text-yellow.*'))
                countdown = 'Unknown'
                if countdown_p:
                    # Mencari tag <span> dengan id 'countdown-...' di dalam <p>
                    countdown_span = countdown_p.find('span', id=re.compile(r'countdown-(gear|egg|seeds)'))
                    if countdown_span:
                        # Mengambil teks dari span, ini adalah nilai countdown
                        countdown = countdown_span.text.strip()
                        logger.info(f"Found countdown for {title}: {countdown}")
                    else:
                        logger.warning(f"Countdown span not found for {title}")
                else:
                    logger.warning(f"Countdown paragraph not found for {title}")
                # ----> AKHIR BAGIAN PENGAMBILAN 'UPDATES IN' <----

                items_list = section.find('ul', class_=re.compile(r'space-y-\d+'))
                if not items_list:
                    logger.warning(f"No items list found for {title}")
                    continue

                items = items_list.find_all('li', class_=re.compile(r'bg-gray-\d+'))
                item_dict = {}

                for item in items:
                    try:
                        name_span = item.find('span')
                        if not name_span: continue

                        # Mencoba mengambil nama dengan lebih hati-hati
                        name_text_node = name_span.find(text=True, recursive=False)
                        name = name_text_node.strip() if name_text_node else 'Unknown Item'

                        quantity_span = item.find('span', class_=re.compile(r'text-gray'))
                        if not quantity_span: continue

                        quantity_text = quantity_span.text.strip()
                        quantity_match = re.search(r'\d+', quantity_text)
                        if not quantity_match: continue

                        quantity = int(quantity_match.group())

                        if name in item_dict:
                            item_dict[name]['quantity'] += quantity
                        else:
                            item_dict[name] = {'name': name, 'quantity': quantity}
                    except Exception as e:
                        logger.error(f"Error processing an item in {title}: {str(e)} - HTML: {item}")
                        continue

                stock_items = list(item_dict.values())

                if 'GEAR' in title:
                    stock_data['gear_stock'] = {'items': stock_items, 'updates_in': countdown}
                    found_any_section = True
                elif 'EGG' in title:
                    stock_data['egg_stock'] = {'items': stock_items, 'updates_in': countdown}
                    found_any_section = True
                elif 'SEEDS' in title:
                    stock_data['seeds_stock'] = {'items': stock_items, 'updates_in': countdown}
                    found_any_section = True
                # else: # Jangan log ini kecuali Anda yakin tidak ada div lain
                #    logger.warning(f"Unknown stock section title: {title}")

            if not found_any_section or not any(stock_data[stock]['items'] for stock in stock_data):
                logger.error("No valid stock data could be extracted.")
                return {'error': 'No stock data found (extraction failed).'}

            logger.info("Successfully scraped and processed stock data")
            cache[cache_key] = stock_data
            return stock_data

        except requests.exceptions.RequestException as e:
            logger.error(f"Request failed on attempt {attempt + 1}: {str(e)}")
            if attempt < max_retries - 1:
                headers['User-Agent'] = ua.random # Ganti User-Agent untuk percobaan berikutnya
                logger.info(f"Retrying after {retry_delay} seconds...")
                time.sleep(retry_delay)
            else:
                return {'error': 'Failed to fetch data after multiple attempts.', 'details': str(e)}
        except Exception as e:
            # Menangkap error umum lainnya
            logger.error(f"An unexpected error occurred on attempt {attempt + 1}: {str(e)}", exc_info=True)
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
            else:
                return {'error': 'An unexpected error occurred.', 'details': str(e)}

    # Jika loop selesai tanpa return (seharusnya tidak terjadi, tapi sebagai cadangan)
    return {'error': 'Scraping failed after all retries.'}


# Flask endpoints (Tidak ada perubahan di sini)
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
    # Pastikan Anda menjalankan ini di lingkungan yang mendukung Flask.
    # Untuk deployment, gunakan server WSGI seperti Gunicorn atau uWSGI.
    app.run(host='0.0.0.0', port=5000, debug=False) # Gunakan debug=False di produksi
