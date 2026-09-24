"""Zápis do Google Sheetu cez service account."""

from __future__ import annotations

import json
import os
from datetime import date

import gspread

from .config import SheetConfig
from .models import (
    AMOUNT_COLUMN,
    AMOUNT_FORMAT,
    COLUMNS,
    DATE_COLUMN,
    DATE_FORMAT,
    IBAN_COLUMN,
    KEY_COLUMN,
    REMOVED_COLUMNS,
    sheets_date,
)


def _client() -> gspread.Client:
    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if raw:
        return gspread.service_account_from_dict(json.loads(raw))
    path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if path:
        return gspread.service_account(filename=path)
    raise RuntimeError(
        "Chýbajú Google prihlasovacie údaje: nastavte GOOGLE_SERVICE_ACCOUNT_JSON "
        "alebo GOOGLE_APPLICATION_CREDENTIALS"
    )


class TransactionSheet:
    def __init__(self, worksheet: gspread.Worksheet):
        self.ws = worksheet
        self._ensure_header()
        self._ensure_formats()

    @classmethod
    def open_all(cls, config: SheetConfig, worksheets: list[str]) -> dict[str, "TransactionSheet"]:
        """Otvorí (alebo vytvorí) zadané hárky v tabuľke."""
        spreadsheet = _client().open_by_key(config.spreadsheet_id)
        if config.locale and spreadsheet.locale != config.locale:
            spreadsheet.update_locale(config.locale)
        sheets = {}
        for name in worksheets:
            try:
                ws = spreadsheet.worksheet(name)
            except gspread.WorksheetNotFound:
                ws = spreadsheet.add_worksheet(name, rows=1000, cols=len(COLUMNS))
            sheets[name] = cls(ws)
        return sheets

    def _ensure_header(self) -> None:
        header = self.ws.row_values(1)
        if not header:
            self.ws.update([COLUMNS], "A1", value_input_option="RAW")
            self.ws.freeze(rows=1)
            header = COLUMNS
        elif header[: len(COLUMNS)] != COLUMNS:
            header = self._migrate_columns(header)
        for column in (KEY_COLUMN, DATE_COLUMN, IBAN_COLUMN, AMOUNT_COLUMN):
            if column not in header:
                raise RuntimeError(f"Hárok {self.ws.title} má hlavičku bez stĺpca '{column}'")
        self.key_col = header.index(KEY_COLUMN) + 1
        self.date_col = header.index(DATE_COLUMN) + 1
        self.iban_col = header.index(IBAN_COLUMN) + 1
        self.amount_col = header.index(AMOUNT_COLUMN) + 1

    def _migrate_columns(self, header: list[str]) -> list[str]:
        """Prestaví existujúci hárok na aktuálne poradie stĺpcov.

        Známe stĺpce sa presunú podľa názvu, stĺpce z REMOVED_COLUMNS sa zahodia
        a vlastné stĺpce používateľa sa zachovajú na konci.
        """
        rows = self.ws.get_all_values(value_render_option=gspread.utils.ValueRenderOption.unformatted)
        extra = [h for h in header if h and h not in COLUMNS and h not in REMOVED_COLUMNS]
        new_header = COLUMNS + extra
        index = {name: i for i, name in enumerate(header)}

        def cell(row: list, name: str):
            i = index.get(name)
            return row[i] if i is not None and i < len(row) else ""

        # Doplnenie prázdnymi bunkami vymaže obsah stĺpcov, ktoré po prestavbe ostanú navyše.
        width = max(len(header), len(new_header))
        pad = [""] * (width - len(new_header))
        values = [new_header + pad] + [[cell(r, h) for h in new_header] + pad for r in rows[1:]]
        self.ws.update(values, "A1", value_input_option="RAW")
        return new_header

    def _ensure_formats(self) -> None:
        amount = gspread.utils.rowcol_to_a1(1, self.amount_col).rstrip("1")
        letter = gspread.utils.rowcol_to_a1(1, self.date_col).rstrip("1")
        self.ws.batch_format([
            {"range": f"{letter}2:{letter}", "format": {"numberFormat": {"type": "DATE", "pattern": DATE_FORMAT}}},
            {"range": f"{amount}2:{amount}", "format": {"numberFormat": {"type": "NUMBER", "pattern": AMOUNT_FORMAT}}},
        ])

        # Staršie riadky zapísané ako text „2026-09-23“ prevedieme na skutočný dátum.
        updates = []
        for row, value in enumerate(self.ws.col_values(self.date_col)[1:], start=2):
            try:
                parsed = date.fromisoformat(value)
            except ValueError:
                continue
            updates.append({"range": f"{letter}{row}", "values": [[sheets_date(parsed)]]})
        if updates:
            self.ws.batch_update(updates, value_input_option="RAW")

    def fill_account_iban(self, ibans: dict[str, str]) -> None:
        """Doplní prázdny IBAN účtu podľa názvu účtu v ID transakcie („Fio:123“)."""
        keys = self.ws.col_values(self.key_col)
        current = self.ws.col_values(self.iban_col)
        letter = gspread.utils.rowcol_to_a1(1, self.iban_col).rstrip("1")
        updates = []
        for row in range(2, len(keys) + 1):
            if row <= len(current) and current[row - 1]:
                continue
            account = keys[row - 1].split(":", 1)[0]
            if account in ibans:
                updates.append({"range": f"{letter}{row}", "values": [[ibans[account]]]})
        if updates:
            self.ws.batch_update(updates, value_input_option="RAW")

    def existing_keys(self) -> set[str]:
        return set(self.ws.col_values(self.key_col)[1:])

    def append(self, rows: list[list]) -> None:
        if rows:
            # RAW, aby Sheets nezmazal úvodné nuly vo VS / číslach účtov.
            self.ws.append_rows(rows, value_input_option="RAW", table_range="A1")
