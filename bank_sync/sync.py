from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date

from .models import Transaction

log = logging.getLogger(__name__)


@dataclass
class SyncResult:
    added: int = 0
    skipped: int = 0
    errors: dict[str, str] = field(default_factory=dict)


def sync(providers: dict, sheet, date_from: date, date_to: date, dry_run: bool = False) -> SyncResult:
    """Stiahne transakcie zo všetkých účtov a do hárku pridá tie, ktoré tam ešte nie sú.

    Chyba jedného účtu nezastaví ostatné – vráti sa v SyncResult.errors.
    """
    result = SyncResult()
    existing = sheet.existing_keys()
    new: list[Transaction] = []

    for name, provider in providers.items():
        try:
            transactions = provider.fetch(date_from, date_to)
        except Exception as exc:  # noqa: BLE001 – chceme pokračovať ďalšími účtami
            log.exception("Účet %s: sťahovanie zlyhalo", name)
            result.errors[name] = str(exc)
            continue
        for tx in transactions:
            if tx.key in existing:
                result.skipped += 1
                continue
            existing.add(tx.key)
            new.append(tx)
        log.info("Účet %s: %d transakcií", name, len(transactions))

    new.sort(key=lambda t: (t.booking_date, t.account, t.transaction_id))
    result.added = len(new)
    if dry_run:
        for tx in new:
            log.info("[dry-run] %s", tx.to_row())
    else:
        sheet.append([tx.to_row() for tx in new])
    return result
