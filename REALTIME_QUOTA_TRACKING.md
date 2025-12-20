# Real-Time Quota Tracking Implementation

## Problem Solved

Previously, SA quota was only updated **after each stage completed**. If you manually interrupted an upload (Ctrl+C) mid-stage, the quota cache wouldn't reflect the files that had already been successfully uploaded. This meant:

- ❌ Next run would think the SA had more capacity than it actually did
- ❌ Could exceed the 750GB daily limit
- ❌ Dashboard showed incomplete SA quota information

## Solution Implemented

**Real-time quota updates**: Every time a file completes successfully during transfer, the quota cache is immediately updated.

### Changes Made

#### 1. `utils/uploader.py`
- Added `quota_callback` parameter to `Uploader.__init__()`
- When a file transfer completes successfully, the callback is invoked with the file size
- The callback updates the SA quota cache in real-time

```python
# In _process_line method (line ~342)
if file_size > 0:
    self.transferred_file_sizes[file_path] = file_size
    
    # Update quota cache in real-time (if callback provided)
    if self.quota_callback:
        try:
            self.quota_callback(file_size)
        except Exception as e:
            log.warning(f"Quota callback failed: {e}")
```

#### 2. `cloudplow.py`
- Created `update_quota_realtime()` callback function in the stage loop
- Passed callback to `Uploader` constructor
- Removed duplicate `update_sa_quota_usage()` call after stage completion (would double-count)

```python
# Create quota update callback for real-time tracking
def update_quota_realtime(bytes_delta):
    """Called each time a file completes to update quota in real-time"""
    update_sa_quota_usage(uploader_remote, sa_file, bytes_delta)

# Pass to uploader
stage_uploader = Uploader(
    ...,
    quota_callback=update_quota_realtime
)
```

#### 3. `utils/dashboard_data.py`
- Fixed `get_service_accounts()` to show the active SA even if it has no quota cache entry yet
- This handles the case where an SA just started and no files have completed yet

```python
# If current SA is not in cache yet, add it with 0 usage
if current_sa and session_state and session_state.get('active'):
    sa_in_results = any(r['sa_file'] == current_sa for r in result)
    if not sa_in_results:
        result.append({
            'sa_file': current_sa,
            'status': 'active',
            'used_bytes': 0,
            'percentage': 0.0,
            'reset_in': 'Ready'
        })
```

## Benefits

### ✅ Interruption-Safe
- Quota accurately reflects transferred files even if upload is interrupted
- No risk of exceeding 750GB limit due to stale cache

### ✅ Real-Time Dashboard Updates
- SA quota bars update as files complete (every 3 seconds via dashboard refresh)
- Active SA always visible in Service Accounts table
- Accurate percentage and remaining capacity shown

### ✅ Accurate Strategy Selection
- `calculate_stage_params_quota_based()` uses current quota for next stage
- If you stop and restart, strategy adapts to actual remaining capacity

## Example Scenario

**Upload Session 1:**
1. Start transfer with SA-01 (750GB available)
2. Transfer 200GB of files
3. Manually interrupt (Ctrl+C) mid-stage
4. ✅ Cache saved: `SA-01: 200GB used, reset in 23h`

**Upload Session 2 (30 minutes later):**
1. Restart cloudplow
2. ✅ Loads cache: SA-01 has 550GB remaining
3. ✅ `calculate_stage_params_quota_based(550GB)` → selects "moderate_mid_sa" strategy
4. ✅ Dashboard shows: SA-01 at 27% usage (200/750 GB)

**Without this fix:**
- ❌ Cache would show: SA-01 at 0% (no completed stages)
- ❌ Strategy would think: 750GB available → "aggressive_fresh_sa" 
- ❌ Could exceed quota limit

## Testing

After deploying, test by:

```bash
# 1. Start an upload
docker compose exec cloudplow python3 /opt/cloudplow/cloudplow.py upload

# 2. Wait for some files to complete (check dashboard for transferred files)

# 3. Interrupt mid-stage (Ctrl+C)

# 4. Check quota cache was updated
docker compose exec cloudplow cat /config/sa_quota_cache.json | jq

# 5. Check dashboard shows correct SA usage
curl http://localhost:47949/api/service_accounts | jq

# 6. Restart upload - strategy should reflect actual remaining capacity
```

## Session Statistics Enhancement

In addition to real-time quota tracking, the dashboard's **Session Statistics** section was enhanced to show live progress data.

### Problem
Session statistics (Avg Speed, Remaining, ETA) were showing zeros because:
- `total_files` and `total_bytes` were only captured after first stage completed
- `transferred_files` and `transferred_bytes` were only updated after each stage
- No live data from current transfers

### Solution
Modified `get_session_stats()` in `utils/dashboard_data.py` to:
1. Pull **live stats from RC API** for current stage (`bytes`, `speed`, `eta`, `totalBytes`)
2. Combine with **cumulative data from session state** (previous stages/SAs)
3. Use RC API totals as fallback if session state totals aren't set yet

### Result
Dashboard now shows:
- ✅ **Live progress** during transfers (27GB / 419GB)
- ✅ **Real-time speed** (91 MB/s)
- ✅ **Accurate ETA** from rclone (71 minutes)
- ✅ **Remaining bytes** calculated correctly
- ✅ **Works immediately** - no need to wait for stage completion

## Related Files

- `utils/uploader.py` - Uploader class with quota callback
- `cloudplow.py` - Creates callback and passes to uploader
- `utils/dashboard_data.py` - Dashboard data provider (quota + session stats)
- `sa_quota_cache.json` - Persistent quota cache file
- `dashboard_session_state.json` - Session state for dashboard

