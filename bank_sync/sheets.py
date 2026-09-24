"""Zápis do Google Sheetu cez service account."""

from __future__ import annotations

import json
import os

import gspread

from .config import SheetConfig
from .models import COLUMNS, KEY_COLUMN


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

    @classmethod
    def open(cls, config: SheetConfig) -> "TransactionSheet":
        spreadsheet = _client().open_by_key(config.spreadsheet_id)
        try:
            ws = spreadsheet.worksheet(config.worksheet)
        except gspread.WorksheetNotFound:
            ws = spreadsheet.add_worksheet(config.worksheet, rows=1000, cols=len(COLUMNS))
        return cls(ws)

    def _ensure_header(self) -> None:
        header = self.ws.row_values(1)
        if not header:
            self.ws.update([COLUMNS], "A1", value_input_option="RAW")
            self.ws.freeze(rows=1)
            self.key_col = COLUMNS.index(KEY_COLUMN) + 1
        elif KEY_COLUMN in header:
            self.key_col = header.index(KEY_COLUMN) + 1
        else:
            raise RuntimeError(f"Hárok {self.ws.title} má hlavičku bez stĺpca '{KEY_COLUMN}'")

    def existing_keys(self) -> set[str]:
        return set(self.ws.col_values(self.key_col)[1:])

    def append(self, rows: list[list]) -> None:
        if rows:
            # RAW, aby Sheets nezmazal úvodné nuly vo VS / číslach účtov.
            self.ws.append_rows(rows, value_input_option="RAW", table_range="A1")
