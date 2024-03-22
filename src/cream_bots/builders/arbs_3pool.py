import argparse
import itertools
import networkx as nx
import os
from pathlib import Path
import sys
import time
import ujson
import web3

from cream_chains import chain_data as cream_chains_data


def main():
    parser = argparse.ArgumentParser(description="3-pool Arb Path Builder")
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

        chain_data = cream_chains_data[chain_name]
        w3 = web3.Web3(web3.WebsocketProvider(chain_data.get("websocket_uri")))

        wrapped_token = chain_data.get("wrapped_token")
        v2_factories = chain_data.get("factories").get("v2")
        v3_factories = chain_data.get("factories").get("v3")

        start_timer = time.monotonic()

        print(
            f"\n***************************************"
            f"\nBUILDING {chain_name.upper()} 3-POOL ARBS"
            f"\n***************************************"
            f"\n"
        )

        chain_data_dir = data_dir / chain_name
        chain_data_dir.mkdir(
            exist_ok=True
        )  # Create the chain-specific directory if it doesn't exist

        v2_lp_data = {}
        for name, _ in v2_factories.items():
            lp_file = chain_data_dir / f"{chain_name}_{name}_v2.json"
            print(f"Loading {lp_file}")
            with open(lp_file) as file:
                for pool in ujson.load(file):
                    v2_lp_data[pool.get("pool_address")] = {
                        key: value for key, value in pool.items() if key != "pool_id"
                    }
        print(f"Found {len(v2_lp_data)} V2 pools")

        v3_lp_data = {}
        for name, _ in v3_factories.items():
            lp_file = chain_data_dir / f"{chain_name}_{name}_v3.json"
            print(f"Loading {lp_file}")
            with open(lp_file) as file:
                for pool in ujson.load(file):
                    v3_lp_data[pool.get("pool_address")] = {
                        key: value for key, value in pool.items() if key != "pool_id"
                    }
        print(f"Found {len(v3_lp_data)} V3 pools")

        # all_v2_pools = set(v2_lp_data.keys())
        # all_v3_pools = set(v3_lp_data.keys())

        all_tokens = set(
            [lp.get("token0") for lp in v2_lp_data.values()]
            + [lp.get("token1") for lp in v2_lp_data.values()]
            + [lp.get("token0") for lp in v3_lp_data.values()]
            + [lp.get("token1") for lp in v3_lp_data.values()]
        )

        # build the graph with tokens as nodes, adding an edge
        # between any two tokens held by a liquidity pool
        G = nx.MultiGraph()
        for pool in v2_lp_data.values():
            G.add_edge(
                pool.get("token0"),
                pool.get("token1"),
                lp_address=pool.get("pool_address"),
                pool_type="UniswapV2",
            )

        for pool in v3_lp_data.values():
            G.add_edge(
                pool.get("token0"),
                pool.get("token1"),
                lp_address=pool.get("pool_address"),
                pool_type="UniswapV3",
            )

        print(f"G ready: {len(G.nodes)} nodes, {len(G.edges)} edges")

        all_tokens_with_weth_pool = list(G.neighbors(wrapped_token))
        print(f"Found {len(all_tokens_with_weth_pool)} tokens with a WETH pair")

        print("*** Finding three-pool arbitrage paths ***")
        three_pool_arb_paths = {}

        # only consider tokens with degree > 1 (number of pools holding the token)
        filtered_tokens = [
            token for token in all_tokens_with_weth_pool if G.degree(token) > 1
        ]
        print(f"Processing {len(filtered_tokens)} tokens with degree > 1")

        for token_a, token_b in itertools.combinations(
            filtered_tokens,
            2,
        ):

            # loop through all token pairs identified in G
            if (token_a, token_b) in G.edges():

                # find all token_a - WETH pairs
                outside_pools_tokenA = [
                    edge for edge in G.get_edge_data(token_a, wrapped_token).values()
                ]

                # find all token_a - token_b pairs
                inside_pools = [
                    edge for edge in G.get_edge_data(token_a, token_b).values()
                ]

                # find all token_b - WETH pairs
                outside_pools_tokenB = [
                    edge for edge in G.get_edge_data(token_b, wrapped_token).values()
                ]

                for swap_pools in itertools.product(
                    outside_pools_tokenA, inside_pools, outside_pools_tokenB
                ):

                    pool_a = swap_pools[0]
                    pool_b = swap_pools[1]
                    pool_c = swap_pools[2]

                    if pool_a.get("pool_type") == "UniswapV2":
                        pool_a_dict = v2_lp_data.get(pool_a.get("lp_address"))
                    elif pool_a.get("pool_type") == "UniswapV3":
                        pool_a_dict = v3_lp_data.get(pool_a.get("lp_address"))
                    else:
                        raise Exception(f"could not identify pool {pool_a}")

                    if pool_b.get("pool_type") == "UniswapV2":
                        pool_b_dict = v2_lp_data.get(pool_b.get("lp_address"))
                    elif pool_b.get("pool_type") == "UniswapV3":
                        pool_b_dict = v3_lp_data.get(pool_b.get("lp_address"))
                    else:
                        raise Exception(f"could not identify pool {pool_b}")

                    if pool_c.get("pool_type") == "UniswapV2":
                        pool_c_dict = v2_lp_data.get(pool_c.get("lp_address"))
                    elif pool_c.get("pool_type") == "UniswapV3":
                        pool_c_dict = v3_lp_data.get(pool_c.get("lp_address"))
                    else:
                        raise Exception(f"could not identify pool {pool_c}")

                    three_pool_arb_paths[id] = {
                        "id": (
                            id := w3.keccak(
                                hexstr="".join(
                                    [
                                        pool_a.get("lp_address")[2:],
                                        pool_b.get("lp_address")[2:],
                                        pool_c.get("lp_address")[2:],
                                    ]
                                )
                            ).hex()
                        ),
                        "pools": {
                            pool_a.get("lp_address"): pool_a_dict,
                            pool_b.get("lp_address"): pool_b_dict,
                            pool_c.get("lp_address"): pool_c_dict,
                        },
                        "arb_types": ["cycle", "flash_borrow_lp_swap"],
                        "path": [
                            pool.get("lp_address") for pool in [pool_a, pool_b, pool_c]
                        ],
                    }
                    three_pool_arb_paths[id] = {
                        "id": (
                            id := w3.keccak(
                                hexstr="".join(
                                    [
                                        pool_c.get("lp_address")[2:],
                                        pool_b.get("lp_address")[2:],
                                        pool_a.get("lp_address")[2:],
                                    ]
                                )
                            ).hex()
                        ),
                        "pools": {
                            pool_c.get("lp_address"): pool_c_dict,
                            pool_b.get("lp_address"): pool_b_dict,
                            pool_a.get("lp_address"): pool_a_dict,
                        },
                        "arb_types": ["cycle", "flash_borrow_lp_swap"],
                        "path": [
                            pool.get("lp_address") for pool in [pool_c, pool_b, pool_a]
                        ],
                    }

        print(
            f"Found {len(three_pool_arb_paths)} unique three-pool arbitrage paths in {time.monotonic() - start_timer:.5f}s"
        )
        print("• Saving pool data to JSON")
        arbs_file = chain_data_dir / f"{chain_name}_arb_paths_3.json"

        with open(arbs_file, "w") as file:
            ujson.dump(three_pool_arb_paths, file, indent=4)


if __name__ == "__main__":
    main()
