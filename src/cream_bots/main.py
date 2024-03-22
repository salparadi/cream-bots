import argparse
import asyncio

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

    sniper_parser = subparsers.add_parser("sniper", help="Run the sniper bot")
    sniper_parser.add_argument(
        "chain_name", type=str, help="The name of the chain to operate on"
    )

    args = parser.parse_args()

    if args.bot_type == "sniper":
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
