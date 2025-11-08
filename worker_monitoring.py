# worker_monitoring.py - Add to your project for debugging worker issues

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Dict, List
from pathlib import Path
import psutil
import json

logger = logging.getLogger(__name__)

class WorkerMonitor:
    """Monitor and debug worker performance issues."""
    
    def __init__(self):
        self.monitoring_enabled = True
        self.log_file = Path("/tmp/worker_monitor.log")
        self.monitor_interval = 30  # Check every 30 seconds
        self.monitor_task = None
        
    async def start_monitoring(self):
        """Start continuous monitoring of worker health."""
        if self.monitor_task:
            return
            
        self.monitor_task = asyncio.create_task(self._monitoring_loop())
        logger.info("🔍 Worker monitoring started")
        
    async def stop_monitoring(self):
        """Stop monitoring."""
        if self.monitor_task:
            self.monitor_task.cancel()
            try:
                await self.monitor_task
            except asyncio.CancelledError:
                pass
            self.monitor_task = None
        logger.info("🔍 Worker monitoring stopped")
        
    async def _monitoring_loop(self):
        """Main monitoring loop."""
        while self.monitoring_enabled:
            try:
                await self._check_system_health()
                await asyncio.sleep(self.monitor_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Monitoring error: {e}")
                await asyncio.sleep(self.monitor_interval)
                
    async def _check_system_health(self):
        """Check overall system health and log issues."""
        timestamp = datetime.now(timezone.utc).isoformat()
        
        # System metrics
        cpu_percent = psutil.cpu_percent(interval=1)
        memory = psutil.virtual_memory()
        
        # Worker config status
        from worker_config import worker_config
        system_status = worker_config.get_system_status()
        
        # Track download managers
        track_status = await self._get_track_manager_status()
        album_status = await self._get_album_manager_status()
        
        health_report = {
            'timestamp': timestamp,
            'system': {
                'cpu_percent': cpu_percent,
                'memory_percent': memory.percent,
                'memory_available_gb': memory.available / (1024**3)
            },
            'workers': system_status,
            'download_managers': {
                'track': track_status,
                'album': album_status
            }
        }
        
        # Log warnings for issues
        await self._analyze_health_report(health_report)
        
        # Write to log file for analysis
        await self._write_health_log(health_report)
        
    async def _get_track_manager_status(self):
        """Get track download manager status."""
        try:
            from track_download_workers import track_download_manager
            
            status = {
                'is_running': track_download_manager._is_running,
                'worker_count': len(track_download_manager.workers),
                'queue_size': track_download_manager.download_queue.qsize(),
                'active_downloads': len(track_download_manager.active_downloads),
                'completed_downloads': len(track_download_manager.completed_downloads),
                'workers_status': []
            }
            
            # Check each worker
            for worker in track_download_manager.workers:
                worker_status = {
                    'worker_id': worker.worker_id,
                    'is_running': worker._is_running,
                    'active_downloads': getattr(worker, '_active_downloads', 0),
                    'stuck_count': getattr(worker, 'stuck_count', 0),
                    'current_task_duration': None,
                    'last_progress_age': None
                }
                
                if hasattr(worker, 'current_task_start') and worker.current_task_start:
                    worker_status['current_task_duration'] = time.time() - worker.current_task_start
                    
                if hasattr(worker, 'last_progress_time') and worker.last_progress_time:
                    worker_status['last_progress_age'] = time.time() - worker.last_progress_time
                    
                status['workers_status'].append(worker_status)
                
            return status
            
        except Exception as e:
            return {'error': str(e)}
            
    async def _get_album_manager_status(self):
        """Get album download manager status."""
        try:
            from album_download_workers import download_manager
            
            status = {
                'is_running': download_manager._is_running,
                'worker_count': len(download_manager.workers),
                'queue_size': download_manager.download_queue.qsize(),
                'active_downloads': len(download_manager.active_downloads),
                'completed_downloads': len(download_manager.completed_downloads),
                'workers_status': []
            }
            
            # Check each worker
            for worker in download_manager.workers:
                worker_status = {
                    'worker_id': worker.worker_id,
                    'is_running': worker._is_running,
                    'stuck_count': getattr(worker, 'stuck_count', 0),
                    'current_task_duration': None,
                    'last_progress_age': None,
                    'current_task': getattr(worker, 'current_download_id', None)
                }
                
                if hasattr(worker, 'current_task_start') and worker.current_task_start:
                    worker_status['current_task_duration'] = time.time() - worker.current_task_start
                    
                if hasattr(worker, 'last_progress_time') and worker.last_progress_time:
                    worker_status['last_progress_age'] = time.time() - worker.last_progress_time
                    
                status['workers_status'].append(worker_status)
                
            return status
            
        except Exception as e:
            return {'error': str(e)}
            
    async def _analyze_health_report(self, report):
        """Analyze health report and log warnings."""
        # Check CPU usage
        if report['system']['cpu_percent'] > 80:
            logger.warning(f"🚨 HIGH CPU USAGE: {report['system']['cpu_percent']:.1f}%")
            
        # Check memory usage
        if report['system']['memory_percent'] > 85:
            logger.warning(f"🚨 HIGH MEMORY USAGE: {report['system']['memory_percent']:.1f}%")
            
        # Check worker counts
        total_workers = report['workers']['total_active_workers']
        max_workers = report['workers']['max_total_workers']
        if total_workers > max_workers:
            logger.error(f"🚨 WORKER OVERFLOW: {total_workers}/{max_workers}")
            
        # Check for stuck workers
        for manager_name, manager_status in report['download_managers'].items():
            if 'workers_status' in manager_status:
                for worker in manager_status['workers_status']:
                    # Check for stuck tasks
                    if worker.get('current_task_duration') and worker['current_task_duration'] > 600:  # 10 minutes
                        logger.warning(
                            f"🐌 STUCK WORKER: {manager_name} worker {worker['worker_id']} "
                            f"running task for {worker['current_task_duration']:.1f}s"
                        )
                        
                    # Check for stale progress
                    if worker.get('last_progress_age') and worker['last_progress_age'] > 300:  # 5 minutes
                        logger.warning(
                            f"📊 STALE PROGRESS: {manager_name} worker {worker['worker_id']} "
                            f"no progress for {worker['last_progress_age']:.1f}s"
                        )
                        
                    # Check stuck count
                    if worker.get('stuck_count', 0) > 2:
                        logger.error(
                            f"🔄 HIGH STUCK COUNT: {manager_name} worker {worker['worker_id']} "
                            f"stuck {worker['stuck_count']} times"
                        )
                        
        # Check queue backup
        track_queue = report['download_managers']['track'].get('queue_size', 0)
        album_queue = report['download_managers']['album'].get('queue_size', 0)
        
        if track_queue > 10:
            logger.warning(f"📦 TRACK QUEUE BACKUP: {track_queue} items")
        if album_queue > 5:
            logger.warning(f"📦 ALBUM QUEUE BACKUP: {album_queue} items")
            
    async def _write_health_log(self, report):
        """Write health report to log file."""
        try:
            with open(self.log_file, 'a') as f:
                f.write(json.dumps(report) + '\n')
        except Exception as e:
            logger.error(f"Error writing health log: {e}")
            
    def get_recent_logs(self, lines: int = 50) -> List[Dict]:
        """Get recent health logs for analysis."""
        try:
            if not self.log_file.exists():
                return []
                
            logs = []
            with open(self.log_file, 'r') as f:
                for line in f.readlines()[-lines:]:
                    try:
                        logs.append(json.loads(line.strip()))
                    except json.JSONDecodeError:
                        continue
            return logs
        except Exception as e:
            logger.error(f"Error reading health logs: {e}")
            return []
            
    def analyze_performance_trends(self) -> Dict:
        """Analyze performance trends from recent logs."""
        logs = self.get_recent_logs(100)
        if not logs:
            return {'error': 'No logs available'}
            
        # Calculate averages
        cpu_values = [log['system']['cpu_percent'] for log in logs]
        memory_values = [log['system']['memory_percent'] for log in logs]
        
        track_queue_sizes = []
        album_queue_sizes = []
        
        for log in logs:
            track_status = log['download_managers'].get('track', {})
            album_status = log['download_managers'].get('album', {})
            
            if 'queue_size' in track_status:
                track_queue_sizes.append(track_status['queue_size'])
            if 'queue_size' in album_status:
                album_queue_sizes.append(album_status['queue_size'])
                
        analysis = {
            'timespan': f"{len(logs)} readings over {len(logs) * self.monitor_interval / 60:.1f} minutes",
            'cpu': {
                'average': sum(cpu_values) / len(cpu_values) if cpu_values else 0,
                'max': max(cpu_values) if cpu_values else 0,
                'min': min(cpu_values) if cpu_values else 0
            },
            'memory': {
                'average': sum(memory_values) / len(memory_values) if memory_values else 0,
                'max': max(memory_values) if memory_values else 0,
                'min': min(memory_values) if memory_values else 0
            },
            'track_queue': {
                'average': sum(track_queue_sizes) / len(track_queue_sizes) if track_queue_sizes else 0,
                'max': max(track_queue_sizes) if track_queue_sizes else 0,
                'current': track_queue_sizes[-1] if track_queue_sizes else 0
            },
            'album_queue': {
                'average': sum(album_queue_sizes) / len(album_queue_sizes) if album_queue_sizes else 0,
                'max': max(album_queue_sizes) if album_queue_sizes else 0,
                'current': album_queue_sizes[-1] if album_queue_sizes else 0
            }
        }
        
        return analysis

# Global monitor instance
worker_monitor = WorkerMonitor()

# API endpoints for monitoring (add these to your app.py)

from fastapi import APIRouter

monitor_router = APIRouter(prefix="/api/admin/monitor", tags=["monitoring"])

@monitor_router.get("/status")
async def get_worker_status():
    """Get current worker status."""
    from worker_config import worker_config
    system_status = worker_config.get_system_status()
    
    # Add detailed worker statuses
    for worker_type in ['track_downloaders', 'download_worker']:
        if worker_type in system_status['worker_breakdown']:
            system_status[f'{worker_type}_detail'] = worker_config.get_worker_status(worker_type)
    
    return system_status

@monitor_router.get("/health")
async def get_system_health():
    """Get detailed system health report."""
    # Get latest health report
    track_status = await worker_monitor._get_track_manager_status()
    album_status = await worker_monitor._get_album_manager_status()
    
    return {
        'system': {
            'cpu_percent': psutil.cpu_percent(interval=1),
            'memory_percent': psutil.virtual_memory().percent,
            'disk_usage': psutil.disk_usage('/tmp').percent
        },
        'download_managers': {
            'track': track_status,
            'album': album_status
        },
        'worker_config': worker_config.get_system_status()
    }

@monitor_router.get("/trends")
async def get_performance_trends():
    """Get performance trend analysis."""
    return worker_monitor.analyze_performance_trends()

@monitor_router.post("/force-scale-down")
async def force_scale_down():
    """Emergency endpoint to force scale down all workers."""
    from worker_config import worker_config
    worker_config.force_scale_down_all()
    return {"message": "Forced scale down initiated"}

@monitor_router.get("/logs")
async def get_recent_logs(lines: int = 20):
    """Get recent monitoring logs."""
    return worker_monitor.get_recent_logs(lines)

# Add this to your app.py:
# app.include_router(monitor_router)

# Debug commands for manual testing
async def debug_worker_status():
    """Debug function to check worker status manually."""
    print("\n=== WORKER STATUS DEBUG ===")
    
    from worker_config import worker_config
    status = worker_config.get_system_status()
    
    print(f"Total Workers: {status['total_active_workers']}/{status['max_total_workers']}")
    print(f"CPU Usage: {status['cpu_usage']:.1f}%")
    print(f"Worker Breakdown: {status['worker_breakdown']}")
    print(f"Queue Lengths: {status['queue_lengths']}")
    
    print("\n--- Track Download Manager ---")
    track_status = await worker_monitor._get_track_manager_status()
    print(f"Running: {track_status.get('is_running', 'Unknown')}")
    print(f"Workers: {track_status.get('worker_count', 0)}")
    print(f"Queue: {track_status.get('queue_size', 0)}")
    print(f"Active: {track_status.get('active_downloads', 0)}")
    
    print("\n--- Album Download Manager ---")
    album_status = await worker_monitor._get_album_manager_status()
    print(f"Running: {album_status.get('is_running', 'Unknown')}")
    print(f"Workers: {album_status.get('worker_count', 0)}")
    print(f"Queue: {album_status.get('queue_size', 0)}")
    print(f"Active: {album_status.get('active_downloads', 0)}")
    
    print("\n=== END DEBUG ===\n")

# Add to app.py lifespan for monitoring
async def start_monitoring():
    """Start worker monitoring (call this in your app lifespan)."""
    await worker_monitor.start_monitoring()
    logger.info("🔍 Worker monitoring enabled")

async def stop_monitoring():
    """Stop worker monitoring (call this in your app cleanup)."""
    await worker_monitor.stop_monitoring()
    logger.info("🔍 Worker monitoring disabled")