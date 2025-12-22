#!/usr/bin/env python3
"""
File Chunker for Cloudplow
Handles generating and chunking file lists for batched uploads to avoid
rclone checking hundreds of thousands of files on each transfer.
"""
import logging
import subprocess
import os
import tempfile

try:
    from shlex import quote as cmd_quote
except ImportError:
    from pipes import quote as cmd_quote

log = logging.getLogger('chunker')


class FileChunker:
    """Handles generating and chunking file lists for batched uploads"""
    
    def __init__(self, rclone_binary_path, rclone_config_path, source_path, rclone_excludes=None, timeout=600):
        """
        Initialize FileChunker
        
        Args:
            rclone_binary_path: Path to rclone binary
            rclone_config_path: Path to rclone config file
            source_path: Local path to scan for files
            rclone_excludes: List of exclude patterns
            timeout: Timeout in seconds for file list generation
        """
        self.rclone_binary_path = rclone_binary_path
        self.rclone_config_path = rclone_config_path
        self.source_path = source_path
        self.rclone_excludes = rclone_excludes or []
        self.timeout = timeout
        
    def generate_file_list(self):
        """
        Generate a complete file list using rclone lsf (fast, no checking)
        
        Returns:
            Tuple of (list_file_path, file_count) or None on error
        """
        try:
            log.info(f"Generating file list for {self.source_path}...")
            
            # Create temp file for the list
            fd, list_file = tempfile.mkstemp(prefix='cloudplow_filelist_', suffix='.txt')
            os.close(fd)
            
            # Build rclone lsf command (much faster than ls, no checksums/sizes)
            cmd = [
                self.rclone_binary_path,
                'lsf',
                self.source_path,
                '--recursive',
                '--files-only',  # Skip directories
                f'--config={self.rclone_config_path}'
            ]
            
            # Add excludes
            for exclude in self.rclone_excludes:
                cmd.append(f'--exclude={exclude}')
            
            log.debug(f"Running: {' '.join(cmd)}")
            
            # Run and capture output to file
            with open(list_file, 'w') as f:
                result = subprocess.run(
                    cmd,
                    stdout=f,
                    stderr=subprocess.PIPE,
                    timeout=self.timeout,
                    text=True
                )
            
            if result.returncode != 0:
                log.error(f"Failed to generate file list: {result.stderr}")
                os.remove(list_file)
                return None
            
            # Count files
            with open(list_file, 'r') as f:
                file_count = sum(1 for _ in f)
            
            if file_count == 0:
                log.warning("No files found to upload")
                os.remove(list_file)
                return None
            
            log.info(f"Generated list of {file_count:,} files")
            return list_file, file_count
            
        except subprocess.TimeoutExpired:
            log.error(f"File list generation timed out after {self.timeout}s")
            if os.path.exists(list_file):
                os.remove(list_file)
            return None
        except Exception as e:
            log.exception(f"Error generating file list: {e}")
            return None
    
    def create_chunks(self, list_file, chunk_size=1000):
        """
        Split a file list into chunks
        
        Args:
            list_file: Path to file containing list of files
            chunk_size: Number of files per chunk
            
        Returns:
            List of (chunk_file_path, file_count) tuples
        """
        try:
            chunks = []
            current_chunk = []
            chunk_num = 1
            
            with open(list_file, 'r') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    
                    current_chunk.append(line)
                    
                    if len(current_chunk) >= chunk_size:
                        # Write chunk to file
                        chunk_file = self._write_chunk(current_chunk, chunk_num)
                        chunks.append((chunk_file, len(current_chunk)))
                        log.debug(f"Created chunk {chunk_num} with {len(current_chunk)} files")
                        
                        current_chunk = []
                        chunk_num += 1
            
            # Write remaining files
            if current_chunk:
                chunk_file = self._write_chunk(current_chunk, chunk_num)
                chunks.append((chunk_file, len(current_chunk)))
                log.debug(f"Created final chunk {chunk_num} with {len(current_chunk)} files")
            
            log.info(f"Split into {len(chunks)} chunks of ~{chunk_size} files each")
            return chunks
            
        except Exception as e:
            log.exception(f"Error creating chunks: {e}")
            return []
    
    def _write_chunk(self, files, chunk_num):
        """
        Write a chunk of files to a temp file
        
        Args:
            files: List of file paths
            chunk_num: Chunk number for naming
            
        Returns:
            Path to chunk file
        """
        fd, chunk_file = tempfile.mkstemp(
            prefix=f'cloudplow_chunk_{chunk_num}_',
            suffix='.txt'
        )
        
        with os.fdopen(fd, 'w') as f:
            f.write('\n'.join(files))
        
        return chunk_file
    
    @staticmethod
    def cleanup_chunk_files(chunk_files):
        """
        Clean up temporary chunk files
        
        Args:
            chunk_files: List of (chunk_file_path, count) tuples
        """
        for chunk_file, _ in chunk_files:
            try:
                if os.path.exists(chunk_file):
                    os.remove(chunk_file)
                    log.debug(f"Cleaned up chunk file: {chunk_file}")
            except Exception as e:
                log.warning(f"Failed to clean up {chunk_file}: {e}")

