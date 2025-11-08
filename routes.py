# routes.py
from fastapi import APIRouter, Depends
from storage import storage

router = APIRouter()

@router.get("/api/workers/status")
async def get_workers_status():
    """Get current status of processing workers"""
    return {
        'workers': storage.preparation_manager.get_worker_status(),
        'queues': {
            name: queue.qsize() 
            for name, queue in storage.preparation_manager._queues.items()
        },
        'resources': {
            'memory': psutil.virtual_memory().percent,
            'cpu': psutil.cpu_percent()
        }
    }

@router.get("/api/tracks/{track_id}/processing")
async def get_track_processing_status(track_id: str):
    """Get detailed processing status for a track"""
    try:
        # Get track from background preparation manager
        status = storage.preparation_manager.get_status(track_id)
        if not status:
            return {
                'status': 'not_found',
                'message': 'Track not found in processing queue'
            }

        # Get segmentation progress if available
        segment_progress = await stream_manager.get_segment_progress(track_id)

        return {
            'status': status['status'],
            'progress': status.get('progress', 0),
            'started_at': status.get('started_at'),
            'completed_at': status.get('completed_at'),
            'error': status.get('error'),
            'priority': status.get('priority', 'normal'),
            'segmentation': segment_progress,
            'worker_id': status.get('worker_id'),
            'metrics': status.get('metrics', {})
        }
    except Exception as e:
        logger.error(f"Error getting track status: {e}")
        return {
            'status': 'error',
            'error': str(e)
        }