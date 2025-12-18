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
            'sas_used': []
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

