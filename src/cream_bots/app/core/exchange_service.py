from eth_typing import HexStr
from eth_utils.address import to_checksum_address
import ujson

from degenbot.exchanges.uniswap.types import (
    UniswapFactoryDeployment,
    UniswapRouterDeployment,
    UniswapTickLensDeployment,
    UniswapV2ExchangeDeployment,
    UniswapV3ExchangeDeployment,
)
from degenbot.uniswap.abi import (
    PANCAKESWAP_V3_POOL_ABI,
    UNISWAP_V2_POOL_ABI,
    UNISWAP_V3_POOL_ABI,
    UNISWAP_V3_TICKLENS_ABI,
)
from degenbot.exchanges.uniswap.deployments import (
    FACTORY_DEPLOYMENTS,
    ROUTER_DEPLOYMENTS,
    TICKLENS_DEPLOYMENTS,
)
from degenbot.exchanges.uniswap.register import (
    register_exchange,
    register_router,
)

from ...config.logging import logger

log = logger(__name__)


class ExchangeService:
    def __init__(self, bot_state):
        self.bot_state = bot_state
        self.w3 = self.bot_state.w3

        log.info(
            f"ExchangeService initialized with app instance at {id(self.bot_state)}"
        )

    async def add_deployments(self):
        chain_id = self.bot_state.chain_data["chain_id"]
        chain_name = self.bot_state.chain_data["name"]
        factory_data = self.bot_state.chain_data.get("factories", {})

        FACTORY_DEPLOYMENTS.setdefault(chain_id, {})
        TICKLENS_DEPLOYMENTS.setdefault(chain_id, {})

        # Iterate through the factories in the chain configuration
        for version, factories in factory_data.items():

            for exchange_name, factory_info in factories.items():

                factory_address = to_checksum_address(
                    factory_info.get("factory_address")
                )
                pool_init_hash = factory_info.get("pool_init_hash")

                if factory_address in FACTORY_DEPLOYMENTS[chain_id]:
                    continue

                if version == "v2":
                    factory_deployment = UniswapV2ExchangeDeployment(
                        name=f"{chain_name} {exchange_name} {version}",
                        chain_id=chain_id,
                        factory=UniswapFactoryDeployment(
                            address=factory_address,
                            deployer=None,
                            pool_init_hash=HexStr(pool_init_hash),
                            pool_abi=UNISWAP_V2_POOL_ABI,
                        ),
                    )
                    try:
                        register_exchange(factory_deployment)
                        log.info(
                            f"{chain_name} / {exchange_name} / {version}: Factory added"
                        )
                    except Exception as exc:
                        log.error(
                            f"Error in 'add_deployments' during 'register_exchange' for {exchange_name} {version} on {chain_name}: {exc}"
                        )

                if version == "v3":
                    tick_lens = to_checksum_address(factory_info.get("tick_lens"))

                    factory_deployment = UniswapV3ExchangeDeployment(
                        name=f"{chain_name} {exchange_name} {version}",
                        chain_id=chain_id,
                        factory=UniswapFactoryDeployment(
                            address=factory_address,
                            deployer=None,
                            pool_init_hash=HexStr(pool_init_hash),
                            pool_abi=UNISWAP_V3_POOL_ABI,
                        ),
                        tick_lens=UniswapTickLensDeployment(
                            address=tick_lens,
                            abi=UNISWAP_V3_TICKLENS_ABI,
                        ),
                    )

                    try:
                        register_exchange(factory_deployment)
                        log.info(
                            f"{chain_name} / {exchange_name} / {version}: Factory and Tick Lens added"
                        )
                    except Exception as exc:
                        log.error(
                            f"Error in 'add_deployments' during 'register_exchange' for {exchange_name} {version} on {chain_name}: {exc}"
                        )

    async def add_routers(self, chain_data):
        """
        Adds router deployments for the current chain.

        This method retrieves the chain information and router details from the app state.
        It iterates through the routers in the chain configuration and performs the following steps for each router:
        - Adds the router address to the app's chain_data.
        - Checks if the router is already deployed for the current chain. If so, it skips to the next router.
        - Creates a UniswapRouterDeployment object with the router address, chain ID, router name, and an empty list of exchanges.
        - Registers the router in Degenbot by calling the 'register_router' function.
        - Initializes the router's w3 contract by creating an instance of the contract using the router address and ABI.

        If any errors occur during the registration or initialization process, appropriate error messages are logged.

        Note: This method assumes the existence of the following variables:
        - self.bot_state.router_addresses: a list to store the router addresses.
        - self.bot_state.w3: an instance of the Web3 class.

        """
        router_addresses = self.bot_state.router_addresses
        w3 = self.bot_state.w3

        chain_data, chain_id, chain_name = self.get_chain_data()
        if not chain_data:
            return

        ROUTER_DEPLOYMENTS.setdefault(chain_id, {})

        routers_data = chain_data.get("routers", {})

        # Iterate through the routers in the chain configuration
        for router_address, router_details in routers_data.items():
            router_address = to_checksum_address(router_address)
            router_name = router_details.get("name")
            exchange_name = router_details.get("exchange")

            # Add the router address to the app chain_data
            router_addresses.append(router_address)

            if router_address in ROUTER_DEPLOYMENTS[chain_id]:
                continue

            router_deployment = UniswapRouterDeployment(
                address=router_address,
                chain_id=chain_id,
                name=router_name,
                exchanges=[],
            )

            # Register the router in Degenbot
            try:
                register_router(router_deployment)
            except Exception as exc:
                log.error(
                    f"Error in 'add_routers' during 'register_router' for {exchange_name} on {chain_name}: {exc}"
                )
            else:
                log.info(
                    f"{chain_name} / {exchange_name} / {router_name}: Router added"
                )

            # Add the router w3 to the router in chain_data
            try:
                router_contract = w3.eth.contract(
                    address=router_address, abi=router_details["abi"]
                )
                router_details["w3"] = router_contract
            except Exception as exc:
                log.error(
                    f"Error in 'add_routers' during 'register_router' for {exchange_name} on {chain_name}: {exc}"
                )
            else:
                log.info(f"Router w3 contract initialized for {chain_name} network.")

        self.bot_state.redis_client.set(
            "router_addresses", ujson.dumps(router_addresses)
        )

    async def add_aggregators(self, chain_data):
        """
        Add aggregator routers for the current chain.

        This method retrieves the aggregator data for the current chain from the app state.
        It then iterates over the aggregator addresses and adds them to the app's chain_data.
        The aggregator addresses are stored in the aggregator_addresses list.

        Returns:
            None
        """
        aggregator_addresses = self.bot_state.aggregator_addresses
        chain_data, chain_id, chain_name = self.get_chain_data()
        if not chain_data:
            return

        aggregators_data = chain_data.get("aggregators", {})

        if aggregators_data is not None:

            for aggregator_address, aggregators_data in chain_data.get(
                "aggregators", {}
            ).items():

                router_address = to_checksum_address(aggregator_address)

                # Add the aggregator router address to the app chain_data
                aggregator_addresses.append(aggregator_address)

        self.bot_state.redis_client.set(
            "aggregator_addresses", ujson.dumps(aggregator_addresses)
        )
