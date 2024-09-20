import argparse
import asyncio

from .app.bots.arb_bot import ArbBot
from .app.bots.callback_bot.callback_bot import CallbackBot
from .app.bots.sniper_bot import SniperBot

async def run_bot(bot_class, chain_name):
    bot = bot_class(chain_name)
    try:
        await bot.run()
    finally:
        await bot.close()


async def main():
    parser = argparse.ArgumentParser(description="Run cream bots")
    subparsers = parser.add_subparsers(dest="bot_type", required=True)
    
    arb_parser = subparsers.add_parser("arb", help="Run the arb bot")
    arb_parser.add_argument(
        "chain_name", type=str, help="The name of the chain to operate on"
    )
    callback_parser = subparsers.add_parser("callback", help="Run the callback bot")
    callback_parser.add_argument(
        "chain_name", type=str, help="The name of the chain to operate on"
    )
    sniper_parser = subparsers.add_parser("sniper", help="Run the sniper bot")
    sniper_parser.add_argument(
        "chain_name", type=str, help="The name of the chain to operate on"
    )
    
    args = parser.parse_args()
    
    if args.bot_type == "arb":
        await run_bot(ArbBot, args.chain_name)
    elif args.bot_type == "callback":
        await run_bot(CallbackBot, args.chain_name)
    elif args.bot_type == "sniper":
        await run_bot(SniperBot, args.chain_name)
    # Add more elif statements for other bots as needed


def run():
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Thanks for playing!")
        # The event loop is closed after asyncio.run(), so no async cleanup should be done here
    except asyncio.CancelledError:
        print("Bot stopped by user or system. Cleaning up...")
    except Exception as e:
        print(f"Unhandled exception: {e}")


if __name__ == "__main__":
    run()
