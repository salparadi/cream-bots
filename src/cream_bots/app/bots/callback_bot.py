from aiohttp import ClientSession
from dataclasses import dataclass
import degenbot
import os
import redis.asyncio as redis
from typing import TYPE_CHECKING, Dict, Optional
import web3

from cream_chains import chain_data as cream_chains_data

from ..core.event_service import EventService
from ...config.constants import REDIS_HOST, REDIS_PORT
from ...config.logging import logger

log = logger(__name__)


@dataclass
class CallbackBotState:
    aggregators: Optional[Dict] = None
    chain_id: Optional[int] = None
    chain_data: Optional[Dict] = None
    chain_name: Optional[str] = None
    http_session: Optional[ClientSession] = None
    http_uri: Optional[str] = None
    live: bool = False
    node: Optional[str] = None
    redis_client: redis.Redis = None
    routers: Optional[Dict] = None
    websocket_uri: Optional[str] = None
    w3: web3.main.Web3 = None


class CallbackBot:
    def __init__(self, chain_name: str):
        """
        Initializes a new instance of the SniperBot class.

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
        self.event_service = EventService(self.bot_state)

    def setup_bot_state(self) -> CallbackBotState:
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

        return CallbackBotState(
            aggregators=chain_data["aggregators"],
            chain_id=chain_id,
            chain_data=chain_data,
            chain_name=self.chain_name,
            http_session=ClientSession(),
            http_uri=chain_data["http_uri"],
            live=True,
            node=chain_data["node"],
            redis_client=redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=0),
            websocket_uri=chain_data["websocket_uri"],
            w3=w3,
        )

    async def bootstrap(self):
        """
        Initializes the bot by adding deployments to the exchange service
        and creating pool managers for the bot state.

        This method should be called before starting the bot.
        """
        pass

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
        await self.event_service.process_aggregator_callback_events()
