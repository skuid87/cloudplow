# Rclone RC Standalone Implementation Summary

## What Was Implemented

A standalone rclone RC (Remote Control) daemon feature has been added to cloudplow that automatically starts when uploads begin. This provides persistent access to the rclone RC API and Web GUI.

## Changes Made

### 1. Core Implementation (`cloudplow.py`)

Added two new functions:

- **`is_rclone_rc_running(port)`** - Checks if RC server is already running on specified port
- **`start_rclone_rc_if_needed()`** - Starts `rclone rcd` daemon if configured and not already running

The RC daemon is started in the `do_upload()` function, right before the dashboard starts, ensuring it's available for both the dashboard and external tools.

### 2. Configuration (`config.json.sample`)

Added new `standalone_rc` section under `dashboard`:

```json
"dashboard": {
    "standalone_rc": {
        "enabled": false,
        "port": 5572,
        "rc_addr": "0.0.0.0:5572",
        "rc_no_auth": true,
        "rc_user": "",
        "rc_pass": "",
        "rc_htpasswd": "",
        "rc_web_gui": true,
        "rc_web_gui_no_open_browser": true,
        "verbose": false
    }
}
```

### 3. Documentation

Created comprehensive documentation:
- **`RCLONE_RC_STANDALONE.md`** - Full feature documentation with examples
- **`RCLONE_RC_IMPLEMENTATION.md`** - This summary document

## Quick Start Guide

### Step 1: Update Your `config.json`

Add the standalone RC configuration (enable it):

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
            "rc_web_gui_no_open_browser": true
        }
    }
}
```

### Step 2: Remove RC Flags from `rclone_extras`

**CRITICAL:** Remove these from your remotes configuration:

```json
"remotes": {
    "google": {
        "rclone_extras": {
            // ❌ DELETE THESE LINES:
            // "--rc": null,
            // "--rc-addr": "0.0.0.0:5572",
            // "--rc-web-gui": null,
            // "--rc-web-gui-no-open-browser": null,
            // "--rc-no-auth": null,
            
            // ✅ KEEP OTHER FLAGS:
            "--drive-chunk-size": "64M",
            "--transfers": 8
        }
    }
}
```

### Step 3: Start an Upload

```bash
python3 cloudplow.py upload
```

You'll see:
```
[INFO] Starting upload
[INFO] Starting standalone rclone RC daemon (rcd) in background...
[INFO] Rclone RC daemon started successfully on port 5572
[INFO] RC Web GUI available at: http://localhost:5572
```

### Step 4: Access the Web GUI

Open your browser to:
```
http://localhost:5572
```

Login with the credentials you set in config (if auth enabled).

## Key Benefits

1. **Persistent RC Server** - Stays running between uploads
2. **Always-On Web GUI** - Access at any time
3. **Better Performance** - Dedicated daemon vs. per-upload server
4. **Dashboard Integration** - Real-time stats in cloudplow dashboard
5. **External Tool Access** - API available for custom scripts

## Security Warning

⚠️ **Do NOT use `rc_no_auth: true` if exposing to network!**

The RC server has full access to your remotes. Always use authentication when binding to `0.0.0.0`.

### Recommended Settings:

**For Local Use Only:**
```json
"rc_addr": "127.0.0.1:5572",
"rc_no_auth": true
```

**For Network Access:**
```json
"rc_addr": "0.0.0.0:5572",
"rc_no_auth": false,
"rc_user": "admin",
"rc_pass": "strong_random_password"
```

## Architecture

```
┌─────────────────┐
│  cloudplow.py   │
│   (do_upload)   │
└────────┬────────┘
         │
         ├─────────────────────────────────────┐
         │                                     │
         ▼                                     ▼
┌────────────────────┐              ┌──────────────────┐
│  rclone rcd        │◄─────────────┤  Dashboard       │
│  (Port 5572)       │  RC API      │  (Port 47949)    │
│                    │              │                  │
│  - Web GUI         │              │  - Real-time     │
│  - RC API          │              │    stats         │
│  - Always running  │              │  - Progress bars │
└────────────────────┘              └──────────────────┘
         ▲
         │
         │ RC Commands
         │
┌────────┴────────┐
│  rclone copy    │
│  (Upload Job)   │
└─────────────────┘
```

## How It Works

1. **Upload starts** - `do_upload()` is called
2. **RC check** - `is_rclone_rc_running()` checks if daemon exists
3. **Start daemon** - If not running, `start_rclone_rc_if_needed()` spawns `rclone rcd`
4. **Dashboard starts** - Connects to RC API at configured URL
5. **Upload begins** - Transfer commands don't start their own RC server
6. **Persistent** - RC daemon stays running after uploads complete

## Testing Your Setup

### 1. Check RC is Running

```bash
# Check process
ps aux | grep "rclone rcd"

# Check port
lsof -i :5572

# Query API
curl http://localhost:5572/rc/noop
```

### 2. Test Authentication (if enabled)

```bash
# Should fail (401)
curl http://localhost:5572/core/stats

# Should succeed
curl -u admin:password http://localhost:5572/core/stats
```

### 3. Access Web GUI

Open browser to `http://localhost:5572` and verify:
- Login page appears (if auth enabled)
- After login, you see the rclone Web GUI
- Can browse remotes
- Can see transfer stats

## Troubleshooting

### RC Daemon Won't Start

**Check logs:**
```bash
tail -f cloudplow.log | grep -i "rc\|rcd"
```

**Test manually:**
```bash
/usr/bin/rclone rcd --rc-addr=:5572 --rc-no-auth -v
```

### Port Conflicts

**Error:** "Port already in use"

```bash
# Find what's using port 5572
lsof -i :5572

# Kill it
kill <pid>
```

Or change the port in `config.json`.

### Web GUI Shows Authentication Error

If you enabled auth but Web GUI shows "Unauthorized":
- Verify `rc_user` and `rc_pass` in config
- Restart the RC daemon: `pkill -f "rclone rcd"`
- Try next upload - daemon will restart

### Upload Fails with RC Error

**Error:** "Failed to start rc: listen tcp :5572: bind: address already in use"

**Cause:** You still have RC flags in `rclone_extras`

**Fix:** Remove `--rc`, `--rc-addr`, etc. from your remote configuration

## Migration from Old RC Setup

### Old Setup (flags in rclone_extras)
```json
"rclone_extras": {
    "--rc": null,
    "--rc-addr": "0.0.0.0:5572",
    "--rc-web-gui": null
}
```

### New Setup (standalone daemon)
```json
// In dashboard section:
"dashboard": {
    "standalone_rc": {
        "enabled": true,
        "port": 5572,
        "rc_web_gui": true
    }
}

// In remotes section:
"rclone_extras": {
    // RC flags removed!
    "--drive-chunk-size": "64M"
}
```

## Next Steps

1. **Enable the feature** - Set `standalone_rc.enabled: true`
2. **Remove old RC flags** - Clean up `rclone_extras`
3. **Test an upload** - Verify RC daemon starts
4. **Access Web GUI** - Check `http://localhost:5572`
5. **Set up authentication** - Use `rc_user`/`rc_pass` for security

## Additional Resources

- Full documentation: `RCLONE_RC_STANDALONE.md`
- Rclone RC docs: https://rclone.org/rc/
- Dashboard docs: `DASHBOARD_README.md`
- Config sample: `config.json.sample`

## Support

If you encounter issues:

1. Check cloudplow logs: `tail -f cloudplow.log`
2. Verify config syntax: `python3 -m json.tool config.json`
3. Test RC manually: `rclone rcd --rc-addr=:5572 --rc-no-auth -v`
4. Check this documentation: `RCLONE_RC_STANDALONE.md`

## Summary

You now have a **persistent rclone RC daemon** that:
- ✅ Starts automatically with uploads
- ✅ Provides always-on Web GUI access
- ✅ Integrates with cloudplow dashboard
- ✅ Supports authentication for security
- ✅ Runs independently of upload jobs

The standalone RC server runs on port **5572** by default, separate from the dashboard on port **47949**.

**Your rclone Web GUI is now available at: `http://localhost:5572`**

