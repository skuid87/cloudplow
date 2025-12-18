# Dashboard Integration Guide

This guide shows how to integrate session state tracking into `cloudplow.py` to enable full dashboard functionality.

## Quick Start

The dashboard is **already functional** and will show:
- ✅ Queue distribution (from learned cache)
- ✅ Transfer history (from learned cache)  
- ✅ Service account quotas (from SA quota cache)
- ✅ Real-time transfers (from rclone RC)

However, for the **session banner and statistics** to work, you need to add session state tracking to `cloudplow.py`.

## Optional: Session State Integration

If you want to see the session status banner and session statistics, add these code snippets to `cloudplow.py`:

### 1. Import SessionStateTracker

At the top of `cloudplow.py`, add:

```python
from utils.session_state import SessionStateTracker
```

### 2. Initialize Tracker

In the `do_upload()` function, after determining the `uploader_remote`:

```python
# Initialize session state tracker for dashboard
session_tracker = SessionStateTracker(config_dir)
```

### 3. Start Session

When starting uploads for a remote, before the SA loop:

```python
# Start dashboard session
session_tracker.start_session(
    uploader=uploader_remote,
    total_sas=available_accounts_size,
    upload_folder=rclone_config['upload_folder']
)
```

### 4. Update SA Status

At the start of each SA iteration in the loop:

```python
# Update current SA in dashboard
session_tracker.update_sa(
    sa_index=i,
    sa_file=sa_file,
    total_sas=available_accounts_size
)
```

### 5. Update Stage

At the start of each stage in the multi-stage loop:

```python
# Update current stage in dashboard
session_tracker.update_stage(stage_number)
```

### 6. End Session

After all uploads complete or on error:

```python
# End dashboard session
session_tracker.end_session()
```

## Complete Integration Example

Here's a complete example showing where to add the tracking calls:

```python
def do_upload(remote=None):
    # ... existing code ...
    
    # Initialize session tracker (add near top of function)
    session_tracker = SessionStateTracker(config_dir)
    
    for uploader_remote, uploader_config in uploaders:
        # ... existing checks ...
        
        # Get available SAs
        available_accounts = get_available_service_accounts(...)
        available_accounts_size = len(available_accounts)
        
        if available_accounts_size:
            # Start session (add here)
            session_tracker.start_session(
                uploader=uploader_remote,
                total_sas=available_accounts_size,
                upload_folder=rclone_config['upload_folder']
            )
            
            for i in range(available_accounts_size):
                sa_file = available_accounts[i]
                
                # Update SA (add here)
                session_tracker.update_sa(i, sa_file, available_accounts_size)
                
                # Multi-stage loop
                stage_number = 1
                while sa_quota_remaining > 10 * 1024**3:
                    # Update stage (add here)
                    session_tracker.update_stage(stage_number)
                    
                    # ... existing stage upload code ...
                    
                    stage_number += 1
            
            # End session (add here)
            session_tracker.end_session()
```

## Testing Without Integration

You can test the dashboard without full integration by creating a dummy session file:

```bash
cat > ~/cloudplow-docker/config/dashboard_session_state.json << 'EOF'
{
  "active": true,
  "uploader": "google_media",
  "total_sas": 5,
  "sa_index": 1,
  "current_sa": "service_account_02.json",
  "stage": 3,
  "total_stages": 8,
  "session_start": "2025-12-18 14:30:00",
  "session_start_time": 1734556800.0,
  "upload_folder": "/mnt/media",
  "sas_used": ["service_account_01.json", "service_account_02.json"]
}
EOF
```

Then access the dashboard and you'll see the session banner and statistics.

## Running the Dashboard

### Method 1: Standalone (Recommended for Testing)

```bash
cd /path/to/cloudplow-master
python3 dashboard/app.py
```

Access at: `http://localhost:47949`

### Method 2: Integrated with Cloudplow (Future)

In a future update, the dashboard can be launched automatically when Cloudplow starts in `run` mode.

### Method 3: Docker

If running Cloudplow in Docker:

1. Ensure your `config.json` has the dashboard section:
```json
{
  "dashboard": {
    "enabled": true,
    "host": "0.0.0.0",
    "port": 47949
  }
}
```

2. Expose the port in `docker-compose.yml`:
```yaml
ports:
  - "7949:7949"    # Rclone RC
  - "47949:47949"  # Dashboard
```

3. Start dashboard in Docker:
```bash
docker exec -d cloudplow python3 /app/dashboard/app.py
```

Or add it to your Docker entrypoint script.

## Verifying Dashboard Functionality

### 1. Check Dashboard is Running

```bash
curl http://localhost:47949/api/health
```

Expected output:
```json
{
  "status": "healthy",
  "config_dir": "/config",
  "rc_url": "http://localhost:7949"
}
```

### 2. Check Session Status

```bash
curl http://localhost:47949/api/status
```

If no upload active:
```json
{
  "active": false,
  "message": "No active upload session"
}
```

If upload active (after integration):
```json
{
  "active": true,
  "uploader": "google_media",
  "current_sa": "service_account_02.json",
  "sa_index": 1,
  "total_sas": 5,
  "stage": 3
}
```

### 3. Check Queue Distribution

```bash
curl http://localhost:47949/api/queue_distribution | jq
```

Should show your learned distribution data if you've run uploads.

### 4. Access Web Interface

Open in browser: `http://localhost:47949`

You should see:
- Green "Connected" indicator
- Queue distribution (if available)
- Service account status (if available)
- "No Active Upload Session" message (if no uploads running)

## Troubleshooting

### Dashboard doesn't start

**Check Flask is installed:**
```bash
pip3 show Flask
```

If not:
```bash
pip3 install Flask==3.0.0
```

### "Config file not found"

Make sure you're running from the cloudplow directory and your `config.json` exists:
```bash
cd /path/to/cloudplow-master
ls -la config.json
python3 dashboard/app.py
```

### Port already in use

Change the port in `config.json`:
```json
"dashboard": {
  "port": 37949
}
```

### No queue distribution showing

Run an upload first:
```bash
python3 cloudplow.py upload
```

Wait for the first stage to complete, then check:
```bash
ls -la ~/cloudplow-docker/config/learned_sizes_cache.json
```

### Rclone stats not available

Ensure rclone RC is configured in `config.json`:
```json
"plex": {
  "rclone": {
    "url": "http://localhost:7949"
  }
}
```

And that rclone is running with RC enabled:
```bash
--rc --rc-addr=:7949 --rc-no-auth
```

## Next Steps

1. ✅ **Dashboard is built** - All files are in place
2. ✅ **Dependencies added** - Flask in requirements.txt
3. ✅ **Configuration ready** - Sample config updated
4. ⏭️ **Optional integration** - Add session tracking to cloudplow.py
5. 🚀 **Start using** - Run dashboard and start monitoring!

## Files Created

```
dashboard/
├── app.py                      # Flask web server
├── templates/
│   └── index.html             # Dashboard UI
└── static/                    # (empty, for future assets)

utils/
├── dashboard_data.py          # Data provider
└── session_state.py           # Session tracking helper

DASHBOARD_README.md            # Complete documentation
DASHBOARD_INTEGRATION.md       # This file
```

## Summary

The dashboard is **fully functional** without any changes to `cloudplow.py`. It will show:
- Queue distribution
- Transfer history
- Service account quotas
- Real-time transfers

Adding session state tracking is **optional** but recommended for seeing:
- Session status banner
- Current SA and stage indicators
- Session progress statistics

Enjoy your new dashboard! 🎉

