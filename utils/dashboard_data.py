"""
Dashboard data provider
Aggregates data from cache files, rclone RC API, and current session state
"""

import json
import os
import time
import logging
import requests
from datetime import datetime, timedelta

log = logging.getLogger("dashboard_data")


class DashboardDataProvider:
    """Provides data for the dashboard by reading cache files and polling rclone RC"""
    
    def __init__(self, config_dir, rc_url=None):
        self.config_dir = config_dir
        self.rc_url = rc_url
        self.sa_quota_cache_file = os.path.join(config_dir, 'sa_quota_cache.json')
        self.learned_sizes_cache_file = os.path.join(config_dir, 'learned_sizes_cache.json')
        self.session_state_file = os.path.join(config_dir, 'dashboard_session_state.json')
    
    def get_status(self):
        """Get overall upload status"""
        try:
            session_state = self._load_session_state()
            
            if not session_state or not session_state.get('active'):
                return {
                    'active': False,
                    'message': 'No active upload session'
                }
            
            return {
                'active': True,
                'uploader': session_state.get('uploader', 'unknown'),
                'current_sa': session_state.get('current_sa', 'unknown'),
                'sa_index': session_state.get('sa_index', 0),
                'total_sas': session_state.get('total_sas', 0),
                'stage': session_state.get('stage', 1),
                'total_stages': session_state.get('total_stages', 0),
                'session_start': session_state.get('session_start', ''),
                'upload_folder': session_state.get('upload_folder', '')
            }
        except Exception as e:
            log.error(f"Error getting status: {e}")
            return {'active': False, 'error': str(e)}
    
    def get_queue_distribution(self, uploader=None):
        """Get queue distribution data"""
        try:
            if not os.path.exists(self.learned_sizes_cache_file):
                return None
            
            with open(self.learned_sizes_cache_file, 'r') as f:
                cache = json.load(f)
            
            # If uploader specified, return just that one
            if uploader and uploader in cache:
                data = cache[uploader]
                if 'queue_distribution' in data:
                    return data['queue_distribution']
            
            # Otherwise return all
            result = {}
            for name, data in cache.items():
                if 'queue_distribution' in data:
                    result[name] = data['queue_distribution']
            
            return result if result else None
            
        except Exception as e:
            log.error(f"Error getting queue distribution: {e}")
            return None
    
    def get_transfer_history(self, uploader=None):
        """Get transfer history data"""
        try:
            if not os.path.exists(self.learned_sizes_cache_file):
                return None
            
            with open(self.learned_sizes_cache_file, 'r') as f:
                cache = json.load(f)
            
            if uploader and uploader in cache:
                data = cache[uploader]
                if 'transfer_history' in data:
                    return data['transfer_history']
            
            result = {}
            for name, data in cache.items():
                if 'transfer_history' in data:
                    result[name] = data['transfer_history']
            
            return result if result else None
            
        except Exception as e:
            log.error(f"Error getting transfer history: {e}")
            return None
    
    def get_service_accounts(self, uploader=None):
        """Get service account status and quota information"""
        try:
            quota_cache = {}
            if os.path.exists(self.sa_quota_cache_file):
                with open(self.sa_quota_cache_file, 'r') as f:
                    quota_cache = json.load(f)
            
            session_state = self._load_session_state()
            current_sa = session_state.get('current_sa', '') if session_state else ''
            current_uploader = session_state.get('uploader', '') if session_state else uploader
            
            result = []
            quota_limit = 750 * 1024**3  # 750GB
            
            # Get SA data for this uploader
            sa_data = quota_cache.get(current_uploader, {}) if current_uploader else {}
            
            # Add all SAs from quota cache
            for sa_file, info in sa_data.items():
                used_bytes = info.get('bytes', 0)
                reset_time = info.get('reset_time', 0)
                
                # Determine status
                if os.path.basename(sa_file) == current_sa:
                    status = 'active'
                elif used_bytes >= quota_limit * 0.95:
                    status = 'complete'
                else:
                    status = 'ready'
                
                # Calculate time until reset
                if reset_time > time.time():
                    seconds_until_reset = int(reset_time - time.time())
                    hours = seconds_until_reset // 3600
                    minutes = (seconds_until_reset % 3600) // 60
                    reset_in = f"{hours}h {minutes}m"
                else:
                    reset_in = "Ready"
                
                result.append({
                    'sa_file': os.path.basename(sa_file),
                    'sa_file_full': sa_file,
                    'status': status,
                    'used_bytes': used_bytes,
                    'quota_bytes': quota_limit,
                    'used_gb': round(used_bytes / 1024**3, 1),
                    'quota_gb': 750,
                    'percentage': round((used_bytes / quota_limit) * 100, 1),
                    'reset_time': reset_time,
                    'reset_in': reset_in
                })
            
            # IMPORTANT: If current SA is not in cache yet, add it with 0 usage
            # This happens when SA just started and no files have completed yet
            if current_sa and session_state and session_state.get('active'):
                sa_in_results = any(r['sa_file'] == current_sa for r in result)
                if not sa_in_results:
                    # Current SA is active but not yet in quota cache
                    result.append({
                        'sa_file': current_sa,
                        'sa_file_full': current_sa,
                        'status': 'active',
                        'used_bytes': 0,
                        'quota_bytes': quota_limit,
                        'used_gb': 0.0,
                        'quota_gb': 750,
                        'percentage': 0.0,
                        'reset_time': 0,
                        'reset_in': 'Ready'
                    })
            
            return sorted(result, key=lambda x: x['sa_file'])
            
        except Exception as e:
            log.error(f"Error getting service accounts: {e}")
            return []
    
    def get_rclone_stats(self):
        """Get real-time stats from rclone RC API"""
        if not self.rc_url:
            return None
        
        try:
            response = requests.post(
                f"{self.rc_url}/core/stats",
                timeout=5
            )
            
            if response.status_code == 200:
                stats = response.json()
                
                # Process transferring files
                transferring = []
                for file_info in stats.get('transferring', []):
                    name = file_info.get('name', 'unknown')
                    size = file_info.get('size', 0)
                    bytes_transferred = file_info.get('bytes', 0)
                    speed = file_info.get('speed', 0)
                    eta = file_info.get('eta', 0)
                    percentage = file_info.get('percentage', 0)
                    
                    transferring.append({
                        'name': name,
                        'size': size,
                        'size_human': self._format_bytes(size),
                        'bytes': bytes_transferred,
                        'bytes_human': self._format_bytes(bytes_transferred),
                        'speed': speed,
                        'speed_human': self._format_bytes(speed) + '/s',
                        'eta': eta,
                        'eta_human': self._format_duration(eta),
                        'percentage': percentage
                    })
                
                return {
                    'bytes': stats.get('bytes', 0),
                    'bytes_human': self._format_bytes(stats.get('bytes', 0)),
                    'speed': stats.get('speed', 0),
                    'speed_human': self._format_bytes(stats.get('speed', 0)) + '/s',
                    'eta': stats.get('eta', 0),
                    'eta_human': self._format_duration(stats.get('eta', 0)),
                    'transfers': stats.get('transfers', 0),
                    'transferring': transferring,
                    'checking': stats.get('checking', []),
                    'errors': stats.get('errors', 0)
                }
            
        except Exception as e:
            log.debug(f"Error getting rclone stats: {e}")
        
        return None
    
    def get_queue_status(self):
        """Get real-time queue status and stage parameters"""
        result = {
            'stage_params': {},
            'queue_stats': {}
        }
        
        # Get session state for stage parameters
        session_state = self._load_session_state()
        if session_state:
            result['stage_params'] = session_state.get('stage_params', {})
        
        # Get live queue stats from RC API
        if self.rc_url:
            try:
                response = requests.post(f"{self.rc_url}/core/stats", timeout=5)
                if response.status_code == 200:
                    stats = response.json()
                    
                    result['queue_stats'] = {
                        'listed': stats.get('listed', 0),
                        'checks': stats.get('checks', 0),
                        'totalChecks': stats.get('totalChecks', 0),
                        'checking': stats.get('checking', [])[:5],  # Limit to 5
                        'checking_count': len(stats.get('checking', [])),
                        'totalTransfers': stats.get('totalTransfers', 0),
                        'transferring_count': len(stats.get('transferring', []))
                    }
            except Exception as e:
                log.debug(f"Error getting queue status from RC: {e}")
        
        # Return result if we have any data, otherwise None
        return result if (result['stage_params'] or result['queue_stats']) else None
    
    def get_session_stats(self, uploader=None):
        """Get cumulative session statistics combining session state and live RC API data"""
        try:
            session_state = self._load_session_state()
            
            if not session_state:
                return None
            
            # Get cumulative data from session state (across all stages/SAs)
            session_transferred_files = session_state.get('transferred_files', 0)
            session_transferred_bytes = session_state.get('transferred_bytes', 0)
            
            # Try to get LIVE stats from RC API for current stage
            current_bytes = 0
            current_speed = 0
            current_eta = 0
            total_files = session_state.get('total_files', 0)
            total_bytes = session_state.get('total_bytes', 0)
            
            if self.rc_url:
                try:
                    response = requests.post(f"{self.rc_url}/core/stats", timeout=5)
                    if response.status_code == 200:
                        stats = response.json()
                        # Get current stage stats
                        current_bytes = stats.get('bytes', 0)
                        current_speed = stats.get('speed', 0)
                        current_eta = stats.get('eta', 0) or 0
                        
                        # If session state doesn't have totals yet, use RC API totals
                        if total_bytes == 0:
                            total_bytes = stats.get('totalBytes', 0)
                        if total_files == 0:
                            # Use totalTransfers + totalChecks as approximation
                            total_files = stats.get('totalTransfers', 0) + stats.get('totalChecks', 0)
                except Exception as e:
                    log.debug(f"Could not get RC stats for session stats: {e}")
            
            # Combine cumulative (from previous stages) + current (from RC API)
            transferred_bytes = session_transferred_bytes + current_bytes
            transferred_files = session_transferred_files  # Files completed (not current)
            
            # Calculate session duration
            session_start = session_state.get('session_start_time', time.time())
            duration_seconds = int(time.time() - session_start)
            
            # Calculate average speed (total transferred / total time)
            avg_speed = transferred_bytes / duration_seconds if duration_seconds > 0 else 0
            
            # Calculate remaining
            remaining_bytes = max(0, total_bytes - transferred_bytes)
            
            # Use RC API's ETA if available, otherwise calculate from avg speed
            if current_eta > 0:
                eta_seconds = current_eta
            else:
                eta_seconds = int(remaining_bytes / avg_speed) if avg_speed > 0 else 0
            
            # Calculate percentages
            file_percentage = (transferred_files / total_files * 100) if total_files > 0 else 0
            byte_percentage = (transferred_bytes / total_bytes * 100) if total_bytes > 0 else 0
            
            return {
                'total_files': total_files,
                'transferred_files': transferred_files,
                'remaining_files': max(0, total_files - transferred_files),
                'file_percentage': round(file_percentage, 1),
                
                'total_bytes': total_bytes,
                'total_bytes_human': self._format_bytes(total_bytes),
                'transferred_bytes': transferred_bytes,
                'transferred_bytes_human': self._format_bytes(transferred_bytes),
                'remaining_bytes': remaining_bytes,
                'remaining_bytes_human': self._format_bytes(remaining_bytes),
                'byte_percentage': round(byte_percentage, 1),
                
                'duration_seconds': duration_seconds,
                'duration_human': self._format_duration(duration_seconds),
                'avg_speed': avg_speed,
                'avg_speed_human': self._format_bytes(avg_speed) + '/s',
                
                'eta_seconds': eta_seconds,
                'eta_human': self._format_duration(eta_seconds),
                
                'session_start': session_state.get('session_start', ''),
                'sas_used': session_state.get('sas_used', [])
            }
            
        except Exception as e:
            log.error(f"Error getting session stats: {e}")
            return None
    
    def _load_session_state(self):
        """Load current session state"""
        try:
            if os.path.exists(self.session_state_file):
                with open(self.session_state_file, 'r') as f:
                    return json.load(f)
        except Exception as e:
            log.debug(f"Error loading session state: {e}")
        return None
    
    def _format_bytes(self, bytes_val):
        """Convert bytes to human readable format"""
        if bytes_val == 0:
            return "0 B"
        for unit in ['B', 'KB', 'MB', 'GB', 'TB', 'PB']:
            if bytes_val < 1024.0:
                return f"{bytes_val:.1f} {unit}"
            bytes_val /= 1024.0
        return f"{bytes_val:.1f} PB"
    
    def _format_duration(self, seconds):
        """Format duration in seconds to human readable"""
        if seconds == 0 or seconds is None:
            return "-"
        
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        
        if hours > 0:
            return f"{hours}h {minutes}m"
        elif minutes > 0:
            return f"{minutes}m {secs}s"
        else:
            return f"{secs}s"

