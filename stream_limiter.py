import asyncio
from datetime import datetime
import logging
from fastapi import HTTPException
from typing import Dict, Any
from redis.asyncio import Redis, WatchError

logger = logging.getLogger(__name__)

class SessionStreamLimiter:
    def __init__(self, redis_client, session_manager):
        # Convert synchronous Redis client to async
        redis_config = {
            'host': redis_client.connection_pool.connection_kwargs['host'],
            'port': redis_client.connection_pool.connection_kwargs['port'],
            'db': redis_client.connection_pool.connection_kwargs['db'],
            'decode_responses': True
        }
        if 'password' in redis_client.connection_pool.connection_kwargs:
            redis_config['password'] = redis_client.connection_pool.connection_kwargs['password']
                
        self.redis = Redis(**redis_config)
        self.session_manager = session_manager
        self._initialized = False
        self._cleanup_task = None
            
        self.max_init_retries = 3
        self.init_retry_delay = 2
        self.stream_timeout = 15
        self.cleanup_interval = 10
        self.key_prefix = "stream:"
        self.heartbeat_timeout = 15
        self.session_tracking = {}  # Track session-specific streams
        
        # Stream limits removed - keeping structure for future implementation
        self.stream_limits = {
            "CREATOR": float('inf'),
            "TEAM": float('inf'),
            "PATREON": float('inf')
        }
            
        logger.info("Stream limiter instance created with no restrictions")
    
    async def init(self) -> bool:
        """Initialize the stream limiter"""
        if self._initialized:
            return True
            
        for attempt in range(self.max_init_retries):
            try:
                if not self.redis:
                    raise RuntimeError("Redis client not provided")
                    
                await self.redis.ping()
                    
                if not self._cleanup_task:
                    self._cleanup_task = asyncio.create_task(
                        self._cleanup_abandoned_streams()
                    )
                    
                self._initialized = True
                logger.info(f"Stream limiter initialized successfully on attempt {attempt + 1}")
                return True
                    
            except Exception as e:
                logger.error(f"Initialization attempt {attempt + 1} failed: {str(e)}")
                if attempt < self.max_init_retries - 1:
                    await asyncio.sleep(self.init_retry_delay)
                continue
            
        logger.error(f"Stream limiter initialization failed after {self.max_init_retries} attempts")
        return False
    
    @property
    def is_initialized(self) -> bool:
        """Check if the stream limiter is properly initialized"""
        return self._initialized and self.redis is not None
    
    async def shutdown(self):
        """Properly shut down the cleanup task"""
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            self._cleanup_task = None
            logger.info("Stream cleanup task stopped")
    
    async def check_stream_limit(self, user: Any, session_id: str, track_id: str) -> Dict[str, Any]:
        """Check stream limit - now always allows streaming while maintaining tracking"""
        if not self.is_initialized:
            raise HTTPException(status_code=500, detail="Streaming service unavailable")
            
        try:
            # Look for existing stream for this track
            user_streams_key = f"{self.key_prefix}user:{user.id}:streams"
            stream_ids = await self.redis.smembers(user_streams_key)
            
            # Check for existing stream for this track
            for sid in stream_ids:
                stream_key = f"{self.key_prefix}stream:{sid}"
                stream_data = await self.redis.hgetall(stream_key)
                
                if stream_data and stream_data.get('track_id') == track_id:
                    # Update session ID if needed
                    if stream_data.get('session_id') != session_id:
                        await self.redis.hset(stream_key, 'session_id', session_id)
                    
                    return {
                        "can_stream": True,
                        "status": "existing_stream", 
                        "stream_id": sid
                    }

            # Create new stream
            stream_id = f"{user.id}:{track_id}:{session_id}"
            now = datetime.now().timestamp()
            
            stream_data = {
                'stream_id': stream_id,
                'user_id': str(user.id),
                'session_id': session_id,
                'track_id': track_id,
                'start_time': str(now),
                'last_segment_time': str(now),
                'last_heartbeat': str(now)
            }
            
            stream_key = f"{self.key_prefix}stream:{stream_id}"
            async with self.redis.pipeline(transaction=True) as pipe:
                pipe.hmset(stream_key, stream_data)
                pipe.expire(stream_key, self.stream_timeout)
                pipe.sadd(user_streams_key, stream_id)
                await pipe.execute()
            
            return {
                "can_stream": True,
                "status": "new_stream",
                "stream_id": stream_id
            }
            
        except Exception as e:
            logger.error(f"Error checking stream limit: {str(e)}")
            raise HTTPException(status_code=500, detail=str(e))
    
    async def decrease_stream_count(self, user_id: str, stream_id: str):
        """Enhanced decrease stream count with proper cleanup"""
        try:
            # Decode the stream_id to verify it belongs to the user
            components = stream_id.split(":")
            if len(components) != 3:
                logger.error(f"Invalid stream ID format: {stream_id}")
                return
            decoded_user_id, decoded_track_id, decoded_session_id = components
            if decoded_user_id != str(user_id):
                logger.error(f"Stream ID {stream_id} does not belong to user {user_id}")
                return
    
            async with self.redis.pipeline(transaction=True) as pipe:
                stream_key = f"{self.key_prefix}stream:{stream_id}"
                user_streams_key = f"{self.key_prefix}user:{user_id}:streams"
                    
                # Remove stream data and update user's stream set
                pipe.delete(stream_key)
                pipe.srem(user_streams_key, stream_id)
                await pipe.execute()
                    
                logger.info(f"Stream {stream_id} ended for user {user_id}")
                    
        except Exception as e:
            logger.error(f"Error decreasing stream count: {str(e)}")
    
    async def update_segment_access(self, user: Any, stream_id: str):
        """Enhanced segment access tracking with session validation"""
        try:
            stream_key = f"{self.key_prefix}stream:{stream_id}"
            stream_data = await self.redis.hgetall(stream_key)
                
            if not stream_data:
                raise HTTPException(status_code=403, detail="Invalid stream access")
    
            # Verify stream belongs to user
            if stream_data.get('user_id', '') != str(user.id):
                raise HTTPException(status_code=403, detail="Invalid stream access")
    
            # Update access time and segment count
            now = datetime.now().timestamp()
            segment_count = int(stream_data.get('segment_count', 0))
                
            await self.redis.hmset(stream_key, {
                'last_segment_time': str(now),
                'segment_count': str(segment_count + 1),
                'last_heartbeat': str(now)
            })
                
            # Set expiration
            await self.redis.expire(stream_key, self.stream_timeout)
    
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error updating segment access: {str(e)}")
            raise HTTPException(status_code=500, detail="Error updating stream access")
    
    async def _update_stream_session(self, stream_id: str, session_id: str):
        """Update the session ID for an existing stream"""
        try:
            stream_key = f"{self.key_prefix}stream:{stream_id}"
            await self.redis.hset(stream_key, 'session_id', session_id)
            now = datetime.now().timestamp()
            await self.redis.hmset(stream_key, {
                'last_segment_time': str(now),
                'last_heartbeat': str(now)
            })
            await self.redis.expire(stream_key, self.stream_timeout)
        except Exception as e:
            logger.error(f"Error updating stream session: {str(e)}")
    
    async def _get_active_streams(self, user_id: int) -> list:
        try:
            active_streams = []
            now = datetime.now().timestamp()
                
            # Get user's stream IDs from set
            user_streams_key = f"{self.key_prefix}user:{user_id}:streams"
            stream_ids = await self.redis.smembers(user_streams_key)
                
            for stream_id in stream_ids:
                stream_id = stream_id if isinstance(stream_id, str) else stream_id.decode()
                stream_key = f"{self.key_prefix}stream:{stream_id}"
                stream_data = await self.redis.hgetall(stream_key)
                    
                if stream_data:
                    # Convert all byte keys to strings
                    stream_data = {
                        k: v for k, v in stream_data.items()
                    }
                        
                    last_segment = float(stream_data.get('last_segment_time', 0))
                        
                    # Only include non-expired streams
                    if now - last_segment < self.stream_timeout:
                        active_streams.append({
                            'stream_id': stream_id,
                            'track_id': stream_data.get('track_id', ''),
                            'start_time': float(stream_data.get('start_time', 0))
                        })
                    else:
                        # Clean up expired stream
                        await self.redis.delete(stream_key)
                        await self.redis.srem(user_streams_key, stream_id)
                            
            return active_streams
                
        except Exception as e:
            logger.error(f"Error getting active streams: {str(e)}")
            return []
    
    async def _cleanup_abandoned_streams(self):
        """Clean up streams with no heartbeat or activity"""
        while True:
            try:
                now = datetime.now().timestamp()
                all_streams = await self.redis.keys(f"{self.key_prefix}stream:*")
                
                for stream_key in all_streams:
                    try:
                        stream_data = await self.redis.hgetall(stream_key)
                        if stream_data:
                            last_heartbeat = float(stream_data.get('last_heartbeat', 0))
                            last_segment = float(stream_data.get('last_segment_time', 0))
                            
                            # Check both heartbeat and segment activity
                            if (now - last_heartbeat >= self.heartbeat_timeout or 
                                now - last_segment >= self.stream_timeout):
                                
                                # Get user and stream IDs for cleanup
                                stream_components = stream_key.split(':')
                                if len(stream_components) >= 4:
                                    user_id = stream_components[2]
                                    user_streams_key = f"{self.key_prefix}user:{user_id}:streams"
                                    
                                    # Clean up stream and user reference
                                    await self.redis.delete(stream_key)
                                    await self.redis.srem(user_streams_key, stream_key.split(':')[-1])
                                    
                                    logger.info(f"Cleaned up inactive stream: {stream_key} " 
                                              f"(No heartbeat: {now - last_heartbeat}s, "
                                              f"No activity: {now - last_segment}s)")
                                    
                    except Exception as e:
                        logger.error(f"Error processing stream {stream_key}: {str(e)}")
                        continue
                        
            except Exception as e:
                logger.error(f"Error in stream cleanup: {str(e)}")
                
            await asyncio.sleep(self.cleanup_interval)
    
    async def get_stream_stats(self, user_id: int) -> Dict[str, Any]:
        """
        Get detailed statistics about a user's current streams
            
        Args:
            user_id: The user's ID
                
        Returns:
            Dictionary containing stream statistics
        """
        try:
            stats = {
                "active_streams": 0,
                "total_segments": 0,
                "oldest_stream": None,
                "newest_stream": None,
                "streams": []
            }
                
            active_streams = await self._get_active_streams(user_id)
                
            if active_streams:
                now = datetime.now().timestamp()
                    
                for stream in active_streams:
                    stream_key = f"{self.key_prefix}stream:{stream['stream_id']}"
                    stream_data = await self.redis.hgetall(stream_key)
                        
                    if stream_data:
                        start_time = float(stream_data.get('start_time', 0))
                        segment_count = int(stream_data.get('segment_count', 0))
                            
                        stream_info = {
                            "stream_id": stream['stream_id'],
                            "track_id": stream_data.get('track_id', ''),
                            "duration": now - start_time,
                            "segment_count": segment_count,
                            "start_time": datetime.fromtimestamp(start_time).isoformat()
                        }
                            
                        stats["streams"].append(stream_info)
                        stats["total_segments"] += segment_count
                            
                        # Track oldest and newest streams
                        if not stats["oldest_stream"] or start_time < float(stats["oldest_stream"]["start_time"]):
                            stats["oldest_stream"] = stream_info
                        if not stats["newest_stream"] or start_time > float(stats["newest_stream"]["start_time"]):
                            stats["newest_stream"] = stream_info
                    
                stats["active_streams"] = len(active_streams)
                
            return stats
                
        except Exception as e:
            logger.error(f"Error getting stream stats: {str(e)}")
            return {
                "error": str(e),
                "active_streams": 0,
                "total_segments": 0
            }
    
    async def force_cleanup(self, user_id: int) -> Dict[str, Any]:
        """
        Force cleanup of all streams for a specific user
            
        Args:
            user_id: The user's ID
                
        Returns:
            Dictionary containing cleanup results
        """
        try:
            cleanup_results = {
                "streams_removed": 0,
                "segments_cleaned": 0,
                "errors": []
            }
                
            # Get all user's stream keys
            user_streams_key = f"{self.key_prefix}user:{user_id}:streams"
            stream_ids = await self.redis.smembers(user_streams_key)
                
            for stream_id in stream_ids:
                try:
                    stream_id = stream_id if isinstance(stream_id, str) else stream_id.decode()
                    stream_key = f"{self.key_prefix}stream:{stream_id}"
                        
                    # Remove stream data
                    await self.redis.delete(stream_key)
                    await self.redis.srem(user_streams_key, stream_id)
                        
                    cleanup_results["streams_removed"] += 1
                        
                except Exception as e:
                    cleanup_results["errors"].append(f"Error cleaning stream {stream_id}: {str(e)}")
                
            return cleanup_results
                
        except Exception as e:
            logger.error(f"Error in force cleanup: {str(e)}")
            return {
                "error": str(e),
                "streams_removed": 0
            }