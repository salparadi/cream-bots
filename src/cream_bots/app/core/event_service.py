import ape
import asyncio
import degenbot
import eth_abi
import eth_account
from eth_utils.address import to_checksum_address
from hexbytes import HexBytes
from typing import TYPE_CHECKING, Union, Optional
import ujson

from .anvil_service import AnvilService

from ...config.constants import *
from ...config import helpers
from ...config.logging import logger

log = logger(__name__)

VERBOSE_EVENT_PROCESSING = True
GAS_LIMIT_MULTIPLIER = 1.2
APE_NETWORK = "base:mainnet-fork:foundry"


class EventService:
    def __init__(self, bot_state):
        self.bot_state = bot_state
        self.redis_client = self.bot_state.redis_client
        self.w3 = self.bot_state.w3
        self.anvil_service = AnvilService(bot_state)

        log.info(f"EventService initialized with app instance at {id(self.bot_state)}")

    async def process_uniswap_events(self):
        """
        Process events from the "cream_events" channel.

        This method subscribes to the "cream_events" channel using Redis pubsub and processes the received events.
        The events are processed based on their type (burn, mint, sync, swap) and the corresponding helper methods are called.
        The processed events are then added to the `pools_to_process` queue for further processing.

        Returns:
            None
        """
        pubsub = self.redis_client.pubsub()
        await pubsub.subscribe("cream_events")

        async def check_queue_size():
            while True:
                log.info(f"Current pools_to_process queue size: {self.bot_state.pools_to_process.qsize()}")
                await asyncio.sleep(60)  # Check every minute

        asyncio.create_task(check_queue_size())

        def process_burn_event(message: dict):
            event_address = to_checksum_address(message["params"]["result"]["address"])
            event_block = int(message["params"]["result"]["blockNumber"], 16)
            event_data = message["params"]["result"]["data"]

            v3_pool_helper: Optional[
                Union[degenbot.V3LiquidityPool, degenbot.LiquidityPool]
            ] = None

            for pool_manager in self.bot_state.pool_managers.values():
                try:
                    v3_pool_helper = pool_manager.get_pool(
                        pool_address=event_address,
                        silent=not VERBOSE_EVENT_UPDATES,
                        # WIP: use previous block state to avoid double-counting liquidity events
                        state_block=event_block - 1,
                    )
                except degenbot.exceptions.ManagerError:
                    continue
                else:
                    break

            if v3_pool_helper is None:
                # ignore events for unknown pools
                return

            if TYPE_CHECKING:
                assert isinstance(v3_pool_helper, degenbot.V3LiquidityPool)

            try:
                _, _, lower, upper = message["params"]["result"]["topics"]
                event_tick_lower = eth_abi.decode(["int24"], bytes.fromhex(lower[2:]))[
                    0
                ]
                event_tick_upper = eth_abi.decode(["int24"], bytes.fromhex(upper[2:]))[
                    0
                ]
                event_liquidity, _, _ = eth_abi.decode(
                    ["uint128", "uint256", "uint256"],
                    bytes.fromhex(event_data[2:]),
                )
            except KeyError:
                return
            else:
                if event_liquidity == 0:
                    return

                event_liquidity *= -1

                try:
                    v3_pool_helper.external_update(
                        update=degenbot.UniswapV3PoolExternalUpdate(
                            block_number=event_block,
                            liquidity_change=(
                                event_liquidity,
                                event_tick_lower,
                                event_tick_upper,
                            ),
                        ),
                    )
                    self.bot_state.snapshot.update_snapshot(
                        pool=event_address,
                        tick_bitmap=v3_pool_helper.tick_bitmap,
                        tick_data=v3_pool_helper.tick_data,
                    )
                # WIP: sys.exit to kill the bot on a failed assert
                # looking to fix "assert self.liquidity >= 0" throwing on some Burn events
                except AssertionError:
                    log.exception(f"(process_burn_event) AssertionError: {message}")

                    # Directly remove the pool from the pool manager
                    for pool_manager in self.bot_state.pool_managers.values():
                        try:
                            # Attempt to delete using the pool helper object
                            pool_manager.__delitem__(v3_pool_helper.address)
                            log.info(
                                f"Removed pool {v3_pool_helper.address} from its pool manager due to AssertionError."
                            )
                            break  # Exit the loop after successfully removing the pool
                        except KeyError:
                            # This exception will occur if the pool was not found in the current manager
                            continue  # Try the next pool manager if the pool was not found in this one
                except:
                    log.exception(f"(process_burn_event): {message}")
                else:
                    asyncio.create_task(
                        self.bot_state.pools_to_process.put(event_address)
                    )

        def process_mint_event(message: dict):
            event_address = to_checksum_address(message["params"]["result"]["address"])
            event_block = int(message["params"]["result"]["blockNumber"], 16)
            event_data = message["params"]["result"]["data"]

            v3_pool_helper: Optional[
                Union[degenbot.V3LiquidityPool, degenbot.LiquidityPool]
            ] = None
            for pool_manager in self.bot_state.pool_managers.values():
                try:
                    v3_pool_helper = pool_manager.get_pool(
                        pool_address=event_address,
                        silent=not VERBOSE_EVENT_UPDATES,
                        # WIP: use previous block state to avoid double-counting liquidity events
                        state_block=event_block - 1,
                    )
                except degenbot.exceptions.ManagerError:
                    continue
                else:
                    break

            if v3_pool_helper is None:
                # ignore events for unknown pools
                return

            if TYPE_CHECKING:
                assert isinstance(v3_pool_helper, degenbot.V3LiquidityPool)

            try:
                _, _, lower, upper = message["params"]["result"]["topics"]
                event_tick_lower = eth_abi.decode(["int24"], bytes.fromhex(lower[2:]))[
                    0
                ]
                event_tick_upper = eth_abi.decode(["int24"], bytes.fromhex(upper[2:]))[
                    0
                ]
                _, event_liquidity, _, _ = eth_abi.decode(
                    ["address", "uint128", "uint256", "uint256"],
                    bytes.fromhex(event_data[2:]),
                )
            except KeyError:
                return
            else:
                if event_liquidity == 0:
                    return

                try:
                    v3_pool_helper.external_update(
                        update=degenbot.UniswapV3PoolExternalUpdate(
                            block_number=event_block,
                            liquidity_change=(
                                event_liquidity,
                                event_tick_lower,
                                event_tick_upper,
                            ),
                        ),
                    )
                    self.bot_state.snapshot.update_snapshot(
                        pool=event_address,
                        tick_bitmap=v3_pool_helper.tick_bitmap,
                        tick_data=v3_pool_helper.tick_data,
                    )

                except Exception as exc:
                    log.exception(f"(process_mint_event): {exc}")
                else:
                    asyncio.create_task(
                        self.bot_state.pools_to_process.put(event_address)
                    )

        def process_sync_event(message: dict):
            event_address = to_checksum_address(message["params"]["result"]["address"])
            event_block = int(message["params"]["result"]["blockNumber"], 16)
            event_data = message["params"]["result"]["data"]

            event_reserves = eth_abi.decode(
                ["uint112", "uint112"],
                bytes.fromhex(event_data[2:]),
            )

            v2_pool_helper = None
            for pool_manager in self.bot_state.pool_managers.values():
                try:
                    v2_pool_helper = pool_manager.get_pool(
                        pool_address=event_address,
                        silent=not VERBOSE_EVENT_UPDATES,
                    )
                except degenbot.exceptions.ManagerError:
                    continue
                else:
                    break

            if v2_pool_helper is None:
                # ignore events for unknown pools
                return

            reserves0, reserves1 = event_reserves

            if TYPE_CHECKING:
                assert isinstance(v2_pool_helper, degenbot.LiquidityPool)

            try:
                v2_pool_helper.update_reserves(
                    external_token0_reserves=reserves0,
                    external_token1_reserves=reserves1,
                    silent=not VERBOSE_EVENT_UPDATES,
                    print_reserves=False,
                    update_block=event_block,
                )
            except degenbot.exceptions.ExternalUpdateError:
                pass
            except Exception as exc:
                log.exception(f"(process_sync_event): {exc}")
            else:
                asyncio.create_task(self.bot_state.pools_to_process.put(event_address))

        def process_swap_event(message: dict):
            event_address = to_checksum_address(message["params"]["result"]["address"])
            event_block = int(message["params"]["result"]["blockNumber"], 16)
            event_data = message["params"]["result"]["data"]

            (
                _,
                _,
                event_sqrt_price_x96,
                event_liquidity,
                event_tick,
            ) = eth_abi.decode(
                [
                    "int256",
                    "int256",
                    "uint160",
                    "uint128",
                    "int24",
                ],
                bytes.fromhex(event_data[2:]),
            )

            v3_pool_helper: Optional[
                Union[degenbot.V3LiquidityPool, degenbot.LiquidityPool]
            ] = None
            for pool_manager in self.bot_state.pool_managers.values():
                try:
                    v3_pool_helper = pool_manager.get_pool(
                        pool_address=event_address,
                        silent=not VERBOSE_EVENT_UPDATES,
                        state_block=event_block - 1,
                    )
                except degenbot.exceptions.ManagerError:
                    continue
                else:
                    break

            if v3_pool_helper is None:
                # ignore events for unknown pools
                return

            if TYPE_CHECKING:
                assert isinstance(v3_pool_helper, degenbot.V3LiquidityPool)

            try:
                v3_pool_helper.external_update(
                    update=degenbot.UniswapV3PoolExternalUpdate(
                        block_number=event_block,
                        liquidity=event_liquidity,
                        tick=event_tick,
                        sqrt_price_x96=event_sqrt_price_x96,
                    ),
                )
            except degenbot.exceptions.ExternalUpdateError:
                pass
            except Exception as exc:
                log.exception(f"(process_swap_event): {exc}")
            else:
                asyncio.create_task(self.bot_state.pools_to_process.put(event_address))

        def process_new_v2_pool_event(message: dict):
            event_address = to_checksum_address(message["params"]["result"]["address"])
            event_block = int(message["params"]["result"]["blockNumber"], 16)
            event_data = message["params"]["result"]["data"]

            pool_address, _ = eth_abi.decode(
                [
                    "address",
                    "uint256",
                ],
                bytes.fromhex(event_data[2:]),
            )

            # Determine if Pool Manager for this factory address already exists
            if event_address not in self.bot_state.pool_managers:
                try:
                    self.bot_state.pool_managers[event_address] = (
                        degenbot.UniswapV2LiquidityPoolManager(
                            factory_address=event_address
                        )
                    )
                except Exception as exc:
                    log.error(f"(process_new_v2_pool_event) {exc}")
                    return

            pool_manager = self.bot_state.pool_managers[event_address]

            # Add new pool to manager
            try:
                new_pool_helper = pool_manager.get_pool(
                    pool_address=pool_address,
                    state_block=event_block,
                    silent=not VERBOSE_EVENT_UPDATES,
                )
            except Exception as exc:
                log.error(f"(process_new_v2_pool_event) (get_pool) {exc}")
                return
            else:
                log.info(
                    f"Created new V2 pool at block {event_block}: {new_pool_helper} @ {pool_address}"
                )

        def process_new_v3_pool_event(message: dict):
            event_address = to_checksum_address(message["params"]["result"]["address"])
            event_block = int(message["params"]["result"]["blockNumber"], 16)
            event_data = message["params"]["result"]["data"]

            _, pool_address = eth_abi.decode(
                types=("int24", "address"),
                data=bytes.fromhex(event_data[2:]),
            )

            # Determine if Pool Manager for this factory address already exists
            if event_address not in self.bot_state.pool_managers:
                try:
                    self.bot_state.pool_managers[event_address] = (
                        degenbot.UniswapV3LiquidityPoolManager(
                            factory_address=event_address
                        )
                    )
                except Exception as exc:
                    log.error(f"(process_new_v3_pool_event) {exc}")
                    return

            pool_manager = self.bot_state.pool_managers[event_address]

            # Add new pool to manager
            try:
                new_pool_helper = pool_manager.get_pool(
                    pool_address=pool_address,
                    state_block=event_block,
                    silent=not VERBOSE_EVENT_UPDATES,
                )
            except Exception as exc:
                log.error(f"(process_new_v3_pool_event) (get_pool) {exc}")
                return
            else:
                log.info(
                    f"Created new V3 pool at block {event_block}: {new_pool_helper} @ {pool_address}"
                )

        _EVENTS = {
            self.w3.keccak(
                text="Sync(uint112,uint112)",
            ).hex(): {
                "name": "Uniswap V2: SYNC",
                "process_func": process_sync_event,
            },
            self.w3.keccak(
                text="Mint(address,address,int24,int24,uint128,uint256,uint256)"
            ).hex(): {
                "name": "Uniswap V3: MINT",
                "process_func": process_mint_event,
            },
            self.w3.keccak(
                text="Burn(address,int24,int24,uint128,uint256,uint256)"
            ).hex(): {
                "name": "Uniswap V3: BURN",
                "process_func": process_burn_event,
            },
            self.w3.keccak(
                text="Swap(address,address,int256,int256,uint160,uint128,int24)"
            ).hex(): {
                "name": "Uniswap V3: SWAP",
                "process_func": process_swap_event,
            },
            self.w3.keccak(text="PairCreated(address,address,address,uint256)").hex(): {
                "name": "Uniswap V2: POOL CREATED",
                "process_func": process_new_v2_pool_event,
            },
            self.w3.keccak(
                text="PoolCreated(address,address,uint24,int24,address)"
            ).hex(): {
                "name": "Uniswap V3: POOL CREATED",
                "process_func": process_new_v3_pool_event,
            },
        }

        # Get events from the redis queue
        while True:
            try:
                async for message in pubsub.listen():
                    if message["type"] == "message":
                        
                        # set the first event
                        if self.bot_state.first_event is None:
                            self.bot_state.first_event = message["params"]["result"]["blockNumber"]
                            log.info(f"First event: {self.bot_state.first_event}")

                        message_data = message.get("data")
                        if message_data:
                            event = ujson.loads(message_data.decode("utf-8"))
                            event_block = event["params"]["result"]["blockNumber"]
                            topic0: str = event["params"]["result"]["topics"][0]

                            try:
                                process_func = _EVENTS[topic0]["process_func"]
                            except KeyError:
                                # handle the KeyError if topic0 is not in process_func_map
                                continue
                            except IndexError:
                                # ignore anonymous events (no topic0)
                                continue
                            except Exception as exc:
                                log.exception(
                                    f"(process_uniswap_events) Unexpected error: {exc}"
                                )
                                continue
                            else:
                                if TYPE_CHECKING:
                                    assert callable(process_func)
                                process_func(event)
                                if VERBOSE_EVENT_PROCESSING:
                                    log.info(
                                        f"processed {_EVENTS[topic0]['name']} event @ {int(event_block,16)}"
                                    )
                                continue

            except asyncio.CancelledError:
                return
            except Exception as exc:
                log.exception(f"(process_uniswap_events) Redis subscription error: {exc}")
                log.info("process_uniswap_events reconnecting...")
                await asyncio.sleep(1)  # wait a bit before reconnecting
