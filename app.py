from flask import Flask, render_template, request, jsonify
import requests
import json
from bs4 import BeautifulSoup
from difflib import get_close_matches
import re
import time
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
import os

app = Flask(__name__)

# Headers for requests
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36',
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'en-US,en;q=0.9',
    'Referer': 'https://www.zomato.com/mumbai',
    'Origin': 'https://www.zomato.com',
}

def get_lat_lng(city):
    """City to lat/lng mapper."""
    city_map = {
        'mumbai': (19.0760, 72.8777),
        'delhi': (28.6139, 77.2090),
        'bangalore': (12.9716, 77.5946),
    }
    return city_map.get(city.lower(), (19.0760, 72.8777))  # Default Mumbai

def search_swiggy_restaurants(lat, lng, dish, retries=3):
    """Search Swiggy for restaurants with retry logic."""
    url = f"https://www.swiggy.com/dapi/restaurants/search/v11?lat={lat}&lng={lng}&str={dish.replace(' ', '%20')}&submitAction=SEARCH"
    for attempt in range(retries):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=5)
            resp.raise_for_status()
            data = resp.json()
            restaurants = []
            if 'data' in data and 'cards' in data['data']:
                for card in data['data']['cards']:
                    if 'data' in card and 'data' in card['data']:
                        rest = card['data']['data']
                        restaurants.append({
                            'id': rest.get('id'),
                            'name': rest.get('name'),
                            'area': rest.get('areaName')
                        })
            return restaurants[:5]
        except (requests.RequestException, ValueError) as e:
            if attempt == retries - 1:
                print(f"Swiggy search failed: {e}")
                return []
            time.sleep(2)
    return []

def get_swiggy_menu(restaurant_id, lat, lng, retries=3):
    """Get Swiggy menu with retry."""
    url = f"https://www.swiggy.com/dapi/menu/pl?page-type=REGULAR_MENU&complete-menu=true&lat={lat}&lng={lng}&restaurantId={restaurant_id}"
    for attempt in range(retries):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=5)
            resp.raise_for_status()
            data = resp.json()
            menu = {}
            if 'data' in data and 'cards' in data['data']:
                for card in data['data']['cards']:
                    if 'groupedCard' in card and 'cardGroupMap' in card['groupedCard']:
                        for group in card['groupedCard']['cardGroupMap'].values():
                            if isinstance(group, list):
                                for item in group:
                                    if 'itemCards' in item:
                                        for ic in item['itemCards']:
                                            dish = ic['card']['info']
                                            name = dish.get('name', '').lower()
                                            price = dish.get('price', 0) / 100
                                            menu[name] = price
            return menu
        except (requests.RequestException, ValueError) as e:
            if attempt == retries - 1:
                print(f"Swiggy menu failed: {e}")
                return {}
            time.sleep(2)
    return []

def search_zomato_restaurants(lat, lng, dish, retries=3):
    """Search Zomato with API, fallback to Selenium."""
    url = f"https://www.zomato.com/webrapi/restaurants/search?lat={lat}&lon={lng}&q={dish.replace(' ', '%20')}&sort=rating"
    for attempt in range(retries):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=5)
            resp.raise_for_status()
            data = resp.json()
            restaurants = []
            if 'restaurants' in data:
                for rest in data['restaurants']:
                    r = rest['restaurant']
                    restaurants.append({
                        'id': r.get('id'),
                        'name': r.get('name'),
                        'area': r.get('location', {}).get('locality')
                    })
            return restaurants[:5]
        except (requests.RequestException, ValueError) as e:
            print(f"Zomato API failed: {e}")
            if attempt == retries - 1:
                print("Falling back to Zomato Selenium scraping...")
                return scrape_zomato_restaurants_selenium(lat, lng, dish)
            time.sleep(2)
    return []

def scrape_zomato_restaurants_selenium(lat, lng, dish):
    """Scrape Zomato using Selenium."""
    try:
        options = Options()
        options.add_argument('--headless')
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument(f'user-agent={HEADERS["User-Agent"]}')
        driver = webdriver.Chrome(options=options)
        url = f"https://www.zomato.com/mumbai/restaurants?q={dish.replace(' ', '%20')}"
        driver.get(url)
        time.sleep(3)  # Wait for JS to load
        soup = BeautifulSoup(driver.page_source, 'lxml')
        driver.quit()
        restaurants = []
        # Adjust selectors based on Zomato's HTML (inspect page)
        for item in soup.select('div.search-snippet-card')[:5]:
            name = item.select_one('a.result-title')
            area = item.select_one('div.search-result-address')
            rest_id = item.get('data-res-id', '')
            restaurants.append({
                'id': rest_id,
                'name': name.text.strip() if name else 'Unknown',
                'area': area.text.strip() if area else 'Unknown'
            })
        return restaurants
    except Exception as e:
        print(f"Zomato Selenium scraping failed: {e}")
        return []

def get_zomato_menu(restaurant_id, retries=3):
    """Get Zomato menu with retry."""
    url = f"https://www.zomato.com/webrapi/restaurant/{restaurant_id}/menu"
    for attempt in range(retries):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=5)
            resp.raise_for_status()
            data = resp.json()
            menu = {}
            if 'menu' in data:
                for section in data['menu']['sections']:
                    for item in section.get('items', []):
                        name = item.get('name', '').lower()
                        price = item.get('price', {}).get('amount', 0)
                        menu[name] = float(price or 0)
            return menu
        except (requests.RequestException, ValueError) as e:
            if attempt == retries - 1:
                print(f"Zomato menu failed: {e}")
                return {}
            time.sleep(2)
    return {}

def find_dish_price(menu, dish_name):
    """Fuzzy match dish in menu."""
    dish_lower = dish_name.lower()
    matches = get_close_matches(dish_lower, menu.keys(), n=1, cutoff=0.6)
    return menu.get(matches[0]) if matches else None

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/favicon.ico')
def favicon():
    return app.send_static_file('favicon.ico')

@app.route('/compare', methods=['POST'])
def compare():
    city = request.form.get('city', 'mumbai')
    dish = request.form.get('dish', 'Butter Chicken')
    lat, lng = get_lat_lng(city)

    # Fetch data
    swiggy_rests = search_swiggy_restaurants(lat, lng, dish)
    swiggy_prices = {}
    for rest in swiggy_rests:
        menu = get_swiggy_menu(rest['id'], lat, lng)
        price = find_dish_price(menu, dish)
        if price:
            swiggy_prices[rest['name']] = price

    zomato_rests = search_zomato_restaurants(lat, lng, dish)
    zomato_prices = {}
    for rest in zomato_rests:
        menu = get_zomato_menu(rest['id'])
        price = find_dish_price(menu, dish)
        if price:
            zomato_prices[rest['name']] = price

    # Comparisons
    common_rests = set(swiggy_prices.keys()) & set(zomato_prices.keys())
    comparisons = []
    chart_data = {'restaurants': [], 'swiggy_prices': [], 'zomato_prices': []}
    error_message = None
    if not zomato_rests and not zomato_prices:
        error_message = "Zomato data unavailable. Showing Swiggy results only."

    for rest in common_rests:
        swiggy_p = swiggy_prices[rest]
        zomato_p = zomato_prices[rest]
        diff = swiggy_p - zomato_p
        cheaper = 'Swiggy' if diff < 0 else 'Zomato' if diff > 0 else 'Tie'
        comparisons.append({
            'restaurant': rest,
            'swiggy_price': swiggy_p,
            'zomato_price': zomato_p,
            'cheaper': cheaper,
            'savings': abs(diff)
        })
        chart_data['restaurants'].append(rest[:20])
        chart_data['swiggy_prices'].append(swiggy_p)
        chart_data['zomato_prices'].append(zomato_p)

    # Fallback: Show best deals if no overlap
    if not comparisons:
        all_sw = [(name, p) for name, p in swiggy_prices.items()]
        all_zo = [(name, p) for name, p in zomato_prices.items()]
        all_sw.sort(key=lambda x: x[1])
        all_zo.sort(key=lambda x: x[1])
        comparisons.append({
            'note': f'No common restaurants for "{dish}". Swiggy deals: {all_sw[:3]}. Zomato deals: {all_zo[:3]}.'
        })

    return render_template('results.html', comparisons=comparisons, dish=dish, city=city, chart_data=chart_data, error_message=error_message)

if __name__ == '__main__':
    app.run(debug=True)
