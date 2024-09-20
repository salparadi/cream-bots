import asyncio
from aiohttp import ClientSession
import degenbot
import redis.asyncio as redis
from typing import Dict, Optional, Set
import web3

from cream_chains import chain_data as cream_chains_data

from .callback_transaction_service import CallbackTransactionService
from ...core.bootstrap_service import BootstrapService
from ....config.constants import REDIS_HOST, REDIS_PORT
from ....config.logging import logger

log = logger(__name__)

class CallbackBotState:
    def __init__(self, chain_name: str, chain_data: Dict):
        self.aggregators: Optional[Dict] = chain_data.get("aggregators")
        self.chain_id: int = chain_data["chain_id"]
        self.chain_data: Dict = chain_data
        self.chain_name: str = chain_name
        self.failed_transactions: Set[str] = set()
        self.http_session: ClientSession = ClientSession()
        self.http_uri: str = chain_data["http_uri"]
        self.live: bool = False
        self.node: str = chain_data["node"]
        self.redis_client: redis.Redis = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=0)
        self.routers: Optional[Dict] = chain_data.get("routers")
        self.websocket_uri: str = chain_data["websocket_uri"]
        self.w3: web3.Web3 = web3.Web3(web3.WebsocketProvider(chain_data["websocket_uri"]))

        # Attributes from external app_state
        self.average_blocktime: Optional[float] = None
        self.base_fee_last: Optional[int] = None
        self.base_fee_next: Optional[int] = None
        self.first_block: Optional[int] = None
        self.first_event: Optional[int] = None
        self.newest_block: Optional[int] = None
        self.newest_block_timestamp: Optional[int] = None
        self.watching_blocks: bool = False
        self.watching_events: bool = False

class CallbackBot:
    def __init__(self, chain_name: str):
        self.chain_name = chain_name
        chain_data = cream_chains_data.get(self.chain_name)
        if not chain_data:
            raise ValueError(f"No chain data found for {self.chain_name}")
        self.bot_state = CallbackBotState(chain_name, chain_data)
        self.transaction_service = CallbackTransactionService(self.bot_state)
        self.bootstrap_service = BootstrapService(self.bot_state)

    async def close(self):
        if self.bot_state.redis_client:
            await self.bot_state.redis_client.aclose()
        if self.bot_state.http_session:
            await self.bot_state.http_session.close()
        log.info("Resources closed.")

    async def run(self):
        """
        Runs the bot by starting the event loop and running the main loop.
        """        
        async def debug_print_state():
            while True:
                log.info(f"Current bot state: newest_block={self.bot_state.newest_block}, live={self.bot_state.live}")
                await asyncio.sleep(10)  # Print every 10 seconds

        await asyncio.gather(
            self.bootstrap_service.start(),
            self.transaction_service.process_pending_transactions(),
            #debug_print_state()
        )