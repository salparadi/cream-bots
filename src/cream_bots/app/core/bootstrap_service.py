import asyncio
import ujson
from ...config.logging import logger

log = logger(__name__)

class BootstrapService:
    def __init__(self, bot_state):
        self.bot_state = bot_state
        self.redis_client = bot_state.redis_client

    async def start(self):
        pubsub = self.redis_client.pubsub()
        await pubsub.subscribe("cream_app_state")
        
        log.info("BootstrapService started, waiting for messages...")
        
        try:
            async for message in pubsub.listen():
                if message["type"] == "message":
                    app_state = ujson.loads(message["data"].decode('utf-8'))
                    #log.info(f"Received app_state update: {app_state}")
                    self.update_bot_state(app_state)
        except asyncio.CancelledError:
            log.info("BootstrapService cancelled")
        except Exception as exc:
            log.error(f"Error in BootstrapService: {exc}")
        finally:
            await pubsub.unsubscribe("cream_app_state")

    def update_bot_state(self, app_state):
        relevant_keys = [
            "average_blocktime",
            "base_fee_last",
            "base_fee_next",
            "chain_id",
            "chain_name",
            "first_block",
            "first_event",
            "newest_block",
            "newest_block_timestamp",
            "live",
            "node",
            "watching_blocks",
            "watching_events",
        ]

        for key in relevant_keys:
            if key in app_state:
                #old_value = getattr(self.bot_state, key, None)
                setattr(self.bot_state, key, app_state[key])
                #log.info(f"Updated {key}: {old_value} -> {app_state[key]}")

        log.info(f"Updated bot state. Newest block: {self.bot_state.newest_block}")