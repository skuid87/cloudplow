import logging
import glob
import time
import datetime
import re
import json
import threading
import requests
import os
from logging.handlers import RotatingFileHandler

from . import path
from .rclone import RcloneUploader

log = logging.getLogger("uploader")


# Helper functions for formatting
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


class RCStatsPoller(threading.Thread):
    """Background thread that polls rclone RC API for transfer stats"""
    
    def __init__(self, rc_url):
        super().__init__(daemon=True)
        self.rc_url = rc_url
        self.running = False
        self.current_stats = {}
        self.poll_interval = 5  # Start with 5 seconds
        self._lock = threading.Lock()
    
    def run(self):
        """Poll RC API in background"""
        self.running = True
        log.info(f"Started RC stats polling at {self.rc_url}")
        
        while self.running:
            try:
                # Poll the RC API
                response = requests.post(
                    f"{self.rc_url}/core/stats",
                    timeout=5
                )
                
                if response.status_code == 200:
                    stats = response.json()
                    
                    with self._lock:
                        self.current_stats = stats
                    
                    # Calculate next poll interval based on current transfers
                    self.poll_interval = self._calculate_poll_interval(stats)
                    
            except Exception as e:
                log.debug(f"RC stats poll error: {e}")
                self.poll_interval = 10  # Back off on errors
            
            # Sleep with check for stop signal
            for _ in range(int(self.poll_interval * 10)):
                if not self.running:
                    break
                time.sleep(0.1)
        
        log.info("Stopped RC stats polling")
    
    def _calculate_poll_interval(self, stats):
        """Calculate optimal polling interval based on current transfers"""
        if not stats or 'transferring' not in stats:
            return 10
        
        transferring = stats['transferring']
        
        if not transferring:
            return 10  # No active transfers, slow poll
        
        # Look at ETAs of current transfers
        min_eta = min((t.get('eta', 999) for t in transferring), default=999)
        
        if min_eta < 15:
            return 2  # Fast completing file, poll aggressively
        elif min_eta < 60:
            return 5  # Medium file, poll frequently
        elif min_eta < 180:
            return 8  # Larger file, moderate polling
        else:
            return 10  # Very large file, slower polling
    
    def get_stats(self):
        """Get current RC stats (thread-safe)"""
        with self._lock:
            return self.current_stats.copy() if self.current_stats else {}
    
    def stop(self):
        """Stop the polling thread"""
        self.running = False


class Uploader:
    def __init__(self, name, uploader_config, rclone_config, rclone_binary_path, rclone_config_path, plex, dry_run, transfer_cache=None, json_log_path=None, rc_url=None):
        self.name = name
        self.uploader_config = uploader_config
        self.rclone_config = rclone_config
        self.trigger_tracks = {}
        self.delayed_check = 0
        self.delayed_trigger = ""
        self.rclone_binary_path = rclone_binary_path
        self.rclone_config_path = rclone_config_path
        self.plex = plex
        self.dry_run = dry_run
        self.service_account = None
        self.transfer_cache = transfer_cache
        self.transferred_files = set()
        self.transferred_file_sizes = {}  # Track file sizes for metrics
        self.json_log_path = json_log_path
        self.rc_url = rc_url
        self.rc_poller = None
        self.json_logger = None
        
        # Initialize JSONL logger with rotation if path provided
        if self.json_log_path:
            self._init_json_logger()

    def set_service_account(self, sa_file):
        self.service_account = sa_file
        log.info(f"Using service account: {sa_file}")
    
    def _init_json_logger(self):
        """Initialize JSONL logger with rotation"""
        try:
            # Create a custom logger that just writes JSON lines
            # We'll handle writes manually to control format
            self.json_logger = RotatingFileHandler(
                self.json_log_path,
                maxBytes=1024 * 1024 * 5,  # 5 MB
                backupCount=50,
                encoding='utf-8'
            )
            log.info(f"Initialized JSONL logger at: {self.json_log_path}")
        except Exception as e:
            log.error(f"Failed to initialize JSONL logger: {e}")
            self.json_logger = None
    
    def _write_json_log(self, entry):
        """Write a JSON entry to the log file with rotation"""
        if not self.json_logger:
            return
        
        try:
            # Manually check file size for rotation
            if os.path.exists(self.json_logger.baseFilename):
                file_size = os.path.getsize(self.json_logger.baseFilename)
                if file_size >= self.json_logger.maxBytes:
                    self.json_logger.doRollover()
            
            # Write JSON line
            with open(self.json_logger.baseFilename, 'a', encoding='utf-8') as f:
                f.write(json.dumps(entry) + '\n')
        except Exception as e:
            log.warning(f"Failed to write to JSONL log: {e}")
    
    def _start_rc_polling(self):
        """Start RC stats polling thread"""
        if self.rc_url and not self.rc_poller:
            try:
                self.rc_poller = RCStatsPoller(self.rc_url)
                self.rc_poller.start()
            except Exception as e:
                log.warning(f"Failed to start RC polling: {e}")
                self.rc_poller = None
    
    def _stop_rc_polling(self):
        """Stop RC stats polling thread"""
        if self.rc_poller:
            try:
                self.rc_poller.stop()
                self.rc_poller.join(timeout=5)
                self.rc_poller = None
            except Exception as e:
                log.warning(f"Error stopping RC polling: {e}")

    def upload(self):
        # Track upload start time for metrics
        upload_start_time = time.time()
        
        # Determine if this is a weekend run
        self.is_weekend = datetime.datetime.now().weekday() in [5, 6]
        self.transferred_files = set()
        self.transferred_file_sizes = {}  # Reset file sizes tracking
        cached_files_count = 0
        rclone_config = self.rclone_config.copy()

        # Load cache and apply excludes on weekdays
        if not self.is_weekend and self.transfer_cache is not None:
            cached_files = self._load_cached_files()
            if cached_files:
                cached_files_count = len(cached_files)
                log.info(f"Weekday run - excluding {cached_files_count} cached files from transfer")
                for cached_file in cached_files:
                    rclone_config['rclone_excludes'].append(cached_file)
        elif self.is_weekend:
            log.info("Weekend run - performing full transfer without cache excludes")
        
        # should we exclude open files
        if self.uploader_config['exclude_open_files']:
            files_to_exclude = self.__opened_files()
            if len(files_to_exclude):
                log.info(f"Excluding these files from being uploaded because they were open: {files_to_exclude}")
                # add files_to_exclude to rclone_config
                for item in files_to_exclude:
                    rclone_config['rclone_excludes'].append(glob.escape(item))

        # Start RC stats polling if configured
        self._start_rc_polling()
        
        try:
            # do upload
            if self.service_account is not None:
                rclone = RcloneUploader(self.name, rclone_config, self.rclone_binary_path, self.rclone_config_path,
                                        self.plex, self.dry_run, self.service_account)
            else:
                rclone = RcloneUploader(self.name, rclone_config, self.rclone_binary_path, self.rclone_config_path,
                                        self.plex, self.dry_run)

            log.info(f"Uploading '{rclone_config['upload_folder']}' to remote: {self.name}")
            self.delayed_check = 0
            self.trigger_tracks = {}
            success = False
            upload_status, return_code = rclone.upload(self.__logic)

            log.debug("return_code is: %s", return_code)

            if return_code == 7:
                success = True
                log.info("Received 'Max Transfer Reached' signal from Rclone.")
                self.delayed_trigger = "Rclone's 'Max Transfer Reached' signal"
                self.delayed_check = 25

            elif return_code == -9:
                success = True
                log.info("Trigger reached configuration limit.")
                self.delayed_trigger = "Trigger reached limit"
                self.delayed_check = 25

            elif upload_status and return_code == 0:
                success = True
                log.info(f"Finished uploading to remote: {self.name}")
            elif return_code == 9999:
                self.delayed_trigger = "Rclone exception occured"
            else:
                self.delayed_trigger = f"Unhandled situation: Exit code: {return_code} - Upload Status: {upload_status}"

            # Update cache after successful transfer
            if success and self.transferred_files and self.transfer_cache is not None:
                if self.is_weekend:
                    self._update_cache_full(self.transferred_files)
                else:
                    self._update_cache_incremental(self.transferred_files)
                log.info(f"Transferred {len(self.transferred_files)} files")

            # Calculate comprehensive metrics
            upload_duration = time.time() - upload_start_time
            total_bytes = sum(self.transferred_file_sizes.values())
            avg_speed = total_bytes / upload_duration if upload_duration > 0 else 0
            
            # Return comprehensive metrics dict
            return {
                'delayed_check': self.delayed_check,
                'delayed_trigger': self.delayed_trigger,
                'success': success,
                'transfer_count': len(self.transferred_files),
                'total_bytes': total_bytes,
                'duration_seconds': upload_duration,
                'avg_speed_bytes': avg_speed,
                'is_weekend': self.is_weekend,
                'cached_files_excluded': cached_files_count
            }
        
        finally:
            # Always stop RC polling when upload completes
            self._stop_rc_polling()

    def remove_empty_dirs(self):
        path.remove_empty_dirs(self.rclone_config['upload_folder'], self.rclone_config['remove_empty_dir_depth'])
        log.info(f"Removed empty directories from '{self.rclone_config['upload_folder']}' with min depth: {self.rclone_config['remove_empty_dir_depth']}")
        return

    # internals
    def __opened_files(self):
        open_files = path.opened_files(self.rclone_config['upload_folder'])
        return [
            item.replace(self.rclone_config['upload_folder'], '')
            for item in open_files
            if not self.__is_opened_file_excluded(item)
        ]

    def __is_opened_file_excluded(self, file_path):
        return any(
            item.lower() in file_path.lower()
            for item in self.uploader_config['opened_excludes']
        )

    def __logic(self, data):
        # Capture successful transfers from rclone output
        if ': Copied (' in data:
            file_path = self._extract_filepath_from_rclone_output(data)
            if file_path:
                self.transferred_files.add(file_path)
                log.debug(f"Captured successful transfer: {file_path}")
                
                # Try to get file size from RC stats or disk
                file_size = self._get_file_size(file_path)
                if file_size > 0:
                    self.transferred_file_sizes[file_path] = file_size
                
                # Log to JSONL with RC stats enrichment
                self._log_completed_file(file_path)
                
                # Periodic cache update every 50 files
                if len(self.transferred_files) % 50 == 0 and self.transfer_cache is not None:
                    if self.is_weekend:
                        self._update_cache_full(self.transferred_files)
                    else:
                        self._update_cache_incremental(self.transferred_files)
                    log.info(f"Periodic cache update: {len(self.transferred_files)} files saved")
        
        # loop sleep triggers
        for trigger_text, trigger_config in self.rclone_config['rclone_sleeps'].items():
            # check/reset trigger timeout
            if (
                trigger_text in self.trigger_tracks
                and self.trigger_tracks[trigger_text]['expires'] != ''
                and time.time() >= self.trigger_tracks[trigger_text]['expires']
            ):
                log.warning(f"Tracking of trigger: {trigger_text} has expired, resetting occurrence count and timeout")
                self.trigger_tracks[trigger_text] = {'count': 0, 'expires': ''}

            # check if trigger_text is in data
            if trigger_text.lower() in data.lower():
                # check / increase tracking count of trigger_text
                if trigger_text not in self.trigger_tracks or self.trigger_tracks[trigger_text]['count'] == 0:
                    # set initial tracking info for trigger
                    self.trigger_tracks[trigger_text] = {'count': 1, 'expires': time.time() + trigger_config['timeout']}
                    log.warning(f"Tracked first occurrence of trigger: {trigger_text}. Expiring in {trigger_config['timeout']} seconds at {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self.trigger_tracks[trigger_text]['expires']))}")
                else:
                    # trigger_text WAS seen before increase count
                    self.trigger_tracks[trigger_text]['count'] += 1
                    log.warning(f"Tracked trigger: {trigger_text} has occurred {self.trigger_tracks[trigger_text]['count']}/{trigger_config['count']} times within {trigger_config['timeout']} seconds")

                    # check if trigger_text was found the required amount of times to abort
                    if self.trigger_tracks[trigger_text]['count'] >= trigger_config['count']:
                        log.warning(f"Tracked trigger {trigger_text} has reached the maximum limit of {trigger_config['count']} occurrences within {trigger_config['timeout']} seconds, aborting upload...")
                        self.delayed_check = trigger_config['sleep']
                        self.delayed_trigger = trigger_text
                        return True
        return False

    # Cache management methods
    def _get_current_config(self):
        """Get current configuration for cache validation"""
        return {
            'upload_remote': self.rclone_config['upload_remote'],
            'upload_folder': self.rclone_config['upload_folder'],
            'uploader_name': self.name
        }

    def _load_cached_files(self):
        """Load cached files for this uploader"""
        if self.transfer_cache is None:
            return []
        
        cache_data = self.transfer_cache.get(self.name, {})
        current_config = self._get_current_config()
        
        # Check if config matches
        if cache_data.get('config') != current_config:
            log.info(f"Cache config mismatch for {self.name} - cache will not be used until next weekend run")
            return []
        
        return cache_data.get('files', [])

    def _update_cache_full(self, transferred_files):
        """Weekend: Merge newly transferred files with existing cache"""
        if self.transfer_cache is None:
            return
        
        old_cache = self.transfer_cache.get(self.name, {})
        current_config = self._get_current_config()
        
        # Config changed? Start fresh
        if old_cache.get('config') != current_config:
            log.warning(f"Config changed for {self.name} - starting fresh cache")
            all_files = set(transferred_files)
        else:
            # Merge old + new
            old_files = set(old_cache.get('files', []))
            new_files = set(transferred_files)
            all_files = old_files.union(new_files)
            
            log.info(f"Cache update for {self.name}: {len(old_files)} previous + {len(new_files)} newly transferred = {len(all_files)} total")
        
        self.transfer_cache[self.name] = {
            'config': current_config,
            'last_full_run': time.time(),
            'files': list(all_files)
        }

    def _update_cache_incremental(self, transferred_files):
        """Weekday: Add newly transferred files to existing cache"""
        if self.transfer_cache is None:
            return
        
        old_cache = self.transfer_cache.get(self.name, {})
        current_config = self._get_current_config()
        
        # Check if cache is empty (first run) or config changed
        if not old_cache:
            # First run - initialize cache
            log.info(f"First cache initialization for {self.name}")
            self.transfer_cache[self.name] = {
                'config': current_config,
                'files': list(transferred_files)
            }
            log.info(f"Weekday cache created for {self.name}: {len(transferred_files)} files")
            return
        
        # Config changed mid-week? Wait for weekend
        if old_cache.get('config') != current_config:
            log.warning(f"Config changed mid-week for {self.name} - cache may be inconsistent until weekend")
            return
        
        old_files = set(old_cache.get('files', []))
        new_files = set(transferred_files)
        all_files = old_files.union(new_files)
        
        log.info(f"Weekday cache update for {self.name}: added {len(new_files)} files (total: {len(all_files)})")
        
        old_cache['files'] = list(all_files)
        self.transfer_cache[self.name] = old_cache

    def _extract_filepath_from_rclone_output(self, data):
        """Extract file path from rclone output line"""
        # Rclone output format: "2024/01/15 10:30:45 INFO  : path/to/file.mkv: Copied (new)"
        # Pattern to match: anything between "INFO  : " and ": Copied ("
        match = re.search(r'INFO\s+:\s+(.+?):\s+Copied\s+\(', data)
        if match:
            return match.group(1).strip()
        return None
    
    def _log_completed_file(self, file_path):
        """Log completed file to JSONL with RC stats enrichment"""
        if not self.json_logger:
            return
        
        try:
            # Get current RC stats
            rc_stats = self.rc_poller.get_stats() if self.rc_poller else {}
            
            # Build base entry
            entry = {
                'timestamp': time.time(),
                'datetime': time.strftime('%Y-%m-%d %H:%M:%S'),
                'uploader': self.name,
                'filename': file_path,
            }
            
            # Try to find this file in recent RC transferring data
            # (it might have been in the last poll before completion)
            file_stats = self._find_file_in_rc_stats(file_path, rc_stats)
            
            if file_stats:
                # Enrich with RC data
                entry['size_bytes'] = file_stats.get('size', 0)
                entry['size_human'] = format_bytes(file_stats.get('size', 0))
                
                avg_speed = file_stats.get('speedAvg', 0)
                entry['avg_speed_bytes'] = int(avg_speed)
                entry['avg_speed_human'] = format_bytes(avg_speed) + '/s'
                
                # Calculate approximate duration
                if avg_speed > 0 and file_stats.get('size', 0) > 0:
                    duration = file_stats['size'] / avg_speed
                    entry['duration_seconds'] = round(duration, 1)
                    entry['duration_human'] = format_duration(duration)
                
                entry['source'] = file_stats.get('srcFs', '')
                entry['destination'] = file_stats.get('dstFs', '')
            
            # Write to JSONL
            self._write_json_log(entry)
            
        except Exception as e:
            log.debug(f"Error logging completed file to JSONL: {e}")
    
    def _find_file_in_rc_stats(self, file_path, rc_stats):
        """Find file stats in RC data by matching filename"""
        if not rc_stats or 'transferring' not in rc_stats:
            return None
        
        # Look for exact match in currently transferring files
        for transfer in rc_stats.get('transferring', []):
            if transfer.get('name') == file_path:
                return transfer
        
        # File not in current transferring list (already completed)
        # This is normal - file completed between our last poll and now
        return None
    
    def _get_file_size(self, file_path):
        """Get file size from RC stats or disk"""
        try:
            # First try to get size from RC stats
            if self.rc_poller:
                rc_stats = self.rc_poller.get_stats()
                file_stats = self._find_file_in_rc_stats(file_path, rc_stats)
                if file_stats and 'size' in file_stats:
                    return file_stats['size']
            
            # Fall back to checking file on disk
            full_path = os.path.join(self.rclone_config['upload_folder'], file_path)
            if os.path.exists(full_path):
                return os.path.getsize(full_path)
            
            return 0
        except Exception as e:
            log.debug(f"Could not get file size for {file_path}: {e}")
            return 0
