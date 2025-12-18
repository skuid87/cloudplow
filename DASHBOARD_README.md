# Cloudplow Dashboard

A beautiful, real-time web dashboard for monitoring Cloudplow uploads, queue distribution, and service account status.

## Features

### 📊 Real-Time Monitoring
- **Current Transfers**: Live view of files being uploaded with progress bars
- **Transfer Speed**: Real-time speed monitoring and ETA calculations
- **Active Files**: See exactly which files are transferring and their individual progress

### 📈 Queue Intelligence
- **Distribution Visualization**: See the size distribution of files in your upload queue
- **Percentile Analysis**: P50, P75, P90, P95, P99 file size breakdowns
- **Confidence Levels**: Know how accurate the learned distribution is
- **Size Buckets**: Visual breakdown by file size categories

### 🔐 Service Account Management
- **SA Status**: See which accounts are active, complete, or ready
- **Quota Tracking**: Real-time quota usage and remaining capacity per SA
- **Reset Timers**: Countdown to quota reset for each service account
- **Visual Indicators**: Color-coded status badges and progress bars

### 📉 Session Statistics
- **Progress Tracking**: Files and bytes transferred vs total
- **Session Duration**: How long the current upload session has been running
- **Average Speed**: Overall transfer speed across the session
- **ETA**: Estimated time to completion based on current progress
- **Multi-SA Summary**: Total files transferred across all service accounts

## Installation

### 1. Install Dependencies

The dashboard requires Flask, which has been added to `requirements.txt`:

```bash
pip install -r requirements.txt
```

Or install Flask directly:

```bash
pip install Flask==3.0.0
```

### 2. Configure Dashboard

Add the dashboard configuration to your `config.json`:

```json
{
  "dashboard": {
    "enabled": true,
    "host": "0.0.0.0",
    "port": 47949,
    "refresh_interval": 3,
    "debug": false
  }
}
```

**Configuration Options:**
- `enabled`: Set to `true` to enable the dashboard
- `host`: Interface to bind to (`0.0.0.0` for all interfaces, `127.0.0.1` for localhost only)
- `port`: Port to run dashboard on (default: `47949`)
- `refresh_interval`: How often to refresh data in seconds (default: `3`)
- `debug`: Enable Flask debug mode (default: `false`)

### 3. Docker Configuration

If running in Docker, expose the dashboard port in your `docker-compose.yml`:

```yaml
services:
  cloudplow:
    ports:
      - "7949:7949"    # Rclone RC (existing)
      - "47949:47949"  # Dashboard (new)
    volumes:
      - ~/cloudplow-docker/config:/config
```

Restart your container:

```bash
docker-compose down
docker-compose up -d
```

## Usage

### Starting the Dashboard

The dashboard now **automatically starts** when you begin an upload! No manual start required.

#### Auto-Start (Recommended)

Simply enable the dashboard in your `config.json`:

```json
{
  "dashboard": {
    "enabled": true,
    "host": "0.0.0.0",
    "port": 47949
  }
}
```

Then start your upload:

```bash
python3 cloudplow.py upload
```

The dashboard will start automatically in the background! You'll see:

```
[INFO] Starting dashboard in background...
[INFO] Dashboard started successfully on port 47949
[INFO] Access dashboard at: http://localhost:47949
```

See [DASHBOARD_AUTO_START.md](DASHBOARD_AUTO_START.md) for full details on the auto-start feature.

#### Manual Mode (Optional)

You can also start the dashboard manually if preferred:

```bash
cd /path/to/cloudplow-master
python3 dashboard/app.py
```

Or use the convenience script:

```bash
./start_dashboard.sh
```

#### Docker Mode

In Docker, the dashboard auto-starts when uploads begin if `enabled: true` is set in config.

### Accessing the Dashboard

Once running, access the dashboard at:

```
http://localhost:47949
```

Or from another machine on your network:

```
http://your-server-ip:47949
```

### Dashboard Interface

The dashboard updates automatically every 3 seconds (configurable) and shows:

1. **Status Banner** (when upload active)
   - Current uploader name
   - Active service account
   - SA position (e.g., "2/5")
   - Current stage number

2. **Current Transfers** (from Rclone RC)
   - Individual file progress bars
   - File sizes and transfer speeds
   - ETAs per file
   - Overall transfer speed and ETA

3. **Queue Distribution** (learned from checkers)
   - Total files and size in queue
   - Percentile breakdown (P50-P99)
   - Size distribution visualization
   - Confidence level indicator

4. **Session Statistics**
   - Files progress (transferred/total)
   - Data progress (bytes transferred/total)
   - Session duration and average speed
   - Remaining data and ETA
   - Service accounts used count

5. **Service Account Status**
   - Table of all SAs with status indicators
   - Quota usage per SA (used/total GB)
   - Visual progress bars
   - Reset countdown timers

## Data Sources

The dashboard aggregates data from multiple sources:

### Cache Files (in `/config/`)
- `sa_quota_cache.json` - Service account quota tracking
- `learned_sizes_cache.json` - Queue distribution and transfer history
- `dashboard_session_state.json` - Current upload session state

### Rclone RC API
- `http://localhost:7949/core/stats` - Real-time transfer statistics
- Provides live transfer data, speeds, and ETAs

### Requirements

For the dashboard to show all data:
1. **Rclone RC must be enabled** - Configure in your `plex.rclone.url` setting
2. **Uploads must be active** - Dashboard shows "No Active Upload Session" when idle
3. **Queue distribution** - Captured automatically during first upload

## API Endpoints

The dashboard exposes REST API endpoints:

```
GET  /                          # Dashboard HTML interface
GET  /api/health                # Health check
GET  /api/status                # Overall upload status
GET  /api/queue_distribution    # Queue distribution data
GET  /api/transfer_history      # Transfer history data
GET  /api/service_accounts      # SA status and quotas
GET  /api/rclone_stats          # Real-time rclone statistics
GET  /api/session_stats         # Cumulative session stats
```

You can query these endpoints directly for integration with other tools:

```bash
# Check status
curl http://localhost:47949/api/status | jq

# Get queue distribution
curl http://localhost:47949/api/queue_distribution | jq

# Get service accounts
curl http://localhost:47949/api/service_accounts | jq
```

## Troubleshooting

### Dashboard Shows "No Active Upload Session"

**Cause**: No upload is currently running or session state file doesn't exist.

**Solution**: 
1. Start an upload: `python3 cloudplow.py upload`
2. Ensure cache directory is writable
3. Check logs for errors

### "Rclone stats not available"

**Cause**: Rclone RC API is not configured or not accessible.

**Solution**:
1. Verify `plex.rclone.url` is set in `config.json`:
   ```json
   "plex": {
     "rclone": {
       "url": "http://localhost:7949"
     }
   }
   ```
2. Ensure rclone is started with RC enabled:
   ```bash
   --rc --rc-addr=:7949 --rc-no-auth
   ```

### "Queue distribution not available"

**Cause**: First upload hasn't started yet or queue learning failed.

**Solution**:
1. Let the first upload stage complete - queue is learned during checking phase
2. Check `learned_sizes_cache.json` exists in `/config/`
3. Review cloudplow logs for queue learning messages

### Port 47949 already in use

**Cause**: Another service is using the port.

**Solution**:
1. Change the port in `config.json`:
   ```json
   "dashboard": {
     "port": 37949
   }
   ```
2. Update Docker port mapping if using Docker

### Cannot access dashboard from other devices

**Cause**: Firewall or host binding issue.

**Solution**:
1. Ensure `host` is set to `0.0.0.0` (not `127.0.0.1`)
2. Open firewall port: `sudo ufw allow 47949`
3. If using Docker, verify port mapping

## Security Considerations

### Network Exposure

The dashboard has **no authentication** by default. If exposing to the internet:

1. **Use a reverse proxy** with authentication (nginx, Caddy, Traefik)
2. **Use a VPN** or secure tunnel (Tailscale, WireGuard)
3. **Bind to localhost only** and use SSH tunneling:
   ```json
   "dashboard": {
     "host": "127.0.0.1"
   }
   ```
   Then SSH tunnel:
   ```bash
   ssh -L 47949:localhost:47949 user@server
   ```

### Docker Security

When running in Docker with `host: "0.0.0.0"`, the dashboard is accessible from your network. Use Docker's internal networking if you only need local access.

## Performance

### Resource Usage

The dashboard is very lightweight:
- **Memory**: ~50-100MB (Flask + templates)
- **CPU**: Minimal (<1% on most systems)
- **Network**: ~5-10KB per refresh cycle

### Scaling

- Handles 10+ concurrent viewers without issues
- Data refresh happens server-side (clients just fetch JSON)
- No database required - reads directly from cache files

## Customization

### Refresh Interval

Change how often the dashboard updates:

```json
"dashboard": {
  "refresh_interval": 5
}
```

Recommended: 2-5 seconds (lower = more real-time, higher = less resource usage)

### Port Selection

Port `47949` was chosen to relate to rclone's port `7949`. You can use any available port:

```json
"dashboard": {
  "port": 8080
}
```

## Integration

### Monitoring Tools

The API endpoints can be integrated with monitoring tools:

**Prometheus:**
```yaml
scrape_configs:
  - job_name: 'cloudplow'
    metrics_path: '/api/session_stats'
    static_configs:
      - targets: ['localhost:47949']
```

**Grafana:**
Use the JSON API data source to create custom Grafana dashboards.

### Notifications

Combine with existing Cloudplow notifications for complete monitoring:
- Dashboard for real-time monitoring
- Notifications for important events

## Future Enhancements

Potential features for future versions:
- [ ] WebSocket support for push updates (no polling)
- [ ] Historical data graphs (transfer speed over time)
- [ ] Multi-uploader support (view all uploaders at once)
- [ ] Export session data to CSV/JSON
- [ ] Dark/light theme toggle
- [ ] Mobile-optimized responsive design improvements
- [ ] Authentication support (basic auth, OAuth)
- [ ] Configurable alerts and thresholds

## Credits

- Built with [Flask](https://flask.palletsprojects.com/)
- Styled with [Tailwind CSS](https://tailwindcss.com/)
- Charts with [Chart.js](https://www.chartjs.org/)
- Integrates with [Rclone](https://rclone.org/) RC API

## Support

For issues, questions, or feature requests:
1. Check the troubleshooting section above
2. Review Cloudplow logs: `tail -f ~/cloudplow-docker/config/cloudplow.log`
3. Open an issue on GitHub

## License

Same license as Cloudplow (GPL-3.0)

