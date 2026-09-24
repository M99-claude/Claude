from __future__ import annotations

from ..config import AccountConfig, Config, ConfigError


def build_provider(account: AccountConfig, config: Config):
    if account.provider == "fio":
        from .fio import FioProvider

        return FioProvider(account.name, account.options)
    if account.provider == "enablebanking":
        from .enablebanking import EnableBankingProvider

        return EnableBankingProvider(account.name, account.options, config.enablebanking)
    raise ConfigError(f"Neznámy provider '{account.provider}' pri účte {account.name}")
