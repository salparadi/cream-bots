from pathlib import Path
from typing import Set
import ujson

from ...config.logging import logger

log = logger(__name__)


class BlacklistService:
    def __init__(self, bot_state):
        self.bot_state = bot_state
        self.chain_name = self.bot_state.chain_name
        self.blacklist_directory = Path(f"data/{self.bot_state.chain_name}")
        self.blacklists = {
            "arbs": set(),
            "deployers": set(),
            "pools": set(),
            "tokens": set(),
        }
        self.data_dir = (
            Path(__file__).resolve().parent.parent / "data" / self.chain_name
        )

        log.info(
            f"BlacklistService initialized with app instance at {id(self.bot_state)}"
        )

    async def load_blacklists(self):
        for blacklist_type in ["arbs", "deployers", "pools", "tokens"]:
            self.blacklists[blacklist_type] = self.load_blacklist(blacklist_type)

        self.bot_state.blacklists = self.blacklists

    def load_blacklist(self, blacklist_type: str) -> Set[str]:
        blacklist_filename = f"{self.chain_name}_blacklist_{blacklist_type}.json"
        blacklist_filepath = self.data_dir / blacklist_filename

        if blacklist_filepath.exists():
            with open(blacklist_filepath, "r", encoding="utf-8") as file:
                return set(ujson.load(file))
        return set()

    async def update_blacklist(self, blacklist_type: str, address: str):
        self.blacklists[blacklist_type].add(address)
        self.bot_state.blacklists = self.blacklists

        blacklist_filename = f"{self.chain_name}_blacklist_{blacklist_type}.json"
        blacklist_filepath = self.data_dir / blacklist_filename

        with open(blacklist_filepath, "w", encoding="utf-8") as file:
            ujson.dump(list(self.blacklists[blacklist_type]), file, indent=2)

    def is_blacklisted(self, blacklist_type: str, address: str) -> bool:
        return address in self.blacklists[blacklist_type]
