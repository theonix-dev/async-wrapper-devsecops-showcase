import os
import json
import logging
import asyncio
from datetime import datetime
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

class DatabaseManager:
    """
    Manages user whitelists, search trackers, and parsed offer states.
    Uses the Proxy Pattern / Adapter Pattern to prioritize high-speed Redis caching for parsed listings,
    with an automated 14-day Time-To-Live (TTL) key policy, falling back to local JSON atomic storage
    if the Redis container goes offline.
    """
    
    def __init__(self, db_file: str, redis_url: Optional[str] = None):
        self.db_file = db_file
        self.redis_url = redis_url
        self.lock = asyncio.Lock()
        self.redis = None
        self.data: Dict[str, Any] = {
            "users": {},
            "tags": [],
            "parsed_items": {}  # local fallback cache (offer_id -> timestamp)
        }

    async def init_db(self) -> None:
        """Initializes database files and establishes connection to the Redis cache cluster."""
        if self.redis_url:
            try:
                import redis.asyncio as aioredis
                self.redis = aioredis.from_url(self.redis_url, decode_responses=True)
                await self.redis.ping()
                logger.info("Connected to Redis cache successfully.")
            except Exception as e:
                logger.error(f"Redis cache connection failed: {e}. Defaulting to JSON fallback.")
                self.redis = None

        async with self.lock:
            # Read local db.json config
            if os.path.exists(self.db_file):
                try:
                    with open(self.db_file, "r", encoding="utf-8") as f:
                        self.data = json.load(f)
                    logger.info("Local configuration loaded successfully.")
                except Exception as e:
                    logger.error(f"Local database read error: {e}. Creating new configuration.")
                    self._create_default_db()
            else:
                self._create_default_db()

    def _create_default_db(self) -> None:
        self.data = {"users": {}, "tags": [], "parsed_items": {}}

    async def is_item_parsed(self, offer_id: int) -> bool:
        """
        Queries whether the listing has been parsed previously.
        Prioritizes Redis (SISMEMBER or EXISTS lookup), falls back to local JSON cache list on failure.
        """
        offer_id_str = str(offer_id)
        if self.redis:
            try:
                exists = await self.redis.exists(f"olx:parsed:{offer_id_str}")
                return exists > 0
            except Exception as e:
                logger.error(f"Redis lookup error: {e}. Falling back to local database.")
        
        return offer_id_str in self.data["parsed_items"]

    async def mark_item_parsed(self, offer_id: int) -> None:
        """
        Marks a listing as parsed.
        Prioritizes Redis (SETEX with 14-day TTL), falls back to disk write operations on failure.
        """
        offer_id_str = str(offer_id)
        if self.redis:
            try:
                # Store in Redis with 14 days expiration (1,209,600 seconds)
                await self.redis.setex(f"olx:parsed:{offer_id_str}", 1209600, "1")
                # Remove from local database to keep local db.json size extremely small
                async with self.lock:
                    if offer_id_str in self.data["parsed_items"]:
                        del self.data["parsed_items"][offer_id_str]
                return
            except Exception as e:
                logger.error(f"Redis write error: {e}. Falling back to local database.")

        async with self.lock:
            self.data["parsed_items"][offer_id_str] = datetime.utcnow().isoformat()
            self._save_to_disk_sync()

    def _save_to_disk_sync(self) -> None:
        """Writes configuration to local disk atomically using a temp file to prevent corruption."""
        temp_file = f"{self.db_file}.tmp"
        try:
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
            os.replace(temp_file, self.db_file)
        except Exception as e:
            logger.error(f"Atomic file write failed: {e}")
            if os.path.exists(temp_file):
                os.remove(temp_file)
