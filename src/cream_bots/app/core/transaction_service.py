import asyncio
import degenbot
from eth_utils.address import to_checksum_address
from hexbytes import HexBytes
from typing import TYPE_CHECKING, Dict, List, Optional, Set
import ujson
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

from .anvil_service import AnvilService

from ...config.constants import *
from ...config.logging import logger

log = logger(__name__)


class TransactionService:
    def __init__(self, bot_state):
        self.bot_state = bot_state
        self.redis_client = self.bot_state.redis_client
        self.w3 = self.bot_state.w3
        self.anvil_service = AnvilService(bot_state)
        
        log.info(
            f"TransactionService initialized with app instance at {id(self.bot_state)}"
        )
    
    async def process_pending_transactions(self):

        while not self.bot_state.live:
            await asyncio.sleep(1)
            log.info("Waiting for bot_state.live to become True...")

        log.info("Bot state is live. Starting to process pending transactions.")

        pubsub = self.redis_client.pubsub()
        await pubsub.subscribe("cream_pending_transactions")
        
        while True:
            try:
                async for message in pubsub.listen():
                    if message["type"] == "message":
                        transaction_data = message["data"]
                        transaction_dict = ujson.loads(transaction_data.decode('utf-8'))

                        # Skip if the transaction is already in failed_transactions
                        if transaction_dict['hash'] in self.bot_state.failed_transactions:
                            if VERBOSE_TRANSACTION_UPDATES:
                                log.info(f"Skipping previously failed transaction: {transaction_dict['hash']}")
                            continue
                        
                        if VERBOSE_TRANSACTION_UPDATES:
                            log.info(f"Received transaction: {transaction_dict['hash']}")
                        
                        cleaned_transaction = await self.clean_transaction(transaction_dict)
                        
                        if cleaned_transaction:
                            if VERBOSE_TRANSACTION_UPDATES:
                                log.info(f"Cleaned transaction: {cleaned_transaction['hash']}")
                            # Further processing can be added here
                            if cleaned_transaction['to'] is None:
                                if VERBOSE_TRANSACTION_UPDATES:
                                    log.info("Contract deployment transaction, skipping further processing")
                            elif cleaned_transaction['to'] in self.bot_state.routers:
                                await self.process_router_transaction(cleaned_transaction)
                            elif cleaned_transaction['to'] in self.bot_state.aggregators:
                                await self.process_aggregator_transaction(cleaned_transaction)
                            else:
                                if VERBOSE_TRANSACTION_UPDATES:
                                    log.info("Transaction not relevant for further processing")
                        else:
                            if VERBOSE_TRANSACTION_UPDATES:
                                log.info(f"Transaction failed cleaning: {transaction_dict['hash']}")
            
            except asyncio.CancelledError:
                log.info("process_pending_transactions cancelled")
                return
            except Exception as exc:
                log.exception(f"Error in process_pending_transactions: {exc}")
                await asyncio.sleep(1)  # Wait before reconnecting

    async def clean_transaction(self, transaction: Dict) -> Optional[Dict]:
        w3 = self.bot_state.w3

        try:
            params_to_keep = [
                "from", "to", "gas", "maxFeePerGas", "maxPriorityFeePerGas",
                "gasPrice", "value", "data", "input", "nonce", "type", "hash"
            ]

            transaction_type = int(transaction["type"], 16)
            
            if transaction_type == 0:
                params_to_keep.remove("maxFeePerGas")
                params_to_keep.remove("maxPriorityFeePerGas")
            elif transaction_type == 1:
                params_to_keep.remove("maxFeePerGas")
                params_to_keep.remove("maxPriorityFeePerGas")
            elif transaction_type in (2, 3):
                params_to_keep.remove("gasPrice")
            else:
                log.warning(f'Unknown transaction type: {transaction_type}')
                self.bot_state.failed_transactions.add(transaction["hash"])
                return None

            transaction_to_test = {k: v for k, v in transaction.items() if k in params_to_keep}

            # Convert addresses to checksummed versions
            for addr_field in ("from", "to"):
                if addr_field in transaction_to_test and transaction_to_test[addr_field]:
                    transaction_to_test[addr_field] = to_checksum_address(transaction_to_test[addr_field])

            # Simulate the transaction
            w3.eth.call(
                transaction=transaction_to_test,
                block_identifier="latest",
            )

            return transaction_to_test

        except web3.exceptions.ContractLogicError as exc:
            if VERBOSE_TRANSACTION_UPDATES:
                log.error(f"Contract logic error in transaction {transaction['hash']}: {exc}")
        except ValueError as exc:
            if VERBOSE_TRANSACTION_UPDATES:
                log.error(f"Value error in transaction {transaction['hash']}: {exc}")
        except Exception as exc:
            if VERBOSE_TRANSACTION_UPDATES: 
                log.error(f"Unexpected error in transaction {transaction['hash']}: {exc}")
        
        self.bot_state.failed_transactions.add(transaction["hash"])
        return None

    async def process_router_transaction(self, transaction):
        log.info("~·~·~·~·~·~·~·~·~·~·~·~·~·~·~·~·~·~·~·~·~·~")
        log.info("~ ROUTER TRANSACTION")
        log.info("~·~·~·~·~·~·~·~·~·~·~·~·~·~·~·~·~·~·~·~·~·~")

        await self.print_transaction_details(transaction)

    async def process_aggregator_transaction(self, transaction):
        log.info("~·~·~·~·~·~·~·~·~·~·~·~·~·~·~·~·~·~·~·~·~·~")
        log.info("~ AGGREGATOR TRANSACTION")
        log.info("~·~·~·~·~·~·~·~·~·~·~·~·~·~·~·~·~·~·~·~·~·~")

        await self.print_transaction_details(transaction)
    
    async def print_transaction_details(self, transaction):
        log.info(f"block                 : {self.bot_state.newest_block + 1}")
        log.info(f"hash                  : {transaction['hash']}")
        log.info(f"from                  : {transaction['from']}")
        log.info(f"to                    : {transaction['to']}")
        log.info(f"type                  : {int(transaction['type'],16)}")

        if 'gasPrice' in transaction:
            log.info(f"gasPrice              : {int(transaction['gasPrice'],16)}")
        if 'maxFeePerGas' in transaction:
            log.info(f"maxFeePerGas          : {int(transaction['maxFeePerGas'],16)}")
        if 'maxPriorityFeePerGas' in transaction:
            log.info(f"maxPriorityFeePerGas  : {int(transaction['maxPriorityFeePerGas'],16)}")

        log.info(f"nonce                 : {int(transaction['nonce'],16)}")
        log.info(f"value                 : {int(transaction['value'],16)}")
