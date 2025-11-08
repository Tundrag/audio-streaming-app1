import psutil
import logging
from typing import Dict
from datetime import datetime, timedelta
from typing import Optional


logger = logging.getLogger(__name__)

class WorkerConfig:
    """Centralized dynamic worker configuration with improved scaling logic."""
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(WorkerConfig, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        # System configuration
        self.cpu_count = psutil.cpu_count(logical=True)
        self.max_cpu_percent = 80       # Maximum total CPU usage (80%)
        self.scale_check_interval = 30  # Seconds between scaling checks

        # -- NEW: Let’s allow up to 3× CPU count across all workers (configurable).
        #         For a 4-core machine, this equals 12.
        self.max_total_workers = self.cpu_count * 3

        # Dynamic tracking of how many workers are active (by type).
        self._active_workers = {
            'track_downloaders': 0,
            'disk_io': 0,        # Example "writer upload queue"
            'download_worker': 0,
            'background': 0,
            'metadata': 0,
            'mega_upload': 0,
            'downloads': 0
        }

        # Worker type configurations
        # Prioritization can be reflected in 'priority' or in how we handle scaling thresholds
        self.worker_configs = {
            'track_downloaders': {
                'min_workers': 1,
                'max_workers': int(self.max_total_workers),  # or some fraction if needed
                'target_queue_per_worker': 2,  # For scale-up threshold
                'scale_down_queue_per_worker': 1,
                'cooldown_period': 15,  # shorter cooldown for track_downloaders
                'priority': 1,         # highest priority
            },
            'disk_io': {
                'min_workers': 1,
                'max_workers': int(self.max_total_workers * 0.5),  # example: half of the total
                'target_queue_per_worker': 2,
                'scale_down_queue_per_worker': 1,
                'cooldown_period': 30,
                'priority': 2,
            },
            'download_worker': {
                'min_workers': 1,
                'max_workers': int(self.max_total_workers * 0.5),
                'target_queue_per_worker': 2,
                'scale_down_queue_per_worker': 1,
                'cooldown_period': 30,
                'priority': 3,
            },
            # The rest have lower priority or different thresholds
            'background': {
                'min_workers': 1,
                'max_workers': int(self.max_total_workers * 0.2),
                'target_queue_per_worker': 3,
                'scale_down_queue_per_worker': 1,
                'cooldown_period': 60,
                'priority': 10,
            },
            'metadata': {
                'min_workers': 1,
                'max_workers': int(self.max_total_workers * 0.1),
                'target_queue_per_worker': 2,
                'scale_down_queue_per_worker': 1,
                'cooldown_period': 30,
                'priority': 11,
            },
            'mega_upload': {
                'min_workers': 1,
                'max_workers': int(self.max_total_workers * 0.1),
                'target_queue_per_worker': 2,
                'scale_down_queue_per_worker': 1,
                'cooldown_period': 30,
                'priority': 12,
            },
            'downloads': {
                'min_workers': 1,
                'max_workers': int(self.max_total_workers * 0.2),
                'target_queue_per_worker': 2,
                'scale_down_queue_per_worker': 1,
                'cooldown_period': 30,
                'priority': 13,
            },
        }

        # When we last scaled each worker type (to manage cooldown).
        self._last_scale_time = {wtype: datetime.min for wtype in self.worker_configs}

        # Track queue lengths per worker type
        self._queue_lengths = {wtype: 0 for wtype in self.worker_configs}

        self._initialized = True
        logger.info("Initialized dynamic worker configuration")
        self._log_configuration()

    def update_queue_length(self, worker_type: str, length: int):
        """Update the queue length for a worker type."""
        self._queue_lengths[worker_type] = length

    def can_scale(self, worker_type: str, extra: Optional[str] = None) -> bool:
        """
        Check if scaling action is allowed (cooldown period).
        We only track a single time for both scale up/down to simplify.
        """
        config = self.worker_configs[worker_type]
        last_scale = self._last_scale_time[worker_type]
        cooldown = timedelta(seconds=config['cooldown_period'])

        return (datetime.now() - last_scale) >= cooldown

    @property
    def total_active_workers(self) -> int:
        """Get total number of active workers across all types."""
        return sum(self._active_workers.values())

    def get_worker_count(self, worker_type: str) -> int:
        """
        Dynamically calculate the needed workers based on:
        - Overall CPU usage
        - Worker-specific queue length
        - Limits: min/max for the worker type & overall max_total_workers
        - Basic scale-up/scale-down heuristics
        """
        config = self.worker_configs[worker_type]
        current_workers = self._active_workers[worker_type]
        queue_length = self._queue_lengths[worker_type]

        # 1. During initialization or when no workers exist yet -> minimum
        if self.total_active_workers == 0 or current_workers == 0:
            return config['min_workers']

        # 2. Check cooldown: If we cannot scale right now, just return current
        if not self.can_scale(worker_type):
            return current_workers

        # 3. Get current CPU usage for the entire system
        current_cpu = psutil.cpu_percent(interval=0.1)

        # 4. If system is overloaded (above max_cpu_percent), attempt scale down
        #    unless we are already at minimum for this worker.
        if current_cpu > self.max_cpu_percent:
            if current_workers > config['min_workers']:
                self._last_scale_time[worker_type] = datetime.now()
                return max(config['min_workers'], current_workers - 1)
            else:
                # Can't scale down below min
                return current_workers

        # 5. Don’t scale at all if queue is <= 1 (i.e. no real backlog)
        if queue_length <= 1:
            # Possibly scale down if workers are above min
            # and the queue is essentially empty
            if current_workers > config['min_workers']:
                self._last_scale_time[worker_type] = datetime.now()
                return current_workers - 1
            else:
                return current_workers

        # 6. Evaluate queue pressure: queue_length / current_workers
        #    If > target_queue_per_worker -> scale up
        #    If < scale_down_queue_per_worker -> scale down
        #    But also enforce the total worker limit across all types
        queue_pressure = queue_length / max(current_workers, 1)

        # For clarity
        scale_up_thresh = config['target_queue_per_worker']
        scale_down_thresh = config['scale_down_queue_per_worker']

        desired_workers = current_workers

        if queue_pressure > scale_up_thresh:
            # Scale up if we haven't hit the max for this worker type
            # and haven't exceeded the total max workers across the system
            if (current_workers < config['max_workers']
                and self.total_active_workers < self.max_total_workers):
                desired_workers = min(config['max_workers'], current_workers + 1)
        elif queue_pressure < scale_down_thresh:
            # Scale down if above minimum
            if current_workers > config['min_workers']:
                desired_workers = max(config['min_workers'], current_workers - 1)

        # If we decided to scale up or down, record the time
        if desired_workers != current_workers:
            self._last_scale_time[worker_type] = datetime.now()

        return desired_workers

    def register_worker(self, worker_type: str):
        """Register a new active worker (e.g., after it’s actually spawned)."""
        self._active_workers[worker_type] += 1
        logger.info(
            f"Registered new {worker_type} worker. Total: {self._active_workers[worker_type]}"
        )

    def unregister_worker(self, worker_type: str):
        """Unregister an active worker (e.g., after it’s shut down)."""
        if self._active_workers[worker_type] > 0:
            self._active_workers[worker_type] -= 1
            logger.info(
                f"Unregistered {worker_type} worker. Total: {self._active_workers[worker_type]}"
            )

    def get_worker_status(self, worker_type: str) -> Dict:
        """Get current status for a worker type."""
        config = self.worker_configs[worker_type]
        return {
            'current_workers': self._active_workers[worker_type],
            'min_workers': config['min_workers'],
            'max_workers': config['max_workers'],
            'queue_length': self._queue_lengths[worker_type],
            'cooldown_remaining': max(
                0,
                (
                    timedelta(seconds=config['cooldown_period'])
                    - (datetime.now() - self._last_scale_time[worker_type])
                ).total_seconds()
            ),
        }

    def _log_configuration(self):
        """Log the current configuration details."""
        logger.info(
            f"Dynamic worker configuration:\n"
            f"System CPU cores: {self.cpu_count}\n"
            f"Max CPU usage allowed: {self.max_cpu_percent}%\n"
            f"Max total workers: {self.max_total_workers}\n"
            f"Worker configurations:"
        )
        for worker_type, cfg in self.worker_configs.items():
            logger.info(
                f"  - {worker_type}:\n"
                f"      Priority: {cfg['priority']}\n"
                f"      Min workers: {cfg['min_workers']}\n"
                f"      Max workers: {cfg['max_workers']}\n"
                f"      Target queue/worker (scale up): {cfg['target_queue_per_worker']}\n"
                f"      Scale down queue/worker: {cfg['scale_down_queue_per_worker']}\n"
                f"      Cooldown period: {cfg['cooldown_period']}s\n"
            )

# Create the global (singleton) instance
worker_config = WorkerConfig()
