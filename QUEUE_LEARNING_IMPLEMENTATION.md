# Queue-Based Learning Implementation Summary

## Overview

This implementation replaces the "learn from transferred files" approach with an intelligent "learn from checker queue" system that:

1. **Captures file distribution from rclone's checker queue** (what WILL be transferred)
2. **Uses queue data for decision-making** (stage strategy calculations)
3. **Tracks actual transfers for analysis only** (visibility and validation)

## Key Changes

### 1. New Functions in `utils/distribution.py`

#### `capture_queue_distribution_from_checkers(rc_url, upload_folder, timeout=300)`
- Monitors rclone RC API during the checking phase
- Captures file sizes from the `checking` and `transferring` arrays
- Builds a `FileDistributionTracker` with upcoming transfer sizes
- Runs in background thread to avoid blocking uploads
- Returns distribution data for immediate use

#### `save_queue_distribution(cache_file, uploader_name, tracker, upload_folder, checking_duration)`
- Saves queue distribution to cache file
- Marks data with `source: "checker_queue"` and `learning_phase: "pre_transfer"`
- Used for **decision-making only**

#### `load_queue_distribution(cache_file, uploader_name, upload_folder)`
- Loads queue distribution from cache
- Returns only the `queue_distribution` section
- Used by stage calculation logic

#### `save_transfer_history(cache_file, uploader_name, transfer_stats, upload_folder, session_info)`
- Saves actual transfer data to cache
- Marks data with `source: "completed_transfers"` and `for_analysis_only: true`
- Compares actual vs predicted (queue) totals
- **NOT used for decision-making**

### 2. Changes in `cloudplow.py`

#### Queue Distribution Capture
- On first stage of first SA, if no queue distribution exists:
  - Starts background thread to monitor rclone checkers
  - Captures file sizes as rclone builds the transfer queue
  - Saves queue distribution immediately after capture
  - Uses it for subsequent stage calculations

#### Decision Logic
- **Before**: Used `learned_dist` from transferred files
- **After**: Uses `queue_dist` from checker queue only
- Function: `calculate_stage_params_with_distribution(sa_quota_remaining, queue_dist)`

#### Transfer History
- **Before**: Updated distribution cache with transferred files and reloaded for next stage
- **After**: Saves transfer history for analysis only, does NOT reload or use in decisions

## Cache File Structure

```json
{
  "google_media": {
    "queue_distribution": {
      "max_file_size": 161061273600,
      "max_file_size_human": "150.0 GB",
      "percentiles": {
        "p50": 524288000,
        "p75": 5368709120,
        "p90": 21474836480,
        "p95": 53687091200,
        "p99": 107374182400
      },
      "percentiles_human": {
        "p50": "500.0 MB",
        "p75": "5.0 GB",
        "p90": "20.0 GB",
        "p95": "50.0 GB",
        "p99": "100.0 GB"
      },
      "size_buckets": {
        "0-100MB": {"count": 342, "total_bytes": 18253611008, "percentage": 15.2},
        "100MB-1GB": {"count": 891, "total_bytes": 445664256000, "percentage": 39.6},
        "1GB-10GB": {"count": 654, "total_bytes": 3298534883328, "percentage": 29.1},
        "10GB-50GB": {"count": 278, "total_bytes": 5588865843200, "percentage": 12.4},
        "50GB+": {"count": 83, "total_bytes": 8053063680000, "percentage": 3.7}
      },
      "large_file_percentage": 3.7,
      "statistics": {
        "total_files": 2248,
        "total_bytes": 17404392273536,
        "mean": 7742490000,
        "mean_human": "7.2 GB",
        "median": 524288000,
        "median_human": "500.0 MB",
        "std_dev": 15234567890,
        "std_dev_human": "14.2 GB",
        "total_bytes_human": "16.2 TB"
      },
      "metadata": {
        "source": "checker_queue",
        "learning_phase": "pre_transfer",
        "last_updated": 1734556800.0,
        "last_updated_human": "2025-12-18 14:30:00",
        "upload_folder": "/mnt/media",
        "sample_count": 2248,
        "confidence": "very_high",
        "checking_duration_seconds": 45
      }
    },
    
    "transfer_history": {
      "max_file_size": 159870123456,
      "max_file_size_human": "148.9 GB",
      "percentiles": {...},
      "size_buckets": {...},
      "statistics": {
        "total_files": 2248,
        "total_bytes": 17204392273536,
        "mean_human": "7.1 GB",
        "median_human": "498.0 MB",
        "total_bytes_human": "16.0 TB"
      },
      "metadata": {
        "source": "completed_transfers",
        "learning_phase": "post_transfer",
        "for_analysis_only": true,
        "last_updated": 1734570330.0,
        "last_updated_human": "2025-12-18 18:45:30",
        "upload_folder": "/mnt/media",
        "sample_count": 2248,
        "session_start": "2025-12-18 14:30:00",
        "stage_number": 8,
        "sa_file": "service_account_01.json"
      },
      "comparison": {
        "queue_predicted_total": "16.2 TB",
        "actual_transferred": "16.0 TB",
        "variance_percentage": 1.2
      },
      "tracker_state": {...}
    }
  }
}
```

## Upload Flow

### First Upload (No Queue Distribution Cached)

1. **Upload starts** → Load queue distribution → Not found
2. **Stage 1 begins** with conservative defaults
3. **Background thread** monitors rclone checkers during checking phase
4. **Rclone starts checking** → Thread captures file sizes from RC API
5. **Queue distribution captured** → Saved to cache immediately
6. **Stage 1 completes** → Transfer history saved for analysis
7. **Stage 2 begins** → Uses learned queue distribution for intelligent params
8. **Subsequent stages** → Continue using queue distribution

### Subsequent Uploads (Queue Distribution Exists)

1. **Upload starts** → Load queue distribution → Found!
2. **Stage 1 begins** → Uses queue distribution for intelligent params immediately
3. **No queue monitoring needed** → Already know what's in the queue
4. **All stages** → Use queue distribution for optimal performance
5. **Transfer history** → Continuously updated for visibility

## Log Messages

```
[INFO] No queue distribution available - will capture during checking phase
[INFO] Monitoring rclone checkers to learn file distribution from upload queue...
[DEBUG] Queue learned: movie_150gb.mkv (150.0 GB)
[DEBUG] Queue learned: episode_s01e01.mkv (2.5 GB)
[INFO] Queue learning progress: 100 files captured...
[INFO] Checker monitoring complete: captured 2248 files
[INFO] Learned queue distribution: 2248 files, P50=500.0 MB, P95=50.0 GB, Max=150.0 GB
[INFO] Queue distribution saved for google_media: 2248 files, confidence=very_high, used for stage strategy decisions
[INFO] Loaded queue distribution for google_media: 2248 files, confidence=very_high (for decisions)

[INFO] SA 1/5 (service_account_01.json), Stage 1: 712.5 GB remaining
[INFO] Distribution: P50=500.0 MB, P75=5.0 GB, P90=20.0 GB, P95=50.0 GB, Max=150.0 GB
[INFO] Large files (50GB+): 3.7%, confidence=very_high
[INFO] Starting stage 1 with: transfers=4, max-transfer=200GB, strategy=balanced

[INFO] Stage 1 complete: uploaded 198.5 GB, quota remaining: 514.0 GB
[INFO] Transfer history updated for google_media: 1247 new files added, 1247 total (for analysis only)

[INFO] SA 1/5 (service_account_01.json), Stage 2: 514.0 GB remaining
[INFO] Starting stage 2 with: transfers=3, max-transfer=180GB, strategy=balanced
```

## Benefits

✅ **Forward-Looking Decisions** - Stage strategy based on what's coming, not what's gone  
✅ **Immediate Intelligence** - First stage after capture uses real distribution data  
✅ **No False Learning** - Transferred data doesn't pollute future decisions  
✅ **Full Visibility** - See both predictions (queue) and reality (transfers)  
✅ **Validation** - Compare predicted vs actual to verify accuracy  
✅ **Efficient** - No expensive pre-scanning of entire directories  
✅ **Persistent** - Queue distribution cached across restarts  

## Testing

To test the implementation:

1. **Check queue distribution capture:**
   ```bash
   # Watch logs for queue monitoring messages
   tail -f ~/cloudplow-docker/config/cloudplow.log | grep -E "(Queue|queue_distribution)"
   ```

2. **View cache file:**
   ```bash
   # See the learned queue distribution
   cat ~/cloudplow-docker/config/learned_sizes_cache.json | jq '.[] | .queue_distribution'
   
   # See transfer history and comparison
   cat ~/cloudplow-docker/config/learned_sizes_cache.json | jq '.[] | .transfer_history'
   ```

3. **Verify decision logic:**
   ```bash
   # Confirm stages use queue distribution
   tail -f ~/cloudplow-docker/config/cloudplow.log | grep -E "(Distribution:|Starting stage)"
   ```

## Backward Compatibility

- Old `learned_sizes_cache.json` files are ignored (different structure)
- First upload will capture queue distribution fresh
- No manual migration needed
- Old `update_distribution_cache()` function removed
- Old `load_learned_distribution()` function replaced with `load_queue_distribution()`

## Future Enhancements

1. **Adaptive queue monitoring** - Skip monitoring if queue distribution is recent and folder unchanged
2. **Multi-remote learning** - Share distribution patterns across similar remotes
3. **Trend analysis** - Track how distribution changes over time
4. **Smart invalidation** - Refresh queue distribution if it becomes stale

