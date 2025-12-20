#!/usr/bin/env python3
"""
Cloudplow Dashboard
Web interface for monitoring uploads, queue distribution, and service account status
"""

import sys
import os
import logging

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, render_template, jsonify, request
from utils.dashboard_data import DashboardDataProvider
import json

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
log = logging.getLogger('dashboard')

# Initialize Flask app
app = Flask(__name__)
app.config['JSON_SORT_KEYS'] = False

# Load configuration directly (avoiding argparse from Config class)
config_file = '/config/config.json'
if not os.path.exists(config_file):
    config_file = '/opt/cloudplow/config.json'
    if not os.path.exists(config_file):
        config_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config.json')

try:
    with open(config_file, 'r') as f:
        conf_data = json.load(f)
    log.info(f"Loaded config from {config_file}")
except Exception as e:
    log.error(f"Failed to load config from {config_file}: {e}")
    conf_data = {}

# Get configuration
dashboard_config = conf_data.get('dashboard', {})
config_dir = os.path.dirname(config_file)

# Try to get RC URL from dashboard config first (preferred)
rc_url = dashboard_config.get('rc_url')

# Fall back to plex config if not in dashboard config
if not rc_url:
    if 'plex' in conf_data and 'rclone' in conf_data['plex']:
        rc_url = conf_data['plex']['rclone'].get('url')

# Fallback to default RC URL if not configured anywhere
if not rc_url:
    rc_url = 'http://localhost:5572'

# Initialize data provider
data_provider = DashboardDataProvider(config_dir, rc_url)


@app.route('/')
def index():
    """Serve the main dashboard page"""
    return render_template('index.html')


@app.route('/api/status')
def api_status():
    """Get overall upload status"""
    try:
        status = data_provider.get_status()
        return jsonify(status)
    except Exception as e:
        log.error(f"Error in /api/status: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/queue_distribution')
def api_queue_distribution():
    """Get queue distribution data"""
    try:
        uploader = request.args.get('uploader')
        distribution = data_provider.get_queue_distribution(uploader)
        
        if distribution is None:
            return jsonify({'available': False})
        
        return jsonify({
            'available': True,
            'data': distribution
        })
    except Exception as e:
        log.error(f"Error in /api/queue_distribution: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/queue_status')
def api_queue_status():
    """Get queue and strategy status"""
    try:
        status = data_provider.get_queue_status()
        
        if status is None:
            return jsonify({'available': False})
        
        return jsonify({
            'available': True,
            'data': status
        })
    except Exception as e:
        log.error(f"Error in /api/queue_status: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/transfer_history')
def api_transfer_history():
    """Get transfer history data"""
    try:
        uploader = request.args.get('uploader')
        history = data_provider.get_transfer_history(uploader)
        
        if history is None:
            return jsonify({'available': False})
        
        return jsonify({
            'available': True,
            'data': history
        })
    except Exception as e:
        log.error(f"Error in /api/transfer_history: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/service_accounts')
def api_service_accounts():
    """Get service account status"""
    try:
        uploader = request.args.get('uploader')
        accounts = data_provider.get_service_accounts(uploader)
        return jsonify(accounts)
    except Exception as e:
        log.error(f"Error in /api/service_accounts: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/rclone_stats')
def api_rclone_stats():
    """Get real-time rclone RC stats"""
    try:
        stats = data_provider.get_rclone_stats()
        
        if stats is None:
            return jsonify({'available': False})
        
        return jsonify({
            'available': True,
            'data': stats
        })
    except Exception as e:
        log.error(f"Error in /api/rclone_stats: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/session_stats')
def api_session_stats():
    """Get cumulative session statistics"""
    try:
        uploader = request.args.get('uploader')
        stats = data_provider.get_session_stats(uploader)
        
        if stats is None:
            return jsonify({'available': False})
        
        return jsonify({
            'available': True,
            'data': stats
        })
    except Exception as e:
        log.error(f"Error in /api/session_stats: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/health')
def api_health():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'config_dir': config_dir,
        'rc_url': rc_url if rc_url else 'not configured'
    })


def main():
    """Run the dashboard server"""
    host = dashboard_config.get('host', '0.0.0.0')
    port = dashboard_config.get('port', 47949)
    debug = dashboard_config.get('debug', False)
    
    log.info(f"Starting Cloudplow Dashboard on {host}:{port}")
    log.info(f"Config directory: {config_dir}")
    log.info(f"Rclone RC URL: {rc_url if rc_url else 'not configured'}")
    
    try:
        app.run(host=host, port=port, debug=debug, threaded=True)
    except Exception as e:
        log.error(f"Failed to start dashboard: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()

