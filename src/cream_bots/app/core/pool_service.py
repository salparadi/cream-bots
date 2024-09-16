import asyncio
import degenbot
from eth_utils.address import to_checksum_address
import os
from pathlib import Path
import time
from tqdm import tqdm
from typing import TYPE_CHECKING, Dict, List, Optional, Union
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

        if not self.bot_state.first_event:
            log.info("Waiting for first event...")
            await asyncio.sleep(1)
        
        log.info(f"First event: {self.bot_state.first_event}")

        if not chain_data:
            log.error(
                f"No chain info available for chain_id {self.bot_state.chain_name}"
            )
            return

        while not self.bot_state.first_event:
            log.info("Waiting for first event...")
            await asyncio.sleep(1)

        snapshot = self.bot_state.snapshot
        factories = chain_data.get("factories")

        snapshot.fetch_new_liquidity_events(self.bot_state.first_event - 1)

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
        v2_factories = chain_data.get("factories").get("v2")
        v3_factories = chain_data.get("factories").get("v3")

        data_dir = Path(__file__).resolve().parent.parent.parent / "data" / chain_name

        lp_filepaths = [
            data_dir / f"{chain_name}_{factory_name}_v{version}.json"
            for version, factories in [("2", v2_factories), ("3", v3_factories)]
            for factory_name in factories.keys()
        ]

        # Identify all liquidity pools
        self.liquidity_pool_data = {}
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
                    self.liquidity_pool_data[pool_address] = pool
        log.info(f"Found {len(self.liquidity_pool_data)} pools")

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
                        if not self.liquidity_pool_data.get(pool_address):
                            passed_checks = False

                    if passed_checks:
                        arb_paths.append(arb)

        log.info(f"Found {len(arb_paths)} arb paths")

        # Identify all unique pool addresses in arb paths
        self.unique_pool_addresses = {
            pool_address
            for arb in arb_paths
            for pool_address in arb["path"]
            if self.liquidity_pool_data.get(pool_address)
        }
        log.info(f"Found {len(self.unique_pool_addresses)} unique pools")

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
                if self.liquidity_pool_data.get(pool_address)
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
                if self.liquidity_pool_data.get(pool_address)
            }
        )
        log.info(f"Found {len(unique_tokens)} unique tokens")

        # Sleep if the event watcher is not running
        while not self.first_event:
            await asyncio.sleep(self.average_blocktime)

        # TEST trim to make the bot load fast.
        # unique_pool_addresses = set(list(unique_pool_addresses)[:100])

        # Create pool helpers
        await self.create_pool_helpers()

        degenbot_weth = degenbot.Erc20Token(chain_data.get("wrapped_token"))

        blacklisted_arbs = self.bot_state.blacklists["arbs"]
        all_pools = self.bot_state.all_pools

        for arb in tqdm(arb_paths):

            arb_id = arb.get("id")
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
        self.bot_state.live = True


    async def create_pool_helper(
        self,
        pool_address: str,
        pool_data: Dict,
        pool_managers: Dict,
        v2_factories: Dict,
        v3_factories: Dict,
        first_event: int
    ) -> Union[degenbot.LiquidityPool, degenbot.V3LiquidityPool, None]:
        pool_type = pool_data["type"]
        pool_exchange = pool_data["exchange"]

        if pool_type == "UniswapV2":
            try:
                pool_manager = pool_managers[v2_factories[pool_exchange]["factory_address"]]
                return pool_manager.get_pool(
                    pool_address=pool_address,
                    silent=True,
                    update_method="external",
                    state_block=first_event - 1,
                )
            except degenbot.exceptions.ManagerError as exc:
                log.error(exc)
                return None

        elif pool_type == "UniswapV3":
            try:
                pool_manager = pool_managers[v3_factories[pool_exchange]["factory_address"]]
                return pool_manager.get_pool(
                    pool_address=pool_address,
                    silent=True,
                    state_block=first_event - 1,
                    v3liquiditypool_kwargs={"fee": pool_data["fee"]},
                )
            except degenbot.exceptions.ManagerError as exc:
                log.error(exc)
                return None

        else:
            log.error(f"Could not identify pool type! {pool_type=}")
            return None
    

    async def create_pool_helpers(self):
        start = time.perf_counter()
        total_pools = len(self.unique_pool_addresses)
        log.info(f"Starting to create {total_pools} pool helpers")

        v2_pools = 0
        v3_pools = 0

        with tqdm(total=total_pools, desc="Creating pool helpers", unit="pool") as pbar:
            for pool_address in self.unique_pool_addresses:
                helper = await self.create_pool_helper(
                    pool_address,
                    self.liquidity_pool_data[pool_address],
                    self.bot_state.pool_managers,
                    self.bot_state.chain_data["factories"]["v2"],
                    self.bot_state.chain_data["factories"]["v3"],
                    self.first_event
                )
                if helper is not None:
                    if isinstance(helper, degenbot.V3LiquidityPool):
                        v3_pools += 1
                    else:
                        v2_pools += 1
                pbar.update(1)

        duration = time.perf_counter() - start
        total_pools_created = v2_pools + v3_pools
        log.info(f"Created {total_pools_created} liquidity pool helpers in {duration:.2f}s")
        log.info(f"V3 pools: {v3_pools}, V2 pools: {v2_pools}")