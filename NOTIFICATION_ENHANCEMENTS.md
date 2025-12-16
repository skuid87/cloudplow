# Enhanced Upload Notifications

## Overview

This document describes the enhanced notification system implemented for Cloudplow uploads. The enhancements provide comprehensive metrics and visibility into upload sessions, including service account cycling, transfer statistics, and cache behavior.

## New Metrics Included

The following metrics are now tracked and displayed in notifications:

1. **Total Data Transferred** - Sum of all uploaded file sizes (GB/TB)
2. **Total Upload Duration** - How long the upload session took
3. **Average Upload Speed** - Overall session average speed
4. **Weekend/Weekday Mode** - Indicates cache behavior (full vs incremental)
5. **Cache Statistics** - Files excluded due to cache on weekday runs
6. **Service Account Info** - Which SA is being used and cycling details

## Notification Types

### 1. Upload Starting Notifications

#### With Service Accounts
```
Upload starting for gdrive using service account: sa_001.json (50 accounts available)
```

#### Without Service Accounts (Weekday)
```
Upload starting for gdrive (Weekday - incremental transfer)
```

#### Without Service Accounts (Weekend)
```
Upload starting for gdrive (Weekend - full transfer)
```

### 2. Service Account Cycling Notification

Sent when a service account hits a rate limit and cycles to the next available account:

```
Service account sa_001.json hit 'user_rate_limit' for gdrive. This SA uploaded: 1200 files (3.5 TB) in 2h 0m. Session total so far: 1200 files (3.5 TB). Cycling to sa_002.json (49 remaining)
```

### 3. Upload Completed - Success

#### With Files Transferred (Multiple SAs)
```
Upload completed for gdrive: 2450 files (6.8 TB) transferred in 4h 15m at avg 466.0 MB/s (cycled through 3 service accounts: sa_001.json, sa_002.json, sa_003.json)
```

#### With Files Transferred (Single SA)
```
Upload completed for gdrive: 1234 files (2.5 TB) transferred in 2h 45m at avg 264.8 MB/s using sa_001.json
```

#### With Files Transferred (No SA)
```
Upload completed for gdrive: 1234 files (2.5 TB) transferred in 2h 45m at avg 264.8 MB/s
```

#### No Files Transferred (Weekday)
```
Upload completed for gdrive: no new files to transfer (5432 files already cached)
```

#### No Files Transferred (Weekend)
```
Upload completed for gdrive: no new files to transfer (Weekend - full scan completed)
```

### 4. Upload Aborted (Trigger Met)

#### With Partial Transfer
```
Upload was aborted for remote: gdrive due to trigger user_rate_limit. Partial upload: 856 files (1.8 TB) transferred in 1h 30m at avg 349.5 MB/s. Uploads suspended for 24 hours
```

#### Without Transfer
```
Upload was aborted for remote: gdrive due to trigger user_rate_limit. Uploads suspended for 24 hours
```

### 5. Upload Failed

#### With Partial Transfer
```
Upload was not completed successfully for remote: gdrive. Partial: 123 files (450.0 GB) transferred before failure after 45m 0s
```

#### Without Transfer
```
Upload was not completed successfully for remote: gdrive (no files transferred)
```

### 6. No Service Accounts Available
```
Upload skipped for gdrive: All service accounts are currently suspended. Next available in 3h 45m
```

## Implementation Details

### Changes to `utils/uploader.py`

1. **Added file size tracking** - `transferred_file_sizes` dict to track individual file sizes
2. **Enhanced return value** - `upload()` now returns a comprehensive metrics dictionary instead of a tuple
3. **New helper method** - `_get_file_size()` to retrieve file sizes from RC stats or disk

**Metrics Dictionary Structure:**
```python
{
    'delayed_check': int,           # Hours to delay if triggered
    'delayed_trigger': str,         # Trigger name if met
    'success': bool,                # Upload success status
    'transfer_count': int,          # Number of files transferred
    'total_bytes': int,             # Total bytes transferred
    'duration_seconds': float,      # Upload duration in seconds
    'avg_speed_bytes': float,       # Average speed in bytes/sec
    'is_weekend': bool,             # Weekend mode flag
    'cached_files_excluded': int    # Number of cached files excluded
}
```

### Changes to `cloudplow.py`

1. **Added cumulative metrics tracking** - Tracks totals across multiple SA runs
2. **Enhanced all notification messages** - Include relevant metrics for each scenario
3. **Service account visibility** - Shows which SA is active and when cycling occurs
4. **Import of format functions** - Uses `format_bytes()` and `format_duration()` from uploader module

**Cumulative Metrics Structure:**
```python
cumulative_metrics = {
    'transfer_count': 0,
    'total_bytes': 0,
    'duration_seconds': 0,
    'start_time': time.time(),
    'sa_used': [],
    'cached_files_excluded': 0,
    'is_weekend': bool
}
```

## Benefits

1. **Complete Visibility** - See exactly what was transferred, how long it took, and at what speed
2. **Service Account Tracking** - Know which SAs are being used and when they cycle
3. **Cache Awareness** - Understand why no files were transferred (cached vs nothing new)
4. **Troubleshooting** - Partial transfer stats help diagnose issues
5. **Performance Monitoring** - Track upload speeds and durations over time
6. **Multi-SA Sessions** - Cumulative stats show total work across all SAs used

## Testing

Run the test script to see example notifications:

```bash
python3 test_notifications_standalone.py
```

This will display all notification formats with realistic example data.

## Backward Compatibility

The changes maintain backward compatibility:
- Existing code that doesn't use service accounts continues to work
- All notification services (Pushover, Slack, Apprise, etc.) work unchanged
- The enhanced metrics are additive - no existing functionality is removed

## Future Enhancements

Potential future additions:
- Peak transfer speed tracking
- File size distribution statistics
- Transfer start/end timestamps
- Per-directory transfer summaries
- Historical trend analysis

