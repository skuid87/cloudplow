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
from utils import config

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
log = logging.getLogger('dashboard')

# Initialize Flask app
app = Flask(__name__)
app.config['JSON_SORT_KEYS'] = False

# Load configuration
conf = config.Config()
conf.load()

# Get configuration
dashboard_config = conf.configs.get('dashboard', {})
config_dir = os.path.dirname(conf.settings.get('config', './config.json'))
rc_url = None

# Try to get RC URL from plex config (existing pattern)
if 'plex' in conf.configs and 'rclone' in conf.configs['plex']:
    rc_url = conf.configs['plex']['rclone'].get('url')

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

