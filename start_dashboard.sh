#!/bin/bash
# Quick start script for Cloudplow Dashboard

echo "========================================="
echo "  Cloudplow Dashboard Starter"
echo "========================================="
echo ""

# Get the directory where this script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

# Check if Flask is installed
if ! python3 -c "import flask" 2>/dev/null; then
    echo "❌ Flask is not installed!"
    echo ""
    echo "Installing Flask..."
    pip3 install Flask==3.0.0
    echo ""
fi

# Check if config exists
if [ ! -f "config.json" ]; then
    echo "⚠️  Warning: config.json not found"
    echo "   The dashboard may not work correctly without a valid config file"
    echo ""
fi

# Start the dashboard
echo "🚀 Starting Cloudplow Dashboard..."
echo ""
echo "   Dashboard will be available at:"
echo "   → http://localhost:47949"
echo ""
echo "   Press Ctrl+C to stop"
echo ""
echo "========================================="
echo ""

python3 dashboard/app.py

