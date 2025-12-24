# Rclone RC Standalone Daemon

This feature allows you to run a standalone `rclone rcd` (Remote Control Daemon) that starts automatically when cloudplow begins uploading. This provides persistent access to the rclone RC API and Web GUI without tying it to individual transfer operations.

## Why Use Standalone RC?

### Without Standalone RC (Old Method)
Previously, you might have added RC flags directly to your `rclone_extras`:
```json
"rclone_extras": {
    "--rc": null,
    "--rc-addr": "0.0.0.0:5572",
    "--rc-web-gui": null
}
```

**Problems with this approach:**
- RC server starts and stops with each upload command
- Web GUI is only accessible during transfers
- Multiple uploads could conflict on the same port
- Less efficient resource usage

### With Standalone RC (New Method)
The standalone RC daemon (`rclone rcd`) runs independently:
- ✅ Persistent server that runs continuously
- ✅ Web GUI always accessible
- ✅ Better performance and resource efficiency
- ✅ No port conflicts between uploads
- ✅ Dashboard can always connect for real-time stats

## Configuration

### Step 1: Enable Standalone RC

Add the `standalone_rc` configuration to your `config.json` under the `dashboard` section:

```json
{
    "dashboard": {
        "enabled": true,
        "host": "0.0.0.0",
        "port": 47949,
        "rc_url": "http://localhost:5572",
        "standalone_rc": {
            "enabled": true,
            "port": 5572,
            "rc_addr": "0.0.0.0:5572",
            "rc_no_auth": false,
            "rc_user": "admin",
            "rc_pass": "your_secure_password",
            "rc_web_gui": true,
            "rc_web_gui_no_open_browser": true,
            "verbose": false
        }
    }
}
```

### Step 2: Remove RC Flags from Remotes

**IMPORTANT:** Remove all RC-related flags from your `rclone_extras` in the `remotes` section:

```json
"remotes": {
    "google": {
        "rclone_extras": {
            // ❌ REMOVE THESE:
            // "--rc": null,
            // "--rc-addr": "0.0.0.0:5572",
            // "--rc-web-gui": null,
            // "--rc-web-gui-no-open-browser": null,
            // "--rc-no-auth": null,
            
            // ✅ KEEP THESE:
            "--drive-chunk-size": "64M",
            "--transfers": 8,
            "--checkers": 16
        }
    }
}
```

If you don't remove them, your upload commands will try to start their own RC server on the same port and fail.

### Step 3: Update Plex RC URL (if using Plex throttling)

Make sure your Plex config points to the standalone RC server:

```json
"plex": {
    "enabled": true,
    "rclone": {
        "url": "http://localhost:5572"
    }
}
```

## Configuration Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `enabled` | boolean | `false` | Enable standalone RC daemon auto-start |
| `port` | integer | `5572` | Port number for RC server |
| `rc_addr` | string | `"0.0.0.0:5572"` | Address to bind RC server to |
| `rc_no_auth` | boolean | `true` | Disable authentication (⚠️ insecure!) |
| `rc_user` | string | `""` | Username for basic auth |
| `rc_pass` | string | `""` | Password for basic auth |
| `rc_htpasswd` | string | `""` | Path to htpasswd file for multi-user auth |
| `rc_web_gui` | boolean | `true` | Enable the Web GUI |
| `rc_web_gui_no_open_browser` | boolean | `true` | Don't auto-open browser |
| `verbose` | boolean | `false` | Enable verbose logging for RC daemon |

## Security Considerations

### ⚠️ Authentication is Critical!

By default, the RC server has **NO AUTHENTICATION** if you set `rc_no_auth: true`. This means anyone who can access the port can:
- View all your transfers
- Access your remote storage
- Execute any rclone command
- Read/write/delete files

### Recommended Security Settings

#### Option 1: Basic Authentication (Simple)
```json
"standalone_rc": {
    "enabled": true,
    "rc_no_auth": false,
    "rc_user": "admin",
    "rc_pass": "strong_random_password_here"
}
```

#### Option 2: htpasswd File (Multiple Users)
```bash
# Create htpasswd file
htpasswd -c /config/rclone_htpasswd admin
htpasswd /config/rclone_htpasswd user2
```

```json
"standalone_rc": {
    "enabled": true,
    "rc_no_auth": false,
    "rc_htpasswd": "/config/rclone_htpasswd"
}
```

#### Option 3: Localhost Only + SSH Tunnel (Most Secure)
```json
"standalone_rc": {
    "enabled": true,
    "rc_addr": "127.0.0.1:5572",
    "rc_no_auth": true
}
```

Then access remotely via SSH tunnel:
```bash
ssh -L 5572:localhost:5572 user@your-server
```

Access at: `http://localhost:5572`

## Usage

### Starting the RC Daemon

The RC daemon starts automatically when you run an upload:

```bash
python3 cloudplow.py upload
```

**Output:**
```
[INFO] Starting upload
[INFO] Starting standalone rclone RC daemon (rcd) in background...
[INFO] Rclone RC daemon started successfully on port 5572
[INFO] RC Web GUI available at: http://localhost:5572
```

### Accessing the Web GUI

Once running, access the Web GUI at:
```
http://localhost:5572
```

Or from another machine:
```
http://your-server-ip:5572
```

The Web GUI provides:
- Real-time transfer statistics
- File browser for all remotes
- Manual file operations (copy, move, delete)
- Configuration management
- Job status and control

### Checking if RC is Running

```bash
# Method 1: Check port
lsof -i :5572

# Method 2: Query health endpoint
curl http://localhost:5572/rc/noop

# Method 3: Check processes
ps aux | grep "rclone rcd"
```

### Stopping the RC Daemon

```bash
# Find the process
ps aux | grep "rclone rcd"

# Kill it
kill <pid>

# Or kill all rcd processes
pkill -f "rclone rcd"
```

The daemon will automatically restart on the next upload if `enabled: true`.

## Integration with Dashboard

The cloudplow dashboard automatically connects to the RC server to display:
- Real-time transfer speeds
- Currently transferring files
- Transfer progress and ETAs
- Queue statistics

Make sure `dashboard.rc_url` matches your standalone RC address:
```json
"dashboard": {
    "rc_url": "http://localhost:5572"
}
```

## Troubleshooting

### "Port already in use"

**Cause:** Another service (or an old RC daemon) is using port 5572.

**Solution:**
```bash
# Find what's using the port
lsof -i :5572

# Kill it
kill <pid>

# Or change the port in config.json
"standalone_rc": {
    "port": 5573,
    "rc_addr": "0.0.0.0:5573"
}
```

### "RC daemon started but port is not responding"

**Cause:** Firewall blocking the port or rclone binary not found.

**Solution:**
```bash
# Check firewall
sudo ufw allow 5572

# Verify rclone binary path in config
"core": {
    "rclone_binary_path": "/usr/bin/rclone"
}

# Test manually
/usr/bin/rclone rcd --rc-addr=:5572 --rc-no-auth
```

### "Authentication failed" in Web GUI

**Cause:** Wrong username/password or auth not properly configured.

**Solution:**
- Double-check `rc_user` and `rc_pass` in config
- Or temporarily set `rc_no_auth: true` for testing
- Check logs: `tail -f cloudplow.log | grep -i "rc"`

### RC flags still in rclone_extras causing conflicts

**Error:**
```
Failed to start rc: listen tcp :5572: bind: address already in use
```

**Solution:**
Remove ALL RC flags from your `rclone_extras`:
```json
"rclone_extras": {
    // Remove these lines:
    // "--rc": null,
    // "--rc-addr": "...",
}
```

### Web GUI not accessible from network

**Cause:** RC server bound to localhost only or firewall.

**Solution:**
```json
"standalone_rc": {
    "rc_addr": "0.0.0.0:5572"  // Not "127.0.0.1:5572"
}
```

Then allow through firewall:
```bash
sudo ufw allow 5572
```

## Docker Usage

### Docker Compose Configuration

```yaml
services:
  cloudplow:
    image: cloudplow:latest
    container_name: cloudplow
    ports:
      - "47949:47949"  # Dashboard
      - "5572:5572"    # Rclone RC (new)
    volumes:
      - ~/cloudplow-docker/config:/config
    environment:
      - TZ=America/New_York
```

### Starting in Docker

```bash
# Start container
docker-compose up -d

# Trigger upload (RC daemon will auto-start)
docker exec cloudplow python3 cloudplow.py upload

# Access Web GUI
http://localhost:5572
```

## Advanced Usage

### Custom RC Commands via API

With the RC daemon running, you can send commands via HTTP:

```bash
# List remotes
curl http://localhost:5572/config/listremotes

# Get transfer stats
curl http://localhost:5572/core/stats

# Bandwidth limit
curl -X POST http://localhost:5572/core/bwlimit \
  -H "Content-Type: application/json" \
  -d '{"rate": "10M"}'
```

With authentication:
```bash
curl -u admin:password http://localhost:5572/core/stats
```

### Running Multiple Instances

You can run multiple RC daemons on different ports:

```json
"standalone_rc": {
    "enabled": true,
    "port": 5573,  // Different port
    "rc_addr": "0.0.0.0:5573"
}
```

## Performance Notes

- **Memory Usage:** ~50-100MB per RC daemon
- **CPU Usage:** <1% when idle, 2-5% during active transfers
- **Network:** Minimal overhead, only serves API requests

The RC daemon is very lightweight and has negligible impact on upload performance.

## References

- [Rclone RC Documentation](https://rclone.org/rc/)
- [Rclone rcd Command](https://rclone.org/commands/rclone_rcd/)
- [RC API Reference](https://rclone.org/rc/#api)
- [Web GUI Setup](https://rclone.org/gui/)

## Support

For issues or questions:
1. Check logs: `tail -f cloudplow.log | grep -i "rc"`
2. Test manually: `rclone rcd --rc-addr=:5572 --rc-no-auth -v`
3. Verify config: `cat config.json | jq .dashboard.standalone_rc`
4. Open an issue on GitHub

## License

Same license as Cloudplow (GPL-3.0)

