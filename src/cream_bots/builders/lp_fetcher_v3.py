import argparse
from pathlib import Path
import signal
import time
import ujson
import web3

from cream_chains import chain_data as cream_chains_data
from cream_chains.abis import UNISWAP_V3_FACTORY_ABI

# Maximum blocks to process with getLogs
BLOCK_SPAN = 10_000
keep_running = True


def signal_handler(signum, frame):
    global keep_running
    print("\nSignal received, initiating graceful shutdown...")
    keep_running = False


def main():
    parser = argparse.ArgumentParser(description="V2 Liquidity Pool Fetcher")
    parser.add_argument(
        "chain_name", type=str, nargs="?", help="The name of the chain", default=None
    )
    args = parser.parse_args()

    if args.chain_name:
        chains_to_process = {args.chain_name: cream_chains_data[args.chain_name]}
    else:
        chains_to_process = cream_chains_data

    current_dir = Path(__file__).resolve().parent
    data_dir = current_dir.parent / "data"
    data_dir.mkdir(exist_ok=True)

    for chain_name, chain_data in chains_to_process.items():

        print(
            f"\n***************************************"
            f"\nFETCHING {chain_name.upper()} V3 LPS"
            f"\n***************************************"
            f"\n"
        )

        chain_data = cream_chains_data[chain_name]
        factories = chain_data.get("factories").get("v3")
        node = chain_data.get("node")
        w3 = web3.Web3(web3.WebsocketProvider(chain_data.get("websocket_uri")))

        for exchange_name, details in factories.items():
            factory_address = details.get("factory_address")
            factory_deployment_block = details.get("factory_deployment_block")

            print(f"Factory: {exchange_name}")

            chain_data_dir = data_dir / chain_name
            chain_data_dir.mkdir(
                exist_ok=True
            )  # Create the chain-specific directory if it doesn't exist

            data_file = chain_data_dir / f"{chain_name}_{exchange_name}_v3.json"

            # See if we have an existing file
            if data_file.exists():
                with open(data_file) as file:
                    lp_data = ujson.load(file)
            else:
                lp_data = []

            # Check if there are LPs in the file
            if lp_data:
                previous_block = lp_data[-1].get("block_number")
                print(f"• Found pool data up to block {previous_block}")
            else:
                previous_block = factory_deployment_block

            # Define the factory contract W3 object
            factory_contract = w3.eth.contract(
                address=factory_address, abi=UNISWAP_V3_FACTORY_ABI
            )

            # Define some details about current block state and current pools
            current_block = w3.eth.get_block_number()
            previously_found_pools = len(lp_data)
            print(f"• Previously found {previously_found_pools} pools")

            # Loop through the blocks to find pools
            for i in range(previous_block + 1, current_block + 1, BLOCK_SPAN):

                if not keep_running:
                    print("Stopping early due to signal interruption.")
                    break

                if i + BLOCK_SPAN > current_block:
                    end_block = current_block
                else:
                    end_block = i + BLOCK_SPAN

                # See if there are any events in the block range
                if pool_created_events := factory_contract.events.PoolCreated.get_logs(
                    fromBlock=i, toBlock=end_block
                ):
                    for event in pool_created_events:
                        lp_data.append(
                            {
                                "pool_address": event.args.pool,
                                "fee": event.args.fee,
                                "token0": event.args.token0,
                                "token1": event.args.token1,
                                "block_number": event.blockNumber,
                                "type": "UniswapV3",
                                "exchange": exchange_name,
                            }
                        )

                # Save them to the file
                with open(data_file, "w") as file:
                    ujson.dump(lp_data, file, indent=2)

                print(
                    f"• Found {len(lp_data) - previously_found_pools} new pools through block {end_block}"
                )

                if node in ["alchemy", "infura"]:
                    time.sleep(0.07)

            if not keep_running:
                # Final save before exiting
                with open(data_file, "w") as file:
                    ujson.dump(lp_data, file, indent=4)
                print(f"Final data saved to {data_file}.")


if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal_handler)
    main()
