
# Overview
CREAMbots is the collection of python tools to work with liquidity pools on EVM chains. The actual bots will continue to grow and as of the initial commit, are simply a proof of concept. There are however standardized fetchers and arbitrage path builders available for data collection and organization. Much of this work has been inspired by [BowTiedDevil](https://twitter.com/BowTiedDevil). Some of the LP/Liquidity/Arb path builders are directly based on his work. Go check out his stack [Degen Code](https://www.degencode.com/) for great insight into blockchain work with Python and Vyper. TY Devil!

## Prerequisites
- Python version 3.10 or newer.
- Redis ([website](https://redis.io))

## Dependencies
- degenbot ([pypi](https://pypi.org/project/degenbot/) | [github](https://github.com/BowTiedDevil/degenbot)): It should be auto installed when you install this and will install it's own set of dependencies, which this app relies on in turn.
- eth-ape ([pypi](https://pypi.org/project/eth-ape/) | [github](https://github.com/ApeWorX/ape)): It should be auto installed when you install this.
- networkx ([pypi](https://pypi.org/project/networkx/)): Used for three-pool arbitrage path building. It should be auto installed when you install this.
- redis ([pypi](https://pypi.org/project/degenbot/)): Used to interact with a redis server. It should be auto installed when you install this.
- tqdm ([pypi](https://pypi.org/project/tqdm/)): Used for progress meters around the app. It should be auto installed when you install this.
- ujson ([pypi](https://pypi.org/project/degenbot/)): Used to parse JSON. It should be auto installed when you install this.

## CREAM dependencies
- CREAM [github](https://github.com/salparadi/cream): If you want to actually react to blockchain transactions/events, you need to install this and run it in a separate process. This isn't a package yet, so you need to `git clone` it and install it as an editable installation in a separate folder.
- CREAMchains [github](https://github.com/salparadi/cream-chains): You'll need this installed to have access to the chain data (things like factories, routers, rpcs, etc). This isn't a package yet, so you need to `git clone` it and install it as an editable installation in a separate folder.

## Installation
At the moment the only way to install is from source. Use `git clone` to create a local copy of this repo, then install with `pip install -e /path/to/repo`. This creates an editable installation that can be imported into a script or Python REPL using `import cream_bots`.

# How the hell do I use this?
You'll need to do a bit of legwork to get your environment set up. Once that is set there are a handful of helper scripts that you can run to keep your chain data up to date for use in bots/apps.

At the moment the supported chains are:
- **arbitrum** (*alchemy* / *local node*)
- **avalanche** (*infura* / *local node*)
- **base** (*alchemy* / *local node*)
- **ethereum** (*alchemy* / *local node*)
- **optimism** (*alchemy* / *local node*)
- **polygon** (*alchemy* / *local node*)

# Redis
Once you have a redis server running, this tool will subscribe to messages to these channels

 - `cream_events`
 - `cream_pending_transactions`
 - `cream_finalized_transactions`

The main CREAM app publishes to these channels. Depending on the chain, either pending or finalized transactions channels are used. Base and Optimism don't have pending transactions so you can only see them after they are confirmed in a block. The rest should work with pending transactions. Arbitrum uses the sequencer. This will certainly be updated during development.

The app expects Redis to be local on port 6379 when you run things. You can alter the host/port as needed in `config/constants.py`. 

## Shell Constants
You'll need to add a few things to your `.bashrc/.zshrc` to ensure the connections can be made. I highly recommend using Alchemy if you don't have a local node. If you do, just configure things for that. See the shell-example.txt file for how to add those. The other CREAM tools rely on Ape for a lot of things so you'll see some ape-specific stuff in various files. The builders don't require Ape, but forthcoming bots will expect that you are managing your accounts with it so you'll need it installed and configured for the chains you are going to use.

## Bootstrapping LP/Liquidity/Arb Paths
There are a variety of scripts that you can run to pull data from chain and find arbitrage pathways. They are all located in `/builders/` They write `json` files to `/data/` by chain.

### Liquidity Pools
The fetchers to retrieve Liquidity Pool (LP) data from chain factories is in `/builders/`. You call the LP fetchers like so:

`cream_lps_v2` gets all V2 pools on all chains\
`cream_lps_v2 ethereum` gets all V2 pools only on ethereum\
`cream_lps_v3` gets all V3 pools on all chains\
`cream_lps_v3 ethereum` gets all V3 pools only on ethereum

### V3 Liquidity Snapshots
After you have V3 pools fetched, you can get liquidity data for them. The fetcher to retrieve V3 liquidity data is in `/builders/`. You call the liquidity fetcher like so:

`cream_liquidity` gets liquidity on all chains\
`cream_liquidity ethereum` gets liquidity only on ethereum

### Arbitrage Pathways
After you have your LP data, you can create two- and three-pool arbitrage pathways. The builders for these are in `/builders/`. You call the arbitrage pathwy builders like so:

`cream_arbs_2pool` gets all two-pool arbitrage pathways on all chains\
`cream_arbs_2pool ethereum` gets all two-pool arbitrage pathways only on ethereum\
`cream_arbs_3pool` gets all three-pool arbitrage pathways on all chains\
`cream_arbs_3pool ethereum` gets all three-pool arbitrage pathways only on ethereum

# What next?
That's about it for this module. Once you've pulled all the data, import this module into your bots to bootstrap pools/liquidity/arb paths etc.

# Work in progress
This is undoubtedly busted in several ways, but I'll be working on it for personal use so it should get refined.