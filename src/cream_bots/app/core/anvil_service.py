from degenbot import AnvilFork
from eth_utils.address import to_checksum_address
from typing import TYPE_CHECKING, List, Optional, Union
import ujson
import web3

from ...config.constants import *
from ...config.logging import logger

log = logger(__name__)


class AnvilService:
    def __init__(self, bot_state):
        self.bot_state = bot_state
        self.w3 = self.bot_state.w3

        log.info(f"AnvilService initialized with app instance at {id(self.bot_state)}")

    def anvil_fork(
        self,
        base_fee_next: int,
        state_block: int,
        transaction,
    ):

        simulator = AnvilFork(
            fork_url=self.bot_state.http_uri,
            fork_block=state_block,
            chain_id=self.bot_state.chain_id,
            base_fee=base_fee_next,
        )
        simulator_w3 = simulator.w3

        success: List[bool] = [False]

        # Process mempool_tx if it exists
        if transaction:
            try:
                transaction_hash = simulator_w3.eth.send_raw_transaction(transaction)
                transaction_receipt = simulator_w3.eth.wait_for_transaction_receipt(
                    transaction_hash, timeout=1
                )
            except Exception as e:
                log.error(f"(anvil_fork): {e}")
                # raise
            else:
                if TYPE_CHECKING:
                    assert transaction_receipt is not None
                # 'status' = 0 for reverts, 1 for success
                if transaction_receipt["status"] == 1:
                    success[0] = True
                if transaction_receipt["status"] == 0:
                    pass

        return success, [transaction_receipt]
