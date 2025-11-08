# ftp_adapter.py

import ftplib
from pathlib import Path
import os
import io
import time
import logging
import threading
from typing import Optional

logger = logging.getLogger(__name__)

class FtpAdapter:
    """FTP adapter that provides Path-like operations with local caching"""
    
    def __init__(self, host, username, password, cache_dir="/tmp/ftp_cache", max_cache_size=20*1024*1024*1024):
        self.host = host
        self.username = username
        self.password = password
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.max_cache_size = max_cache_size  # 20GB default
        self._connection_pool = []
        self._max_connections = 5
        self._pool_lock = threading.Lock()
        self._last_cache_check = time.time()
        
        logger.info(f"FTP Adapter initialized with cache at {cache_dir}")
        
        # Test the connection
        try:
            conn = self._get_connection()
            conn.voidcmd("NOOP")
            self._release_connection(conn)
            logger.info(f"Successfully connected to FTP server {host}")
        except Exception as e:
            logger.error(f"Failed to connect to FTP server: {e}")
            raise
        
    def _get_connection(self):
        """Get an FTP connection from the pool or create a new one"""
        with self._pool_lock:
            if self._connection_pool:
                try:
                    conn = self._connection_pool.pop()
                    # Test if connection is still alive
                    conn.voidcmd("NOOP")
                    return conn
                except:
                    # Connection is dead, create a new one
                    pass
                    
            conn = ftplib.FTP(self.host)
            conn.login(self.username, self.password)
            return conn
        
    def _release_connection(self, conn):
        """Return connection to pool or close it if pool is full"""
        with self._pool_lock:
            try:
                if len(self._connection_pool) < self._max_connections:
                    self._connection_pool.append(conn)
                else:
                    conn.quit()
            except:
                pass
    
    def _cache_path(self, remote_path):
        """Convert a remote path to a local cache path"""
        # Ensure the path is a string and normalize it
        path_str = str(remote_path).lstrip('/')
        return self.cache_dir / path_str
    
    def exists(self, path):
        """Check if path exists on FTP server"""
        path_str = str(path)
        
        # First check cache
        cache_path = self._cache_path(path_str)
        if cache_path.exists():
            return True
            
        # Then check remote
        conn = self._get_connection()
        try:
            try:
                # Try as a file first
                conn.size(path_str)
                return True
            except ftplib.error_perm:
                try:
                    # Then try as a directory
                    current = conn.pwd()
                    conn.cwd(path_str)
                    conn.cwd(current)  # Change back to previous directory
                    return True
                except ftplib.error_perm:
                    return False
        finally:
            self._release_connection(conn)
    
    def open(self, path, mode="rb"):
        """Open a file-like object for the given path"""
        path_str = str(path)
        cache_path = self._cache_path(path_str)
        
        # Ensure the parent directory exists
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        
        if "r" in mode:  # Reading mode
            # Check if cached version exists and is fresh (< 5 min old)
            if cache_path.exists() and (time.time() - cache_path.stat().st_mtime < 300):
                logger.debug(f"Using cached file: {cache_path}")
                return open(cache_path, mode)
                
            # Download from FTP
            conn = self._get_connection()
            try:
                logger.debug(f"Downloading from FTP: {path_str} to {cache_path}")
                
                # Create the directory if it doesn't exist
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                
                with open(cache_path, "wb") as f:
                    try:
                        conn.retrbinary(f'RETR {path_str}', f.write)
                    except ftplib.error_perm as e:
                        logger.error(f"FTP error retrieving {path_str}: {e}")
                        if cache_path.exists():
                            if cache_path.stat().st_size == 0:
                                cache_path.unlink()
                            else:
                                logger.warning(f"Using existing cached file: {cache_path}")
                                return open(cache_path, mode)
                        raise
                
                # Manage cache size periodically
                self._manage_cache()
                
                logger.debug(f"Downloaded file: {cache_path}")
                return open(cache_path, mode)
            except Exception as e:
                logger.error(f"Error downloading file {path_str}: {e}")
                # If download fails but cached version exists, use it anyway
                if cache_path.exists():
                    logger.warning(f"Using cached version of {path_str} after download failure")
                    return open(cache_path, mode)
                raise
            finally:
                self._release_connection(conn)
        else:  # Writing mode
            # Return a special file wrapper that uploads on close
            return FtpFile(self, path_str, cache_path, mode)
    
    def mkdir(self, path, parents=False, exist_ok=False):
        """Create directory on FTP server"""
        path_str = str(path).lstrip('/')
        
        # Create in cache
        cache_path = self._cache_path(path_str)
        cache_path.mkdir(parents=True, exist_ok=True)
        
        # Check if it already exists
        if exist_ok:
            try:
                conn = self._get_connection()
                try:
                    current = conn.pwd()
                    conn.cwd(path_str)
                    conn.cwd(current)
                    self._release_connection(conn)
                    return  # Already exists
                except ftplib.error_perm:
                    # Directory doesn't exist, continue with creation
                    pass
                finally:
                    self._release_connection(conn)
            except Exception:
                pass
        
        # Create on FTP server
        conn = self._get_connection()
        try:
            if parents:
                # Create parent directories if needed
                parts = Path(path_str).parts
                current = ""
                for part in parts:
                    if not part:  # Skip empty parts
                        continue
                    current = f"{current}/{part}" if current else part
                    try:
                        conn.mkd(current)
                        logger.debug(f"Created FTP directory: {current}")
                    except ftplib.error_perm as e:
                        # Directory might already exist
                        if not exist_ok and "550" not in str(e):  # 550 = already exists
                            raise
            else:
                try:
                    conn.mkd(path_str)
                    logger.debug(f"Created FTP directory: {path_str}")
                except ftplib.error_perm as e:
                    if not exist_ok:
                        raise
        except Exception as e:
            logger.error(f"Error creating directory {path_str}: {e}")
            raise
        finally:
            self._release_connection(conn)
    
    def remove(self, path):
        """Remove a file from FTP server"""
        path_str = str(path).lstrip('/')
        
        # Remove from cache first
        cache_path = self._cache_path(path_str)
        if cache_path.exists():
            os.unlink(cache_path)
        
        # Remove from FTP
        conn = self._get_connection()
        try:
            conn.delete(path_str)
            logger.debug(f"Removed file: {path_str}")
        except Exception as e:
            logger.error(f"Error removing file {path_str}: {e}")
            raise
        finally:
            self._release_connection(conn)
    
    def _manage_cache(self):
        """Ensure cache doesn't exceed maximum size"""
        now = time.time()
        
        # Only check every 5 minutes
        if now - self._last_cache_check < 300:
            return
            
        self._last_cache_check = now
        
        # Get cache stats
        cache_size = sum(f.stat().st_size for f in self.cache_dir.glob('**/*') if f.is_file())
        
        # If under limit, no need to clean
        if cache_size < self.max_cache_size:
            return
            
        logger.info(f"Cache size {cache_size/1024/1024:.2f}MB exceeds limit, cleaning...")
        
        # Get files sorted by access time (oldest first)
        cache_files = [(f, f.stat().st_atime) for f in self.cache_dir.glob('**/*') if f.is_file()]
        cache_files.sort(key=lambda x: x[1])
        
        # Remove oldest files until under limit
        target_size = self.max_cache_size * 0.8  # Aim to get to 80% of max
        current_size = cache_size
        
        for file, _ in cache_files:
            if current_size <= target_size:
                break
                
            file_size = file.stat().st_size
            file.unlink()
            current_size -= file_size
            logger.debug(f"Removed {file} from cache (size: {file_size/1024/1024:.2f}MB)")
        
        logger.info(f"Cache cleaned. New size: {current_size/1024/1024:.2f}MB")


class FtpFile:
    """File-like object for writing to FTP"""
    def __init__(self, adapter, remote_path, cache_path, mode):
        self.adapter = adapter
        self.remote_path = remote_path
        self.cache_path = cache_path
        self.mode = mode
        self.file = open(cache_path, mode)
        self._closed = False
        
    def write(self, data):
        """Write data to local file"""
        return self.file.write(data)
        
    def read(self, size=-1):
        """Read from local file"""
        return self.file.read(size)
        
    def close(self):
        """Upload file to FTP server when closed"""
        if self._closed:
            return
            
        self.file.flush()
        self.file.close()
        
        # Only upload if writing
        if "w" in self.mode or "a" in self.mode or "+" in self.mode:
            conn = self.adapter._get_connection()
            try:
                # Ensure parent directory exists
                parent_dir = os.path.dirname(self.remote_path)
                if parent_dir:
                    try:
                        current = conn.pwd()
                        conn.cwd(parent_dir)
                        conn.cwd(current)  # Change back
                    except:
                        # Create parent directories if they don't exist
                        self.adapter.mkdir(parent_dir, parents=True, exist_ok=True)
                        
                # Upload the file
                with open(self.cache_path, 'rb') as f:
                    conn.storbinary(f'STOR {self.remote_path}', f)
                logger.debug(f"Uploaded file to FTP: {self.remote_path}")
            except Exception as e:
                logger.error(f"Error uploading file {self.remote_path}: {e}")
                raise
            finally:
                self.adapter._release_connection(conn)
                
        self._closed = True
        
    def __enter__(self):
        return self
        
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()