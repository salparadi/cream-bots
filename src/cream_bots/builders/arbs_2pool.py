import argparse
from pathlib import Path
import time
import ujson
import web3

from cream_chains import chain_data as cream_chains_data


def main():
    parser = argparse.ArgumentParser(description="2-pool Arb Path Builder")
    parser.add_argument(
        "chain_name", type=str, nargs="?", help="The name of the chain", default=None
    )
    args = parser.parse_args()

    if args.chain_name:
        chains_to_process = {args.chain_name: cream_chains_data[args.chain_name]}
    else:
        chains_to_process = cream_chains_data

    for chain_name, chain_data in chains_to_process.items():

        chain_data = cream_chains_data[chain_name]
        w3 = web3.Web3(web3.WebsocketProvider(chain_data.get("websocket_uri")))

        wrapped_token = chain_data.get("wrapped_token")
        v2_factories = chain_data.get("factories").get("v2")
        v3_factories = chain_data.get("factories").get("v3")

        start_timer = time.monotonic()

        print(
            f"\n***************************************"
            f"\nBUILDING {chain_name.upper()} 2-POOL ARBS"
            f"\n***************************************"
            f"\n"
        )

        current_dir = Path(__file__).resolve().parent
        data_dir = current_dir.parent / "data"

        v2_lp_data = {}
        for name, _ in v2_factories.items():
            lp_file = data_dir / chain_name / f"{chain_name}_{name}_v2.json"
            print(f"Loading {lp_file}")
            with open(lp_file) as file:
                for pool in ujson.load(file):
                    v2_lp_data[pool.get("pool_address")] = {
                        key: value for key, value in pool.items() if key != "pool_id"
                    }

        print(f"Found {len(v2_lp_data)} V2 pools")

        # Loading V3 pools
        v3_lp_data = {}
        for name, _ in v3_factories.items():
            lp_file = data_dir / chain_name / f"{chain_name}_{name}_v3.json"
            print(f"Loading {lp_file}")
            with open(lp_file) as file:
                for pool in ujson.load(file):
                    v3_lp_data[pool.get("pool_address")] = {
                        key: value for key, value in pool.items() if key != "pool_id"
                    }

        print(f"Found {len(v3_lp_data)} V3 pools")

        # Combine both files
        all_pools = {**v2_lp_data, **v3_lp_data}

        # Constant for wrapped ether (WETH)
        start_token = wrapped_token

        # Create a dictionary to hold pools indexed by their tokens
        token_to_pools = {}

        # Populate the dictionary
        for pool in all_pools.values():
            token0, token1 = pool["token0"], pool["token1"]
            if token0 not in token_to_pools:
                token_to_pools[token0] = []
            if token1 not in token_to_pools:
                token_to_pools[token1] = []
            token_to_pools[token0].append(pool)
            token_to_pools[token1].append(pool)

        # Initialize two_pool_arb_paths dictionary
        two_pool_arb_paths = {}

        print("Finding two-pool arbitrage paths")

        # Start from pools that contain WETH
        for pool_a in token_to_pools.get(start_token, []):
            # Skip if the pool doesn't contain WETH
            if pool_a["token0"] != start_token and pool_a["token1"] != start_token:
                continue

            # Find the other token in the first pool
            other_token = (
                pool_a["token1"]
                if pool_a["token0"] == start_token
                else pool_a["token0"]
            )

            # Look for a second pool that connects back to WETH
            for pool_b in token_to_pools.get(other_token, []):
                if pool_b["token0"] == start_token or pool_b["token1"] == start_token:
                    # Skip if pool_b is the same as pool_a
                    if pool_b["pool_address"] == pool_a["pool_address"]:
                        continue

                    id_hash = w3.keccak(
                        hexstr="".join(
                            [
                                pool_a.get("pool_address")[2:],
                                pool_b.get("pool_address")[2:],
                            ]
                        )
                    ).hex()

                    two_pool_arb_paths[id_hash] = {
                        "id": id_hash,
                        "pools": {
                            pool_a.get("pool_address"): pool_a,
                            pool_b.get("pool_address"): pool_b,
                        },
                        "arb_types": ["cycle"],
                        "path": [pool.get("pool_address") for pool in [pool_a, pool_b]],
                    }

        print(
            f"Found {len(two_pool_arb_paths)} unique two-pool arbitrage paths in {time.monotonic() - start_timer:.5f}s"
        )
        print("• Saving pool data to JSON")

        arbs_file = data_dir / chain_name / f"{chain_name}_arb_paths_2.json"

        with open(arbs_file, "w") as file:
            ujson.dump(two_pool_arb_paths, file, indent=4)


if __name__ == "__main__":
    main()
