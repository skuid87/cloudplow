"""
Session state tracking for dashboard
Updates dashboard_session_state.json with current upload information
"""

import json
import os
import time
import logging

log = logging.getLogger("session_state")


class SessionStateTracker:
    """Tracks current upload session state for dashboard visibility"""
    
    def __init__(self, config_dir):
        self.config_dir = config_dir
        self.session_file = os.path.join(config_dir, 'dashboard_session_state.json')
        self.session_data = {}
    
    def start_session(self, uploader, total_sas, upload_folder):
        """Mark session as started"""
        self.session_data = {
            'active': True,
            'uploader': uploader,
            'total_sas': total_sas,
            'sa_index': 0,
            'current_sa': '',
            'stage': 1,
            'total_stages': 0,
            'session_start': time.strftime('%Y-%m-%d %H:%M:%S'),
            'session_start_time': time.time(),
            'upload_folder': upload_folder,
            'sas_used': [],
            'total_files': 0,
            'total_bytes': 0,
            'transferred_files': 0,
            'transferred_bytes': 0
        }
        self._save()
        log.info(f"Started dashboard session for {uploader}")
    
    def update_sa(self, sa_index, sa_file, total_sas):
        """Update current service account"""
        if not self.session_data.get('active'):
            return
        
        self.session_data['sa_index'] = sa_index
        self.session_data['current_sa'] = os.path.basename(sa_file) if sa_file else ''
        self.session_data['total_sas'] = total_sas
        
        # Track unique SAs used
        if sa_file and sa_file not in self.session_data.get('sas_used', []):
            if 'sas_used' not in self.session_data:
                self.session_data['sas_used'] = []
            self.session_data['sas_used'].append(os.path.basename(sa_file))
        
        self._save()
        log.debug(f"Updated SA: {sa_index + 1}/{total_sas}")
    
    def update_stage(self, stage_number, total_stages=None):
        """Update current stage"""
        if not self.session_data.get('active'):
            return
        
        self.session_data['stage'] = stage_number
        if total_stages is not None:
            self.session_data['total_stages'] = total_stages
        
        self._save()
        log.debug(f"Updated stage: {stage_number}")
    
    def set_totals(self, total_files, total_bytes):
        """Set total files and bytes to transfer (from rclone scan)
        Only updates ONCE when totals are not yet set (preserves initial scan totals)
        This prevents overwriting cumulative totals with per-stage subset totals
        """
        if not self.session_data.get('active'):
            return
        
        current_total_files = self.session_data.get('total_files', 0)
        current_total_bytes = self.session_data.get('total_bytes', 0)
        
        # Only set files if not already set (first scan wins)
        if current_total_files == 0 and total_files > 0:
            self.session_data['total_files'] = total_files
            log.info(f"Initial total files captured: {total_files:,}")
        
        # Only set bytes if not already set and new value is non-zero
        # Skip if new value is 0 (means size unknown, don't overwrite existing)
        if current_total_bytes == 0 and total_bytes > 0:
            self.session_data['total_bytes'] = total_bytes
            log.info(f"Initial total bytes captured: {total_bytes:,}")
        
        self._save()
    
    def update_transferred(self, files_delta, bytes_delta):
        """Update cumulative transferred files and bytes"""
        if not self.session_data.get('active'):
            return
        
        # Initialize if not present
        if 'transferred_files' not in self.session_data:
            self.session_data['transferred_files'] = 0
        if 'transferred_bytes' not in self.session_data:
            self.session_data['transferred_bytes'] = 0
        
        # Add deltas
        self.session_data['transferred_files'] += files_delta
        self.session_data['transferred_bytes'] += bytes_delta
        
        # Validation: warn if transferred exceeds total (indicates totals might be wrong)
        total_files = self.session_data.get('total_files', 0)
        total_bytes = self.session_data.get('total_bytes', 0)
        if total_files > 0 and self.session_data['transferred_files'] > total_files:
            log.warning(f"Transferred files ({self.session_data['transferred_files']:,}) "
                       f"exceeds total ({total_files:,}) - totals may need recalculation")
        if total_bytes > 0 and self.session_data['transferred_bytes'] > total_bytes:
            log.warning(f"Transferred bytes ({self.session_data['transferred_bytes']:,}) "
                       f"exceeds total ({total_bytes:,}) - totals may need recalculation")
        
        self._save()
        log.debug(f"Updated transferred: +{files_delta} files, +{bytes_delta} bytes "
                 f"(total: {self.session_data['transferred_files']} files, "
                 f"{self.session_data['transferred_bytes']} bytes)")

    def update_transferred_realtime(self, bytes_delta):
        """Update transferred bytes in real-time (called per file)"""
        if not self.session_data.get('active'):
            return
        
        # Initialize if not present
        if 'transferred_bytes' not in self.session_data:
            self.session_data['transferred_bytes'] = 0
        if 'transferred_files' not in self.session_data:
            self.session_data['transferred_files'] = 0
        
        # Add deltas
        self.session_data['transferred_files'] += 1
        self.session_data['transferred_bytes'] += bytes_delta
        
        # Save every 10 files to avoid excessive I/O
        if self.session_data['transferred_files'] % 10 == 0:
            self._save()
            
    def update_stage_params(self, stage_params):
        """Update current stage parameters for dashboard display"""
        if not self.session_data.get('active'):
            return
        
        self.session_data['stage_params'] = {
            'strategy': stage_params.get('strategy', 'unknown'),
            'max_transfer': stage_params.get('max_transfer', '0G'),
            'max_size': stage_params.get('max_size', '0G'),
            'transfers': stage_params.get('transfers', 0),
            'order_by': stage_params.get('order_by'),
            'max_backlog': stage_params.get('max_backlog')
        }
        
        self._save()
        log.debug(f"Updated stage params: {stage_params.get('strategy', 'unknown')}")
    
    def end_session(self):
        """Mark session as ended"""
        if not self.session_data.get('active'):
            return
        
        self.session_data['active'] = False
        self.session_data['session_end'] = time.strftime('%Y-%m-%d %H:%M:%S')
        self.session_data['session_end_time'] = time.time()
        
        # Calculate total duration
        if 'session_start_time' in self.session_data:
            duration = time.time() - self.session_data['session_start_time']
            self.session_data['total_duration_seconds'] = int(duration)
        
        self._save()
        log.info(f"Ended dashboard session for {self.session_data.get('uploader', 'unknown')}")
    
    def is_active(self):
        """Check if session is active"""
        return self.session_data.get('active', False)
    
    def _save(self):
        """Save session state to file"""
        try:
            with open(self.session_file, 'w') as f:
                json.dump(self.session_data, f, indent=2)
        except Exception as e:
            log.warning(f"Failed to save session state: {e}")
    
    def _load(self):
        """Load existing session state"""
        try:
            if os.path.exists(self.session_file):
                with open(self.session_file, 'r') as f:
                    self.session_data = json.load(f)
                return True
        except Exception as e:
            log.debug(f"Failed to load session state: {e}")
        return False

