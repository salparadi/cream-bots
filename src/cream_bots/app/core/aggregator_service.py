from web3 import Web3

class AggregatorService:
    def __init__(self, bot_state):
        self.bot_state = bot_state
        self.aggregators = bot_state.aggregators

    def get_aggregator_info(self, address):
        return self.aggregators.get(Web3.to_checksum_address(address))

    def is_aggregator(self, address):
        return Web3.to_checksum_address(address) in self.aggregators

    def get_aggregator_abi(self, address):
        info = self.get_aggregator_info(address)
        return info['abi'] if info else None

    def get_aggregator_name(self, address):
        info = self.get_aggregator_info(address)
        return info['name'] if info else None

    def get_aggregator_type(self, address):
        info = self.get_aggregator_info(address)
        return info['aggregator'] if info else None
