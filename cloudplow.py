#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import datetime
import json
import logging
import os
import sys
import time
from logging.handlers import RotatingFileHandler
from multiprocessing import Process

import schedule
import requests

from utils import config, lock, path, decorators, version, misc
from utils.cache import Cache
from utils.notifications import Notifications
from utils.nzbget import Nzbget
from utils.sabnzbd import Sabnzbd
from utils.plex import Plex
from utils.rclone import RcloneThrottler, RcloneMover
from utils.syncer import Syncer
from utils.threads import Thread
from utils.unionfs import UnionfsHiddenFolder
from utils.uploader import Uploader
from utils.session_state import SessionStateTracker

############################################################
# INIT
############################################################

# Logging
log_formatter = logging.Formatter(u'%(asctime)s - %(levelname)-10s - %(name)-20s - %(funcName)-30s - %(message)s')
root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)

# Set schedule logger to ERROR
logging.getLogger('schedule').setLevel(logging.ERROR)
logging.getLogger("requests").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("sqlitedict").setLevel(logging.WARNING)

# Set console logger
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(log_formatter)
root_logger.addHandler(console_handler)

# Init config
conf = config.Config()

# Set file logger
file_handler = RotatingFileHandler(
    conf.settings['logfile'],
    maxBytes=1024 * 1024 * 5,
    backupCount=5,
    encoding='utf-8'
)
file_handler.setFormatter(log_formatter)
root_logger.addHandler(file_handler)

# Set chosen logging level
root_logger.setLevel(conf.settings['loglevel'])
log = root_logger.getChild('cloudplow')

# Load config from disk
conf.load()

# Init Cache class
cache = Cache(conf.settings['cachefile'])

# Init Notifications class
notify = Notifications()

# Init Syncer class
syncer = Syncer(conf.configs)

# Ensure lock folder exists
lock.ensure_lock_folder()

# Init thread class
thread = Thread()

# Logic vars
uploader_delay = cache.get_cache('uploader_bans')
syncer_delay = cache.get_cache('syncer_bans')
plex_monitor_thread = None
sa_delay = cache.get_cache('sa_bans')
transferred_files_cache = cache.get_cache('transferred_files')

# Quota tracking system
sa_quota_usage = {}  # Track {uploader: {sa_file: {'bytes': X, 'reset_time': timestamp}}}
SA_DAILY_QUOTA = 750 * 1024 * 1024 * 1024  # 750GB in bytes
SA_QUOTA_RESET_HOURS = 24

# Cache file paths
config_dir = os.path.dirname(conf.settings['config'])
sa_quota_cache_file = os.path.join(config_dir, 'sa_quota_cache.json')
learned_sizes_cache_file = os.path.join(config_dir, 'learned_sizes_cache.json')

# JSON transfer stats log path
json_transfer_log = os.path.join(os.path.dirname(conf.settings['logfile']), 'transfer-stats.jsonl')


############################################################
# MISC FUNCS
############################################################

def cleanup_temp_exclude_files():
    """Clean up any orphaned exclude files from previous runs"""
    try:
        # Get the directory where rclone config is stored
        rclone_config_path = conf.configs['core']['rclone_config_path']
        config_dir = os.path.dirname(rclone_config_path)
        
        if not os.path.isdir(config_dir):
            return
        
        # Find all cloudplow exclude temp files
        import glob
        pattern = os.path.join(config_dir, 'cloudplow_exclude_*.txt')
        orphaned_files = glob.glob(pattern)
        
        if orphaned_files:
            log.info(f"Cleaning up {len(orphaned_files)} orphaned exclude file(s) from previous runs")
            for file_path in orphaned_files:
                try:
                    os.remove(file_path)
                    log.debug(f"Removed orphaned exclude file: {file_path}")
                except Exception as e:
                    log.warning(f"Failed to remove orphaned exclude file {file_path}: {e}")
    except Exception:
        log.exception("Exception during temp file cleanup: ")


def init_notifications():
    try:
        for notification_name, notification_config in conf.configs['notifications'].items():
            notify.load(**notification_config)
    except Exception:
        log.exception("Exception initializing notification agents: ")
    return


def init_service_accounts():
    global sa_delay
    global uploader_delay
    log.debug("Start initializing of service accounts.")
    for uploader_remote, uploader_config in conf.configs['uploader'].items():
        if uploader_remote not in sa_delay:
            sa_delay[uploader_remote] = None
        if 'service_account_path' in uploader_config and os.path.exists(uploader_config['service_account_path']):
            # If service_account path provided, loop over the service account files and provide
            # one at a time when starting the uploader. If upload completes successfully, do not attempt
            # to use the other accounts
            accounts = {os.path.join(os.path.normpath(uploader_config['service_account_path']),
                                     sa_file): None for sa_file in
                        os.listdir(os.path.normpath(uploader_config['service_account_path'])) if
                        sa_file.endswith(".json")}
            current_accounts = sa_delay[uploader_remote]
            if current_accounts is not None:
                # Service account files may have moved, invalidate any missing cached accounts.
                cached_accounts = list(current_accounts)
                for cached_account in cached_accounts:
                    log.debug(f"Checking for cached service account file '{cached_account}' for remote '{uploader_remote}'")
                    if not cached_account.startswith(os.path.normpath(uploader_config['service_account_path'])):
                        log.debug(f"Cached service account file '{cached_account}' for remote '{uploader_remote}' is not located in specified service_account_path ('{uploader_config['service_account_path']}'). Removing from available accounts.")
                        current_accounts.pop(cached_account)
                    if not os.path.exists(cached_account):
                        log.debug(f"Cached service account file '{cached_account}' for remote '{uploader_remote}' could not be located. Removing from available accounts.")
                        current_accounts.pop(cached_account)

                # Add any new account files.
                for account in accounts:
                    if account not in current_accounts:
                        log.debug(f"New service account '{account}' has been added for remote '{uploader_remote}'")
                        current_accounts[account] = None
                sa_delay[uploader_remote] = current_accounts
                if len(current_accounts) < len(accounts):
                    log.debug(f"Additional service accounts were added. Lifting any current bans for remote '{uploader_remote}'")
                    uploader_delay.pop(uploader_remote, None)
            else:
                log.debug(f"The following accounts are defined: '{accounts}' and are about to be added to remote '{uploader_remote}'")
                sa_delay[uploader_remote] = accounts
    log.debug("Finished initializing of service accounts.")


def init_syncers():
    try:
        for syncer_name, syncer_config in conf.configs['syncer'].items():
            # remove irrelevant parameters before loading syncer agent
            filtered_config = syncer_config.copy()
            filtered_config.pop('sync_interval', None)
            filtered_config['syncer_name'] = syncer_name
            # load syncer agent
            syncer.load(**filtered_config)
    except Exception:
        log.exception("Exception initializing syncer agents: ")


############################################################
# QUOTA TRACKING SYSTEM
############################################################

def init_sa_quota_tracking():
    """Initialize SA quota tracking from cache file"""
    global sa_quota_usage
    
    if not os.path.exists(sa_quota_cache_file):
        log.debug("No existing SA quota cache found, starting fresh")
        sa_quota_usage = {}
        return
    
    try:
        with open(sa_quota_cache_file, 'r') as f:
            sa_quota_usage = json.load(f)
        log.info("Loaded SA quota tracking from cache")
        
        # Clean up expired quotas
        cleanup_expired_quotas()
    except Exception as e:
        log.warning(f"Failed to load SA quota cache: {e}")
        sa_quota_usage = {}


def cleanup_expired_quotas():
    """Remove quota entries older than 24 hours"""
    global sa_quota_usage
    global sa_delay
    current_time = time.time()
    
    for uploader in list(sa_quota_usage.keys()):
        for sa_file in list(sa_quota_usage[uploader].keys()):
            reset_time = sa_quota_usage[uploader][sa_file].get('reset_time', 0)
            if current_time >= reset_time:
                log.info(f"Quota reset for SA: {os.path.basename(sa_file)}")
                del sa_quota_usage[uploader][sa_file]
                
                # Also unban the SA in sa_delay when quota resets
                if uploader in sa_delay and sa_delay[uploader] is not None:
                    if sa_file in sa_delay[uploader]:
                        sa_delay[uploader][sa_file] = None
                        log.info(f"Unbanned SA in sa_delay: {os.path.basename(sa_file)}")
        
        # Clean up empty uploader entries
        if not sa_quota_usage[uploader]:
            del sa_quota_usage[uploader]


def save_sa_quota_cache():
    """Save SA quota tracking to cache file"""
    try:
        with open(sa_quota_cache_file, 'w') as f:
            json.dump(sa_quota_usage, f, indent=2)
        log.debug("Saved SA quota cache")
    except Exception as e:
        log.warning(f"Failed to save SA quota cache: {e}")


def get_sa_remaining_quota(uploader_remote, sa_file):
    """
    Calculate remaining quota for a service account
    Returns remaining bytes
    """
    global sa_quota_usage
    
    if uploader_remote not in sa_quota_usage:
        sa_quota_usage[uploader_remote] = {}
    
    if sa_file not in sa_quota_usage[uploader_remote]:
        # First use of this SA in current period
        return SA_DAILY_QUOTA
    
    used_bytes = sa_quota_usage[uploader_remote][sa_file].get('bytes', 0)
    reset_time = sa_quota_usage[uploader_remote][sa_file].get('reset_time', 0)
    
    # Check if quota period has expired
    if time.time() >= reset_time:
        log.info(f"Quota period expired for {os.path.basename(sa_file)}, resetting to full capacity")
        del sa_quota_usage[uploader_remote][sa_file]
        return SA_DAILY_QUOTA
    
    remaining = SA_DAILY_QUOTA - used_bytes
    return max(0, remaining)


def update_sa_quota_usage(uploader_remote, sa_file, bytes_uploaded):
    """Update quota usage for a service account"""
    global sa_quota_usage
    from utils.distribution import format_bytes
    
    if uploader_remote not in sa_quota_usage:
        sa_quota_usage[uploader_remote] = {}
    
    if sa_file not in sa_quota_usage[uploader_remote]:
        # Initialize with 24-hour reset time from first upload
        sa_quota_usage[uploader_remote][sa_file] = {
            'bytes': 0,
            'reset_time': time.time() + (SA_QUOTA_RESET_HOURS * 3600),
            'first_upload': time.time()
        }
    
    # Add to accumulated bytes
    sa_quota_usage[uploader_remote][sa_file]['bytes'] += bytes_uploaded
    
    log.debug(f"Updated quota for {os.path.basename(sa_file)}: "
              f"{format_bytes(sa_quota_usage[uploader_remote][sa_file]['bytes'])} / "
              f"{format_bytes(SA_DAILY_QUOTA)}")
    
    # Save to cache after each update
    save_sa_quota_cache()


############################################################
# DYNAMIC PARAMETER CALCULATION
############################################################

def calculate_stage_params_quota_based(remaining_quota_bytes, sa_daily_quota=750*1024**3):
    """
    Calculate stage parameters based purely on remaining SA quota
    Returns parameters including dynamic ordering flags
    
    Args:
        remaining_quota_bytes: Remaining SA quota in bytes
        sa_daily_quota: Daily quota limit in bytes (default 750GB)
    
    Returns:
        dict with max_transfer, max_size, transfers, strategy, order_by, max_backlog
    """
    from utils.distribution import format_bytes
    
    quota_percent = (remaining_quota_bytes / sa_daily_quota) * 100
    
    if quota_percent >= 80:  # 600GB+ remaining (Fresh SA)
        return {
            'max_transfer': f"{int(remaining_quota_bytes * 0.5 / 1024**3)}G",
            'max_size': f"{int(remaining_quota_bytes * 0.8 / 1024**3)}G",
            'transfers': 8,
            'stage_size_bytes': int(remaining_quota_bytes * 0.5),
            'strategy': 'aggressive_fresh_sa',
            # Use ordering to prioritize large files when we have quota
            'order_by': 'size,desc',
            'max_backlog': 2000
        }
    
    elif quota_percent >= 50:  # 375-600GB remaining
        return {
            'max_transfer': f"{int(remaining_quota_bytes * 0.6 / 1024**3)}G",
            'max_size': f"{int(remaining_quota_bytes * 0.5 / 1024**3)}G",
            'transfers': 4,
            'stage_size_bytes': int(remaining_quota_bytes * 0.6),
            'strategy': 'moderate_mid_sa',
            # Still use ordering but might have fewer large files left
            'order_by': 'size,desc',
            'max_backlog': 1000
        }
    
    elif quota_percent >= 25:  # 187-375GB remaining
        return {
            'max_transfer': f"{int(remaining_quota_bytes * 0.7 / 1024**3)}G",
            'max_size': f"{int(remaining_quota_bytes * 0.3 / 1024**3)}G",
            'transfers': 6,
            'stage_size_bytes': int(remaining_quota_bytes * 0.7),
            'strategy': 'cautious_low_quota',
            # Skip ordering - max-size already restricts to small files
            'order_by': None,
            'max_backlog': None
        }
    
    else:  # < 187GB remaining (Low quota)
        return {
            'max_transfer': f"{int(remaining_quota_bytes * 0.8 / 1024**3)}G",
            'max_size': f"{int(remaining_quota_bytes * 0.2 / 1024**3)}G",
            'transfers': 8,
            'stage_size_bytes': int(remaining_quota_bytes * 0.8),
            'strategy': 'conservative_cleanup',
            # Skip ordering - just transfer small files quickly
            'order_by': None,
            'max_backlog': None
        }


def check_suspended_sa(uploader_to_check):
    global sa_delay
    try:
        if sa_delay[uploader_to_check] is not None:
            log.debug(f"Proceeding to check any timeouts which have passed for remote {uploader_to_check}")
            for account, suspension_expiry in sa_delay[uploader_to_check].items():
                if suspension_expiry is not None:
                    log.debug(f"Service account {suspension_expiry} was previously banned. Checking if timeout has passed")
                    # Remove any ban times for service accounts which have passed
                    if time.time() > suspension_expiry:
                        log.debug(f"Setting ban status for service_account {account} to None since timeout has passed")
                        current_data = sa_delay[uploader_to_check]
                        current_data[account] = None
                        sa_delay[uploader_to_check] = current_data
    except Exception:
        log.exception("Exception checking suspended service accounts: ")


def check_suspended_uploaders(uploader_to_check=None):
    global uploader_delay

    suspended = False
    try:
        for uploader_name, suspension_expiry in dict(uploader_delay.items()).items():
            if time.time() < suspension_expiry:
                # this remote is still delayed due to a previous abort due to triggers
                use_logger = (
                    log.debug
                    if not uploader_to_check or uploader_name != uploader_to_check
                    else log.info
                )

                use_logger(f"{uploader_name} is still suspended due to a previously aborted upload. Normal operation in {misc.seconds_to_string(int(suspension_expiry - time.time()))} at {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(suspension_expiry))}")
                # return True when suspended if uploader_to_check is supplied and this is that remote
                if uploader_to_check and uploader_name == uploader_to_check:
                    suspended = True
            else:
                log.warning(f"{uploader_name} is no longer suspended due to a previous aborted upload!")
                uploader_delay.pop(uploader_name, None)
                # send notification that remote is no longer timed out
                notify.send(message=f"Upload suspension has expired for remote: {uploader_name}")

    except Exception:
        log.exception("Exception checking suspended uploaders: ")
    return suspended


def check_suspended_syncers(syncer_to_check=None):
    global syncer_delay

    suspended = False
    try:
        for syncer_name, suspension_expiry in dict(syncer_delay.items()).items():
            if time.time() < suspension_expiry:
                # this syncer is still delayed due to a previous abort due to triggers
                use_logger = (
                    log.debug
                    if not syncer_to_check or syncer_name != syncer_to_check
                    else log.info
                )

                use_logger(f"{syncer_name} is still suspended due to a previously aborted sync. Normal operation in {misc.seconds_to_string(int(suspension_expiry - time.time()))} at {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(suspension_expiry))}")
                # return True when suspended if syncer_to_check is supplied and this is that remote
                if syncer_to_check and syncer_name == syncer_to_check:
                    suspended = True
            else:
                log.warning(f"{syncer_name} is no longer suspended due to a previous aborted sync!")
                syncer_delay.pop(syncer_name, None)
                # send notification that remote is no longer timed out
                notify.send(message=f"Sync suspension has expired for syncer: {syncer_name}")

    except Exception:
        log.exception("Exception checking suspended syncers: ")
    return suspended


def run_process(task, manager_dict, **kwargs):
    try:
        new_process = Process(target=task, args=(manager_dict,), kwargs=kwargs)
        return new_process.start()
    except Exception:
        log.exception("Exception starting process with kwargs=%r: ", kwargs)


############################################################
# RCLONE RC STANDALONE DAEMON
############################################################

def is_rclone_rc_running(port=5572):
    """Check if rclone RC server is already running by attempting to connect to it"""
    try:
        import socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1)
        # We check localhost since this is where we'd start it
        result = sock.connect_ex(('127.0.0.1', port))
        sock.close()
        return result == 0
    except Exception:
        return False


def start_rclone_rc_if_needed():
    """Start standalone rclone rcd daemon if configured and not already running"""
    # Check if standalone RC is enabled in config
    rc_config = conf.configs.get('dashboard', {}).get('standalone_rc', {})
    if not rc_config.get('enabled', False):
        log.debug("Standalone rclone RC daemon not enabled in config, skipping auto-start")
        return False
    
    port = rc_config.get('port', 5572)
    
    # Check if already running
    if is_rclone_rc_running(port):
        log.info(f"Rclone RC daemon is already running on port {port}")
        return True
    
    try:
        import subprocess
        rclone_binary = conf.configs['core']['rclone_binary_path']
        rclone_config = conf.configs['core']['rclone_config_path']
        
        log.info("Starting standalone rclone RC daemon (rcd) in background...")
        
        # Use rcd command - the dedicated RC daemon
        cmd = [rclone_binary, 'rcd', f'--config={rclone_config}']
        
        # RC address binding
        rc_addr = rc_config.get('rc_addr', f'0.0.0.0:{port}')
        cmd.append(f'--rc-addr={rc_addr}')
        
        # Authentication - consider security!
        if rc_config.get('rc_no_auth', False):
            cmd.append('--rc-no-auth')
            log.warning("RC server starting with NO AUTHENTICATION - ensure your network is secure!")
        else:
            # Use authentication if provided
            if rc_config.get('rc_user') and rc_config.get('rc_pass'):
                cmd.extend([
                    f"--rc-user={rc_config['rc_user']}",
                    f"--rc-pass={rc_config['rc_pass']}"
                ])
                log.info(f"RC server will use authentication with user: {rc_config['rc_user']}")
            elif rc_config.get('rc_htpasswd'):
                cmd.append(f"--rc-htpasswd={rc_config['rc_htpasswd']}")
                log.info("RC server will use htpasswd authentication")
            else:
                log.warning("No authentication configured for RC server - consider setting rc_user/rc_pass")
        
        # Web GUI
        if rc_config.get('rc_web_gui', False):
            cmd.append('--rc-web-gui')
            if rc_config.get('rc_web_gui_no_open_browser', True):
                cmd.append('--rc-web-gui-no-open-browser')
        
        # Optional: verbose logging for debugging
        if rc_config.get('verbose', False):
            cmd.append('-v')
        
        # Log the command (without sensitive info)
        log.debug(f"Starting rcd with command: {' '.join([c for c in cmd if 'pass' not in c.lower()])}")
        
        # Start process detached
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True
        )
        
        # Give it a moment to start
        time.sleep(2)
        
        if is_rclone_rc_running(port):
            log.info(f"Rclone RC daemon started successfully on port {port}")
            if rc_config.get('rc_web_gui'):
                log.info(f"RC Web GUI available at: http://localhost:{port}")
            return True
        else:
            log.warning("Rclone RC daemon process started but port is not responding")
            return False
            
    except Exception as e:
        log.warning(f"Failed to auto-start rclone RC daemon: {e}")
        return False


############################################################
# DASHBOARD AUTO-START
############################################################

dashboard_process = None


def is_dashboard_running():
    """Check if dashboard is already running by attempting to connect to it"""
    if 'dashboard' not in conf.configs or not conf.configs['dashboard'].get('enabled'):
        return False
    
    port = conf.configs['dashboard'].get('port', 47949)
    
    try:
        import socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1)
        result = sock.connect_ex(('127.0.0.1', port))
        sock.close()
        return result == 0
    except Exception:
        return False


def start_dashboard_if_needed():
    """Start dashboard in background if enabled and not already running"""
    global dashboard_process
    
    # Check if dashboard is enabled
    if 'dashboard' not in conf.configs or not conf.configs['dashboard'].get('enabled'):
        log.debug("Dashboard not enabled in config, skipping auto-start")
        return False
    
    # Check if already running
    if is_dashboard_running():
        log.info("Dashboard is already running")
        return True
    
    # Start dashboard in background
    try:
        import subprocess
        dashboard_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'dashboard', 'app.py')
        
        if not os.path.exists(dashboard_script):
            log.warning(f"Dashboard script not found at {dashboard_script}, skipping auto-start")
            return False
        
        log.info("Starting dashboard in background...")
        
        # Start dashboard as subprocess (detached)
        dashboard_process = subprocess.Popen(
            [sys.executable, dashboard_script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True
        )
        
        # Give it a moment to start
        time.sleep(2)
        
        # Verify it started
        if is_dashboard_running():
            port = conf.configs['dashboard'].get('port', 47949)
            log.info(f"Dashboard started successfully on port {port}")
            log.info(f"Access dashboard at: http://localhost:{port}")
            return True
        else:
            log.warning("Dashboard process started but not responding on port")
            return False
            
    except Exception as e:
        log.warning(f"Failed to auto-start dashboard: {e}")
        return False


############################################################
# DOER FUNCS
############################################################


@decorators.timed
def do_upload(remote=None):
    global plex_monitor_thread, uploader_delay
    global sa_delay

    nzbget = None
    nzbget_paused = False

    sabnzbd = None
    sabnzbd_paused = False

    lock_file = lock.upload()
    if lock_file.is_locked():
        log.info("Waiting for running upload to finish before proceeding...")

    with lock_file:
        log.info("Starting upload")
        
        # Initialize session state tracker for dashboard
        session_tracker = SessionStateTracker(config_dir)
        
        # Auto-start standalone rclone RC daemon if configured (before dashboard)
        start_rclone_rc_if_needed()
        
        # Auto-start dashboard if enabled and not already running
        start_dashboard_if_needed()
        
        try:
            # loop each supplied uploader config
            for uploader_remote, uploader_config in conf.configs['uploader'].items():
                # if remote is not None, skip this remote if it is not == remote
                if remote and uploader_remote != remote:
                    continue

                # retrieve rclone config for this remote
                rclone_config = conf.configs['remotes'][uploader_remote]

                # determine RC URL for stats polling
                rc_url = None
                if conf.configs['plex'].get('enabled') and 'rclone' in conf.configs['plex']:
                    rc_url = conf.configs['plex']['rclone'].get('url')
                if not rc_url:
                    # Default RC URL if not configured via Plex
                    rc_url = 'http://localhost:5572'

                # create uploader with transfer cache
                uploader = Uploader(uploader_remote,
                                    uploader_config,
                                    rclone_config,
                                    conf.configs['core']['rclone_binary_path'],
                                    conf.configs['core']['rclone_config_path'],
                                    conf.configs['plex'],
                                    conf.configs['core']['dry_run'],
                                    transferred_files_cache,
                                    json_transfer_log,
                                    rc_url)

                # Initialize cumulative metrics for this upload session
                session_start_time = time.time()
                cumulative_metrics = {
                    'transfer_count': 0,
                    'total_bytes': 0,
                    'duration_seconds': 0,
                    'start_time': session_start_time,
                    'sa_used': [],
                    'cached_files_excluded': 0,
                    'is_weekend': datetime.datetime.now().weekday() in [5, 6]
                }

                # send notification that upload is starting
                notify.send(message=f"Upload starting for {uploader_remote}")

                # start the plex stream monitor before the upload begins, if enabled for both plex and the uploader
                if conf.configs['plex']['enabled'] and plex_monitor_thread is None:
                    # Only disable throttling if 'can_be_throttled' is both present in uploader_config and is set to False.
                    if 'can_be_throttled' in uploader_config and not uploader_config['can_be_throttled']:
                        log.debug(f"Skipping check for Plex stream due to throttling disabled in remote: {uploader_remote}")
                    # Otherwise, assume throttling is desired.
                    else:
                        plex_monitor_thread = thread.start(do_plex_monitor, 'plex-monitor')

                # Initialize chunking variables (used later for cleanup)
                use_chunking = False
                chunks = []
                list_file = None
                total_files_from_list = 0

                # pause the nzbget queue before starting the upload, if enabled
                if conf.configs['nzbget']['enabled']:
                    nzbget = Nzbget(conf.configs['nzbget']['url'])
                    if nzbget.pause_queue():
                        nzbget_paused = True
                        log.info("Paused the Nzbget download queue, upload commencing!")
                        notify.send(message="Paused the Nzbget download queue, upload commencing!")
                    else:
                        log.error("Failed to pause the Nzbget download queue, upload commencing anyway...")
                        notify.send(message="Failed to pause the Nzbget download queue, upload commencing anyway...")

                # pause the sabnzbd queue before starting the upload, if enabled
                if conf.configs['sabnzbd']['enabled']:
                    sabnzbd = Sabnzbd(conf.configs['sabnzbd']['url'], conf.configs['sabnzbd']['apikey'])
                    if sabnzbd.pause_queue():
                        sabnzbd_paused = True
                        log.info("Paused the Sabnzbd download queue, upload commencing!")
                        notify.send(message="Paused the Sabnzbd download queue, upload commencing!")
                    else:
                        print(sabnzbd.pause_queue())
                        log.error("Failed to pause the Sabnzbd download queue, upload commencing anyway...")
                        notify.send(message="Failed to pause the Sabnzbd download queue, upload commencing anyway...")

                # Check for any expired SA bans before checking available accounts
                check_suspended_sa(uploader_remote)

                if sa_delay[uploader_remote] is not None:
                    available_accounts = [account for account, last_ban_time in sa_delay[uploader_remote].items() if
                                          last_ban_time is None]
                    available_accounts_size = len(available_accounts)

                    if available_accounts_size:
                        available_accounts = misc.sorted_list_by_digit_asc(available_accounts)

                    log.info(f"There is {available_accounts_size} available service accounts")
                    log.debug(f"Available service accounts: {str(available_accounts)}")

                    # If there are no service accounts available, do not even bother attempting the upload
                    if not available_accounts_size:
                        log.info(f"Upload aborted due to the fact that no service accounts are currently unbanned and available to use for remote {uploader_remote}")
                        # add remote to uploader_delay
                        time_till_unban = misc.get_lowest_remaining_time(sa_delay[uploader_remote])
                        log.info(f"Lowest Remaining time till unban is {misc.seconds_to_string(int(time_till_unban - time.time()))}")
                        uploader_delay[uploader_remote] = time_till_unban
                        
                        # Send notification about no available accounts
                        notify.send(message=f"Upload skipped for {uploader_remote}: All service accounts are currently suspended. Next available in {misc.seconds_to_string(int(time_till_unban - time.time()))}")
                    else:
                        # Clean up expired quotas before starting
                        cleanup_expired_quotas()
                        
                        # Update start notification with SA info
                        notify.send(message=f"Upload starting for {uploader_remote} using service account: {available_accounts[0]} ({available_accounts_size} accounts available)")
                        
                        # Start dashboard session
                        session_tracker.start_session(
                            uploader=uploader_remote,
                            total_sas=available_accounts_size,
                            upload_folder=rclone_config['upload_folder']
                        )
                        
                        # Check if chunked upload is enabled
                        chunked_config = uploader_config.get('chunked_upload', {})
                        use_chunking = chunked_config.get('enabled', False)
                        
                        if use_chunking:
                            from utils.chunker import FileChunker
                            
                            chunk_size = chunked_config.get('chunk_size', 1000)
                            list_timeout = chunked_config.get('generate_list_timeout', 600)
                            
                            # Prepare excludes list including cached files (before generating file list)
                            excludes_for_chunking = rclone_config.get('rclone_excludes', []).copy()
                            
                            # Load cached files for weekday runs (same logic as in Uploader)
                            is_weekend = datetime.datetime.now().weekday() in [5, 6]
                            if not is_weekend and transferred_files_cache is not None:
                                try:
                                    # Load cached files from the cache dictionary
                                    cache_data = transferred_files_cache.get(uploader_remote, {})
                                    cached_files = cache_data.get('files', [])
                                    
                                    if cached_files:
                                        log.info(f"Weekday run - adding {len(cached_files)} cached files to excludes for file list generation")
                                        excludes_for_chunking.extend(cached_files)
                                    else:
                                        log.debug(f"No cached files found for {uploader_remote}")
                                except Exception as e:
                                    log.warning(f"Failed to load cached files for chunking: {e}")
                            elif is_weekend:
                                log.info("Weekend run - full scan, no cache excludes")
                            
                            # Also check for open files if configured
                            if uploader_config.get('exclude_open_files', False):
                                try:
                                    from utils import path as path_utils
                                    import glob
                                    open_files = path_utils.opened_files(rclone_config['upload_folder'])
                                    # Filter out files that match opened_excludes patterns
                                    opened_excludes = uploader_config.get('opened_excludes', [])
                                    files_to_exclude = [
                                        item.replace(rclone_config['upload_folder'], '')
                                        for item in open_files
                                        if not any(excl.lower() in item.lower() for excl in opened_excludes)
                                    ]
                                    if files_to_exclude:
                                        log.info(f"Adding {len(files_to_exclude)} open files to excludes for file list generation")
                                        for item in files_to_exclude:
                                            excludes_for_chunking.append(glob.escape(item))
                                except Exception as e:
                                    log.warning(f"Failed to check for open files: {e}")
                            
                            log.info(f"Chunked upload enabled (chunk_size={chunk_size}) - generating file list with {len(excludes_for_chunking)} excludes")
                            chunker = FileChunker(
                                conf.configs['core']['rclone_binary_path'],
                                conf.configs['core']['rclone_config_path'],
                                rclone_config['upload_folder'],
                                excludes_for_chunking,
                                rclone_config.get('rclone_extras', {}),
                                timeout=list_timeout
                            )
                            
                            result = chunker.generate_file_list()
                            if not result:
                                log.error("Failed to generate file list - falling back to normal upload")
                                use_chunking = False
                            else:
                                list_file, total_files_from_list = result
                                chunks = chunker.create_chunks(list_file, chunk_size)
                                
                                if not chunks:
                                    log.error("Failed to create chunks - falling back to normal upload")
                                    use_chunking = False
                                    if list_file and os.path.exists(list_file):
                                        os.remove(list_file)
                                        list_file = None
                                else:
                                    log.info(f"Created {len(chunks)} chunks from {total_files_from_list:,} files")
                                    # Set totals in session tracker immediately
                                    session_tracker.set_totals(total_files_from_list, 0)  # Size unknown until upload
                        
                        for i in range(available_accounts_size):
                            sa_file = available_accounts[i]
                            sa_start_time = time.time()
                            
                            # Update current SA in dashboard
                            session_tracker.update_sa(
                                sa_index=i,
                                sa_file=sa_file,
                                total_sas=available_accounts_size
                            )
                            
                            # Check remaining quota for this SA
                            sa_quota_remaining = get_sa_remaining_quota(uploader_remote, sa_file)
                            
                            # Skip SA if insufficient quota
                            if sa_quota_remaining < 1 * 1024**3:  # Less than 1GB
                                from utils.distribution import format_bytes
                                log.warning(f"SA {os.path.basename(sa_file)} has insufficient quota ({format_bytes(sa_quota_remaining)}), skipping")
                                # Mark as temporarily suspended until quota resets
                                if sa_file in sa_quota_usage.get(uploader_remote, {}):
                                    reset_time = sa_quota_usage[uploader_remote][sa_file].get('reset_time')
                                    if reset_time:
                                        current_data = sa_delay[uploader_remote]
                                        current_data[sa_file] = reset_time
                                        sa_delay[uploader_remote] = current_data
                                continue
                            
                            # === MULTI-STAGE LOOP FOR THIS SA ===
                            stage_number = 1
                            sa_total_uploaded = 0
                            resp_delay = 0
                            resp_trigger = ""
                            resp_success = False
                            session_start_time = time.time()
                            totals_captured = False  # Track if we've captured total files/bytes
                            
                            while sa_quota_remaining > 10 * 1024**3:  # Continue while >10GB remains
                                from utils.distribution import format_bytes
                                log.info(f"SA {i+1}/{available_accounts_size} ({os.path.basename(sa_file)}), "
                                         f"Stage {stage_number}: {format_bytes(sa_quota_remaining)} remaining")
                                
                                # Update current stage in dashboard
                                session_tracker.update_stage(stage_number)
                                
                                # Calculate dynamic parameters for this stage based on quota
                                stage_params = calculate_stage_params_quota_based(
                                    sa_quota_remaining,
                                    SA_DAILY_QUOTA
                                )
                                
                                # Create dynamic rclone config for this stage
                                dynamic_rclone_config = rclone_config.copy()
                                if 'rclone_extras' not in dynamic_rclone_config:
                                    dynamic_rclone_config['rclone_extras'] = {}
                                
                                # Apply dynamic parameters
                                dynamic_rclone_config['rclone_extras']['--max-transfer'] = stage_params['max_transfer']
                                dynamic_rclone_config['rclone_extras']['--max-size'] = stage_params['max_size']
                                dynamic_rclone_config['rclone_extras']['--transfers'] = stage_params['transfers']
                                dynamic_rclone_config['rclone_extras']['--cutoff-mode'] = 'cautious'
                                
                                # Apply dynamic ordering flags based on strategy
                                if stage_params.get('order_by'):
                                    dynamic_rclone_config['rclone_extras']['--order-by'] = stage_params['order_by']
                                    log.info(f"Ordering files by: {stage_params['order_by']}")
                                else:
                                    # Remove ordering if it was in base config (for speed at low quota)
                                    dynamic_rclone_config['rclone_extras'].pop('--order-by', None)
                                    log.info("Skipping file ordering for faster start")
                                
                                if stage_params.get('max_backlog'):
                                    dynamic_rclone_config['rclone_extras']['--max-backlog'] = str(stage_params['max_backlog'])
                                    log.info(f"Max backlog: {stage_params['max_backlog']} files")
                                else:
                                    dynamic_rclone_config['rclone_extras'].pop('--max-backlog', None)
                                
                                # Create quota update callback for real-time tracking
                                def update_quota_realtime(bytes_delta):
                                    """Called each time a file completes to update quota in real-time"""
                                    update_sa_quota_usage(uploader_remote, sa_file, bytes_delta)
                                    session_tracker.update_transferred_realtime(bytes_delta)
                                
                                # For chunked uploads, remove flags incompatible with --files-from
                                if use_chunking:
                                    # Remove filter flags (they were applied during file list generation)
                                    filter_flags_to_remove = ['--min-age', '--max-age', '--skip-links', '--max-size']
                                    for flag in filter_flags_to_remove:
                                        if flag in dynamic_rclone_config['rclone_extras']:
                                            dynamic_rclone_config['rclone_extras'].pop(flag)
                                            log.debug(f"Removed {flag} for chunked upload (applied during file list generation)")
                                    
                                    # Remove flags that conflict with --files-from
                                    conflicting_flags = ['--order-by', '--max-backlog']
                                    for flag in conflicting_flags:
                                        if flag in dynamic_rclone_config['rclone_extras']:
                                            dynamic_rclone_config['rclone_extras'].pop(flag)
                                            log.debug(f"Removed {flag} for chunked upload (incompatible with --files-from)")
                                
                                # Create uploader with dynamic config for this stage
                                stage_uploader = Uploader(
                                    uploader_remote,
                                    uploader_config,
                                    dynamic_rclone_config,
                                    conf.configs['core']['rclone_binary_path'],
                                    conf.configs['core']['rclone_config_path'],
                                    conf.configs['plex'],
                                    conf.configs['core']['dry_run'],
                                    transferred_files_cache,
                                    json_transfer_log,
                                    rc_url,
                                    quota_callback=update_quota_realtime
                                )
                                stage_uploader.set_service_account(sa_file)
                                
                                log.info(f"Starting stage {stage_number} with: "
                                         f"transfers={stage_params['transfers']}, "
                                         f"max-transfer={stage_params['max_transfer']}, "
                                         f"strategy={stage_params['strategy']}")
                                
                                # Save stage params to session state for dashboard
                                session_tracker.update_stage_params(stage_params)
                                
                                # Start the upload
                                upload_start_time = time.time()
                                
                                # Capture total files BEFORE stage starts (from initial rclone scan)
                                if not totals_captured and rc_url and stage_number == 1:
                                    # Wait a bit for rclone to populate stats
                                    time.sleep(2)
                                    try:
                                        response = requests.post(f"{rc_url}/core/stats", timeout=5)
                                        if response.status_code == 200:
                                            stats = response.json()
                                            total_files = stats.get('listed', 0) or stats.get('totalChecks', 0)
                                            total_bytes = stats.get('totalBytes', 0)
                                            
                                            if total_files > 0:
                                                session_tracker.set_totals(total_files, total_bytes)
                                                totals_captured = True
                                                log.info(f"Captured session totals: {total_files} files, {format_bytes(total_bytes)}")
                                    except Exception as e:
                                        log.debug(f"Could not capture totals from RC API: {e}")
                                
                                # Run this stage (with chunks if enabled)
                                if use_chunking and stage_number == 1:
                                    # Chunked upload: upload each chunk separately
                                    log.info(f"=== Starting chunked upload: {len(chunks)} chunks ===")
                                    total_chunk_transfers = 0
                                    total_chunk_bytes = 0
                                    
                                    for chunk_idx, (chunk_file, chunk_file_count) in enumerate(chunks, 1):
                                        log.info(f"=== Uploading chunk {chunk_idx}/{len(chunks)} ({chunk_file_count} files) ===")
                                        
                                        # Upload this chunk
                                        chunk_resp = stage_uploader.upload(files_from=chunk_file)
                                        
                                        if not chunk_resp['success']:
                                            log.error(f"Chunk {chunk_idx} failed: {chunk_resp.get('delayed_trigger', 'Unknown error')}")
                                            # Set the response to the failed chunk response and break
                                            resp = chunk_resp
                                            break
                                        
                                        # Accumulate chunk results
                                        total_chunk_transfers += chunk_resp['transfer_count']
                                        total_chunk_bytes += chunk_resp['total_bytes']
                                        
                                        log.info(f"Chunk {chunk_idx}/{len(chunks)} completed: "
                                                f"{chunk_resp['transfer_count']} files, "
                                                f"{format_bytes(chunk_resp['total_bytes'])}")
                                        
                                        # Check if SA quota is exhausted, need to rotate
                                        sa_quota_remaining = get_sa_remaining_quota(uploader_remote, sa_file)
                                        if sa_quota_remaining < 10 * 1024**3:  # Less than 10GB
                                            log.info(f"SA quota low ({format_bytes(sa_quota_remaining)}), stopping chunk loop to rotate SA")
                                            break
                                    
                                    # Create combined response from all chunks
                                    upload_duration = time.time() - upload_start_time
                                    resp = {
                                        'success': True,
                                        'transfer_count': total_chunk_transfers,
                                        'total_bytes': total_chunk_bytes,
                                        'delayed_check': 0,
                                        'delayed_trigger': '',
                                        'duration_seconds': upload_duration,
                                        'avg_speed_bytes': total_chunk_bytes / upload_duration if upload_duration > 0 else 0,
                                        'is_weekend': stage_uploader.is_weekend,
                                        'cached_files_excluded': 0  # Chunk uploads don't use cache excludes in the same way
                                    }
                                    
                                    log.info(f"=== All chunks completed: {total_chunk_transfers} files, {format_bytes(total_chunk_bytes)} ===")
                                else:
                                    # Normal upload (no chunking or not stage 1)
                                    resp = stage_uploader.upload()
                                
                                # Process upload response
                                resp_delay = resp['delayed_check']
                                resp_trigger = resp['delayed_trigger']
                                resp_success = resp['success']
                                transfer_count = resp['transfer_count']
                                bytes_uploaded = resp['total_bytes']
                                
                                # Try to capture total files from RC API (only once, after first stage)
                                # Use the "listed" field which shows files found during scan
                                if not totals_captured and rc_url:
                                    try:
                                        response = requests.post(f"{rc_url}/core/stats", timeout=5)
                                        if response.status_code == 200:
                                            stats = response.json()
                                            # "listed" shows total files found during rclone's initial scan
                                            total_files = stats.get('listed', 0)
                                            # totalBytes shows the total size of all files to transfer
                                            total_bytes = stats.get('totalBytes', 0)
                                            
                                            # If we have meaningful totals, update session
                                            if total_files > 0:
                                                session_tracker.set_totals(total_files, total_bytes)
                                                totals_captured = True
                                                log.info(f"Captured session totals from RC: {total_files} files, {format_bytes(total_bytes)}")
                                    except Exception as e:
                                        log.debug(f"Could not capture totals from RC API: {e}")
                                
                                # Update dashboard session with stage progress
                                session_tracker.update_transferred(
                                    files_delta=transfer_count,
                                    bytes_delta=bytes_uploaded
                                )
                                
                                # Refresh quota tracking (already updated in real-time per file)
                                # Note: quota was updated via callback as each file completed
                                sa_quota_remaining = get_sa_remaining_quota(uploader_remote, sa_file)
                                sa_total_uploaded += bytes_uploaded
                                
                                log.info(f"Stage {stage_number} complete: uploaded {format_bytes(bytes_uploaded)}, "
                                         f"quota remaining: {format_bytes(sa_quota_remaining)}")
                                
                                # Accumulate metrics from this stage
                                cumulative_metrics['transfer_count'] += transfer_count
                                cumulative_metrics['total_bytes'] += bytes_uploaded
                                cumulative_metrics['duration_seconds'] += resp['duration_seconds']
                            if resp['cached_files_excluded'] > 0:
                                cumulative_metrics['cached_files_excluded'] = resp['cached_files_excluded']
                                
                                # Check stage completion status
                                # Exit code 7 = max-transfer reached, continue to next stage
                                # Exit code 0 = all files done, SA complete
                                # Other = error or trigger, abort SA
                                
                                if resp_delay:
                                    # Trigger was hit - exit stage loop
                                    log.info(f"Stage {stage_number} aborted due to trigger: {resp_trigger}")
                                    break
                                
                                # If we successfully completed but no more quota, exit
                                if sa_quota_remaining < 10 * 1024**3:
                                    log.info(f"SA {os.path.basename(sa_file)} quota depleted after {stage_number} stages")
                                    break
                                
                                # Continue to next stage
                                stage_number += 1
                            
                            # === END OF STAGE LOOP ===
                            
                            # Record that this SA was used
                            if sa_file not in cumulative_metrics['sa_used']:
                                cumulative_metrics['sa_used'].append(sa_file)
                            
                            log.info(f"SA {os.path.basename(sa_file)} complete after {stage_number} stage(s), "
                                     f"total uploaded: {format_bytes(sa_total_uploaded)}")
                            
                            # Handle delays and triggers
                            if resp_delay:
                                current_data = sa_delay[uploader_remote]
                                current_data[sa_file] = time.time() + ((60 * 60) * resp_delay)
                                sa_delay[uploader_remote] = current_data
                                log.debug(f"Setting account {os.path.basename(sa_file)} as unbanned at {sa_delay[uploader_remote][sa_file]}")
                                
                                if i != (len(available_accounts) - 1):
                                    log.info(f"Upload aborted due to trigger: {resp_trigger} being met, {uploader_remote} is cycling to service_account file: {available_accounts[i + 1]}")
                                    # Set unban time for current service account
                                    log.debug(f"Setting service account {os.path.basename(sa_file)} as banned for remote: {uploader_remote}")
                                    
                                    # Send SA cycling notification with this SA's stats and cumulative totals
                                    from utils.distribution import format_bytes
                                    from utils.uploader import format_duration
                                    sa_duration = time.time() - sa_start_time
                                    sa_msg = (f"Service account {os.path.basename(sa_file)} hit '{resp_trigger}' for {uploader_remote}. "
                                             f"This SA uploaded: {format_bytes(sa_total_uploaded)} across {stage_number} stage(s) "
                                             f"in {format_duration(sa_duration)}. "
                                             f"Session total so far: {cumulative_metrics['transfer_count']} files "
                                             f"({format_bytes(cumulative_metrics['total_bytes'])}). "
                                             f"Cycling to {os.path.basename(available_accounts[i + 1])} ({available_accounts_size - i - 1} remaining)")
                                    notify.send(message=sa_msg)
                                    
                                    continue
                                else:
                                    # non 0 result indicates a trigger was met, the result is how many hours
                                    # to sleep this remote for
                                    # Before banning remote, check that a service account did not become unbanned
                                    # during upload
                                    check_suspended_sa(uploader_remote)

                                    unban_time = misc.get_lowest_remaining_time(sa_delay[uploader_remote])
                                    if unban_time is not None:
                                        log.info(f"Upload aborted due to trigger: {resp_trigger} being met, {uploader_remote} will continue automatic uploading normally in {resp_delay} hours")

                                        # add remote to uploader_delay
                                        log.debug(f"Adding unban time for {uploader_remote} as {misc.get_lowest_remaining_time(sa_delay[uploader_remote])}")
                                        uploader_delay[uploader_remote] = misc.get_lowest_remaining_time(
                                            sa_delay[uploader_remote])

                                        # send aborted upload notification with cumulative stats
                                        from utils.uploader import format_bytes, format_duration
                                        total_duration = time.time() - cumulative_metrics['start_time']
                                        avg_speed = cumulative_metrics['total_bytes'] / total_duration if total_duration > 0 else 0
                                        
                                        abort_msg = (f"Upload was aborted for remote: {uploader_remote} due to trigger {resp_trigger}. "
                                                    f"Partial upload: {cumulative_metrics['transfer_count']} files "
                                                    f"({format_bytes(cumulative_metrics['total_bytes'])}) transferred "
                                                    f"in {format_duration(total_duration)} "
                                                    f"at avg {format_bytes(avg_speed)}/s. "
                                                    f"Uploads suspended for {resp_delay} hours")
                                        notify.send(message=abort_msg)
                            else:
                                if resp_success:
                                    log.info(f"Upload completed successfully for uploader: {uploader_remote}")
                                    # send successful upload notification with cumulative metrics
                                    from utils.uploader import format_bytes, format_duration
                                    
                                    if cumulative_metrics['transfer_count'] > 0:
                                        total_duration = time.time() - cumulative_metrics['start_time']
                                        avg_speed = cumulative_metrics['total_bytes'] / total_duration if total_duration > 0 else 0
                                        
                                        # Build SA info string
                                        sa_info = ""
                                        if len(cumulative_metrics['sa_used']) > 1:
                                            sa_info = f" (cycled through {len(cumulative_metrics['sa_used'])} service accounts: {', '.join(cumulative_metrics['sa_used'])})"
                                        elif len(cumulative_metrics['sa_used']) == 1:
                                            sa_info = f" using {cumulative_metrics['sa_used'][0]}"
                                        
                                        success_msg = (f"Upload completed for {uploader_remote}: "
                                                      f"{cumulative_metrics['transfer_count']} files "
                                                      f"({format_bytes(cumulative_metrics['total_bytes'])}) transferred "
                                                      f"in {format_duration(total_duration)} "
                                                      f"at avg {format_bytes(avg_speed)}/s"
                                                      f"{sa_info}")
                                        notify.send(message=success_msg)
                                    else:
                                        # No files transferred - show cache info
                                        mode_info = "Weekend - full scan completed" if cumulative_metrics['is_weekend'] else f"{cumulative_metrics['cached_files_excluded']} files already cached"
                                        notify.send(message=f"Upload completed for {uploader_remote}: no new files to transfer ({mode_info})")
                                else:
                                    log.info(f"Upload not completed successfully for uploader: {uploader_remote}")
                                    # send unsuccessful upload notification with partial stats if any
                                    from utils.uploader import format_bytes, format_duration
                                    
                                    if cumulative_metrics['transfer_count'] > 0:
                                        total_duration = time.time() - cumulative_metrics['start_time']
                                        fail_msg = (f"Upload was not completed successfully for remote: {uploader_remote}. "
                                                   f"Partial: {cumulative_metrics['transfer_count']} files "
                                                   f"({format_bytes(cumulative_metrics['total_bytes'])}) transferred "
                                                   f"before failure after {format_duration(total_duration)}")
                                        notify.send(message=fail_msg)
                                    else:
                                        notify.send(message=f"Upload was not completed successfully for remote: {uploader_remote} (no files transferred)")

                                # Remove ban for service account
                                sa_delay[uploader_remote][sa_file] = None
                                break
                else:
                    # No service accounts - single upload run
                    # Send enhanced start notification with cache info
                    mode_str = "Weekend - full transfer" if cumulative_metrics['is_weekend'] else "Weekday - incremental transfer"
                    notify.send(message=f"Upload starting for {uploader_remote} ({mode_str})")
                    
                    # Upload returns a dict now
                    resp = uploader.upload()
                    resp_delay = resp['delayed_check']
                    resp_trigger = resp['delayed_trigger']
                    resp_success = resp['success']
                    transfer_count = resp['transfer_count']
                    
                    # Update cumulative metrics
                    cumulative_metrics['transfer_count'] = transfer_count
                    cumulative_metrics['total_bytes'] = resp['total_bytes']
                    cumulative_metrics['duration_seconds'] = resp['duration_seconds']
                    cumulative_metrics['cached_files_excluded'] = resp['cached_files_excluded']
                    
                    if resp_delay:
                        if uploader_remote not in uploader_delay:
                            # this uploader was not already in the delay dict, so lets put it there
                            log.info(f"Upload aborted due to trigger: {resp_trigger} being met, {uploader_remote} will continue automatic uploading normally in {resp_delay} hours")
                            # add remote to uploader_delay
                            uploader_delay[uploader_remote] = time.time() + 60 ** 2 * resp_delay
                            # send aborted upload notification with metrics
                            from utils.uploader import format_bytes, format_duration
                            total_duration = time.time() - cumulative_metrics['start_time']
                            avg_speed = cumulative_metrics['total_bytes'] / total_duration if total_duration > 0 else 0
                            
                            if cumulative_metrics['transfer_count'] > 0:
                                abort_msg = (f"Upload was aborted for remote: {uploader_remote} due to trigger {resp_trigger}. "
                                            f"Partial upload: {cumulative_metrics['transfer_count']} files "
                                            f"({format_bytes(cumulative_metrics['total_bytes'])}) transferred "
                                            f"in {format_duration(total_duration)} "
                                            f"at avg {format_bytes(avg_speed)}/s. "
                                            f"Uploads suspended for {resp_delay} hours")
                            else:
                                abort_msg = f"Upload was aborted for remote: {uploader_remote} due to trigger {resp_trigger}. Uploads suspended for {resp_delay} hours"
                            notify.send(message=abort_msg)
                        else:
                            # this uploader is already in the delay dict, lets not delay it any further
                            log.info(f"Upload aborted due to trigger: {resp_trigger} being met for {uploader_remote} uploader")
                            # send aborted upload notification
                            from utils.uploader import format_bytes, format_duration
                            
                            if cumulative_metrics['transfer_count'] > 0:
                                total_duration = time.time() - cumulative_metrics['start_time']
                                avg_speed = cumulative_metrics['total_bytes'] / total_duration if total_duration > 0 else 0
                                abort_msg = (f"Upload was aborted for remote: {uploader_remote} due to trigger {resp_trigger}. "
                                            f"Partial upload: {cumulative_metrics['transfer_count']} files "
                                            f"({format_bytes(cumulative_metrics['total_bytes'])}) transferred "
                                            f"in {format_duration(total_duration)} "
                                            f"at avg {format_bytes(avg_speed)}/s")
                            else:
                                abort_msg = f"Upload was aborted for remote: {uploader_remote} due to trigger {resp_trigger}."
                            notify.send(message=abort_msg)
                    else:
                        if resp_success:
                            log.info(f"Upload completed successfully for uploader: {uploader_remote}")
                            # send successful upload notification with metrics
                            from utils.uploader import format_bytes, format_duration
                            
                            if cumulative_metrics['transfer_count'] > 0:
                                total_duration = time.time() - cumulative_metrics['start_time']
                                avg_speed = cumulative_metrics['total_bytes'] / total_duration if total_duration > 0 else 0
                                
                                success_msg = (f"Upload completed for {uploader_remote}: "
                                              f"{cumulative_metrics['transfer_count']} files "
                                              f"({format_bytes(cumulative_metrics['total_bytes'])}) transferred "
                                              f"in {format_duration(total_duration)} "
                                              f"at avg {format_bytes(avg_speed)}/s")
                                notify.send(message=success_msg)
                            else:
                                # No files transferred - show cache info
                                mode_info = "Weekend - full scan completed" if cumulative_metrics['is_weekend'] else f"{cumulative_metrics['cached_files_excluded']} files already cached"
                                notify.send(message=f"Upload completed for {uploader_remote}: no new files to transfer ({mode_info})")
                        else:
                            log.info(f"Upload not completed successfully for uploader: {uploader_remote}")
                            # send unsuccessful upload notification with partial stats if any
                            from utils.uploader import format_bytes, format_duration
                            
                            if cumulative_metrics['transfer_count'] > 0:
                                total_duration = time.time() - cumulative_metrics['start_time']
                                fail_msg = (f"Upload was not completed successfully for remote: {uploader_remote}. "
                                           f"Partial: {cumulative_metrics['transfer_count']} files "
                                           f"({format_bytes(cumulative_metrics['total_bytes'])}) transferred "
                                           f"before failure after {format_duration(total_duration)}")
                                notify.send(message=fail_msg)
                            else:
                                notify.send(message=f"Upload was not completed successfully for remote: {uploader_remote} (no files transferred)")

                        # remove uploader from uploader_delays (as its no longer banned)
                        if uploader_remote in uploader_delay and uploader_delay.pop(uploader_remote, None) is not None:
                            # this uploader was in the delay dict, but upload was successful, lets remove it
                            log.info(f"{uploader_remote} is no longer suspended due to a previous aborted upload!")
                        
                        # Cleanup chunk files if chunking was used
                        if use_chunking and chunks:
                            from utils.chunker import FileChunker
                            FileChunker.cleanup_chunk_files(chunks)
                            if list_file and os.path.exists(list_file):
                                os.remove(list_file)
                                log.info("Cleaned up chunked upload temporary files")
                        
                        # End dashboard session after all SAs complete
                        session_tracker.end_session()

                # remove leftover empty directories from disk
                if not conf.configs['core']['dry_run']:
                    uploader.remove_empty_dirs()

                # resume the nzbget queue, if enabled
                if conf.configs['nzbget']['enabled'] and nzbget is not None and nzbget_paused:
                    if nzbget.resume_queue():
                        nzbget_paused = False
                        log.info("Resumed the Nzbget download queue!")
                        notify.send(message="Resumed the Nzbget download queue!")
                    else:
                        log.error("Failed to resume the Nzbget download queue??")
                        notify.send(message="Failed to resume the Nzbget download queue??")
                # resume the Sabnzbd queue, if enabled
                if conf.configs['sabnzbd']['enabled'] and sabnzbd is not None and sabnzbd_paused:
                    if sabnzbd.resume_queue():
                        sabnzbd_paused = False
                        log.info("Resumed the Sabnzbd download queue!")
                        notify.send(message="Resumed the Sabnzbd download queue!")
                    else:
                        log.error("Failed to resume the Sabnzbd download queue??")
                        notify.send(message="Failed to resume the Sabnzbd download queue??")

                # move from staging remote to main ?
                if 'mover' in uploader_config and 'enabled' in uploader_config['mover']:
                    if not uploader_config['mover']['enabled']:
                        # if not enabled, continue the uploader loop
                        continue

                    # validate we have the bare minimum config settings set
                    required_configs = ['move_from_remote', 'move_to_remote', 'rclone_extras']
                    required_set = True
                    for setting in required_configs:
                        if setting not in uploader_config['mover']:
                            log.error(f"Unable to act on '{uploader_remote}' mover because there was no '{setting}' setting in the mover configuration")
                            required_set = False
                            break

                    # do move if good
                    if required_set:
                        mover = RcloneMover(uploader_config['mover'],
                                            conf.configs['core']['rclone_binary_path'],
                                            conf.configs['core']['rclone_config_path'],
                                            conf.configs['plex'],
                                            conf.configs['core']['dry_run'])
                        log.info(f"Move starting from {uploader_config['mover']['move_from_remote']} -> {uploader_config['mover']['move_to_remote']}")

                        # send notification that mover has started
                        notify.send(message=f"Move has started for {uploader_config['mover']['move_from_remote']} -> {uploader_config['mover']['move_to_remote']}")

                        if mover.move():
                            log.info(f"Move completed successfully from {uploader_config['mover']['move_from_remote']} -> {uploader_config['mover']['move_to_remote']}")
                            # send notification move has finished
                            notify.send(message=f"Move finished successfully for {uploader_config['mover']['move_from_remote']} -> {uploader_config['mover']['move_to_remote']}")

                        else:
                            log.error(f"Move failed from {uploader_config['mover']['move_from_remote']} -> {uploader_config['mover']['move_to_remote']} ....?")
                            # send notification move has failed
                            notify.send(message=f"Move failed for {uploader_config['mover']['move_from_remote']} -> {uploader_config['mover']['move_to_remote']}")

        except Exception:
            log.exception("Exception occurred while uploading: ")
            notify.send(message="Exception occurred while uploading: ")
            # End dashboard session on exception
            try:
                session_tracker.end_session()
            except:
                pass

    log.info("Finished upload")


@decorators.timed
def do_sync(use_syncer=None):
    global syncer_delay

    lock_file = lock.sync()
    if lock_file.is_locked():
        log.info("Waiting for running sync to finish before proceeding...")

    with lock_file:
        log.info("Starting sync")
        try:
            for sync_name, sync_config in conf.configs['syncer'].items():
                # if syncer is not None, skip this syncer if not == syncer
                if use_syncer and sync_name != use_syncer:
                    continue

                # send notification that sync is starting
                if sync_config['service'].lower() != 'local':
                    notify.send(message=f"Sync initiated for syncer: {sync_name}. {'Creating' if sync_config['instance_destroy'] else 'Starting'} {sync_config['service']} instance...")

                # startup instance
                resp, instance_id = syncer.startup(service=sync_config['service'], name=sync_name)
                if not resp:
                    # send notification of failure to startup instance
                    notify.send(message=f'Syncer: {sync_name} failed to startup a {"new" if sync_config["instance_destroy"] else "existing"} instance. Manually check no instances are still running!')
                    continue

                # setup instance
                resp = syncer.setup(service=sync_config['service'], instance_id=instance_id,
                                    rclone_config=conf.configs['core']['rclone_config_path'])
                if not resp:
                    # send notification of failure to set up instance
                    notify.send(message=f'Syncer: {sync_name} failed to setup a {"new" if sync_config["instance_destroy"] else "existing"} instance. Manually check no instances are still running!')
                    continue

                # send notification of sync start
                notify.send(message=f'Sync has begun for syncer: {sync_name}')

                # do sync
                resp, resp_delay, resp_trigger = syncer.sync(service=sync_config['service'], instance_id=instance_id,
                                                             dry_run=conf.configs['core']['dry_run'],
                                                             rclone_config=conf.configs['core']['rclone_config_path'])

                if not resp and not resp_delay:
                    log.error("Sync unexpectedly failed for syncer: %s", sync_name)
                    # send unexpected sync fail notification
                    notify.send(message=f'Sync failed unexpectedly for syncer: {sync_name}. Manually check no instances are still running!')

                elif not resp and resp_trigger:
                    # non 0 resp_delay result indicates a trigger was met, the result is how many hours to sleep
                    if sync_name not in syncer_delay:
                        # this syncer was not in the syncer delay dict, so lets put it there
                        log.info(f"Sync aborted due to trigger: {resp_trigger} being met, {sync_name} will continue automatic syncing normally in {resp_delay} hours")
                        # add syncer to syncer_delay
                        syncer_delay[sync_name] = time.time() + 60 ** 2 * resp_delay
                        # send aborted sync notification
                        notify.send(message=f"Sync was aborted for syncer: {sync_name} due to trigger {resp_trigger}. Syncs suspended for {resp_delay} hours")
                    else:
                        # this syncer was already in the syncer delay dict, so lets not delay it any further
                        log.info(f"Sync aborted due to trigger: {resp_trigger} being met for {sync_name} syncer")
                        # send aborted sync notification
                        notify.send(message=f"Sync was aborted for syncer: {sync_name} due to trigger {resp_trigger}.")
                else:
                    log.info(f"Syncing completed successfully for syncer: {sync_name}")
                    # send successful sync notification
                    notify.send(message=f"Sync was completed successfully for syncer: {sync_name}")
                    # remove syncer from syncer_delay(as its no longer banned)
                    if sync_name in syncer_delay and syncer_delay.pop(sync_name, None) is not None:
                        # this syncer was in the delay dict, but sync was successful, lets remove it
                        log.info(f"{sync_name} is no longer suspended due to a previous aborted sync!")

                # destroy instance
                resp = syncer.destroy(service=sync_config['service'], instance_id=instance_id)
                if not resp and sync_config['service'].lower() != 'local':
                    # send notification of failure to destroy/stop instance
                    notify.send(message=f"Syncer: {sync_name} failed to {'destroy' if sync_config['instance_destroy'] else 'stop'} its instance: {instance_id}. Manually check no instances are still running!")
                elif sync_config['service'].lower() != 'local':
                    notify.send(
                        message=f"Syncer: {sync_name} has {'destroyed' if sync_config['instance_destroy'] else 'stopped'} its {sync_config['service']} instance")

        except Exception:
            log.exception("Exception occurred while syncing: ")

    log.info("Finished sync")


@decorators.timed
def do_hidden():
    lock_file = lock.hidden()
    if lock_file.is_locked():
        log.info("Waiting for running hidden cleaner to finish before proceeding...")

    with lock_file:
        log.info("Starting hidden cleaning")
        try:
            # loop each supplied hidden folder
            for hidden_folder, hidden_config in conf.configs['hidden'].items():
                hidden = UnionfsHiddenFolder(hidden_folder, conf.configs['core']['dry_run'],
                                             conf.configs['core']['rclone_binary_path'],
                                             conf.configs['core']['rclone_config_path'])

                # loop the chosen remotes for this hidden config cleaning files
                for hidden_remote_name in hidden_config['hidden_remotes']:
                    # retrieve rclone config for this remote
                    hidden_remote_config = conf.configs['remotes'][hidden_remote_name]

                    # clean remote
                    clean_resp, deleted_ok, deleted_fail = hidden.clean_remote(hidden_remote_name, hidden_remote_config)

                    # send notification
                    if deleted_ok or deleted_fail:
                        notify.send(message=f"Cleaned {deleted_ok} hidden(s) with {deleted_fail} failure(s) from remote: {hidden_remote_name}")

                # remove the HIDDEN~ files from disk and empty directories from unionfs-fuse folder
                if not conf.configs['core']['dry_run']:
                    hidden.remove_local_hidden()
                    hidden.remove_empty_dirs()

        except Exception:
            log.exception("Exception occurred while cleaning hiddens: ")

    log.info("Finished hidden cleaning")


@decorators.timed
def do_plex_monitor():
    global plex_monitor_thread

    # create the plex object
    plex = Plex(conf.configs['plex']['url'], conf.configs['plex']['token'])
    if not plex.validate():
        log.error("Aborting Plex Media Server stream monitor due to failure to validate supplied server URL and/or Token.")
        plex_monitor_thread = None
        return

    # sleep 15 seconds to allow rclone to start
    log.info("Plex Media Server URL + Token were validated. Sleeping for 15 seconds before checking Rclone RC URL.")
    time.sleep(15)

    # create the rclone throttle object
    rclone = RcloneThrottler(conf.configs['plex']['rclone']['url'])
    if not rclone.validate():
        log.error("Aborting Plex Media Server stream monitor due to failure to validate supplied Rclone RC URL.")
        plex_monitor_thread = None
        return
    else:
        log.info("Rclone RC URL was validated. Stream monitoring for Plex Media Server will now begin.")

    throttled = False
    throttle_speed = None
    lock_file = lock.upload()
    while lock_file.is_locked():
        streams = plex.get_streams()
        if streams is None:
            log.error(f"Failed to check Plex Media Server stream(s). Trying again in {conf.configs['plex']['poll_interval']} seconds...")
        else:
            # we had a response
            stream_count = sum(
                stream.state in ['playing', 'buffering'] and not stream.local
                for stream in streams
            )
            local_stream_count = sum(
                stream.state in ['playing', 'buffering'] and stream.local
                for stream in streams
            )

            # if we are accounting for local streams, add them to the stream count
            if not conf.configs['plex']['ignore_local_streams']:
                stream_count += local_stream_count

            # are we already throttled?
            if ((not throttled or (throttled and not rclone.throttle_active(throttle_speed))) and (
                    stream_count >= conf.configs['plex']['max_streams_before_throttle'])):
                log.info(f"There was {stream_count} playing stream(s) on Plex Media Server while it was currently un-throttled.")
                for stream in streams:
                    log.info(stream)
                log.info("Upload throttling will now commence.")

                # send throttle request
                throttle_speed = misc.get_nearest_less_element(conf.configs['plex']['rclone']['throttle_speeds'],
                                                               stream_count)
                throttled = rclone.throttle(throttle_speed)

                # send notification
                if throttled and conf.configs['plex']['notifications']:
                    notify.send(message=f"Throttled current upload to {throttle_speed} because there was {stream_count} playing stream(s) on Plex")

            elif throttled:
                if stream_count < conf.configs['plex']['max_streams_before_throttle']:
                    log.info(f"There was less than {conf.configs['plex']['max_streams_before_throttle']} playing stream(s) on Plex Media Server while it was currently throttled. Removing throttle ...")
                    # send un-throttle request
                    throttled = not rclone.no_throttle()
                    throttle_speed = None

                    # send notification
                    if not throttled and conf.configs['plex']['notifications']:
                        notify.send(message=f"Un-throttled current upload because there was less than {conf.configs['plex']['max_streams_before_throttle']} playing stream(s) on Plex Media Server")

                elif misc.get_nearest_less_element(conf.configs['plex']['rclone']['throttle_speeds'],
                                                   stream_count) != throttle_speed:
                    # throttle speed changed, probably due to more/fewer streams, re-throttle
                    throttle_speed = misc.get_nearest_less_element(conf.configs['plex']['rclone']['throttle_speeds'],
                                                                   stream_count)
                    log.info(f"Adjusting throttle speed for current upload to {throttle_speed} because there was now {stream_count} playing stream(s) on Plex Media Server")

                    throttled = rclone.throttle(throttle_speed)

                    # send notification
                    if throttled and conf.configs['plex']['notifications']:
                        notify.send(message=f'Throttle for current upload was adjusted to {throttle_speed} due to {stream_count} playing stream(s) on Plex Media Server')

                else:
                    log.info(f"There was {stream_count} playing stream(s) on Plex Media Server it was already throttled to {throttle_speed}. Throttling will continue.")

        # the lock_file exists, so we can assume an upload is in progress at this point
        time.sleep(conf.configs['plex']['poll_interval'])

    log.info("Finished monitoring Plex stream(s)!")
    plex_monitor_thread = None


############################################################
# SCHEDULED FUNCS
############################################################

def scheduled_uploader(uploader_name, uploader_settings):
    log.debug(f"Scheduled disk check triggered for uploader: {uploader_name}")
    try:
        rclone_settings = conf.configs['remotes'][uploader_name]

        # check suspended uploaders
        if check_suspended_uploaders(uploader_name):
            return

        # clear any banned service accounts
        check_suspended_sa(uploader_name)

        # check used disk space
        used_space = path.get_size(rclone_settings['upload_folder'], uploader_settings['size_excludes'])

        # if disk space is above the limit, clean hidden files then upload
        if used_space >= uploader_settings['max_size_gb']:
            log.info(f"Uploader: {uploader_name}. Local folder size is currently {used_space - uploader_settings['max_size_gb']} GB over the maximum limit of {uploader_settings['max_size_gb']} GB")

            # does this uploader have schedule settings
            if 'schedule' in uploader_settings and uploader_settings['schedule']['enabled']:
                # there is a schedule set for this uploader, check if we are within the allowed times
                current_time = time.strftime('%H:%M')
                if not misc.is_time_between((uploader_settings['schedule']['allowed_from'],
                                             uploader_settings['schedule']['allowed_until'])):
                    log.info(f"Uploader: {uploader_name}. The current time {current_time} is not within the allowed upload time periods {uploader_settings['schedule']['allowed_from']} -> {uploader_settings['schedule']['allowed_until']}")
                    return

            # clean hidden files
            do_hidden()
            # upload
            do_upload(uploader_name)

        else:
            log.info(f"Uploader: {uploader_name}. Local folder size is currently {used_space} GB. Still have {uploader_settings['max_size_gb'] - used_space} GB remaining before its eligible to begin uploading...")

    except Exception:
        log.exception(f"Unexpected exception occurred while processing uploader {uploader_name}: ")


def scheduled_syncer(syncer_name):
    log.info(f"Scheduled sync triggered for syncer: {syncer_name}")
    try:
        # check suspended syncers
        if check_suspended_syncers(syncer_name):
            return

        # do sync
        do_sync(syncer_name)

    except Exception:
        log.exception(f"Unexpected exception occurred while processing syncer: {syncer_name}")


############################################################
# MAIN
############################################################


if __name__ == "__main__":
    # show the latest version info from git
    version.check_version()

    # cleanup orphaned temp files from previous runs
    cleanup_temp_exclude_files()

    # run chosen mode
    try:

        if conf.args['cmd'] == 'clean':
            log.info("Started in clean mode")
            # init notifications
            init_notifications()
            do_hidden()
        elif conf.args['cmd'] == 'upload':
            log.info("Started in upload mode")
            # init notifications
            init_notifications()
            # initialize service accounts if provided in config
            init_service_accounts()
            # initialize SA quota tracking
            init_sa_quota_tracking()
            do_hidden()
            do_upload()
        elif conf.args['cmd'] == 'sync':
            log.info("Starting in sync mode")
            log.warning("Sync currently has a bug while displaying output to the console. Tail the logfile to view readable logs!")
            # init notifications
            init_notifications()
            init_syncers()
            do_sync()
        elif conf.args['cmd'] == 'run':
            log.info("Started in run mode")

            # init notifications
            init_notifications()
            # initialize service accounts if provided in confing
            init_service_accounts()
            # initialize SA quota tracking
            init_sa_quota_tracking()

            # add uploaders to schedule
            for uploader, uploader_conf in conf.configs['uploader'].items():
                schedule.every(uploader_conf['check_interval']).minutes.do(scheduled_uploader, uploader, uploader_conf)
                log.info(f"Added {uploader} uploader to schedule, checking available disk space every {uploader_conf['check_interval']} minutes")

            # add syncers to schedule
            init_syncers()
            for syncer_name, syncer_conf in conf.configs['syncer'].items():
                if syncer_conf['service'].lower() == 'local':
                    schedule.every(syncer_conf['sync_interval']).hours.do(scheduled_syncer, syncer_name=syncer_name)
                else:
                    schedule.every(syncer_conf['sync_interval']).hours.do(run_process, scheduled_syncer,
                                                                          syncer_name=syncer_name)
                log.info(f"Added {syncer_name} syncer to schedule, syncing every {syncer_conf['sync_interval']} hours")

            # run schedule
            while True:
                try:
                    schedule.run_pending()
                except Exception:
                    log.exception("Unhandled exception occurred while processing scheduled tasks: ")
                time.sleep(1)
        elif conf.args['cmd'] == 'update_config':
            exit(0)
        else:
            log.error("Unknown command: %r", conf.args['cmd'])

    except KeyboardInterrupt:
        log.info("cloudplow was interrupted by Ctrl + C")
    except Exception:
        log.exception("Unexpected fatal exception occurred: ")
