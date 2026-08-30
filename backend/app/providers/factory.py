"""Provider selection.

The rest of the application asks for *a* `PropertyDataProvider`; only this module
knows which concrete adapter that is. If a live provider is configured but fails
to construct (missing credentials), we surface a clear error rather than silently
serving fixtures — quietly swapping demo data in for live data would be exactly
the kind of dishonesty this product exists to avoid.
"""

from __future__ import annotations

from functools import lru_cache

from app.config import ProviderName, Settings, get_settings
from app.providers.base import PropertyDataProvider, ProviderNotConfiguredError


def build_provider(settings: Settings | None = None) -> PropertyDataProvider:
    settings = settings or get_settings()
    if settings.property_provider is ProviderName.DEMO:
        from app.providers.demo import DemoProvider

        return DemoProvider()
    if settings.property_provider is ProviderName.PROPTRACK:
        from app.providers.proptrack import PropTrackProvider

        return PropTrackProvider(settings)
    if settings.property_provider is ProviderName.DOMAIN:
        from app.providers.domain_provider import DomainProvider

        return DomainProvider(settings)
    raise ProviderNotConfiguredError(f"Unknown provider: {settings.property_provider!r}")


@lru_cache(maxsize=1)
def get_provider() -> PropertyDataProvider:
    """Process-wide singleton (holds an HTTP connection pool for live adapters)."""
    return build_provider()


def reset_provider_cache() -> None:
    get_provider.cache_clear()
