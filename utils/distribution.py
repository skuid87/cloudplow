import bisect
import logging
import json
import os
import time
import requests
from collections import defaultdict

log = logging.getLogger("distribution")


class FileDistributionTracker:
    """
    Track file size distribution without storing all files
    Uses reservoir sampling for percentiles and buckets for distribution
    """
    
    def __init__(self, reservoir_size=10000):
        self.reservoir = []  # Sample of file sizes for percentile calculation
        self.reservoir_size = reservoir_size
        self.count = 0
        
        # Size buckets (in bytes)
        self.buckets = {
            '0-100MB': {'count': 0, 'total': 0, 'min': 0, 'max': 100 * 1024**2},
            '100MB-1GB': {'count': 0, 'total': 0, 'min': 100 * 1024**2, 'max': 1 * 1024**3},
            '1GB-10GB': {'count': 0, 'total': 0, 'min': 1 * 1024**3, 'max': 10 * 1024**3},
            '10GB-50GB': {'count': 0, 'total': 0, 'min': 10 * 1024**3, 'max': 50 * 1024**3},
            '50GB+': {'count': 0, 'total': 0, 'min': 50 * 1024**3, 'max': float('inf')}
        }
        
        # Running statistics
        self.total_bytes = 0
        self.max_size = 0
        self.sum_squares = 0  # For std deviation
    
    def add_file(self, file_size):
        """Add a file size to the distribution"""
        self.count += 1
        self.total_bytes += file_size
        self.max_size = max(self.max_size, file_size)
        self.sum_squares += file_size ** 2
        
        # Update buckets
        for bucket_name, bucket_info in self.buckets.items():
            if bucket_info['min'] <= file_size < bucket_info['max']:
                bucket_info['count'] += 1
                bucket_info['total'] += file_size
                break
        
        # Reservoir sampling for percentiles
        if len(self.reservoir) < self.reservoir_size:
            bisect.insort(self.reservoir, file_size)
        else:
            # Random replacement with decreasing probability
            import random
            j = random.randint(0, self.count - 1)
            if j < self.reservoir_size:
                # Remove old value and insert new one
                old_val = self.reservoir[j]
                self.reservoir.pop(j)
                bisect.insort(self.reservoir, file_size)
    
    def get_percentile(self, p):
        """Get the p-th percentile (p in 0-100)"""
        if not self.reservoir:
            return 0
        idx = int(len(self.reservoir) * p / 100)
        return self.reservoir[min(idx, len(self.reservoir) - 1)]
    
    def get_statistics(self):
        """Calculate all statistics"""
        if self.count == 0:
            return None
        
        mean = self.total_bytes / self.count
        variance = (self.sum_squares / self.count) - (mean ** 2)
        std_dev = int(variance ** 0.5) if variance > 0 else 0
        
        # Calculate large file percentage (50GB+)
        large_file_count = self.buckets['50GB+']['count']
        large_file_pct = (large_file_count / self.count * 100) if self.count > 0 else 0
        
        return {
            'max_file_size': self.max_size,
            'percentiles': {
                'p50': self.get_percentile(50),
                'p75': self.get_percentile(75),
                'p90': self.get_percentile(90),
                'p95': self.get_percentile(95),
                'p99': self.get_percentile(99),
            },
            'size_buckets': {
                name: {
                    'count': info['count'],
                    'total_bytes': info['total'],
                    'percentage': (info['count'] / self.count * 100) if self.count > 0 else 0
                }
                for name, info in self.buckets.items()
            },
            'statistics': {
                'total_files': self.count,
                'total_bytes': self.total_bytes,
                'mean': int(mean),
                'median': self.get_percentile(50),
                'std_dev': std_dev
            },
            'large_file_percentage': large_file_pct
        }
    
    def to_dict(self):
        """Export tracker state for persistence"""
        return {
            'count': self.count,
            'total_bytes': self.total_bytes,
            'max_size': self.max_size,
            'sum_squares': self.sum_squares,
            'reservoir': self.reservoir,
            'buckets': self.buckets
        }
    
    @classmethod
    def from_dict(cls, data):
        """Reconstruct tracker from saved state"""
        tracker = cls()
        tracker.count = data.get('count', 0)
        tracker.total_bytes = data.get('total_bytes', 0)
        tracker.max_size = data.get('max_size', 0)
        tracker.sum_squares = data.get('sum_squares', 0)
        tracker.reservoir = data.get('reservoir', [])
        tracker.buckets = data.get('buckets', tracker.buckets)
        return tracker


def format_bytes(bytes_val):
    """Convert bytes to human readable format"""
    if bytes_val == 0:
        return "0 B"
    for unit in ['B', 'KB', 'MB', 'GB', 'TB', 'PB']:
        if bytes_val < 1024.0:
            return f"{bytes_val:.1f} {unit}"
        bytes_val /= 1024.0
    return f"{bytes_val:.1f} PB"


def load_distribution_cache(cache_file):
    """Load distribution cache from file"""
    if not os.path.exists(cache_file):
        return {}
    
    try:
        with open(cache_file, 'r') as f:
            return json.load(f)
    except Exception as e:
        log.warning(f"Failed to load distribution cache: {e}")
        return {}


def save_distribution_cache(cache_file, cache_data):
    """Save distribution cache to file"""
    try:
        with open(cache_file, 'w') as f:
            json.dump(cache_data, f, indent=2)
        log.debug(f"Saved distribution cache to {cache_file}")
    except Exception as e:
        log.warning(f"Failed to save distribution cache: {e}")


def update_distribution_cache(cache_file, uploader_name, transfer_stats, upload_folder):
    """
    Update distribution cache with new transfer data
    
    Args:
        cache_file: Path to cache file
        uploader_name: Name of the uploader
        transfer_stats: Dict with file_sizes {path: size_bytes}
        upload_folder: Upload folder path for validation
    """
    # Load existing cache
    cache = load_distribution_cache(cache_file)
    
    # Get or create tracker
    if uploader_name in cache and 'tracker_state' in cache[uploader_name]:
        tracker = FileDistributionTracker.from_dict(cache[uploader_name]['tracker_state'])
    else:
        tracker = FileDistributionTracker()
    
    # Add new files
    for file_path, file_size in transfer_stats.items():
        tracker.add_file(file_size)
    
    # Calculate statistics
    stats = tracker.get_statistics()
    
    if stats:
        # Determine confidence level
        sample_count = stats['statistics']['total_files']
        if sample_count > 1000:
            confidence = 'very_high'
        elif sample_count > 100:
            confidence = 'high'
        elif sample_count > 10:
            confidence = 'medium'
        else:
            confidence = 'low'
        
        # Build cache entry
        cache[uploader_name] = {
            'max_file_size': stats['max_file_size'],
            'max_file_size_human': format_bytes(stats['max_file_size']),
            
            'percentiles': stats['percentiles'],
            'percentiles_human': {
                f'p{p}': format_bytes(stats['percentiles'][f'p{p}'])
                for p in [50, 75, 90, 95, 99]
            },
            
            'size_buckets': stats['size_buckets'],
            'large_file_percentage': stats['large_file_percentage'],
            
            'statistics': {
                **stats['statistics'],
                'mean_human': format_bytes(stats['statistics']['mean']),
                'median_human': format_bytes(stats['statistics']['median']),
                'std_dev_human': format_bytes(stats['statistics']['std_dev']),
                'total_bytes_human': format_bytes(stats['statistics']['total_bytes'])
            },
            
            'metadata': {
                'last_updated': time.time(),
                'last_updated_human': time.strftime('%Y-%m-%d %H:%M:%S'),
                'upload_folder': upload_folder,
                'sample_count': sample_count,
                'confidence': confidence
            },
            
            # Save tracker state for next time
            'tracker_state': tracker.to_dict()
        }
        
        # Save to disk
        save_distribution_cache(cache_file, cache)
        
        log.info(f"Distribution cache updated for {uploader_name}: "
                 f"{sample_count} files, confidence={confidence}, "
                 f"P50={format_bytes(stats['percentiles']['p50'])}, "
                 f"P95={format_bytes(stats['percentiles']['p95'])}, "
                 f"Max={format_bytes(stats['max_file_size'])}")
        
        return stats
    
    return None


def load_learned_distribution(cache_file, uploader_name, upload_folder):
    """
    Load learned distribution for a specific uploader
    
    Returns:
        dict with distribution data, or None if not found/invalid
    """
    cache = load_distribution_cache(cache_file)
    
    if uploader_name not in cache:
        log.debug(f"No learned distribution for {uploader_name}")
        return None
    
    entry = cache[uploader_name]
    
    # Validate upload folder matches
    cached_folder = entry.get('metadata', {}).get('upload_folder', '')
    if cached_folder and cached_folder != upload_folder:
        log.warning(f"Learned distribution for {uploader_name} is from different folder "
                   f"({cached_folder} vs {upload_folder}). Ignoring cached data.")
        return None
    
    sample_count = entry.get('metadata', {}).get('sample_count', 0)
    confidence = entry.get('metadata', {}).get('confidence', 'low')
    
    log.info(f"Loaded learned distribution for {uploader_name}: "
             f"{sample_count} files, confidence={confidence}")
    
    return entry


def capture_queue_distribution_from_checkers(rc_url, upload_folder, timeout=300):
    """
    Monitor rclone checkers during the initial scan phase to learn about
    upcoming transfer sizes. This provides forward-looking data about what
    WILL be transferred, not what HAS been transferred.
    
    Args:
        rc_url: Rclone RC API URL
        upload_folder: Upload folder path for validation
        timeout: Maximum time to wait for checkers (seconds)
    
    Returns:
        FileDistributionTracker with sizes of files in the queue,
        or None if unable to capture
    """
    tracker = FileDistributionTracker()
    start_time = time.time()
    seen_files = set()
    last_check_count = 0
    no_new_files_count = 0
    
    log.info("Monitoring rclone checkers to learn file distribution from upload queue...")
    
    try:
        while time.time() - start_time < timeout:
            try:
                response = requests.post(f"{rc_url}/core/stats", timeout=5)
                if response.status_code == 200:
                    stats = response.json()
                    
                    # Capture files currently being checked
                    checking = stats.get('checking', [])
                    for file_info in checking:
                        file_name = file_info.get('name', '')
                        file_size = file_info.get('size', 0)
                        
                        if file_name and file_name not in seen_files and file_size > 0:
                            tracker.add_file(file_size)
                            seen_files.add(file_name)
                            log.debug(f"Queue learned: {file_name} ({format_bytes(file_size)})")
                    
                    # Also capture files in the transferring queue (they were checked)
                    transferring = stats.get('transferring', [])
                    for file_info in transferring:
                        file_name = file_info.get('name', '')
                        file_size = file_info.get('size', 0)
                        
                        if file_name and file_name not in seen_files and file_size > 0:
                            tracker.add_file(file_size)
                            seen_files.add(file_name)
                            log.debug(f"Queue learned: {file_name} ({format_bytes(file_size)})")
                    
                    current_check_count = len(seen_files)
                    
                    # If we're not learning new files and transfers have started, we're done
                    if current_check_count == last_check_count:
                        no_new_files_count += 1
                        if no_new_files_count >= 5 and len(transferring) > 0:
                            log.info(f"Checker monitoring complete: captured {current_check_count} files")
                            break
                    else:
                        no_new_files_count = 0
                    
                    last_check_count = current_check_count
                    
                    # Log progress periodically
                    if current_check_count > 0 and current_check_count % 100 == 0:
                        log.info(f"Queue learning progress: {current_check_count} files captured...")
                        
            except Exception as e:
                log.debug(f"Error monitoring checkers: {e}")
            
            time.sleep(2)  # Poll every 2 seconds
        
        if len(seen_files) > 0:
            stats = tracker.get_statistics()
            if stats:
                log.info(f"Learned queue distribution: {len(seen_files)} files, "
                        f"P50={format_bytes(stats['percentiles']['p50'])}, "
                        f"P95={format_bytes(stats['percentiles']['p95'])}, "
                        f"Max={format_bytes(stats['max_file_size'])}")
                return tracker
        else:
            log.warning("No files captured from checker queue")
            return None
            
    except Exception as e:
        log.error(f"Failed to capture queue distribution: {e}")
        return None


def save_queue_distribution(cache_file, uploader_name, tracker, upload_folder, checking_duration):
    """
    Save queue distribution to cache (used for decision-making)
    
    Args:
        cache_file: Path to cache file
        uploader_name: Name of the uploader
        tracker: FileDistributionTracker with queue data
        upload_folder: Upload folder path
        checking_duration: How long checking took (seconds)
    """
    if not tracker:
        log.warning("No tracker provided, skipping queue distribution save")
        return
    
    stats = tracker.get_statistics()
    if not stats:
        log.warning("No statistics available from tracker")
        return
    
    # Load existing cache
    cache = load_distribution_cache(cache_file)
    
    # Ensure entry exists
    if uploader_name not in cache:
        cache[uploader_name] = {}
    
    sample_count = stats['statistics']['total_files']
    
    # Determine confidence level
    if sample_count > 1000:
        confidence = 'very_high'
    elif sample_count > 100:
        confidence = 'high'
    elif sample_count > 10:
        confidence = 'medium'
    else:
        confidence = 'low'
    
    # Save queue distribution (USED FOR DECISIONS)
    cache[uploader_name]['queue_distribution'] = {
        'max_file_size': stats['max_file_size'],
        'max_file_size_human': format_bytes(stats['max_file_size']),
        
        'percentiles': stats['percentiles'],
        'percentiles_human': {
            f'p{p}': format_bytes(stats['percentiles'][f'p{p}'])
            for p in [50, 75, 90, 95, 99]
        },
        
        'size_buckets': stats['size_buckets'],
        'large_file_percentage': stats['large_file_percentage'],
        
        'statistics': {
            **stats['statistics'],
            'mean_human': format_bytes(stats['statistics']['mean']),
            'median_human': format_bytes(stats['statistics']['median']),
            'std_dev_human': format_bytes(stats['statistics']['std_dev']),
            'total_bytes_human': format_bytes(stats['statistics']['total_bytes'])
        },
        
        'metadata': {
            'source': 'checker_queue',
            'learning_phase': 'pre_transfer',
            'last_updated': time.time(),
            'last_updated_human': time.strftime('%Y-%m-%d %H:%M:%S'),
            'upload_folder': upload_folder,
            'sample_count': sample_count,
            'confidence': confidence,
            'checking_duration_seconds': checking_duration
        }
    }
    
    # Save to disk
    save_distribution_cache(cache_file, cache)
    
    log.info(f"Queue distribution saved for {uploader_name}: "
             f"{sample_count} files, confidence={confidence}, "
             f"used for stage strategy decisions")


def load_queue_distribution(cache_file, uploader_name, upload_folder):
    """
    Load queue distribution for decision-making
    
    Returns:
        dict with queue distribution, or None if not found
    """
    cache = load_distribution_cache(cache_file)
    
    if uploader_name not in cache:
        log.debug(f"No queue distribution for {uploader_name}")
        return None
    
    entry = cache[uploader_name]
    
    if 'queue_distribution' not in entry:
        log.debug(f"No queue distribution found for {uploader_name}")
        return None
    
    queue_dist = entry['queue_distribution']
    
    # Validate upload folder matches
    cached_folder = queue_dist.get('metadata', {}).get('upload_folder', '')
    if cached_folder and cached_folder != upload_folder:
        log.warning(f"Queue distribution for {uploader_name} is from different folder "
                   f"({cached_folder} vs {upload_folder}). Ignoring cached data.")
        return None
    
    sample_count = queue_dist.get('metadata', {}).get('sample_count', 0)
    confidence = queue_dist.get('metadata', {}).get('confidence', 'low')
    
    log.info(f"Loaded queue distribution for {uploader_name}: "
             f"{sample_count} files, confidence={confidence} (for decisions)")
    
    return queue_dist


def save_transfer_history(cache_file, uploader_name, transfer_stats, upload_folder, session_info):
    """
    Save actual transfer data for analysis/visibility only
    Does NOT affect decision logic
    
    Args:
        cache_file: Path to cache file
        uploader_name: Name of the uploader
        transfer_stats: Dict with file_sizes {path: size_bytes}
        upload_folder: Upload folder path
        session_info: Dict with session_start, stage_number, etc.
    """
    # Load existing cache
    cache = load_distribution_cache(cache_file)
    
    # Ensure entry exists
    if uploader_name not in cache:
        cache[uploader_name] = {}
    
    # Get or create tracker for transfer history
    if 'transfer_history' in cache[uploader_name] and 'tracker_state' in cache[uploader_name]['transfer_history']:
        tracker = FileDistributionTracker.from_dict(cache[uploader_name]['transfer_history']['tracker_state'])
    else:
        tracker = FileDistributionTracker()
    
    # Add new files
    files_added = 0
    for file_path, file_size in transfer_stats.items():
        tracker.add_file(file_size)
        files_added += 1
    
    if files_added == 0:
        return
    
    # Calculate statistics
    stats = tracker.get_statistics()
    
    if stats:
        sample_count = stats['statistics']['total_files']
        
        # Save transfer history (FOR ANALYSIS ONLY)
        cache[uploader_name]['transfer_history'] = {
            'max_file_size': stats['max_file_size'],
            'max_file_size_human': format_bytes(stats['max_file_size']),
            
            'percentiles': stats['percentiles'],
            'percentiles_human': {
                f'p{p}': format_bytes(stats['percentiles'][f'p{p}'])
                for p in [50, 75, 90, 95, 99]
            },
            
            'size_buckets': stats['size_buckets'],
            'large_file_percentage': stats['large_file_percentage'],
            
            'statistics': {
                **stats['statistics'],
                'mean_human': format_bytes(stats['statistics']['mean']),
                'median_human': format_bytes(stats['statistics']['median']),
                'std_dev_human': format_bytes(stats['statistics']['std_dev']),
                'total_bytes_human': format_bytes(stats['statistics']['total_bytes'])
            },
            
            'metadata': {
                'source': 'completed_transfers',
                'learning_phase': 'post_transfer',
                'for_analysis_only': True,
                'last_updated': time.time(),
                'last_updated_human': time.strftime('%Y-%m-%d %H:%M:%S'),
                'upload_folder': upload_folder,
                'sample_count': sample_count,
                **session_info
            },
            
            # Save tracker state for incremental updates
            'tracker_state': tracker.to_dict()
        }
        
        # Compare with queue distribution if available
        if 'queue_distribution' in cache[uploader_name]:
            queue_dist = cache[uploader_name]['queue_distribution']
            queue_total = queue_dist.get('statistics', {}).get('total_bytes', 0)
            actual_total = stats['statistics']['total_bytes']
            
            if queue_total > 0:
                variance = abs(queue_total - actual_total) / queue_total * 100
                cache[uploader_name]['transfer_history']['comparison'] = {
                    'queue_predicted_total': format_bytes(queue_total),
                    'actual_transferred': format_bytes(actual_total),
                    'variance_percentage': round(variance, 2)
                }
        
        # Save to disk
        save_distribution_cache(cache_file, cache)
        
        log.info(f"Transfer history updated for {uploader_name}: "
                 f"{files_added} new files added, {sample_count} total (for analysis only)")

