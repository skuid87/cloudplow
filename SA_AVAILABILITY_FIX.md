# Service Account Availability Bug Fix

## Problem

Service accounts were showing as unavailable even after their quota reset period expired, resulting in:
- "There is 0 available service accounts" error
- "Lowest Remaining time till unban is -1 days, 4 hours..." (negative time indicating expired bans)
- Uploads being skipped unnecessarily

## Root Cause

There are two separate tracking systems for service accounts:

1. **`sa_quota_usage`** (JSON cache) - Tracks quota usage and reset times
2. **`sa_delay`** (SQLite cache) - Tracks which SAs are "banned" with unban timestamps

**The bug:** When quotas expired and were cleaned up from `sa_quota_usage`, the corresponding ban entries in `sa_delay` were NOT being cleared. This caused SAs to remain marked as "banned" even though their quota had reset.

Additionally, `check_suspended_sa()` (which unbans SAs with expired timestamps) was only called AFTER checking available accounts, so expired bans weren't being removed in time.

## Changes Made

### 1. Fixed `cleanup_expired_quotas()` (lines 225-242)

Added synchronization between the two tracking systems:

```python
def cleanup_expired_quotas():
    """Remove quota entries older than 24 hours"""
    global sa_quota_usage
    global sa_delay  # Added
    current_time = time.time()
    
    for uploader in list(sa_quota_usage.keys()):
        for sa_file in list(sa_quota_usage[uploader].keys()):
            reset_time = sa_quota_usage[uploader][sa_file].get('reset_time', 0)
            if current_time >= reset_time:
                log.info(f"Quota reset for SA: {os.path.basename(sa_file)}")
                del sa_quota_usage[uploader][sa_file]
                
                # NEW: Also unban the SA in sa_delay when quota resets
                if uploader in sa_delay and sa_delay[uploader] is not None:
                    if sa_file in sa_delay[uploader]:
                        sa_delay[uploader][sa_file] = None
                        log.info(f"Unbanned SA in sa_delay: {os.path.basename(sa_file)}")
        
        # Clean up empty uploader entries
        if not sa_quota_usage[uploader]:
            del sa_quota_usage[uploader]
```

### 2. Added Early `check_suspended_sa()` Call (line 648)

Moved the check for expired SA bans to BEFORE checking available accounts:

```python
# Check for any expired SA bans before checking available accounts
check_suspended_sa(uploader_remote)

if sa_delay[uploader_remote] is not None:
    available_accounts = [account for account, last_ban_time in sa_delay[uploader_remote].items() if
                          last_ban_time is None]
    # ... rest of logic
```

## Impact

- ✅ SAs with expired quotas are now properly marked as available
- ✅ Both tracking systems stay synchronized
- ✅ Expired bans are checked and cleared before availability check
- ✅ Uploads will resume automatically after 24-hour quota reset period

## Testing

Run the upload command and verify:
1. Service accounts show as available after quota reset
2. No more negative time values in logs
3. Uploads proceed normally with available SAs

```bash
docker compose exec cloudplow python3 /opt/cloudplow/cloudplow.py --loglevel DEBUG upload
```

Expected log output:
```
INFO - cloudplow - cleanup_expired_quotas - Quota reset for SA: 01-ldn-macmini-xxxxx.json
INFO - cloudplow - cleanup_expired_quotas - Unbanned SA in sa_delay: 01-ldn-macmini-xxxxx.json
INFO - cloudplow - do_upload - There is 20 available service accounts
```

