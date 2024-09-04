import asyncio
import degenbot
from eth_utils.address import to_checksum_address
import os
from pathlib import Path
import time
from tqdm import tqdm
from typing import TYPE_CHECKING, Optional, Union
import ujson

from .arbitrage_service import ArbDetails
from ...config.constants import *
from ...config.helpers import get_redis_value
from ...config.logging import logger

log = logger(__name__)


class PoolService:
    def __init__(self, bot_state):
        self.bot_state = bot_state
        self.redis_client = self.bot_state.redis_client

        log.info(f"PoolService initialized with app instance at {id(self.bot_state)}")

    async def create_pool_managers(self):
        """
        Creates pool managers for different versions of Uniswap liquidity pools based on the chain data.

        This method retrieves the chain data from the bot state and creates pool managers for each version
        of Uniswap liquidity pools specified in the chain data. The pool managers are then stored in the
        `pool_managers` dictionary of the bot state.

        Returns:
            None
        """
        chain_data = self.bot_state.chain_data

        self.app_state = await get_redis_value(self.redis_client, "app_state")
        self.average_blocktime = self.app_state.get("average_blocktime")
        self.first_event = self.app_state.get("first_event")

        log.info(f"First event: {self.first_event}")

        if not chain_data:
            log.error(
                f"No chain info available for chain_id {self.bot_state.chain_name}"
            )
            return

        while not self.first_event:
            await asyncio.sleep(1)

        snapshot = self.bot_state.snapshot
        factories = chain_data.get("factories")

        snapshot.fetch_new_liquidity_events(self.first_event - 1)

        for version, factories in factories.items():
            for exchange_name, factory_info in factories.items():
                factory_address = factory_info.get("factory_address")

                if version == "v2":
                    v2_pool_manager = degenbot.UniswapV2LiquidityPoolManager(
                        factory_address=factory_address
                    )
                    self.bot_state.pool_managers[factory_address] = v2_pool_manager
                    log.info(
                        f"{self.bot_state.chain_name} / Created {version} pool manager for {exchange_name}."
                    )

                elif version == "v3":
                    v3_pool_manager = degenbot.UniswapV3LiquidityPoolManager(
                        factory_address=factory_address,
                        snapshot=snapshot,
                    )
                    self.bot_state.pool_managers[factory_address] = v3_pool_manager
                    log.info(
                        f"{self.bot_state.chain_name} / Created {version} pool manager for {exchange_name}."
                    )

        self.bot_state.pool_managers_ready = True

    async def load_pools(self):
        chain_data = self.bot_state.chain_data
        chain_name = self.bot_state.chain_name
        pool_managers = self.bot_state.pool_managers
        v2_factories = chain_data.get("factories").get("v2")
        v3_factories = chain_data.get("factories").get("v3")

        # print(Path(__file__).resolve())
        # print(Path(__file__).resolve().parent)
        # print(Path(__file__).resolve().parent.parent)
        # print(Path(__file__).resolve().parent.parent.parent)

        data_dir = Path(__file__).resolve().parent.parent.parent / "data" / chain_name

        lp_filepaths = [
            data_dir / f"{chain_name}_{factory_name}_v{version}.json"
            for version, factories in [("2", v2_factories), ("3", v3_factories)]
            for factory_name in factories.keys()
        ]

        # Identify all liquidity pools
        liquidity_pool_data = {}
        for lp_filename in lp_filepaths:
            with open(lp_filename, encoding="utf-8") as file:
                for pool in ujson.load(file):
                    if (
                        pool_address := pool["pool_address"]
                    ) in self.bot_state.blacklists["pools"]:
                        continue
                    if pool["token0"] in self.bot_state.blacklists["pools"]:
                        continue
                    if pool["token1"] in self.bot_state.blacklists["pools"]:
                        continue
                    liquidity_pool_data[pool_address] = pool
        log.info(f"Found {len(liquidity_pool_data)} pools")

        # This dictionary stores file paths
        arb_file_paths = {
            "arb_paths_2": data_dir / f"{chain_name}_arb_paths_2.json",
            "arb_paths_3": data_dir / f"{chain_name}_arb_paths_3.json",
        }

        # This list will store the arbitrage paths
        arb_paths = []

        # Iterate over the values of the dictionary (file paths)
        for arb_file_path in arb_file_paths.values():
            with arb_file_path.open(encoding="utf-8") as file:
                arb_data = ujson.load(file)
                for arb_id, arb in arb_data.items():
                    passed_checks = True
                    if arb_id in self.bot_state.blacklists["arbs"]:
                        passed_checks = False

                    for pool_address in arb.get("path", []):
                        if not liquidity_pool_data.get(pool_address):
                            passed_checks = False

                    if passed_checks:
                        arb_paths.append(arb)

        log.info(f"Found {len(arb_paths)} arb paths")

        # Identify all unique pool addresses in arb paths
        unique_pool_addresses = {
            pool_address
            for arb in arb_paths
            for pool_address in arb["path"]
            if liquidity_pool_data.get(pool_address)
        }
        log.info(f"Found {len(unique_pool_addresses)} unique pools")

        # Identify all unique tokens in the liquidity pools
        unique_tokens = (
            # all token0 addresses
            {
                token_address
                for arb in arb_paths
                for pool_address in arb.get("path")
                for pool_dict in arb.get("pools").values()
                if (token_address := pool_dict.get("token0"))
                if token_address not in self.bot_state.blacklists["tokens"]
                if liquidity_pool_data.get(pool_address)
            }
            |
            # all token1 addresses
            {
                token_address
                for arb in arb_paths
                for pool_address in arb.get("path")
                for pool_dict in arb.get("pools").values()
                if (token_address := pool_dict.get("token1"))
                if token_address not in self.bot_state.blacklists["tokens"]
                if liquidity_pool_data.get(pool_address)
            }
        )
        log.info(f"Found {len(unique_tokens)} unique tokens")

        start = time.perf_counter()

        # Sleep if the event watcher is not running
        while not self.first_event:
            await asyncio.sleep(self.average_blocktime)

        # TEST trim to make the bot load fast.
        # unique_pool_addresses = set(list(unique_pool_addresses)[:100])

        # Add the liquidity pools to the pool managers
        for pool_address in tqdm(unique_pool_addresses):
            await asyncio.sleep(0)

            pool_type: str = liquidity_pool_data[pool_address]["type"]
            pool_exchange: str = liquidity_pool_data[pool_address]["exchange"]

            pool_helper: Optional[
                Union[
                    degenbot.LiquidityPool,
                    degenbot.V3LiquidityPool,
                ]
            ] = None

            if pool_type == "UniswapV2":
                try:
                    pool_manager = pool_managers[
                        v2_factories[pool_exchange]["factory_address"]
                    ]

                    pool_helper = pool_manager.get_pool(
                        pool_address=pool_address,
                        silent=True,
                        update_method="external",
                        state_block=self.first_event - 1,
                    )

                except degenbot.exceptions.ManagerError as exc:
                    log.error(exc)
                    continue

            elif pool_type == "UniswapV3":
                try:
                    pool_manager = pool_managers[
                        v3_factories[pool_exchange]["factory_address"]
                    ]

                    pool_helper = pool_manager.get_pool(
                        pool_address=pool_address,
                        silent=True,
                        state_block=self.first_event - 1,
                        v3liquiditypool_kwargs={
                            "fee": liquidity_pool_data[pool_address]["fee"]
                        },
                    )

                except degenbot.exceptions.ManagerError as exc:
                    log.error(exc)
                    continue

            else:
                raise Exception(f"Could not identify pool type! {pool_type=}")

            if isinstance(pool_helper, degenbot.V3LiquidityPool):
                assert pool_helper.sparse_bitmap == False

            if TYPE_CHECKING:
                assert isinstance(
                    pool_helper,
                    (
                        degenbot.LiquidityPool,
                        degenbot.V3LiquidityPool,
                    ),
                )

        log.info(
            f"Created {len(self.bot_state.all_pools)} liquidity pool helpers in {time.perf_counter() - start:.2f}s"
        )

        degenbot_weth = degenbot.Erc20Token(chain_data.get("wrapped_token"))

        blacklisted_arbs = self.bot_state.blacklists["arbs"]
        all_pools = self.bot_state.all_pools

        for arb in tqdm(arb_paths):
            await asyncio.sleep(0)
            arb_id = arb.get("id")
            
            # Skip blacklisted arbs
            if arb_id in blacklisted_arbs:
                continue

            # Get pool objects for the arb path
            swap_pools = []
            for pool_address in arb["path"]:
                pool_obj = all_pools.get(pool_address)
                if not pool_obj:
                    break
                swap_pools.append(pool_obj)

            # Skip if not all pools are available
            if len(swap_pools) != len(arb["path"]):
                continue

            self.bot_state.all_arbs[arb_id] = ArbDetails(
                lp_cycle=degenbot.UniswapLpCycle(
                    input_token=degenbot_weth,
                    swap_pools=swap_pools,
                    max_input=MAX_INPUT,
                    id=arb_id,
                ),
                status="load",
            )
        log.info(f"Built {len(self.bot_state.all_arbs)} cycle arb helpers")
        log.info("Arb loading complete")

        self.bot_state.pools_loaded = True
