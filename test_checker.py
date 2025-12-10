#!/usr/bin/env python3
"""
Test script for checking pending files without running a full upload.
This can be run while cloudplow is running to test the checker independently.
"""
import sys
import os

# Add cloudplow to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils.rclone import RcloneUploader
from utils.config import Config

def main():
    print("Loading cloudplow configuration...")
    
    # Load your config
    conf = Config()
    
    # Get your uploader config for gdrive-2025
    uploader_name = 'gdrive-2025'
    
    if uploader_name not in conf.configs['uploader']:
        print(f"Error: Uploader '{uploader_name}' not found in config")
        print(f"Available uploaders: {list(conf.configs['uploader'].keys())}")
        return
    
    uploader_config = conf.configs['uploader'][uploader_name]
    rclone_config = conf.configs['remotes'][uploader_name]
    
    print(f"\n=== Testing Checker for '{uploader_name}' ===")
    print(f"Source: {rclone_config['upload_folder']}")
    print(f"Destination: {rclone_config['upload_remote']}")
    
    # Determine service account (if applicable)
    service_account = None
    if 'service_account_path' in conf.configs['remotes'][uploader_name]:
        sa_path = conf.configs['remotes'][uploader_name]['service_account_path']
        if sa_path and os.path.exists(sa_path):
            # Get first service account for testing
            sa_files = [f for f in os.listdir(sa_path) if f.endswith('.json')]
            if sa_files:
                service_account = os.path.join(sa_path, sa_files[0])
                print(f"Using service account: {service_account}")
    
    # Create the RcloneUploader instance
    print("\nCreating RcloneUploader instance...")
    rclone = RcloneUploader(
        uploader_name,
        rclone_config,
        conf.configs['core']['rclone_binary_path'],
        conf.configs['core']['rclone_config_path'],
        conf.configs['plex'],
        dry_run=False,
        service_account=service_account
    )
    
    # Run the check
    print("\nRunning pending files check (this may take a few minutes)...")
    print("-" * 60)
    
    result = rclone.check_pending_files()
    
    print("-" * 60)
    
    if result:
        print("\n✓ Check completed successfully!")
        print("\n=== Detailed Results ===")
        print(f"Total files in source:     {result['total_count']:,} files ({result['total_size_gb']:,.2f} GB)")
        print(f"\nPending to upload:         {result['pending_count']:,} files ({result['pending_size_gb']:,.2f} GB)")
        print(f"  ├─ New files (missing):  {result['missing_count']:,} files")
        print(f"  └─ Modified files:       {result['modified_count']:,} files")
        print(f"\nAlready synced:            {result['synced_count']:,} files ({result['synced_size_gb']:,.2f} GB)")
        print(f"\nProgress:                  {result['percent_complete']:.1f}% complete")
        
        # Show notification preview
        print("\n=== Notification Preview ===")
        print(f"Upload starting for {uploader_name}: "
              f"{result['pending_size_gb']} GB pending "
              f"({result['pending_count']} files - "
              f"{result['missing_count']} new, "
              f"{result['modified_count']} modified), "
              f"{result['synced_size_gb']} GB already synced "
              f"({result['percent_complete']}% complete)")
    else:
        print("\n✗ Check failed - see error messages above")
        return 1
    
    return 0

if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n\nInterrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n✗ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
