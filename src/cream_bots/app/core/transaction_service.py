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

from ...config.constants import constants
from ...config.logging import logger

log = logger(__name__)


class TransactionService:
    def __init__(self):
        self.w3 = self.bot_state.w3
        log.info(
            f"TransactionService initialized with app instance at {id(self.bot_state)}"
        )

    async def clean_transaction(self, transaction):
        w3 = self.bot_state.w3

        # Clean up the transaction and simulate it
        try:
            params_to_keep = [
                "from",
                "to",
                "gas",
                "maxFeePerGas",
                "maxPriorityFeePerGas",
                "gasPrice",
                "value",
                "data",
                "input",
                "nonce",
                "type",
            ]

            if transaction["type"] in (0, "0x0"):
                params_to_keep.remove("maxFeePerGas")
                params_to_keep.remove("maxPriorityFeePerGas")
            elif transaction["type"] in (1, "0x1"):
                params_to_keep.remove("maxFeePerGas")
                params_to_keep.remove("maxPriorityFeePerGas")
            elif transaction["type"] in (2, "0x2", 3, "0x3"):
                params_to_keep.remove("gasPrice")
            else:
                log.info(f'Unknown TX type: {transaction["type"]}')
                log.info(f"{transaction=}")
                return

            transaction_to_test = {
                k: v for k, v in transaction.items() if k in params_to_keep
            }

            # Convert the "from" address to a checksummed version
            if "from" in transaction_to_test and transaction_to_test["from"]:
                transaction_to_test["from"] = to_checksum_address(
                    transaction_to_test["from"]
                )

            # Convert the "to" address to a checksummed version
            if "to" in transaction_to_test and transaction_to_test["to"]:
                transaction_to_test["to"] = to_checksum_address(
                    transaction_to_test["to"]
                )

            w3.eth.call(
                transaction=transaction_to_test,
                block_identifier="latest",
            )
        except web3.exceptions.ContractLogicError as exc:
            self.failed_transactions.add(transaction["hash"])
            # log.error(f"(process_pending_transactions) (web3.exceptions.ContractLogicError): {exc}")
            return
        except ValueError as exc:
            self.failed_transactions.add(transaction["hash"])
            # log.error(f"(process_pending_transactions) (ValueError): {exc}")
            return
        except Exception as exc:
            self.failed_transactions.add(transaction["hash"])
            # log.error(f"(process_pending_transactions) ({type(exc)}): {exc}")
            return

        # If it's a contract deployment, continue
        if transaction["to"] == None:
            return

        return transaction

    async def process_router_transaction(self, transaction):
        w3 = self.bot_state.w3

        try:
            func_object, func_parameters = w3.eth.contract(
                address=transaction["to"],
                abi=self.routers[transaction["to"]]["abi"],
            ).decode_function_input(transaction["data"])
        except ValueError as exc:
            log.error(
                f"(process_pending_transactions) (decode_function_input) (ValueError): {exc}"
            )
            return
        except Exception as exc:
            log.error(
                f"(process_pending_transactions) (decode_function_input) ({type(exc)}): {exc}"
            )
            return

        # Test transaction and create helper if it's valid
        try:
            log.info("~·~·~·~·~·~·~·~·~·~·~·~·~·~·~·~·~·~·~·~·~·~")
            log.info("~ ROUTER TRANSACTION")
            log.info("~·~·~·~·~·~·~·~·~·~·~·~·~·~·~·~·~·~·~·~·~·~")
            log.info(f"block                 : {self.bot_state.newest_block + 1}")
            log.info(f"hash                  : {transaction['hash']}")
            log.info(f"from                  : {transaction['from']}")
            log.info(f"to                    : {transaction['to']}")
            log.info(f"type                  : {int(transaction['type'],16)}")
            # log.info(f"input                : {tx_calldata}")
            log.info(f"gas                   : {int(transaction['gas'],16)}")
            log.info(f"gasPrice              : {int(transaction['gasPrice'],16)}")
            log.info(f"base_fee_next         : {self.bot_state.base_fee_next}")

            if transaction["type"] in (2, "0x2", 3, "0x3"):
                log.info(
                    f"maxFeePerGas          : {int(transaction['maxFeePerGas'],16)}"
                )
                log.info(
                    f"maxPriorityFeePerGas  : {int(transaction['maxPriorityFeePerGas'],16)}"
                )

            log.info(f"nonce                 : {int(transaction['nonce'],16)}")
            log.info(f"value                 : {int(transaction['value'],16)}")

            tx_helper = degenbot.UniswapTransaction(
                chain_id=self.bot_state.chain_id,
                tx_hash=transaction["hash"],
                tx_nonce=transaction["nonce"],
                tx_value=transaction["value"],
                tx_sender=transaction["from"],
                func_name=func_object.fn_name,
                func_params=func_parameters,
                router_address=transaction["to"],
            )
        except degenbot.exceptions.TransactionError as exc:
            log.error(f"(process_pending_transactions) (TransactionError) {exc}")
            return
        except web3.exceptions.TransactionNotFound as exc:
            log.error(f"(process_pending_transactions) (TransactionNotFound) {exc}")
            return
        except Exception as exc:
            log.error(
                f"(process_pending_transactions) (catch-all) ({type(exc)}): {exc}"
            )
            return

        try:
            sim_results = tx_helper.simulate(silent=True)
            num_pools = len(sim_results)
            log.info(f"{num_pools} Pool(s) affected")

            # for sim_result in sim_results:
            # 	#log.info(f"sim_result           : {sim_result}")
            # 	log.info(f"current_state        : {sim_result[1].current_state}")
            # 	log.info(f"future_state         : {sim_result[1].future_state}")
            # 	print()
            # log.info(f"sim_results          : {sim_results}")

        except degenbot.exceptions.ManagerError as exc:
            log.info(f"(process_mempool_transactions) (ManagerError): {exc}")
            return
        except degenbot.exceptions.TransactionError as exc:
            log.error(f"(process_mempool_transactions) (TransactionError): {exc}")
            return
        except KeyError as exc:
            log.error(f"(process_mempool_transactions) (KeyError): {exc}")
            return
        except Exception as exc:
            log.error(
                f"(process_pending_transactions) (catch-all) ({type(exc)}): {exc}"
            )
            return

        # Cache the set of pools in the TX
        pool_set = set([pool for pool, _ in sim_results])
        log.info(f"Pools                 : {pool_set}")

        # Find arbitrage helpers that use pools in the TX path
        arb_helpers: List[degenbot.UniswapLpCycle] = [
            arb_details.lp_cycle
            for arb_details in self.bot_state.all_arbs.values()
            if (
                # 2pool arbs are always evaluated
                len(arb_details.lp_cycle.swap_pools) == 2
                and pool_set.intersection(
                    set(pool.address for pool in arb_details.lp_cycle.swap_pools)
                )
            )
            or (
                len(arb_details.lp_cycle.swap_pools) == 3
                and pool_set.intersection(
                    # evaluate only 3pool arbs with an affected pool in
                    # the middle position if "REDUCE" mode is active
                    (arb_details.lp_cycle.swap_pools[1].address,)
                    if constants.REDUCE_TRIANGLE_ARBS
                    else
                    # evaluate all 3pool arbs
                    set(pool.address for pool in arb_details.lp_cycle.swap_pools)
                )
            )
        ]

        # If there are helpers that contain any of the pools, process them.
        if arb_helpers:
            log.info("Call process_backrun_arbs")
            # await process_backrun_arbs(
            # 	arb_helpers=arb_helpers,
            # 	override_state=sim_results,
            # 	pending_transaction=transaction,
            # )

        print()

    async def process_aggregator_transaction(self, transaction):
        log.info("~·~·~·~·~·~·~·~·~·~·~·~·~·~·~·~·~·~·~·~·~·~")
        log.info("~ AGGREGATOR TRANSACTION")
        log.info("~·~·~·~·~·~·~·~·~·~·~·~·~·~·~·~·~·~·~·~·~·~")
        log.info(f"block                 : {self.bot_state.newest_block + 1}")
        log.info(f"hash                  : {transaction['hash']}")
        log.info(f"from                  : {transaction['from']}")
        log.info(f"to                    : {transaction['to']}")
        log.info(f"type                  : {int(transaction['type'],16)}")
        # log.info(f"input                : {tx_calldata}")
        log.info(f"gas                   : {int(transaction['gas'],16)}")
        log.info(f"gasPrice              : {int(transaction['gasPrice'],16)}")
        log.info(f"base_fee_next         : {self.bot_state.base_fee_next}")

        if transaction["type"] in (2, "0x2", 3, "0x3"):
            log.info(f"maxFeePerGas          : {int(transaction['maxFeePerGas'],16)}")
            log.info(
                f"maxPriorityFeePerGas  : {int(transaction['maxPriorityFeePerGas'],16)}"
            )

        log.info(f"nonce                 : {int(transaction['nonce'],16)}")
        log.info(f"value                 : {int(transaction['value'],16)}")

        # TODO do something with the transaction

        print()
