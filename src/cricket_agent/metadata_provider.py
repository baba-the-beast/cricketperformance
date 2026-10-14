from typing import Any, Protocol

class MetadataProvider(Protocol):
    def enrich_player(self, player_name: str) -> dict[str, Any] | None:
        """Return a dictionary of metadata for a player, or None if not found."""
        ...

class DummyMetadataProvider:
    def enrich_player(self, player_name: str) -> dict[str, Any] | None:
        return None

_provider: MetadataProvider = DummyMetadataProvider()

def set_metadata_provider(provider: MetadataProvider) -> None:
    global _provider
    _provider = provider

def get_metadata_provider() -> MetadataProvider:
    return _provider
