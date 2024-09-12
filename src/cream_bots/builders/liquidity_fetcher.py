import argparse
import degenbot
from pathlib import Path
import ujson
import web3

from threading import Lock
from tqdm import tqdm
from typing import Dict
from web3._utils.events import get_event_data
from web3._utils.filters import construct_event_filter_params

from degenbot.uniswap.v3_types import (
    UniswapV3BitmapAtWord,
    UniswapV3LiquidityAtTick,
    UniswapV3PoolExternalUpdate,
    UniswapV3PoolState,
)

from cream_chains import chain_data as cream_chains_data

UNISWAPV3_START_BLOCK = 1000
BLOCK_SPAN = 10_000

TICKSPACING_BY_FEE: Dict = {
    100: 1,
    500: 10,
    2500: 50,
    3000: 60,
    10000: 200,
}


class MockV3LiquidityPool(degenbot.V3LiquidityPool):
    def __init__(self):
        self.state = UniswapV3PoolState(
            pool=self,
            liquidity=0,
            sqrt_price_x96=0,
            tick=0,
            tick_bitmap=dict(),
            tick_data=dict(),
        )


def main():
    parser = argparse.ArgumentParser(description="V3 Liquidity Fetcher")
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
        newest_block = w3.eth.block_number

        chain_data_dir = data_dir / chain_name
        chain_data_dir.mkdir(
            exist_ok=True
        )  # Create the chain-specific directory if it doesn't exist
        snapshot_file = chain_data_dir / f"{chain_name}_v3_liquidity_snapshot.json"
        print()

        print(
            f"\n***************************************"
            f"\nFETCHING {chain_name.upper()} LIQUIDITY"
            f"\n***************************************"
            f"\n"
        )

        liquidity_snapshot: Dict[str, Dict] = {}

        lp_data: Dict[str, Dict] = {}

        paths = []
        factories = chain_data.get("factories").get("v3")

        for name, _ in factories.items():
            lp_file = chain_data_dir / f"{chain_name}_{name}_v3.json"
            paths.append(lp_file)

        for path in paths:
            if path.exists():
                with open(path) as file:
                    l = ujson.load(file)
                for lp in l:
                    lp_data[lp["pool_address"]] = lp
            else:
                print("File does not exist")
                return

        try:
            with open(snapshot_file, "r") as file:
                json_liquidity_snapshot = ujson.load(file)
        except:
            snapshot_last_block = None
        else:
            snapshot_last_block = json_liquidity_snapshot.pop("snapshot_block")
            print(
                f"Loaded LP snapshot: {len(json_liquidity_snapshot)} pools @ block {snapshot_last_block}"
            )

            assert (
                snapshot_last_block < newest_block
            ), f"Aborting, snapshot block ({snapshot_last_block}) is newer than current chain height ({newest_block})"

            for pool_address, snapshot in json_liquidity_snapshot.items():
                liquidity_snapshot[pool_address] = {
                    "tick_bitmap": {
                        int(k): UniswapV3BitmapAtWord(**v)
                        for k, v in snapshot["tick_bitmap"].items()
                    },
                    "tick_data": {
                        int(k): UniswapV3LiquidityAtTick(**v)
                        for k, v in snapshot["tick_data"].items()
                    },
                }

        V3LP = w3.eth.contract(abi=degenbot.uniswap.abi.UNISWAP_V3_POOL_ABI)

        liquidity_events = {}

        for event in [V3LP.events.Mint, V3LP.events.Burn]:
            print(f"processing {event.event_name} events")

            start_block = (
                max(UNISWAPV3_START_BLOCK, snapshot_last_block + 1)
                if snapshot_last_block is not None
                else UNISWAPV3_START_BLOCK
            )
            block_span = BLOCK_SPAN
            done = False

            event_abi = event._get_event_abi()

            while not done:
                end_block = min(newest_block, start_block + block_span)

                _, event_filter_params = construct_event_filter_params(
                    event_abi=event_abi,
                    abi_codec=w3.codec,
                    argument_filters={},
                    fromBlock=start_block,
                    toBlock=end_block,
                )

                try:
                    event_logs = w3.eth.get_logs(event_filter_params)
                except:
                    block_span = int(0.75 * block_span)
                    continue

                for event in event_logs:
                    decoded_event = get_event_data(w3.codec, event_abi, event)

                    pool_address = decoded_event["address"]
                    block = decoded_event["blockNumber"]
                    tx_index = decoded_event["transactionIndex"]
                    liquidity = decoded_event["args"]["amount"] * (
                        -1 if decoded_event["event"] == "Burn" else 1
                    )
                    tick_lower = decoded_event["args"]["tickLower"]
                    tick_upper = decoded_event["args"]["tickUpper"]

                    if liquidity == 0:
                        continue

                    try:
                        liquidity_events[pool_address]
                    except KeyError:
                        liquidity_events[pool_address] = []

                    liquidity_events[pool_address].append(
                        (
                            block,
                            tx_index,
                            (
                                liquidity,
                                tick_lower,
                                tick_upper,
                            ),
                        )
                    )

                print(f"Fetched events: block span [{start_block},{end_block}]")

                if end_block == newest_block:
                    done = True
                else:
                    start_block = end_block + 1
                    block_span = int(1.05 * block_span)

        lp_helper = MockV3LiquidityPool()
        lp_helper._sparse_bitmap = False
        lp_helper._liquidity_lock = Lock()
        lp_helper._slot0_lock = Lock()
        lp_helper._state_lock = Lock()
        lp_helper._update_log = list()
        lp_helper._subscribers = set()
        lp_helper.state = UniswapV3PoolState(
            pool=lp,
            liquidity=0,
            sqrt_price_x96=0,
            tick=0,
            tick_bitmap={},
            tick_data={},
        )

        lp_helper._pool_state_archive = {}

        for pool_address in tqdm(liquidity_events.keys()):

            if not lp_data.get(pool_address):
                continue

            try:
                previous_snapshot_tick_data = liquidity_snapshot[pool_address][
                    "tick_data"
                ]
            except KeyError:
                previous_snapshot_tick_data = {}

            try:
                previous_snapshot_tick_bitmap = liquidity_snapshot[pool_address][
                    "tick_bitmap"
                ]
            except KeyError:
                previous_snapshot_tick_bitmap = {}

            lp_helper.address = "0x0000000000000000000000000000000000000000"
            lp_helper.liquidity = 1 << 256
            lp_helper.tick_data = previous_snapshot_tick_data
            lp_helper.tick_bitmap = previous_snapshot_tick_bitmap
            lp_helper._update_block = snapshot_last_block or UNISWAPV3_START_BLOCK
            lp_helper.liquidity_update_block = (
                snapshot_last_block or UNISWAPV3_START_BLOCK
            )
            lp_helper.tick = 0

            lp_helper._fee = lp_data[pool_address]["fee"]

            lp_helper._tick_spacing = TICKSPACING_BY_FEE[lp_helper._fee]

            sorted_liquidity_events = sorted(
                liquidity_events[pool_address],
                key=lambda event: (event[0], event[1]),
            )

            for liquidity_event in sorted_liquidity_events:
                (
                    event_block,
                    _,
                    (liquidity_delta, tick_lower, tick_upper),
                ) = liquidity_event

                lp_helper.external_update(
                    update=UniswapV3PoolExternalUpdate(
                        block_number=event_block,
                        liquidity_change=(
                            liquidity_delta,
                            tick_lower,
                            tick_upper,
                        ),
                    ),
                )

            try:
                liquidity_snapshot[pool_address]
            except KeyError:
                liquidity_snapshot[pool_address] = {
                    "tick_bitmap": {},
                    "tick_data": {},
                }

            liquidity_snapshot[pool_address]["tick_bitmap"].update(
                lp_helper.tick_bitmap
            )
            liquidity_snapshot[pool_address]["tick_data"].update(lp_helper.tick_data)

        for pool_address in liquidity_snapshot:
            liquidity_snapshot[pool_address] = {
                "tick_data": {
                    key: value.to_dict()
                    for key, value in liquidity_snapshot[pool_address][
                        "tick_data"
                    ].items()
                },
                "tick_bitmap": {
                    key: value.to_dict()
                    for key, value in liquidity_snapshot[pool_address][
                        "tick_bitmap"
                    ].items()
                    if value.bitmap
                },
            }

        liquidity_snapshot["snapshot_block"] = newest_block

        with open(snapshot_file, "w") as file:
            ujson.dump(
                liquidity_snapshot,
                file,
                indent=2,
                sort_keys=True,
            )
            print("Writing LP snapshot")


if __name__ == "__main__":
    main()
