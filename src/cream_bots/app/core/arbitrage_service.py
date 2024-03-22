import asyncio
import degenbot
from eth_utils.address import to_checksum_address
from hexbytes import HexBytes
from typing import TYPE_CHECKING, Dict, List, Set

import web3

from degenbot.arbitrage.uniswap_lp_cycle import (
    ArbitrageCalculationResult,
    UniswapLpCycle,
)
from degenbot.uniswap.v3_dataclasses import (
    UniswapV3PoolExternalUpdate,
)
from degenbot.uniswap.v3_snapshot import (
    UniswapV3LiquiditySnapshot,
)

from ...config.logging import logger

log = logger(__name__)


class ArbDetails:
    def __init__(self, lp_cycle: degenbot.UniswapLpCycle, status: str):
        self.lp_cycle = lp_cycle
        self.status = status


class ArbitrageService:
    def __init__(self):
        self.websocket_uri = self.app_state.redis_client.get("websocket_uri")

        log.info(
            f"ArbitrageService initialized with app instance at {id(self.app_state)}"
        )

    async def process_backrun_arbs(
        self,
        arb_helpers: List[degenbot.UniswapLpCycle],
        override_state,
        pending_transaction,
    ):
        all_arbs = self.app_state.all_arbs
        http_session = self.app_state.http_session
        executor = self.app_state.executor

        transaction_hash = pending_transaction["hash"]

        try:
            raw_transaction = self.w3.eth.get_raw_transaction(transaction_hash)
        except web3.exceptions.TransactionNotFound as e:
            log.error(f"(process_backrun_arbs) (TransactionNotFound) {e}")
        else:
            num_arbs = len(arb_helpers)
            log.info(f"{num_arbs} arb(s) affected by {transaction_hash}")

            # No arbs affected, quit
            if num_arbs == 0:
                return

            """
            # Display the arb details
            for arb_helper in arb_helpers:
                logger.info("===========================")
                logger.info(f"arb_id               : {arb_helper.id}")
                logger.info(f"name                 : {arb_helper.name}")
                logger.info(f"swap_pool_addresses  : {arb_helper.swap_pool_addresses}")
                
                for i, pool in enumerate(arb_helper.swap_pools, 1):
                    logger.info(f"pool {i}               : {pool.state}")
            """

            # Calculate profitability for all paths
            log.info(
                f"(process_backrun_arbs) {transaction_hash} calculate_with_pool() called"
            )
            """
            calculation_futures = [
                await arb_helper.calculate_with_pool(
                    executor=process_pool,
                    override_state=override_state,
                )
                for arb_helper in arb_helpers
            ]
            """

            calculation_futures = []

            for arb_helper in arb_helpers:
                try:
                    future = await arb_helper.calculate_with_pool(
                        executor=executor,
                        override_state=override_state,
                    )
                    calculation_futures.append(future)
                except degenbot.exceptions.ArbitrageError as exc:
                    pass
                    # logger.info(f"(process_backrun_arbs) (bot.exceptions.ArbitrageError): {exc}")
                except Exception as exc:
                    log.error(
                        f"(process_backrun_arbs) Unexpected exception: {type(exc).__name__} - {exc}"
                    )

            calculation_results: List[ArbitrageCalculationResult] = []

            for task in asyncio.as_completed(calculation_futures):
                try:
                    result = await task
                    calculation_results.append(result)
                except degenbot.exceptions.ArbitrageError as exc:
                    log.error(
                        f"(process_backrun_arbs) (bot.exceptions.ArbitrageError): {exc}"
                    )
                except Exception as exc:  # Catch all exceptions
                    log.error(
                        f"(process_backrun_arbs) Unexpected exception: {type(exc).__name__} - {exc}"
                    )

            # logger.info(f"(process_backrun_arbs) {transaction_hash} calculation_results: {calculation_results}")

            # Show the calculation results
            """
            for calc_result in calculation_results:
                logger.info(f"arb_id                : {calc_result.id}")
                logger.info(f"input_amount          : {calc_result.input_amount}")
                logger.info(f"profit_amount         : {calc_result.profit_amount}")
                
                for i, swap_amount in enumerate(calc_result.swap_amounts, 1):
                    logger.info(f"pool_{i}_swap           : {swap_amount}")
            """
            # Sort the arb helpers by profit
            all_profitable_calc_results = sorted(
                [
                    calc_result
                    for calc_result in calculation_results
                    if calc_result.profit_amount >= 0
                ],
                key=lambda calc_result: calc_result.profit_amount,
                reverse=True,
            )

            all_profitable_arbs = [
                arb_details.lp_cycle
                for calc_result in all_profitable_calc_results
                if (arb_details := all_arbs.get(calc_result.id)) is not None
            ]

            # Store results and arbitrage IDs for easy retrieval later
            results_by_arb_id: Dict[str, ArbitrageCalculationResult] = dict()
            for calc_result, arb in zip(
                all_profitable_calc_results,
                all_profitable_arbs,
                strict=True,
            ):
                if TYPE_CHECKING:
                    assert arb is not None
                results_by_arb_id[arb.id] = calc_result

            if not all_profitable_arbs:
                # logger.info(f"No profitable arbs for {transaction_hash}")
                return

            arbs_without_overlap: Set[UniswapLpCycle] = set()

            while True:
                most_profitable_arb = all_profitable_arbs.pop(0)
                if TYPE_CHECKING:
                    assert most_profitable_arb is not None
                arbs_without_overlap.add(most_profitable_arb)

                conflicting_arbs = [
                    arb_helper
                    for arb_helper in all_profitable_arbs
                    if set(most_profitable_arb.swap_pools) & set(arb_helper.swap_pools)
                ]

                # Drop conflicting arbs from working set
                for arb in conflicting_arbs:
                    all_profitable_arbs.remove(arb)

                # Escape the loop if no arbs remain
                if not all_profitable_arbs:
                    break

            log.info(f"Reduced {len(arb_helpers)} arbs to {len(arbs_without_overlap)}")

            for arb_helper in arbs_without_overlap:
                arb_result = results_by_arb_id[arb_helper.id]

                # Determine the bribe amount
                arb_details = all_arbs.get(arb_result.id)
                # bribe = SMALL_BRIBE if arb_details and arb_details.status == "new" else BACKRUN_BRIBE
                # bribe = BUILDER_BRIBE
                # logger.info('')
                # logger.info(f'Trigger               : {transaction_hash}')

                if TYPE_CHECKING:
                    assert isinstance(raw_transaction, HexBytes)

                log.info("EXECUTE ARB")

                # await execute_arb(
                # 	all_arbs=all_arbs,
                # 	arb_result=arb_result,
                # 	bot_status=bot_status,
                # 	bribe=bribe,
                # 	http_session=http_session,
                # 	override_state=override_state,
                # 	state_block=bot_status.newest_block,
                # 	target_block=bot_status.newest_block + 1,
                # 	tx_to_backrun=raw_transaction,
                # 	transaction_hash=transaction_hash
                # )
