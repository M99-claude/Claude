from datetime import date
from decimal import Decimal

import pytest

from bank_sync.cli import main
from bank_sync.config import ConfigError, load_config
from bank_sync.models import COLUMNS, Transaction
from bank_sync.providers import enablebanking, fio
from bank_sync.sync import sync

FIO_TX = {
    "column22": {"value": 26500123456, "name": "ID pohybu", "id": 22},
    "column0": {"value": "2026-09-23+0200", "name": "Datum", "id": 0},
    "column1": {"value": -125.5, "name": "Objem", "id": 1},
    "column14": {"value": "EUR", "name": "Měna", "id": 14},
    "column2": {"value": "2900123456", "name": "Protiúčet", "id": 2},
    "column3": {"value": "8330", "name": "Kód banky", "id": 3},
    "column10": {"value": "Dodávateľ s.r.o.", "name": "Název protiúčtu", "id": 10},
    "column5": {"value": "0020260101", "name": "VS", "id": 5},
    "column4": None,
    "column16": {"value": "Faktúra 101", "name": "Zpráva pro příjemce", "id": 16},
    "column8": {"value": "Platba prevodom", "name": "Typ", "id": 8},
}

EB_TX = {
    "entry_reference": "TB-987",
    "transaction_amount": {"currency": "EUR", "amount": "42.10"},
    "credit_debit_indicator": "DBIT",
    "status": "BOOK",
    "booking_date": "2026-09-23",
    "creditor": {"name": "Obchod a.s."},
    "creditor_account": {"iban": "SK3112000000198742637541"},
    "debtor": {"name": "Ja"},
    "reference_number": "/VS12345/SS9/KS0308",
    "remittance_information": ["Nákup", "karta"],
    "bank_transaction_code": {"description": "Platba kartou"},
}


def test_fio_parse():
    tx = fio.parse_transaction("Fio", FIO_TX)
    assert tx.transaction_id == "26500123456"
    assert tx.booking_date == date(2026, 9, 23)
    assert tx.amount == Decimal("-125.5")
    assert tx.counterparty_account == "2900123456/8330"
    assert tx.variable_symbol == "0020260101"
    assert tx.constant_symbol == ""
    assert tx.description == "Faktúra 101"
    assert tx.key == "Fio:26500123456"
    assert len(tx.to_row()) == len(COLUMNS)


def test_enablebanking_parse_debit_is_negative():
    tx = enablebanking.parse_transaction("Tatra", EB_TX)
    assert tx.amount == Decimal("-42.10")
    assert tx.counterparty_name == "Obchod a.s."
    assert tx.counterparty_account == "SK3112000000198742637541"
    assert (tx.variable_symbol, tx.specific_symbol, tx.constant_symbol) == ("12345", "9", "0308")
    assert tx.description == "Nákup karta"
    assert tx.type == "Platba kartou"


def test_enablebanking_credit_uses_debtor():
    raw = dict(EB_TX, credit_debit_indicator="CRDT", debtor={"name": "Klient"}, reference_number=None)
    tx = enablebanking.parse_transaction("Tatra", raw)
    assert tx.amount == Decimal("42.10")
    assert tx.counterparty_name == "Klient"
    assert tx.variable_symbol == ""


def test_enablebanking_skips_pending_and_paginates():
    class FakeClient:
        def __init__(self):
            self.calls = []

        def transactions(self, uid, date_from, date_to):
            return [EB_TX, dict(EB_TX, entry_reference="P1", status="PDNG")]

    p = enablebanking.EnableBankingProvider("Tatra", {"account_uid": "u1"}, {}, client=FakeClient())
    assert [t.transaction_id for t in p.fetch(date(2026, 9, 23), date(2026, 9, 23))] == ["TB-987"]


def test_enablebanking_client_follows_continuation_key(monkeypatch):
    client = enablebanking.EnableBankingClient.__new__(enablebanking.EnableBankingClient)
    pages = iter([
        {"transactions": [{"a": 1}], "continuation_key": "k"},
        {"transactions": [{"a": 2}], "continuation_key": None},
    ])
    seen = []

    def fake_request(method, path, params=None, **kw):
        seen.append(dict(params))
        return next(pages)

    monkeypatch.setattr(client, "request", fake_request)
    assert client.transactions("u", date(2026, 9, 1), date(2026, 9, 2)) == [{"a": 1}, {"a": 2}]
    assert seen[1]["continuation_key"] == "k"


class FakeSheet:
    def __init__(self, keys=()):
        self.keys = set(keys)
        self.rows = []

    def existing_keys(self):
        return set(self.keys)

    def append(self, rows):
        self.rows.extend(rows)

    def fill_account_iban(self, ibans):
        self.ibans = ibans


class FakeProvider:
    def __init__(self, txs=None, error=None):
        self.txs, self.error = txs or [], error

    def fetch(self, date_from, date_to):
        if self.error:
            raise self.error
        return self.txs


def _tx(account, tid, day=23):
    return Transaction(account, tid, date(2026, 9, day), Decimal("1"), "EUR")


def test_sync_deduplicates_and_sorts():
    sheet = FakeSheet(keys={"A:1"})
    providers = {
        "A": FakeProvider([_tx("A", "1"), _tx("A", "2", day=23), _tx("A", "2", day=23)]),
        "B": FakeProvider([_tx("B", "1", day=22)]),
    }
    result = sync(providers, sheet, date(2026, 9, 22), date(2026, 9, 23))
    assert (result.added, result.skipped) == (2, 2)
    assert [r[COLUMNS.index("ID transakcie")] for r in sheet.rows] == ["B:1", "A:2"]


def test_sync_continues_after_account_error():
    sheet = FakeSheet()
    providers = {"bad": FakeProvider(error=RuntimeError("HTTP 500")), "ok": FakeProvider([_tx("ok", "1")])}
    result = sync(providers, sheet, date(2026, 9, 23), date(2026, 9, 23))
    assert result.added == 1
    assert result.errors == {"bad": "HTTP 500"}


def test_sync_dry_run_writes_nothing():
    sheet = FakeSheet()
    result = sync({"A": FakeProvider([_tx("A", "1")])}, sheet, date(2026, 9, 23), date(2026, 9, 23), dry_run=True)
    assert result.added == 1 and sheet.rows == []


def test_config(tmp_path):
    path = tmp_path / "c.yaml"
    path.write_text(
        "google_sheet: {spreadsheet_id: abc}\n"
        "accounts:\n  - {name: Fio, provider: fio, token_env: T}\n"
        "  - {name: Iný, provider: fio, token_env: U, worksheet: Druhý}\n",
        encoding="utf-8",
    )
    cfg = load_config(path)
    assert cfg.sheet.worksheet == "Transakcie"
    assert cfg.sheet.locale == "sk_SK"
    assert cfg.accounts[0].options == {"token_env": "T"}
    assert [a.worksheet for a in cfg.accounts] == ["Transakcie", "Druhý"]
    assert cfg.accounts[1].options == {"token_env": "U"}

    path.write_text("google_sheet: {spreadsheet_id: abc}\naccounts: []\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(path)


def test_example_config_is_valid():
    assert len(load_config("config.example.yaml").accounts) == 3


def test_cli_rejects_inverted_range(tmp_path, monkeypatch):
    path = tmp_path / "c.yaml"
    path.write_text("google_sheet: {spreadsheet_id: abc}\naccounts:\n  - {name: F, provider: fio}\n")
    assert main(["-c", str(path), "sync", "--date", "2026-09-01", "--from", "2026-09-05"]) == 2


def test_enablebanking_jwt_is_signed_with_app_id(monkeypatch):
    import jwt
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    monkeypatch.setenv("ENABLEBANKING_PRIVATE_KEY", pem)
    client = enablebanking.EnableBankingClient({"application_id": "app-1"}, session=object())
    token = client._headers()["Authorization"].removeprefix("Bearer ")
    assert jwt.get_unverified_header(token)["kid"] == "app-1"
    claims = jwt.decode(token, key.public_key(), algorithms=["RS256"], audience="api.enablebanking.com")
    assert claims["iss"] == "enablebanking.com"


class FakeWorksheet:
    title = "Transakcie"

    def __init__(self, rows=None):
        self.rows = rows or []

    def row_values(self, n):
        return self.rows[n - 1] if len(self.rows) >= n else []

    def update(self, values, rng, value_input_option):
        self.rows[: len(values)] = [list(v) for v in values]

    def get_all_values(self, value_render_option=None):
        return [list(r) for r in self.rows]

    def freeze(self, rows):
        pass

    def col_values(self, col):
        return [str(r[col - 1]) for r in self.rows]

    def batch_format(self, formats):
        self.formats = {f["range"]: f["format"]["numberFormat"] for f in formats}

    def batch_update(self, updates, value_input_option):
        from gspread.utils import a1_to_rowcol

        for u in updates:
            row, col = a1_to_rowcol(u["range"])
            line = self.rows[row - 1]
            line.extend([""] * (col - len(line)))
            line[col - 1] = u["values"][0][0]

    def append_rows(self, rows, value_input_option, table_range):
        self.rows.extend(rows)


def test_sheet_creates_header_and_reads_keys():
    from bank_sync.sheets import TransactionSheet

    ws = FakeWorksheet()
    sheet = TransactionSheet(ws)
    assert ws.rows[0] == COLUMNS
    sheet.append([_tx("A", "1").to_row()])
    assert sheet.existing_keys() == {"A:1"}
    assert ws.formats == {
        "A2:A": {"type": "DATE", "pattern": "dd.mm.yyyy"},
        "D2:D": {"type": "NUMBER", "pattern": "#,##0.00"},
    }


def test_date_is_sheets_serial():
    from bank_sync.models import sheets_date

    assert sheets_date(date(2026, 9, 23)) == 46288  # =DATE(2026;9;23) v Google Sheets
    assert _tx("A", "1").to_row()[0] == sheets_date(date(2026, 9, 23))


def test_sheet_converts_old_iso_dates():
    from bank_sync.sheets import TransactionSheet

    old = ["2026-09-22"] + [""] * (len(COLUMNS) - 1)
    already = [46288] + [""] * (len(COLUMNS) - 1)
    ws = FakeWorksheet([list(COLUMNS), old, already])
    TransactionSheet(ws)
    assert ws.rows[1][0] == 46287
    assert ws.rows[2][0] == 46288


def test_sheet_migrates_old_column_layout():
    from bank_sync.sheets import TransactionSheet

    old_header = ["Dátum", "Účet", "Suma", "Mena", "Protistrana", "Účet protistrany", "VS", "KS",
                  "SS", "Popis", "Typ", "ID transakcie", "Stiahnuté", "Moja kategória"]
    old_row = [46288, "Fio", -12.5, "EUR", "Obchod", "SK31", "0012", "0308", "", "Nákup", "Platba",
               "Fio:1", "2026-09-24 08:00:00", "jedlo"]
    ws = FakeWorksheet([old_header, old_row])
    sheet = TransactionSheet(ws)

    assert ws.rows[0][: len(COLUMNS) + 1] == COLUMNS + ["Moja kategória"]
    assert ws.rows[1][: len(COLUMNS) + 1] == [46288, "SK31", "Obchod", -12.5, "0012", "Nákup", "",
                                              "Fio:1", "2026-09-24 08:00:00", "jedlo"]
    # zvyšné staré stĺpce sú vyprázdnené
    assert ws.rows[0][len(COLUMNS) + 1:] == [""] * (len(old_header) - len(COLUMNS) - 1)
    assert sheet.existing_keys() == {"Fio:1"}


def test_fio_fetch_reads_account_iban(monkeypatch):
    class Resp:
        status_code = 200

        def json(self):
            return {"accountStatement": {"info": {"iban": "SK1283300000002900123456"},
                                         "transactionList": {"transaction": [FIO_TX]}}}

    class Http:
        def get(self, url, timeout):
            return Resp()

    monkeypatch.setenv("FIO_TEST_TOKEN", "x")
    p = fio.FioProvider("Fio", {"token_env": "FIO_TEST_TOKEN"}, session=Http())
    (tx,) = p.fetch(date(2026, 9, 23), date(2026, 9, 23))
    assert p.iban == tx.account_iban == "SK1283300000002900123456"
    assert tx.to_row()[COLUMNS.index("IBAN účtu")] == "SK1283300000002900123456"


def test_sync_backfills_iban_for_existing_rows():
    sheet = FakeSheet()
    provider = FakeProvider([_tx("A", "1")])
    provider.iban = "SK00"
    sync({"A": provider, "B": FakeProvider()}, sheet, date(2026, 9, 23), date(2026, 9, 23))
    assert sheet.ibans == {"A": "SK00"}


def test_sheet_fill_account_iban_only_empty_cells():
    from bank_sync.sheets import TransactionSheet

    def row(key, iban=""):
        r = [46288] + [""] * (len(COLUMNS) - 1)
        r[COLUMNS.index("ID transakcie")] = key
        r[COLUMNS.index("IBAN účtu")] = iban
        return r

    ws = FakeWorksheet([list(COLUMNS), row("Fio:1"), row("Fio 2:5"), row("Fio:2", "SK_OLD")])
    TransactionSheet(ws).fill_account_iban({"Fio": "SK_A", "Fio 2": "SK_B"})
    col = COLUMNS.index("IBAN účtu")
    assert [r[col] for r in ws.rows[1:]] == ["SK_A", "SK_B", "SK_OLD"]


def test_cli_writes_each_account_to_its_worksheet(tmp_path, monkeypatch):
    import bank_sync.providers as providers_mod
    from bank_sync.sheets import TransactionSheet

    path = tmp_path / "c.yaml"
    path.write_text(
        "google_sheet: {spreadsheet_id: abc}\n"
        "accounts:\n"
        "  - {name: A, provider: fio}\n"
        "  - {name: B, provider: fio}\n"
        "  - {name: C, provider: fio, worksheet: Iný}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(providers_mod, "build_provider", lambda a, cfg: FakeProvider([_tx(a.name, "1")]))
    sheets = {"Transakcie": FakeSheet(), "Iný": FakeSheet()}
    monkeypatch.setattr(TransactionSheet, "open_all", classmethod(lambda cls, cfg, names: {n: sheets[n] for n in names}))

    assert main(["-c", str(path), "sync", "--date", "2026-09-23"]) == 0
    key = COLUMNS.index("ID transakcie")
    assert [r[key] for r in sheets["Transakcie"].rows] == ["A:1", "B:1"]
    assert [r[key] for r in sheets["Iný"].rows] == ["C:1"]
