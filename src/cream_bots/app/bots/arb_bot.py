import asyncio
from asyncio import Queue
from aiohttp import ClientSession
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
import degenbot
import os
import redis.asyncio as redis
from typing import Dict, Optional, Set
import web3

from degenbot.uniswap.v3_snapshot import (
    UniswapV3LiquiditySnapshot,
)

from cream_chains import chain_data as cream_chains_data

from ..core.arbitrage_service import ArbitrageService
from ..core.blacklist_service import BlacklistService
from ..core.bootstrap_service import BootstrapService
from ..core.event_service import EventService
from ..core.exchange_service import ExchangeService
from ..core.pool_service import PoolService
from ...config.constants import REDIS_HOST, REDIS_PORT
from ...config.logging import logger

log = logger(__name__)


class ArbDetails:
    def __init__(self, lp_cycle: degenbot.UniswapLpCycle, status: str):
        self.lp_cycle = lp_cycle
        self.status = status


class ArbBotState:
    def __init__(self, chain_name: str, chain_data: Dict):
        self.aggregators: Optional[Dict] = chain_data.get("aggregators")
        self.all_arbs: Dict[str, ArbDetails] = {}
        self.all_pools: degenbot.AllPools = degenbot.AllPools(chain_data["chain_id"])
        self.blacklists: Set[str] = set()
        self.chain_id: int = chain_data["chain_id"]
        self.chain_data: Dict = chain_data
        self.chain_name: str = chain_name
        self.executor: ProcessPoolExecutor = ProcessPoolExecutor(max_workers=8)
        self.factories: Optional[Dict] = chain_data.get("factories")
        self.http_session: ClientSession = ClientSession()
        self.http_uri: str = chain_data["http_uri"]
        self.live: bool = False
        self.node: str = chain_data["node"]
        self.pool_managers: Dict = {}
        self.pools_to_process: Queue = Queue()
        self.redis_client: redis.Redis = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=0)
        self.routers: Optional[Dict] = chain_data.get("routers")
        self.snapshot: Optional[UniswapV3LiquiditySnapshot] = None
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


class ArbBot:
    def __init__(self, chain_name: str):
        """
        Initializes a new instance of the ArbBot class.

        Args:
            chain_name (str): The name of the chain.

        Attributes:
            chain_name (str): The name of the chain.
            arbitrage_service (ArbitrageService): The arbitrage service used by the bot.
            blacklist_service (BlacklistService): The blacklist service used by the bot.
            bot_state (BotState): The state of the bot.
            bootstrap_service (BootstrapService): The bootstrap service used by the bot.
            event_service (EventService): The event service used by the bot.
            exchange_service (ExchangeService): The exchange service used by the bot.
            pool_service (PoolService): The pool service used by the bot.
        """
        self.chain_name = chain_name
        chain_data = cream_chains_data.get(self.chain_name)
        if not chain_data:
            raise ValueError(f"No chain data found for {self.chain_name}")
        self.bot_state = ArbBotState(self.chain_name, chain_data)

        # Initialize web3
        degenbot.set_web3(self.bot_state.w3)

        self.arbitrage_service = ArbitrageService(self.bot_state)
        self.bootstrap_service = BootstrapService(self.bot_state)
        self.blacklist_service = BlacklistService(self.bot_state)
        self.event_service = EventService(self.bot_state)
        self.exchange_service = ExchangeService(self.bot_state)
        self.pool_service = PoolService(self.bot_state)

        # Initialize snapshot
        script_dir = os.path.dirname(os.path.abspath(__file__))
        data_dir = os.path.abspath(
            os.path.join(script_dir, "..", "..", "data", self.chain_name)
        )
        snapshot_filename = f"{self.chain_name}_v3_liquidity_snapshot.json"
        snapshot_filepath = os.path.join(data_dir, snapshot_filename)
        self.bot_state.snapshot = UniswapV3LiquiditySnapshot(snapshot_filepath)

    async def initialize(self):
        """
        Initializes the bot by adding deployments to the exchange service
        and creating pool managers for the bot state.

        This method should be called before starting the bot.
        """
        await self.exchange_service.add_deployments()
        await self.pool_service.create_pool_managers()
        await self.blacklist_service.load_blacklists()
        await self.pool_service.load_pools()

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
        # Start bootstrap service in the background
        bootstrap_task = asyncio.create_task(self.bootstrap_service.start())

        # Run initialization
        await self.initialize()

        # Start process_uniswap_events immediately in the background
        uniswap_events_task = asyncio.create_task(self.event_service.process_uniswap_events())

        # Wait for bot_state.live to become True
        while not self.bot_state.live:
            await asyncio.sleep(1)
            log.info("Waiting for bot_state.live to become True...")

        log.info("Bot state is live. Starting main processes.")

        # Start arbitrage process
        arbitrage_task = asyncio.create_task(self.arbitrage_service.find_onchain_arbs())

        await asyncio.gather(
            uniswap_events_task,
            arbitrage_task,
            bootstrap_task
        )
