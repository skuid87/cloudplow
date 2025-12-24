# Chunked Upload Feature

## Overview

The **Chunked Upload** feature dramatically improves upload performance for directories containing hundreds of thousands of files by splitting the upload into smaller batches. This bypasses rclone's time-consuming "checking" phase for the entire directory on each transfer.

## Problem Statement

When uploading large directories (e.g., 30TB with 300k+ files) to Google Drive using a single rclone command:
- **Issue**: Rclone spends significant time checking ALL files before starting transfers
- **Impact**: Each upload stage requires checking the entire directory, even if only transferring a small subset
- **Result**: Wasted time on checking, slow progress, and inefficient SA quota utilization

## Solution: Batch Processing with `--files-from`

The chunked upload feature:
1. **Generates a file list once** using `rclone lsf` (fast, no checking)
2. **Splits into batches** (e.g., 1000 files per batch)
3. **Uploads each batch** with `--files-from` flag (only checks those specific files)
4. **Tracks progress** across all batches with existing session state

### Performance Improvement

**Before (Single Upload):**
```
Stage 1: Check 300,000 files → Upload subset → Repeat checking for Stage 2
Time wasted: Hours checking the same files repeatedly
```

**After (Chunked Upload):**
```
Generate list: 300,000 files (fast, done once)
Chunk 1: Check 1,000 files → Upload → Done
Chunk 2: Check 1,000 files → Upload → Done
...
Chunk 300: Check 1,000 files → Upload → Done
Time wasted: Minimal, only checks files being uploaded
```

## Configuration

Add the `chunked_upload` section to your uploader config in `config.json`:

```json
{
  "uploader": {
    "google": {
      "can_be_throttled": true,
      "check_interval": 30,
      "exclude_open_files": true,
      "max_size_gb": 200,
      
      "chunked_upload": {
        "enabled": true,
        "chunk_size": 1000,
        "generate_list_timeout": 600
      },
      
      "service_account_path": "/path/to/service_accounts/",
      "opened_excludes": ["/downloads/"],
      // ... rest of config
    }
  }
}
```

### Configuration Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `enabled` | boolean | `false` | Enable/disable chunked uploads |
| `chunk_size` | integer | `1000` | Number of files per batch |
| `generate_list_timeout` | integer | `600` | Timeout in seconds for generating the file list |

### Recommended Settings

- **Small directories** (<10k files): Disable chunking or use large chunk_size (5000+)
- **Medium directories** (10k-100k files): `chunk_size: 2000-3000`
- **Large directories** (100k-1M files): `chunk_size: 1000-2000`
- **Very large directories** (1M+ files): `chunk_size: 500-1000`

**Note**: Smaller chunks provide better progress visibility but create more file list overhead. Larger chunks reduce overhead but take longer per chunk.

## How It Works

### 1. File List Generation

When an upload starts with chunking enabled:

```python
# Uses rclone lsf (fast, lists filenames only)
rclone lsf /local/path --recursive --files-only --config=/path/to/rclone.conf
```

- **Fast**: No checksums, sizes, or modification times calculated
- **Respects excludes**: Applies your `rclone_excludes` patterns
- **Output**: Plain text file with one filename per line

### 2. Chunk Creation

The file list is split into batches:

```
# Example: 5,432 files with chunk_size=1000
chunk_1.txt: files 1-1000
chunk_2.txt: files 1001-2000
chunk_3.txt: files 2001-3000
chunk_4.txt: files 3001-4000
chunk_5.txt: files 4001-5000
chunk_6.txt: files 5001-5432 (final chunk with remaining files)
```

### 3. Batch Upload

Each chunk is uploaded with `--files-from`:

```bash
rclone copy /local/path gdrive:/remote/path \
  --files-from=/tmp/cloudplow_chunk_1_xxxxx.txt \
  --max-transfer=200G \
  --transfers=8 \
  # ... other flags
```

**Key Benefits:**
- Rclone only checks files listed in the chunk file
- No wasted time scanning the entire directory
- Progress visible at chunk level (e.g., "Chunk 45/300")

### 4. Progress Tracking

Chunked uploads integrate with Cloudplow's existing features:

- **Session State**: Total files set upfront from file list count
- **Dashboard**: Shows chunk progress (e.g., "Chunk 12/150")
- **Real-time Updates**: Transferred bytes/files updated as each file completes
- **SA Rotation**: Automatically rotates to next SA when quota low
- **Early Termination**: Each chunk can terminate early when `max-transfer` reached

## Integration with Existing Features

### Service Account (SA) Rotation

Chunked uploads work seamlessly with SA rotation:

```
SA 1 (750GB quota):
  ├─ Chunk 1: Upload 1000 files (150GB)
  ├─ Chunk 2: Upload 1000 files (145GB)
  ├─ Chunk 3: Upload 1000 files (152GB)
  └─ Quota low → Rotate to SA 2

SA 2 (750GB quota):
  ├─ Chunk 4: Upload 1000 files (148GB)
  └─ ... continues
```

**Behavior:**
- Cloudplow checks SA quota after each chunk completes
- If quota < 10GB remaining, rotates to next SA
- New SA continues with next chunk in sequence
- All chunk progress tracked in session state

### Quota-Based Strategy

The quota-based stage parameters apply to each chunk:

```python
# High Quota (>80%): Aggressive
--max-transfer=375G --max-size=600G --transfers=8 --order-by=size,desc

# Medium Quota (50-80%): Moderate  
--max-transfer=450G --max-size=375G --transfers=4 --order-by=size,desc

# Low Quota (25-50%): Cautious
--max-transfer=525G --max-size=225G --transfers=6 (no ordering)

# Critical Quota (<25%): Conservative
--max-transfer=600G --max-size=150G --transfers=8 (no ordering)
```

Each chunk upload uses these dynamic parameters based on current SA quota.

### Early Termination

Early termination works within each chunk:

1. Chunk upload starts with `--max-transfer=200G`
2. Rclone reports "max transfer limit reached"
3. Cloudplow waits 5 seconds for active transfers to complete
4. Verifies via RC API that transfers stopped but checking continues
5. Terminates rclone process early
6. Moves to next chunk (or rotates SA if quota low)

**Result**: No time wasted checking remaining files in a chunk after quota exhausted.

### Dashboard Display

The dashboard shows chunked upload progress:

**Session Statistics:**
```
Files Progress: 45,231 / 287,456 (15.7%)
Bytes Progress: 8.2 TB / 28.5 TB (28.8%)
Current Speed: 95.3 MB/s
ETA: 3h 42m
```

**Queue & Strategy:**
```
Strategy: aggressive_fresh_sa
Stage: 1.45 (chunk 45 of stage 1)
Files Scanned: 1,000 (chunk size)
Currently Checking: 23
Transfer Queue: 156
Active Transfers: 8
```

**Service Accounts:**
```
SA: 03-ldn-macmini-xxx.json [ACTIVE]
Used: 245.7 GB / 750 GB (32.8%)
Reset In: 18h 23m
```

## Logging

Chunked uploads produce detailed logs:

### Start of Upload

```
2025-12-22 10:15:23 - INFO - Chunked upload enabled (chunk_size=1000) - generating file list for /mnt/local/Media
2025-12-22 10:15:45 - INFO - Generated list of 287,456 files
2025-12-22 10:15:48 - INFO - Split into 288 chunks of ~1000 files each
2025-12-22 10:15:48 - INFO - Captured session totals: 287456 files, 0 B
```

### Chunk Progress

```
2025-12-22 10:16:00 - INFO - === Uploading chunk 1/288 (1000 files) ===
2025-12-22 10:16:00 - INFO - Uploading batch from 'cloudplow_chunk_1_xxxxx.txt' to remote: google
2025-12-22 10:18:32 - INFO - Chunk 1/288 completed: 998 files, 2.3 GB

2025-12-22 10:18:33 - INFO - === Uploading chunk 2/288 (1000 files) ===
2025-12-22 10:18:33 - INFO - Uploading batch from 'cloudplow_chunk_2_xxxxx.txt' to remote: google
2025-12-22 10:21:15 - INFO - Chunk 2/288 completed: 1000 files, 2.5 GB
```

### SA Rotation

```
2025-12-22 11:45:22 - INFO - Chunk 45/288 completed: 992 files, 2.1 GB
2025-12-22 11:45:23 - INFO - SA quota low (8.3 GB), stopping chunk loop to rotate SA
2025-12-22 11:45:23 - INFO - SA 1/18 (01-ldn-macmini-xxx.json), Stage 2: 8.3 GB remaining
2025-12-22 11:45:23 - INFO - Rotating to next service account: 02-ldn-macmini-yyy.json
2025-12-22 11:45:24 - INFO - === Uploading chunk 46/288 (1000 files) ===
```

### Completion

```
2025-12-22 18:32:18 - INFO - Chunk 288/288 completed: 456 files, 1.1 GB
2025-12-22 18:32:18 - INFO - === All chunks completed: 287456 files, 28.5 TB ===
2025-12-22 18:32:18 - INFO - Cleaned up chunked upload temporary files
2025-12-22 18:32:18 - INFO - Upload completed for google: 287456 files (28.5 TB) transferred in 8h 17m at avg 965.2 MB/s using 3 service accounts
```

## Troubleshooting

### Issue: "Failed to generate file list"

**Possible Causes:**
- Timeout too short for large directory
- Disk I/O issues
- Invalid path or excludes

**Solutions:**
```json
{
  "chunked_upload": {
    "enabled": true,
    "chunk_size": 1000,
    "generate_list_timeout": 1200  // Increase to 20 minutes
  }
}
```

### Issue: Chunks failing repeatedly

**Possible Causes:**
- Network issues
- SA quota exhausted
- Invalid files in chunk

**Solutions:**
1. Check logs for specific rclone errors
2. Verify SA quota in dashboard
3. Check if certain file patterns causing issues
4. Add problematic patterns to `rclone_excludes`

### Issue: Chunking slower than normal upload

**Possible Causes:**
- Chunk size too small (overhead from starting/stopping rclone)
- Directory already optimized (few files)
- Disk I/O bottleneck

**Solutions:**
```json
{
  "chunked_upload": {
    "enabled": true,
    "chunk_size": 5000,  // Increase chunk size
    "generate_list_timeout": 600
  }
}
```

Or disable chunking for this uploader:
```json
{
  "chunked_upload": {
    "enabled": false
  }
}
```

## When to Use Chunking

### ✅ Use Chunking When:

- Directory has **>50,000 files**
- Rclone spends **>5 minutes checking** before transfers start
- You need **granular progress visibility** (chunk-level)
- Uploading to **Google Drive with SA rotation** (many stages)
- Files are **relatively uniform in size** (predictable chunks)

### ❌ Don't Use Chunking When:

- Directory has **<10,000 files** (overhead not worth it)
- Files are **very large** (e.g., 4K movies >100GB each)
- You're doing **sync operations** (not moves/copies)
- Rclone already starts transfers quickly
- You want **simplicity** over optimization

## Technical Details

### File List Format

The generated file list contains relative paths:

```
Movies/Action/Movie1 (2020)/movie1.mkv
Movies/Action/Movie1 (2020)/movie1.srt
Movies/Comedy/Movie2 (2021)/movie2.mkv
Series/Show1/Season 01/s01e01.mkv
Series/Show1/Season 01/s01e02.mkv
```

### Temporary Files

Cloudplow creates temporary files in the system temp directory:

```
/tmp/cloudplow_filelist_xxxxx.txt       # Master file list
/tmp/cloudplow_chunk_1_xxxxx.txt        # Chunk 1
/tmp/cloudplow_chunk_2_xxxxx.txt        # Chunk 2
...
/tmp/cloudplow_chunk_288_xxxxx.txt      # Chunk 288
```

**Cleanup:**
- All chunk files deleted after upload completes
- Master file list deleted after upload completes
- Cleanup happens even if upload fails or is aborted

### Memory Usage

Chunking is memory-efficient:
- File list stored on disk, not in memory
- Only one chunk processed at a time
- Typical memory overhead: <10MB for 1M files

## Implementation Files

The chunked upload feature consists of:

1. **`utils/chunker.py`**: File list generation and chunk creation
2. **`utils/rclone.py`**: Modified to accept `files_from` parameter
3. **`utils/uploader.py`**: Modified to pass `files_from` to rclone
4. **`cloudplow.py`**: Main integration logic for chunk loop
5. **`config.json.sample`**: Configuration example

## FAQ

**Q: Does chunking work with Plex integration?**  
A: Yes, chunking is fully compatible with Plex throttling and monitoring.

**Q: Can I pause/resume a chunked upload?**  
A: Each chunk is atomic. If interrupted, the next run will skip already-uploaded files (if using `--update` flag).

**Q: Does chunking affect transfer speed?**  
A: No, transfer speed is the same. Chunking only eliminates wasted checking time.

**Q: Can I change chunk_size mid-upload?**  
A: No, chunks are created at upload start. Changing config only affects future uploads.

**Q: What happens if a chunk fails?**  
A: The upload stops, and the error is logged. Manual intervention required to retry.

**Q: Does chunking work with mover (staging remote)?**  
A: Chunking is designed for local→remote uploads. Mover (remote→remote) uses normal upload.

## Conclusion

Chunked upload is a powerful optimization for large-scale uploads, dramatically reducing wasted time on file checking. When properly configured, it provides:

- **🚀 Faster uploads**: Eliminate redundant checking
- **📊 Better visibility**: Chunk-level progress tracking  
- **🔄 Seamless integration**: Works with SA rotation, quotas, early termination
- **💾 Efficient**: Minimal memory overhead
- **🛡️ Reliable**: Automatic cleanup and error handling

Enable it in your config and watch your upload efficiency soar! 🎉


