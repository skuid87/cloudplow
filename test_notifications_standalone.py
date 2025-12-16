#!/usr/bin/env python3
"""
Standalone test script to verify enhanced notification message formats
This doesn't require any dependencies
"""

def format_bytes(bytes_val):
    """Convert bytes to human readable format"""
    if bytes_val == 0:
        return "0 B"
    for unit in ['B', 'KB', 'MB', 'GB', 'TB', 'PB']:
        if bytes_val < 1024.0:
            return f"{bytes_val:.1f} {unit}"
        bytes_val /= 1024.0
    return f"{bytes_val:.1f} PB"


def format_duration(seconds):
    """Convert seconds to human readable format"""
    if seconds < 60:
        return f"{int(seconds)}s"
    elif seconds < 3600:
        minutes = int(seconds // 60)
        secs = int(seconds % 60)
        return f"{minutes}m {secs}s"
    else:
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        return f"{hours}h {minutes}m"


def test_format_functions():
    """Test the formatting helper functions"""
    print("Testing format_bytes:")
    print(f"  0 bytes: {format_bytes(0)}")
    print(f"  1024 bytes: {format_bytes(1024)}")
    print(f"  1 MB: {format_bytes(1024 * 1024)}")
    print(f"  1 GB: {format_bytes(1024 * 1024 * 1024)}")
    print(f"  2.5 TB: {format_bytes(2.5 * 1024 * 1024 * 1024 * 1024)}")
    
    print("\nTesting format_duration:")
    print(f"  30 seconds: {format_duration(30)}")
    print(f"  90 seconds: {format_duration(90)}")
    print(f"  3665 seconds: {format_duration(3665)}")
    print(f"  10000 seconds: {format_duration(10000)}")


def test_notification_messages():
    """Test example notification messages"""
    print("\n" + "="*80)
    print("EXAMPLE NOTIFICATION MESSAGES")
    print("="*80)
    
    # Example metrics
    transfer_count = 1234
    total_bytes = 2.5 * 1024 * 1024 * 1024 * 1024  # 2.5 TB
    duration = 2 * 3600 + 45 * 60  # 2h 45m
    avg_speed = total_bytes / duration
    
    print("\n1. Upload Starting (with SA):")
    print(f"   Upload starting for gdrive using service account: sa_001.json (50 accounts available)")
    
    print("\n2. Upload Starting (without SA, weekday):")
    print(f"   Upload starting for gdrive (Weekday - incremental transfer)")
    
    print("\n3. Service Account Cycling:")
    sa_count = 1200
    sa_bytes = 3.5 * 1024 * 1024 * 1024 * 1024  # 3.5 TB
    sa_duration = 2 * 3600  # 2h
    cumulative_count = 1200
    cumulative_bytes = sa_bytes
    print(f"   Service account sa_001.json hit 'user_rate_limit' for gdrive. "
          f"This SA uploaded: {sa_count} files ({format_bytes(sa_bytes)}) "
          f"in {format_duration(sa_duration)}. "
          f"Session total so far: {cumulative_count} files "
          f"({format_bytes(cumulative_bytes)}). "
          f"Cycling to sa_002.json (49 remaining)")
    
    print("\n4. Upload Completed (with files, multiple SAs):")
    print(f"   Upload completed for gdrive: "
          f"{transfer_count} files "
          f"({format_bytes(total_bytes)}) transferred "
          f"in {format_duration(duration)} "
          f"at avg {format_bytes(avg_speed)}/s "
          f"(cycled through 3 service accounts: sa_001.json, sa_002.json, sa_003.json)")
    
    print("\n5. Upload Completed (with files, single SA):")
    print(f"   Upload completed for gdrive: "
          f"{transfer_count} files "
          f"({format_bytes(total_bytes)}) transferred "
          f"in {format_duration(duration)} "
          f"at avg {format_bytes(avg_speed)}/s "
          f"using sa_001.json")
    
    print("\n6. Upload Completed (no files, weekday):")
    cached = 5432
    print(f"   Upload completed for gdrive: no new files to transfer ({cached} files already cached)")
    
    print("\n7. Upload Completed (no files, weekend):")
    print(f"   Upload completed for gdrive: no new files to transfer (Weekend - full scan completed)")
    
    print("\n8. Upload Aborted (with partial stats):")
    partial_count = 856
    partial_bytes = 1.8 * 1024 * 1024 * 1024 * 1024  # 1.8 TB
    partial_duration = 1.5 * 3600  # 1.5h
    partial_speed = partial_bytes / partial_duration
    print(f"   Upload was aborted for remote: gdrive due to trigger user_rate_limit. "
          f"Partial upload: {partial_count} files "
          f"({format_bytes(partial_bytes)}) transferred "
          f"in {format_duration(partial_duration)} "
          f"at avg {format_bytes(partial_speed)}/s. "
          f"Uploads suspended for 24 hours")
    
    print("\n9. Upload Failed (with partial stats):")
    print(f"   Upload was not completed successfully for remote: gdrive. "
          f"Partial: 123 files "
          f"({format_bytes(450 * 1024 * 1024 * 1024)}) transferred "
          f"before failure after {format_duration(45 * 60)}")
    
    print("\n10. No Service Accounts Available:")
    print(f"   Upload skipped for gdrive: All service accounts are currently suspended. Next available in 3h 45m")


def test_metrics_dict():
    """Test the metrics dictionary structure"""
    print("\n" + "="*80)
    print("METRICS DICTIONARY STRUCTURE")
    print("="*80)
    
    example_metrics = {
        'delayed_check': 0,
        'delayed_trigger': '',
        'success': True,
        'transfer_count': 1234,
        'total_bytes': 2.5 * 1024 * 1024 * 1024 * 1024,
        'duration_seconds': 9900,
        'avg_speed_bytes': 2.5 * 1024 * 1024 * 1024 * 1024 / 9900,
        'is_weekend': False,
        'cached_files_excluded': 5432
    }
    
    print("\nExample metrics returned from uploader.upload():")
    for key, value in example_metrics.items():
        if 'bytes' in key and key != 'avg_speed_bytes':
            print(f"  {key}: {value:.0f} ({format_bytes(value)})")
        elif key == 'avg_speed_bytes':
            print(f"  {key}: {value:.0f} ({format_bytes(value)}/s)")
        elif key == 'duration_seconds':
            print(f"  {key}: {value} ({format_duration(value)})")
        else:
            print(f"  {key}: {value}")


def test_cumulative_tracking():
    """Test cumulative metrics tracking across multiple SA runs"""
    print("\n" + "="*80)
    print("CUMULATIVE METRICS TRACKING (Multi-SA Scenario)")
    print("="*80)
    
    # Simulate 3 SA runs
    sa_runs = [
        {'sa': 'sa_001.json', 'files': 1200, 'bytes': 3.5 * 1024**4, 'duration': 7200},
        {'sa': 'sa_002.json', 'files': 800, 'bytes': 2.1 * 1024**4, 'duration': 5400},
        {'sa': 'sa_003.json', 'files': 450, 'bytes': 1.2 * 1024**4, 'duration': 2700},
    ]
    
    cumulative = {'files': 0, 'bytes': 0, 'duration': 0, 'sa_list': []}
    
    print("\nSimulating service account cycling:\n")
    for i, run in enumerate(sa_runs):
        cumulative['files'] += run['files']
        cumulative['bytes'] += run['bytes']
        cumulative['duration'] += run['duration']
        cumulative['sa_list'].append(run['sa'])
        
        print(f"SA {i+1} ({run['sa']}):")
        print(f"  This SA: {run['files']} files, {format_bytes(run['bytes'])}, {format_duration(run['duration'])}")
        print(f"  Cumulative: {cumulative['files']} files, {format_bytes(cumulative['bytes'])}, {format_duration(cumulative['duration'])}")
        
        if i < len(sa_runs) - 1:
            print(f"  → Cycling to next SA...\n")
        else:
            avg_speed = cumulative['bytes'] / cumulative['duration']
            print(f"\nFinal notification:")
            print(f"  Upload completed for gdrive: {cumulative['files']} files "
                  f"({format_bytes(cumulative['bytes'])}) transferred "
                  f"in {format_duration(cumulative['duration'])} "
                  f"at avg {format_bytes(avg_speed)}/s "
                  f"(cycled through {len(cumulative['sa_list'])} service accounts: {', '.join(cumulative['sa_list'])})")


if __name__ == '__main__':
    print("Enhanced Notification Metrics Test")
    print("="*80)
    
    test_format_functions()
    test_notification_messages()
    test_metrics_dict()
    test_cumulative_tracking()
    
    print("\n" + "="*80)
    print("✓ Test completed successfully!")
    print("="*80)

