"""Enable Banking – PSD2 agregátor, cez ktorý sa dá pripojiť Tatra banka, SLSP, VÚB, ČSOB SK,
365.bank, UniCredit a ďalšie banky v EÚ.

Registrácia aplikácie: https://enablebanking.com/cp/applications
(pre vlastné účty stačí bezplatný režim „restricted production“ s prepojenými vlastnými účtami).

Postup:
  1. vytvoriť aplikáciu, stiahnuť súkromný kľúč (.pem) a zapamätať si application_id,
  2. `python -m bank_sync eb-banks SK` – nájsť presný názov banky,
  3. `python -m bank_sync eb-auth "Tatra banka" SK` – otvoriť odkaz, prihlásiť sa do banky,
     skopírovať `code` z URL presmerovania,
  4. `python -m bank_sync eb-session <code>` – vypíše uid účtov na vloženie do config.yaml.
Súhlas (consent) platí podľa banky zvyčajne 90–180 dní, potom treba kroky 3–4 zopakovať.
"""

from __future__ import annotations

import time
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import jwt
import requests

from ..config import ConfigError, env
from ..models import Transaction

API_URL = "https://api.enablebanking.com"


class EnableBankingClient:
    def __init__(self, settings: dict, session: requests.Session | None = None):
        self.app_id = settings.get("application_id") or env("ENABLEBANKING_APP_ID")
        self.private_key = self._load_key(settings)
        self.http = session or requests.Session()

    @staticmethod
    def _load_key(settings: dict) -> str:
        path = settings.get("private_key_path")
        if path:
            with open(path, encoding="utf-8") as fh:
                return fh.read()
        return env(settings.get("private_key_env", "ENABLEBANKING_PRIVATE_KEY"))

    def _headers(self) -> dict:
        now = int(time.time())
        token = jwt.encode(
            {"iss": "enablebanking.com", "aud": "api.enablebanking.com", "iat": now, "exp": now + 3600},
            self.private_key,
            algorithm="RS256",
            headers={"kid": self.app_id},
        )
        return {"Authorization": f"Bearer {token}"}

    def request(self, method: str, path: str, **kwargs) -> dict:
        resp = self.http.request(method, API_URL + path, headers=self._headers(), timeout=60, **kwargs)
        if resp.status_code >= 400:
            raise RuntimeError(f"Enable Banking {method} {path}: HTTP {resp.status_code} {resp.text[:500]}")
        return resp.json()

    # --- jednorazové nastavenie (CLI príkazy eb-*) ---

    def list_banks(self, country: str) -> list[dict]:
        return self.request("GET", "/aspsps", params={"country": country})["aspsps"]

    def start_auth(self, bank: str, country: str, redirect_url: str, days: int = 180) -> str:
        valid_until = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
        body = {
            "access": {"valid_until": valid_until},
            "aspsp": {"name": bank, "country": country},
            "state": str(uuid.uuid4()),
            "redirect_url": redirect_url,
            "psu_type": "personal",
        }
        return self.request("POST", "/auth", json=body)["url"]

    def create_session(self, code: str) -> dict:
        return self.request("POST", "/sessions", json={"code": code})

    # --- denné sťahovanie ---

    def transactions(self, account_uid: str, date_from: date, date_to: date) -> list[dict]:
        result = []
        params = {"date_from": date_from.isoformat(), "date_to": date_to.isoformat()}
        while True:
            data = self.request("GET", f"/accounts/{account_uid}/transactions", params=params)
            result.extend(data.get("transactions") or [])
            key = data.get("continuation_key")
            if not key:
                return result
            params["continuation_key"] = key


def _party_account(party: dict | None) -> str:
    if not party:
        return ""
    return party.get("iban") or (party.get("other") or {}).get("identification") or ""


def _symbol(ref: str, name: str) -> str:
    """SK banky posielajú symboly v end_to_end_id ako /VS123/SS456/KS0308."""
    for part in ref.split("/"):
        if part.upper().startswith(name):
            return part[len(name):]
    return ""


def parse_transaction(account: str, tx: dict) -> Transaction:
    amount = Decimal(str(tx["transaction_amount"]["amount"]))
    outgoing = tx.get("credit_debit_indicator") == "DBIT"
    if outgoing and amount > 0:
        amount = -amount

    party = tx.get("creditor") if outgoing else tx.get("debtor")
    party_account = tx.get("creditor_account") if outgoing else tx.get("debtor_account")

    tx_id = tx.get("entry_reference") or tx.get("transaction_id")
    if not tx_id:
        raise ValueError(f"Transakcia bez ID: {tx}")

    ref = tx.get("reference_number") or ""
    e2e = ""
    for candidate in (ref, *(tx.get("remittance_information") or [])):
        if "/VS" in candidate.upper():
            e2e = candidate
            break

    booking = tx.get("booking_date") or tx.get("value_date") or tx.get("transaction_date")
    bank_code = tx.get("bank_transaction_code") or {}

    return Transaction(
        account=account,
        transaction_id=str(tx_id),
        booking_date=date.fromisoformat(booking[:10]),
        amount=amount,
        currency=tx["transaction_amount"]["currency"],
        counterparty_name=(party or {}).get("name") or "",
        counterparty_account=_party_account(party_account),
        variable_symbol=_symbol(e2e, "VS"),
        constant_symbol=_symbol(e2e, "KS"),
        specific_symbol=_symbol(e2e, "SS"),
        description=" ".join(tx.get("remittance_information") or []).strip(),
        type=bank_code.get("description") or bank_code.get("code") or "",
    )


class EnableBankingProvider:
    def __init__(self, account: str, options: dict, settings: dict, client: EnableBankingClient | None = None):
        self.account = account
        self.account_uid = options.get("account_uid")
        if not self.account_uid:
            raise ConfigError(f"Účet {account}: chýba account_uid (získate cez eb-session)")
        self.client = client or EnableBankingClient(settings)

    def fetch(self, date_from: date, date_to: date) -> list[Transaction]:
        raw = self.client.transactions(self.account_uid, date_from, date_to)
        # Zapisujeme len zaúčtované transakcie; čakajúce (PDNG) sa ešte môžu zmeniť.
        booked = [t for t in raw if t.get("status", "BOOK") == "BOOK"]
        return [parse_transaction(self.account, t) for t in booked]
