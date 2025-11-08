import os
import logging
from typing import Any, Optional, List, Set, Dict, Union
from redis import Redis
from redis.asyncio import Redis as AsyncRedis
from redis.exceptions import ConnectionError, RedisError
from dotenv import load_dotenv

load_dotenv()

# Configure logger
logger = logging.getLogger(__name__)

# Redis connection settings
REDIS_HOST = os.getenv('REDIS_HOST', 'redis')
REDIS_PORT = int(os.getenv('REDIS_PORT', 6379))
REDIS_PASSWORD = os.getenv('REDIS_PASSWORD')
REDIS_DB = int(os.getenv('REDIS_DB', 0))
REDIS_URL = os.getenv('REDIS_URL', f"redis://{REDIS_HOST}:{REDIS_PORT}/{REDIS_DB}")

# Fallback Redis settings
FALLBACK_REDIS_HOST = os.getenv('FALLBACK_REDIS_HOST', 'localhost')
FALLBACK_REDIS_PORT = int(os.getenv('FALLBACK_REDIS_PORT', 6379))
FALLBACK_REDIS_PASSWORD = os.getenv('FALLBACK_REDIS_PASSWORD')
FALLBACK_REDIS_DB = int(os.getenv('FALLBACK_REDIS_DB', 0))
FALLBACK_REDIS_URL = os.getenv('FALLBACK_REDIS_URL', 
                              f"redis://{FALLBACK_REDIS_HOST}:{FALLBACK_REDIS_PORT}/{FALLBACK_REDIS_DB}")

# Common Redis configuration
REDIS_CONFIG = {
    "host": REDIS_HOST,
    "port": REDIS_PORT,
    "db": REDIS_DB,
    "decode_responses": True,
    "socket_timeout": 2.0,
    "socket_connect_timeout": 2.0,
    "retry_on_timeout": True
}

FALLBACK_REDIS_CONFIG = {
    "host": FALLBACK_REDIS_HOST,
    "port": FALLBACK_REDIS_PORT,
    "db": FALLBACK_REDIS_DB,
    "decode_responses": True,
    "socket_timeout": 2.0,
    "socket_connect_timeout": 2.0,
    "retry_on_timeout": True
}

if REDIS_PASSWORD:
    REDIS_CONFIG["password"] = REDIS_PASSWORD

if FALLBACK_REDIS_PASSWORD:
    FALLBACK_REDIS_CONFIG["password"] = FALLBACK_REDIS_PASSWORD


class ResilientRedisClient:
    """
    A wrapper for Redis client that gracefully handles connection failures
    and provides fallback values instead of crashing the application.
    """
    
    def __init__(
        self,
        primary_config: Dict = REDIS_CONFIG,
        fallback_config: Dict = FALLBACK_REDIS_CONFIG,
        default_ttl: int = 86400,
        max_retries: int = 2
    ):
        self.primary_config = primary_config
        self.fallback_config = fallback_config
        self.default_ttl = default_ttl
        self.max_retries = max_retries
        self._primary_client = None
        self._fallback_client = None
        self._primary_available = False
        self._fallback_available = False
        
        # Initialize connections
        self._init_connections()
    
    def _init_connections(self) -> None:
        """Initialize Redis connections"""
        # Try primary connection
        if not self._primary_client:
            try:
                self._primary_client = Redis(**self.primary_config)
                self._primary_client.ping()
                self._primary_available = True
                logger.info("Connected to primary Redis successfully")
            except Exception as e:
                logger.warning(f"Primary Redis connection failed: {e}")
                self._primary_available = False
        
        # Try fallback connection if primary failed
        if not self._primary_available and not self._fallback_client:
            try:
                self._fallback_client = Redis(**self.fallback_config)
                self._fallback_client.ping()
                self._fallback_available = True
                logger.info("Connected to fallback Redis successfully")
            except Exception as e:
                logger.warning(f"Fallback Redis connection failed: {e}")
                self._fallback_available = False
    
    def _get_client(self) -> Optional[Redis]:
        """Get available Redis client or None if all are unavailable"""
        # Check primary client
        if self._primary_available:
            try:
                self._primary_client.ping()
                return self._primary_client
            except Exception:
                logger.warning("Primary Redis is no longer available")
                self._primary_available = False
        
        # Check fallback client
        if self._fallback_available:
            try:
                self._fallback_client.ping()
                return self._fallback_client
            except Exception:
                logger.warning("Fallback Redis is no longer available")
                self._fallback_available = False
        
        # Try to reconnect if both are unavailable
        self._init_connections()
        
        # Return available client or None
        if self._primary_available:
            return self._primary_client
        elif self._fallback_available:
            return self._fallback_client
        else:
            return None
    
    def _execute_with_fallback(self, method: str, *args, fallback_value: Any = None, **kwargs) -> Any:
        """Execute Redis command with fallback value on failure"""
        client = self._get_client()
        
        if not client:
            logger.warning(f"Redis unavailable for {method}, using fallback value")
            return fallback_value
        
        for attempt in range(self.max_retries):
            try:
                redis_method = getattr(client, method)
                return redis_method(*args, **kwargs)
            except (ConnectionError, RedisError) as e:
                logger.warning(f"Redis error on attempt {attempt+1}/{self.max_retries}: {e}")
                if attempt == self.max_retries - 1:
                    logger.error(f"All Redis attempts failed for {method}, using fallback value")
                    return fallback_value
                # Try to get a working client for next attempt
                client = self._get_client()
                if not client:
                    logger.error("No Redis connection available, using fallback value")
                    return fallback_value
    
    # Set operations
    def sadd(self, key: str, *values) -> int:
        """Add values to set, return number of items added or 0 on failure"""
        return self._execute_with_fallback('sadd', key, *values, fallback_value=0)
    
    def srem(self, key: str, *values) -> int:
        """Remove values from set, return number of items removed or 0 on failure"""
        return self._execute_with_fallback('srem', key, *values, fallback_value=0)
    
    def scard(self, key: str) -> int:
        """Return set cardinality (number of elements) or 0 on failure"""
        return self._execute_with_fallback('scard', key, fallback_value=0)
    
    def smembers(self, key: str) -> Set:
        """Return all members of the set or empty set on failure"""
        return self._execute_with_fallback('smembers', key, fallback_value=set())
    
    def sismember(self, key: str, value) -> bool:
        """Return True if value is in set, False otherwise or on failure"""
        return self._execute_with_fallback('sismember', key, value, fallback_value=False)
    
    # Key-value operations
    def get(self, key: str) -> Optional[str]:
        """Get value for key or None on failure"""
        return self._execute_with_fallback('get', key, fallback_value=None)
    
    def set(self, key: str, value: str, ex: int = None, nx: bool = False) -> bool:
        """Set key to value with optional expiration, return True on success or False on failure"""
        return self._execute_with_fallback('set', key, value, ex=ex or self.default_ttl, nx=nx, fallback_value=False)
    
    def delete(self, *keys) -> int:
        """Delete keys, return number of keys deleted or 0 on failure"""
        return self._execute_with_fallback('delete', *keys, fallback_value=0)
    
    def exists(self, *keys) -> int:
        """Check if keys exist, return count of existing keys or 0 on failure"""
        return self._execute_with_fallback('exists', *keys, fallback_value=0)
    
    def incr(self, key: str) -> int:
        """Increment value, return new value or 1 on failure (assumes first increment)"""
        return self._execute_with_fallback('incr', key, fallback_value=1)
    
    def decr(self, key: str) -> int:
        """Decrement value, return new value or 0 on failure"""
        return self._execute_with_fallback('decr', key, fallback_value=0)
    
    # Hash operations
    def hget(self, key: str, field: str) -> Optional[str]:
        """Get hash field or None on failure"""
        return self._execute_with_fallback('hget', key, field, fallback_value=None)
    
    def hset(self, key: str, field: str, value: str) -> int:
        """Set hash field, return 1 if field is new or 0 if field existed"""
        return self._execute_with_fallback('hset', key, field, value, fallback_value=0)
    
    def hmset(self, key: str, mapping: Dict) -> bool:
        """Set multiple hash fields, return True on success or False on failure"""
        return self._execute_with_fallback('hmset', key, mapping, fallback_value=False)
    
    def hmget(self, key: str, fields: List) -> List:
        """Get multiple hash fields, return list of values or list of None on failure"""
        return self._execute_with_fallback('hmget', key, fields, fallback_value=[None] * len(fields))
    
    def hgetall(self, key: str) -> Dict:
        """Get all hash fields and values, return dict or empty dict on failure"""
        return self._execute_with_fallback('hgetall', key, fallback_value={})
    
    # Other operations
    def keys(self, pattern: str) -> List:
        """Find keys matching pattern, return list or empty list on failure"""
        return self._execute_with_fallback('keys', pattern, fallback_value=[])

    def scan_iter(self, match: str = None, count: int = None):
        """Iterate over keys matching pattern using SCAN, yield keys or return empty iterator on failure"""
        client = self._get_client()
        if not client:
            logger.warning("No Redis connection available for scan_iter, returning empty iterator")
            return iter([])

        try:
            return client.scan_iter(match=match, count=count)
        except Exception as e:
            logger.warning(f"scan_iter failed: {e}, returning empty iterator")
            return iter([])

    # List operations
    def lpush(self, key: str, *values) -> int:
        """Push values to start of list, return list length or 0 on failure"""
        return self._execute_with_fallback('lpush', key, *values, fallback_value=0)
    
    def rpush(self, key: str, *values) -> int:
        """Push values to end of list, return list length or 0 on failure"""
        return self._execute_with_fallback('rpush', key, *values, fallback_value=0)
    
    def lrange(self, key: str, start: int, end: int) -> List:
        """Get list elements from start to end, return list or empty list on failure"""
        return self._execute_with_fallback('lrange', key, start, end, fallback_value=[])
    
    def ltrim(self, key: str, start: int, end: int) -> bool:
        """Trim list to specified range, return True on success or False on failure"""
        return self._execute_with_fallback('ltrim', key, start, end, fallback_value=False)
    
    # Expiration operations
    def expire(self, key: str, seconds: int) -> bool:
        """Set key expiration, return True if key exists or False on failure"""
        return self._execute_with_fallback('expire', key, seconds, fallback_value=False)
    
    def ttl(self, key: str) -> int:
        """Get key time-to-live in seconds, return -2 if key doesn't exist, -1 if no expiry, or seconds"""
        return self._execute_with_fallback('ttl', key, fallback_value=-2)
    
    # Pub/Sub operations
    def publish(self, channel: str, message: str) -> int:
        """Publish message to channel, return number of subscribers or 0 on failure"""
        return self._execute_with_fallback('publish', channel, message, fallback_value=0)

    # Transaction support
    def pipeline(self, transaction: bool = True) -> 'ResilientRedisPipeline':
        """Return a pipeline object"""
        client = self._get_client()
        if not client:
            return ResilientRedisPipeline(None, self)

        try:
            redis_pipeline = client.pipeline(transaction=transaction)
            return ResilientRedisPipeline(redis_pipeline, self)
        except Exception as e:
            logger.error(f"Error creating pipeline: {e}")
            return ResilientRedisPipeline(None, self)


class ResilientRedisPipeline:
    """A wrapper for Redis pipeline with fallback capabilities"""
    
    def __init__(self, pipeline, parent_client: ResilientRedisClient):
        self.pipeline = pipeline
        self.parent = parent_client
        self.commands = []
        self.fallback_values = []
    
    def __getattr__(self, name: str):
        """Capture Redis commands to the pipeline"""
        def wrapper(*args, **kwargs):
            self.commands.append((name, args, kwargs))
            self.fallback_values.append(self._get_fallback_value(name))
            if self.pipeline:
                method = getattr(self.pipeline, name)
                method(*args, **kwargs)
            return self
        return wrapper
    
    def _get_fallback_value(self, command_name: str) -> Any:
        """Return appropriate fallback value based on command type"""
        if command_name in ['sadd', 'srem', 'scard', 'incr', 'decr', 'lpush', 'rpush']:
            return 0
        elif command_name in ['get', 'hget']:
            return None
        elif command_name in ['keys', 'lrange', 'hmget']:
            return []
        elif command_name in ['smembers']:
            return set()
        elif command_name in ['hgetall']:
            return {}
        elif command_name in ['sismember', 'set', 'expire', 'ltrim']:
            return False
        else:
            return None
    
    def execute(self) -> List:
        """Execute pipeline commands with fallback handling"""
        if not self.pipeline:
            logger.warning("Redis unavailable for pipeline, using fallback values")
            return self.fallback_values
        
        try:
            return self.pipeline.execute()
        except Exception as e:
            logger.error(f"Pipeline execution failed: {e}")
            return self.fallback_values


class ResilientAsyncRedisClient:
    """Async version of the resilient Redis client wrapper"""
    
    def __init__(
        self,
        primary_url: str = REDIS_URL,
        fallback_url: str = FALLBACK_REDIS_URL,
        default_ttl: int = 86400,
        max_retries: int = 2
    ):
        self.primary_url = primary_url
        self.fallback_url = fallback_url
        self.default_ttl = default_ttl
        self.max_retries = max_retries
        self._primary_client = None
        self._fallback_client = None
        self._primary_available = False
        self._fallback_available = False
    
    async def _init_connections(self) -> None:
        """Initialize Redis connections"""
        # Try primary connection
        if not self._primary_client:
            try:
                self._primary_client = AsyncRedis.from_url(
                    self.primary_url,
                    encoding="utf-8",
                    decode_responses=True,
                    socket_timeout=2.0,
                    socket_connect_timeout=2.0,
                    retry_on_timeout=True
                )
                await self._primary_client.ping()
                self._primary_available = True
                logger.info("Connected to primary async Redis successfully")
            except Exception as e:
                logger.warning(f"Primary async Redis connection failed: {e}")
                self._primary_available = False
        
        # Try fallback connection if primary failed
        if not self._primary_available and not self._fallback_client:
            try:
                self._fallback_client = AsyncRedis.from_url(
                    self.fallback_url,
                    encoding="utf-8",
                    decode_responses=True,
                    socket_timeout=2.0,
                    socket_connect_timeout=2.0,
                    retry_on_timeout=True
                )
                await self._fallback_client.ping()
                self._fallback_available = True
                logger.info("Connected to fallback async Redis successfully")
            except Exception as e:
                logger.warning(f"Fallback async Redis connection failed: {e}")
                self._fallback_available = False
    
    async def _get_client(self) -> Optional[AsyncRedis]:
        """Get available Redis client or None if all are unavailable"""
        await self._init_connections()
        
        # Check primary client
        if self._primary_available:
            try:
                await self._primary_client.ping()
                return self._primary_client
            except Exception:
                logger.warning("Primary async Redis is no longer available")
                self._primary_available = False
        
        # Check fallback client
        if self._fallback_available:
            try:
                await self._fallback_client.ping()
                return self._fallback_client
            except Exception:
                logger.warning("Fallback async Redis is no longer available")
                self._fallback_available = False
        
        # Try to reconnect if both are unavailable
        await self._init_connections()
        
        # Return available client or None
        if self._primary_available:
            return self._primary_client
        elif self._fallback_available:
            return self._fallback_client
        else:
            return None
    
    async def _execute_with_fallback(self, method: str, *args, fallback_value: Any = None, **kwargs) -> Any:
        """Execute Redis command with fallback value on failure"""
        client = await self._get_client()
        
        if not client:
            logger.warning(f"Async Redis unavailable for {method}, using fallback value")
            return fallback_value
        
        for attempt in range(self.max_retries):
            try:
                redis_method = getattr(client, method)
                return await redis_method(*args, **kwargs)
            except Exception as e:
                logger.warning(f"Async Redis error on attempt {attempt+1}/{self.max_retries}: {e}")
                if attempt == self.max_retries - 1:
                    logger.error(f"All async Redis attempts failed for {method}, using fallback value")
                    return fallback_value
                # Try to get a working client for next attempt
                client = await self._get_client()
                if not client:
                    logger.error("No async Redis connection available, using fallback value")
                    return fallback_value
    
    # Implement the Redis methods you need for async usage
    async def get(self, key: str) -> Optional[str]:
        """Get value for key or None on failure"""
        return await self._execute_with_fallback('get', key, fallback_value=None)
    
    async def set(self, key: str, value: str, ex: int = None, nx: bool = False) -> bool:
        """Set key to value with optional expiration, return True on success or False on failure"""
        return await self._execute_with_fallback('set', key, value, ex=ex or self.default_ttl, nx=nx, fallback_value=False)
    
    async def delete(self, *keys) -> int:
        """Delete keys, return number of keys deleted or 0 on failure"""
        return await self._execute_with_fallback('delete', *keys, fallback_value=0)
    
    async def incr(self, key: str) -> int:
        """Increment value, return new value or 1 on failure"""
        return await self._execute_with_fallback('incr', key, fallback_value=1)
    
    async def sadd(self, key: str, *values) -> int:
        """Add values to set, return number of items added or 0 on failure"""
        return await self._execute_with_fallback('sadd', key, *values, fallback_value=0)
    
    async def srem(self, key: str, *values) -> int:
        """Remove values from set, return number of items removed or 0 on failure"""
        return await self._execute_with_fallback('srem', key, *values, fallback_value=0)
    
    async def scard(self, key: str) -> int:
        """Return set cardinality (number of elements) or 0 on failure"""
        return await self._execute_with_fallback('scard', key, fallback_value=0)
    
    async def smembers(self, key: str) -> Set:
        """Return all members of the set or empty set on failure"""
        return await self._execute_with_fallback('smembers', key, fallback_value=set())
    
    async def sismember(self, key: str, value) -> bool:
        """Return True if value is in set, False otherwise or on failure"""
        return await self._execute_with_fallback('sismember', key, value, fallback_value=False)
    
    async def close(self) -> None:
        """Close Redis connections"""
        if self._primary_client:
            await self._primary_client.close()
        if self._fallback_client:
            await self._fallback_client.close()


# Create global instances
redis_client = ResilientRedisClient()

# Function to get async Redis client
async def get_async_redis() -> ResilientAsyncRedisClient:
    client = ResilientAsyncRedisClient()
    await client._init_connections()
    return client