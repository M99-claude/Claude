from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .config import load_config

log = logging.getLogger("bank_sync")


def _yesterday(tz: str) -> date:
    return datetime.now(ZoneInfo(tz)).date() - timedelta(days=1)


def cmd_sync(args) -> int:
    from .providers import build_provider
    from .sheets import TransactionSheet
    from .sync import sync

    config = load_config(args.config)
    date_to = date.fromisoformat(args.date) if args.date else _yesterday(config.timezone)
    date_from = date.fromisoformat(args.date_from) if args.date_from else date_to - timedelta(days=args.days - 1)
    if date_from > date_to:
        log.error("Dátum od (%s) je po dátume do (%s)", date_from, date_to)
        return 2

    accounts = [a for a in config.accounts if not args.account or a.name in args.account]
    # Účty zoskupené podľa hárku, do ktorého sa zapisujú.
    groups: dict[str, dict] = {}
    for a in accounts:
        groups.setdefault(a.worksheet, {})[a.name] = build_provider(a, config)
    sheets = TransactionSheet.open_all(config.sheet, list(groups))

    log.info("Sťahujem transakcie %s – %s pre %d účtov", date_from, date_to, len(accounts))
    errors = {}
    for worksheet, providers in groups.items():
        result = sync(providers, sheets[worksheet], date_from, date_to, dry_run=args.dry_run)
        log.info(
            "Hárok %s: pridaných %d, už existujúcich %d, chýb %d",
            worksheet, result.added, result.skipped, len(result.errors),
        )
        errors.update(result.errors)
    for name, err in errors.items():
        log.error("  %s: %s", name, err)
    return 1 if errors else 0


def _eb_client(args):
    from .providers.enablebanking import EnableBankingClient

    return EnableBankingClient(load_config(args.config).enablebanking)


def cmd_eb_banks(args) -> int:
    for bank in _eb_client(args).list_banks(args.country):
        print(f"{bank['name']}  (max. platnosť súhlasu: {bank.get('maximum_consent_validity', '?')} s)")
    return 0


def cmd_eb_auth(args) -> int:
    url = _eb_client(args).start_auth(args.bank, args.country, args.redirect_url, args.valid_days)
    print("Otvorte v prehliadači, prihláste sa do banky a potvrďte prístup:\n")
    print(url)
    print("\nPo presmerovaní skopírujte hodnotu parametra 'code' z URL a spustite:")
    print("  python -m bank_sync eb-session <code>")
    return 0


def cmd_eb_session(args) -> int:
    session = _eb_client(args).create_session(args.code)
    print(f"session_id: {session['session_id']}")
    print(f"platnosť súhlasu do: {(session.get('access') or {}).get('valid_until', '?')}\n")
    print("Účty – doplňte account_uid do config.yaml:")
    for acc in session.get("accounts") or []:
        iban = (acc.get("account_id") or {}).get("iban", "")
        print(f"  account_uid: {acc['uid']}   # {iban} {acc.get('name') or ''} {acc.get('currency') or ''}")
    if args.json:
        print(json.dumps(session, indent=2, ensure_ascii=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bank_sync", description="Bankové transakcie → Google Sheet")
    parser.add_argument("-c", "--config", default="config.yaml")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("sync", help="stiahnuť transakcie a doplniť ich do hárku (predvolene za včerajšok)")
    p.add_argument("--date", help="posledný deň obdobia YYYY-MM-DD (predvolene včera)")
    p.add_argument("--from", dest="date_from", help="prvý deň obdobia YYYY-MM-DD")
    p.add_argument("--days", type=int, default=1, help="počet dní dozadu vrátane --date (predvolene 1)")
    p.add_argument("--account", action="append", help="len vybraný účet (dá sa opakovať)")
    p.add_argument("--dry-run", action="store_true", help="nič nezapisovať, len vypísať")
    p.set_defaults(func=cmd_sync)

    p = sub.add_parser("eb-banks", help="Enable Banking: zoznam podporovaných bánk")
    p.add_argument("country", nargs="?", default="SK")
    p.set_defaults(func=cmd_eb_banks)

    p = sub.add_parser("eb-auth", help="Enable Banking: začať autorizáciu prístupu k banke")
    p.add_argument("bank", help='presný názov z eb-banks, napr. "Tatra banka"')
    p.add_argument("country", nargs="?", default="SK")
    p.add_argument("--redirect-url", default="https://enablebanking.com/", help="musí byť povolená v aplikácii")
    p.add_argument("--valid-days", type=int, default=180)
    p.set_defaults(func=cmd_eb_auth)

    p = sub.add_parser("eb-session", help="Enable Banking: dokončiť autorizáciu a vypísať účty")
    p.add_argument("code")
    p.add_argument("--json", action="store_true", help="vypísať celú odpoveď")
    p.set_defaults(func=cmd_eb_session)

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    try:
        return args.func(args)
    except Exception as exc:  # noqa: BLE001
        if args.verbose:
            raise
        log.error("%s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
