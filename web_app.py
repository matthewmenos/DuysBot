"""
web_app.py - Flask web server for DUYS Trading Bot dashboard.
Serves the index.html and provides API endpoints for charts and data.

Run:
    python web_app.py
"""

from flask import Flask, render_template, jsonify
import os
from config import BOT_TOKEN  # Assuming config has necessary settings
from exchange import get_exchange  # Assuming exchange.py has get_exchange function
from strategy import get_signals  # Assuming strategy.py has signal functions
import ccxt

app = Flask(__name__, template_folder='.')

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/chart_data/<symbol>')
def chart_data(symbol):
    # Fetch historical OHLCV data for the symbol
    exchange = ccxt.binance()  # Default to Binance, can be made configurable
    try:
        ohlcv = exchange.fetch_ohlcv(f'{symbol}/USDT', timeframe='1h', limit=100)
        data = {
            'timestamps': [candle[0] for candle in ohlcv],
            'open': [candle[1] for candle in ohlcv],
            'high': [candle[2] for candle in ohlcv],
            'low': [candle[3] for candle in ohlcv],
            'close': [candle[4] for candle in ohlcv],
            'volume': [candle[5] for candle in ohlcv]
        }
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/signals')
def signals():
    # Get current signals
    signals_data = get_signals()  # Implement in strategy.py
    return jsonify(signals_data)

if __name__ == '__main__':
    app.run(debug=True, port=5000)