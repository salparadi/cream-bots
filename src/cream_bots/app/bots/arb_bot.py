from asyncio import Queue
from aiohttp import ClientSession
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
import degenbot
import os
import redis.asyncio as redis
from typing import TYPE_CHECKING, Dict, List, Optional, Set
import web3

from degenbot.uniswap.v3_snapshot import (
    UniswapV3LiquiditySnapshot,
)
from degenbot.arbitrage.uniswap_lp_cycle import (
    ArbitrageCalculationResult,
    UniswapLpCycle,
)

from cream_chains import chain_data as cream_chains_data

from ..core.arbitrage_service import ArbitrageService
from ..core.blacklist_service import BlacklistService
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


@dataclass
class ArbBotState:
    aggregators: Optional[Dict] = None
    all_arbs: Dict[str, ArbDetails] = field(default_factory=dict)
    all_pools: degenbot.AllPools = field(default_factory=degenbot.AllPools)
    blacklists: Set[str] = field(default_factory=set)
    chain_id: Optional[int] = None
    chain_data: Optional[Dict] = None
    chain_name: Optional[str] = None
    executor: Optional[ProcessPoolExecutor] = None
    factories: Optional[Dict] = None
    http_session: Optional[ClientSession] = None
    http_uri: Optional[str] = None
    live: bool = False
    node: Optional[str] = None
    pool_managers: Optional[Dict] = None
    pools_to_process: Queue = field(default_factory=Queue)
    redis_client: redis.Redis = None
    routers: Optional[Dict] = None
    snapshot: Optional[UniswapV3LiquiditySnapshot] = None
    websocket_uri: Optional[str] = None
    w3: web3.main.Web3 = None


class ArbBot:
    def __init__(self, chain_name: str):
        """
        Initializes a new instance of the ArbBot class.

        Args:
            chain_name (str): The name of the chain.

        Attributes:
            chain_name (str): The name of the chain.
            bot_state (BotState): The state of the bot.
            blacklist_service (BlacklistService): The blacklist service used by the bot.
            exchange_service (ExchangeService): The exchange service used by the bot.
            pool_service (PoolService): The pool service used by the bot.
        """
        self.chain_name = chain_name
        self.bot_state = self.setup_bot_state()
        self.arbitrage_service = ArbitrageService(self.bot_state)
        self.blacklist_service = BlacklistService(self.bot_state)
        self.event_service = EventService(self.bot_state)
        self.exchange_service = ExchangeService(self.bot_state)
        self.pool_service = PoolService(self.bot_state)

    def setup_bot_state(self) -> ArbBotState:
        """
        Sets up the state of the bot by initializing various attributes and retrieving chain data.

        Returns:
            BotState: The initialized state of the bot.

        Raises:
            ValueError: If no chain data is found for the specified chain name.
        """
        chain_data = cream_chains_data.get(self.chain_name)
        chain_id = chain_data["chain_id"]
        w3 = web3.Web3(web3.WebsocketProvider(chain_data["websocket_uri"]))
        degenbot.set_web3(w3)

        if not chain_data:
            raise ValueError(f"No chain data found for {self.chain_name}")

        script_dir = os.path.dirname(os.path.abspath(__file__))
        data_dir = os.path.abspath(
            os.path.join(script_dir, "..", "..", "data", self.chain_name)
        )
        snapshot_filename = f"{self.chain_name}_v3_liquidity_snapshot.json"
        snapshot_filepath = os.path.join(data_dir, snapshot_filename)
        snapshot = UniswapV3LiquiditySnapshot(snapshot_filepath)

        return ArbBotState(
            aggregators=chain_data["aggregators"],
            all_arbs={},
            all_pools=degenbot.AllPools(chain_id),
            blacklists={},
            chain_id=chain_id,
            chain_data=chain_data,
            chain_name=self.chain_name,
            executor=ProcessPoolExecutor(max_workers=8),
            factories=chain_data["factories"],
            http_session=ClientSession(),
            http_uri=chain_data["http_uri"],
            live=True,
            node=chain_data["node"],
            pool_managers={},
            pools_to_process=Queue(),
            redis_client=redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=0),
            routers=chain_data["routers"],
            snapshot=snapshot,
            websocket_uri=chain_data["websocket_uri"],
            w3=w3,
        )

    async def bootstrap(self):
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
        await self.bootstrap()
        await self.event_service.process_uniswap_events()
        await self.arbitrage_service.find_onchain_arbs()
