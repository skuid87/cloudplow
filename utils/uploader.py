import logging
import glob
import time
import datetime
import re

from . import path
from .rclone import RcloneUploader

log = logging.getLogger("uploader")


class Uploader:
    def __init__(self, name, uploader_config, rclone_config, rclone_binary_path, rclone_config_path, plex, dry_run, transfer_cache=None):
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

    def set_service_account(self, sa_file):
        self.service_account = sa_file
        log.info(f"Using service account: {sa_file}")

    def upload(self):
        # Determine if this is a weekend run
        self.is_weekend = datetime.datetime.now().weekday() in [5, 6]
        self.transferred_files = set()
        rclone_config = self.rclone_config.copy()

        # Load cache and apply excludes on weekdays
        if not self.is_weekend and self.transfer_cache is not None:
            cached_files = self._load_cached_files()
            if cached_files:
                log.info(f"Weekday run - excluding {len(cached_files)} cached files from transfer")
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

        return self.delayed_check, self.delayed_trigger, success, len(self.transferred_files)

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
        
        # Config changed? Wait for weekend
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
