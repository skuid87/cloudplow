# Dashboard Auto-Start Feature ✨

The dashboard now **automatically starts** when you begin an upload! No need to manually start it in a separate terminal.

## How It Works

When you run `python3 cloudplow.py upload` (or when scheduled uploads start in `run` mode), the system:

1. ✅ **Checks if dashboard is enabled** in your `config.json`
2. ✅ **Checks if dashboard is already running** on the configured port
3. ✅ **Starts dashboard automatically** if not running
4. ✅ **Runs in background** as a detached process
5. ✅ **Logs the dashboard URL** so you know where to access it

## Configuration

Ensure dashboard is enabled in your `config.json`:

```json
{
  "dashboard": {
    "enabled": true,
    "host": "0.0.0.0",
    "port": 47949,
    "refresh_interval": 3
  }
}
```

### Configuration Options

- **`enabled`**: Set to `true` to enable auto-start (default: `false`)
- **`host`**: Interface to bind to (default: `"0.0.0.0"`)
- **`port`**: Port number (default: `47949`)
- **`refresh_interval`**: How often dashboard refreshes in seconds (default: `3`)

## Usage Examples

### Manual Upload Mode

```bash
cd /path/to/cloudplow-master
python3 cloudplow.py upload
```

**Output:**
```
2025-12-18 20:30:00 - cloudplow - INFO - Starting upload
2025-12-18 20:30:00 - cloudplow - INFO - Starting dashboard in background...
2025-12-18 20:30:02 - cloudplow - INFO - Dashboard started successfully on port 47949
2025-12-18 20:30:02 - cloudplow - INFO - Access dashboard at: http://localhost:47949
```

The dashboard is now running! Open `http://localhost:47949` in your browser.

### Scheduled Mode (Run)

```bash
python3 cloudplow.py run
```

When the scheduled upload triggers, the dashboard starts automatically.

### Docker Mode

If running in Docker, the dashboard will auto-start when uploads begin. Just make sure:

1. Dashboard is enabled in `config.json`
2. Port `47949` is exposed in `docker-compose.yml`:

```yaml
ports:
  - "7949:7949"    # Rclone RC
  - "47949:47949"  # Dashboard
```

Then access from your host: `http://localhost:47949`

## Benefits

### ✅ No Manual Start Required
Just start your upload and the dashboard launches automatically.

### ✅ Always Available
The dashboard stays running even after uploads complete, so you can review stats anytime.

### ✅ Smart Detection
Won't start duplicate instances - checks if dashboard is already running first.

### ✅ Background Process
Runs detached so it doesn't block your upload process.

### ✅ Clean Logs
Shows you exactly when and where the dashboard is accessible.

## Log Messages

### Successful Auto-Start

```
[INFO] Starting dashboard in background...
[INFO] Dashboard started successfully on port 47949
[INFO] Access dashboard at: http://localhost:47949
```

### Dashboard Already Running

```
[INFO] Dashboard is already running
```

### Dashboard Not Enabled

```
[DEBUG] Dashboard not enabled in config, skipping auto-start
```

### Auto-Start Failed

```
[WARNING] Failed to auto-start dashboard: [error message]
```

The upload will continue normally even if dashboard fails to start.

## Manual Control

You can still start/stop the dashboard manually if needed:

### Start Manually

```bash
python3 dashboard/app.py
```

Or use the convenience script:

```bash
./start_dashboard.sh
```

### Stop Dashboard

```bash
# Find the dashboard process
ps aux | grep "dashboard/app.py"

# Kill it
kill <pid>
```

Or:

```bash
# Kill all dashboard processes
pkill -f "dashboard/app.py"
```

### Check If Running

```bash
# Try to connect to the port
curl http://localhost:47949/api/health
```

Expected response if running:
```json
{
  "status": "healthy",
  "config_dir": "/config",
  "rc_url": "http://localhost:7949"
}
```

## Behavior Details

### Multiple Upload Sessions

- **First upload**: Dashboard starts automatically
- **Subsequent uploads**: Dashboard is already running, no new instance created
- **Dashboard persists**: Stays running between upload sessions

### Port Conflicts

If port `47949` is already in use by another service:

1. Change the port in `config.json`:
   ```json
   "dashboard": {
     "port": 37949
   }
   ```

2. Restart your upload - dashboard will auto-start on the new port

### Disable Auto-Start

To disable auto-start but keep dashboard available for manual use:

```json
{
  "dashboard": {
    "enabled": false
  }
}
```

Then start manually when needed:
```bash
./start_dashboard.sh
```

## Troubleshooting

### Dashboard doesn't start

**Check config:**
```bash
cat config.json | grep -A 5 "dashboard"
```

Ensure `"enabled": true` is set.

**Check logs:**
```bash
tail -f cloudplow.log | grep -i dashboard
```

Look for error messages about why it failed to start.

**Check port availability:**
```bash
lsof -i :47949
```

If another process is using the port, either stop it or change the dashboard port.

**Check dashboard script exists:**
```bash
ls -la dashboard/app.py
```

Should show the Flask app file.

### Dashboard starts but shows errors

**Check Flask is installed:**
```bash
pip3 show Flask
```

If not installed:
```bash
pip3 install -r requirements.txt
```

**Check permissions:**
```bash
ls -la dashboard/app.py
```

Should be readable and executable.

### Dashboard accessible from localhost but not other devices

**Change host in config:**
```json
{
  "dashboard": {
    "host": "0.0.0.0"
  }
}
```

`0.0.0.0` allows connections from any interface.

**Check firewall:**
```bash
sudo ufw allow 47949
```

## Docker Specific

### Docker Auto-Start

In Docker, when you run:
```bash
docker exec cloudplow python3 cloudplow.py upload
```

The dashboard auto-starts inside the container. Access it from your host at `http://localhost:47949` (assuming port is mapped).

### Docker Compose Integration

Your `docker-compose.yml` should include:

```yaml
services:
  cloudplow:
    image: cloudplow:latest
    container_name: cloudplow
    ports:
      - "7949:7949"    # Rclone RC
      - "47949:47949"  # Dashboard (auto-starts)
    volumes:
      - ~/cloudplow-docker/config:/config
    environment:
      - TZ=America/New_York
```

### Persistent Dashboard in Docker

To ensure dashboard stays running even when container restarts, you could add it to your Docker entrypoint:

```bash
#!/bin/bash
# Start dashboard in background
python3 /app/dashboard/app.py &

# Start cloudplow
python3 /app/cloudplow.py run
```

## Performance Impact

The dashboard auto-start feature has **minimal impact**:

- **Startup time**: Adds ~2 seconds to upload initialization (one-time)
- **Memory**: Dashboard uses ~50-100MB RAM
- **CPU**: <1% during normal operation
- **Network**: Dashboard only listens, doesn't make external requests

The dashboard runs independently and doesn't affect upload performance.

## Security Considerations

The dashboard has **no authentication** by default. When auto-starting with `host: "0.0.0.0"`:

### Recommendations

1. **Use on trusted networks only** (home/private networks)
2. **Don't expose to internet** without additional security
3. **Use reverse proxy** if you need remote access:
   ```nginx
   location /dashboard/ {
       auth_basic "Cloudplow Dashboard";
       auth_basic_user_file /etc/nginx/.htpasswd;
       proxy_pass http://localhost:47949/;
   }
   ```

4. **Or use SSH tunnel** for secure remote access:
   ```bash
   ssh -L 47949:localhost:47949 user@server
   ```
   Then access locally: `http://localhost:47949`

## Advanced: Custom Start Logic

If you need custom dashboard startup behavior, you can modify the `start_dashboard_if_needed()` function in `cloudplow.py`.

Example - start only on weekends:
```python
def start_dashboard_if_needed():
    # Only auto-start on weekends
    if datetime.datetime.now().weekday() not in [5, 6]:
        log.debug("Not weekend, skipping dashboard auto-start")
        return False
    
    # ... rest of function ...
```

## Summary

The dashboard auto-start feature provides **seamless monitoring** without manual intervention:

✅ **Automatic** - Starts when uploads begin  
✅ **Smart** - Detects if already running  
✅ **Persistent** - Stays running for continuous monitoring  
✅ **Optional** - Can be disabled in config  
✅ **Safe** - Doesn't affect uploads if startup fails  

**Just enable it in your config and forget about it!** The dashboard will be there whenever you need it. 🚀

