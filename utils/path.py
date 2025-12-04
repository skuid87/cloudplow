import hashlib
import os
import json
from pathlib import Path

from . import process

try:
    from shlex import quote as cmd_quote
except ImportError:
    from pipes import quote as cmd_quote

import logging

log = logging.getLogger('path')


def is_rclone_remote(path):
    """Check if a path is an rclone remote (contains ':' and not a Windows drive path)"""
    if not isinstance(path, str):
        return False
    # Check if it contains ':' but is not a Windows path (e.g., C:\)
    if ':' in path:
        # Windows paths have : after a single letter (e.g., C:)
        if len(path) > 2 and path[1] == ':' and path[0].isalpha() and (path[2] == '\\' or path[2] == '/'):
            return False
        return True
    return False


def get_file_extension(filepath):
    extensions = Path(filepath).suffixes
    extension = ''.join(extensions).lstrip('.')
    return extension.lower()


def get_file_hash(filepath):
    # get file size for hash
    file_size = 0
    try:
        file_size = os.path.getsize(filepath)
    except Exception:
        log.exception(f"Exception getting file size of {filepath}: ")
    # set basic string to use for hash
    key = "{filename}-{size}".format(filename=os.path.basename(filepath), size=file_size)
    return hashlib.md5(key.encode('utf-8')).hexdigest()


def find_items(folder, extension=None, depth=None):
    folder_list = []
    start_count = folder.count(os.sep)
    for path, subdirs, files in os.walk(folder, topdown=True):
        for name in subdirs:
            if depth and path.count(os.sep) - start_count >= depth:
                del subdirs[:]
                continue
            filepath = os.path.join(path, name)
            if not extension:
                folder_list.append(filepath)
            elif filepath.lower().endswith(extension.lower()):
                folder_list.append(filepath)
    return sorted(folder_list, key=lambda x: x.count(os.path.sep), reverse=True)


def opened_files(path):
    files = []

    # Skip for rclone remotes - lsof only works on local paths
    if is_rclone_remote(path):
        log.debug(f"Skipping open files check for rclone remote: {path}")
        return []

    try:
        process = os.popen(f'lsof -wFn +D {cmd_quote(path)} | tail -n +2 | cut -c2-')
        data = process.read()
        files.extend(item for item in data.split('\n') if item and len(item) > 3 and not item.isdigit() and os.path.isfile(item))

        return files

    except Exception:
        log.exception(f"Exception retrieving open files from {path}: ")
    return []


def delete(path):
    if isinstance(path, list):
        for item in path:
            if os.path.exists(item):
                log.debug("Removing %r", item)
                try:
                    if not os.path.isdir(item):
                        os.remove(item)
                    else:
                        os.rmdir(item)
                except Exception:
                    log.exception("Exception deleting '%s': ", item)
            else:
                log.debug("Skipping deletion of '%s' as it does not exist", item)
    elif os.path.exists(path):
        log.debug("Removing %r", path)
        try:
            if not os.path.isdir(path):
                os.remove(path)
            else:
                os.rmdir(path)
        except Exception:
            log.exception("Exception deleting '%s': ", path)
    else:
        log.debug("Skipping deletion of '%s' as it does not exist", path)


def remove_empty_dirs(path, depth):
    # Skip for rclone remotes - use 'rclone rmdirs' instead
    if is_rclone_remote(path):
        log.info(f"Skipping empty directory removal for rclone remote: {path} (use 'rclone rmdirs' manually if needed)")
        return True

    if os.path.exists(path):
        log.debug("Removing empty directories from '%s' with mindepth %d", path, depth)
        cmd = 'find %s -mindepth %d -type d -empty -delete' % (cmd_quote(path), depth)
        try:
            log.debug("Using: %s", cmd)
            process.execute(cmd, logs=False)
            return True
        except Exception:
            log.exception("Exception while removing empty directories from '%s': ", path)
            return False
    else:
        log.error("Cannot remove empty directories from '%s' as it does not exist", path)
    return False


def get_size(path, excludes=None):
    try:
        # Handle rclone remotes
        if is_rclone_remote(path):
            log.debug(f"Using rclone to get size of remote: {path}")
            cmd = f"rclone size {cmd_quote(path)} --json"
            
            # Add excludes if provided
            if excludes:
                for item in excludes:
                    cmd += f' --exclude {cmd_quote(item)}'
            
            log.debug("Using: %s", cmd)
            proc = os.popen(cmd)
            data = proc.read().strip()
            proc.close()
            
            if data:
                try:
                    size_data = json.loads(data)
                    # rclone size returns bytes, convert to GB
                    bytes_size = size_data.get('bytes', 0)
                    gb_size = bytes_size / (1024 ** 3)
                    log.debug(f"Remote size: {gb_size:.2f} GB ({bytes_size} bytes)")
                    return int(gb_size)
                except json.JSONDecodeError:
                    log.error(f"Failed to parse rclone size output: {data}")
                    return 0
            return 0
        
        # Handle local paths
        cmd = "du -s --block-size=1G"
        if excludes:
            for item in excludes:
                cmd += f' --exclude={cmd_quote(item)}'
        cmd += f' {cmd_quote(path)} | cut -f1'
        log.debug("Using: %s", cmd)
        # get size
        proc = os.popen(cmd)
        data = proc.read().strip("\n")
        proc.close()
        return int(data) if data.isdigit() else 0
    except Exception:
        log.exception("Exception getting size of %r: ", path)
    return 0
