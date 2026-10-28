"""Google Sheets access through a service account (gspread)."""

from __future__ import annotations

import time

import gspread
from google.oauth2.service_account import Credentials

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

TABS = {
    "settings": "Settings",
    "contacts": "Contacts",
    "companies": "Companies",
    "bulk": "Bulk list",
    "do_not_email": "Do not email",
    "sent_log": "Sent log",
    "activity": "Activity",
}


def _retry(fn, *args, **kwargs):
    for attempt in range(4):
        try:
            return fn(*args, **kwargs)
        except gspread.exceptions.APIError as e:
            code = getattr(getattr(e, "response", None), "status_code", 0)
            if code in (429, 500, 502, 503) and attempt < 3:
                time.sleep(2 ** attempt * 5)
                continue
            raise


class Sheet:
    def __init__(self, sheet_id: str, service_account_info: dict):
        creds = Credentials.from_service_account_info(service_account_info, scopes=SCOPES)
        self.book = gspread.authorize(creds).open_by_key(sheet_id)
        self._ws: dict[str, gspread.Worksheet] = {}

    def ws(self, key: str) -> gspread.Worksheet:
        if key not in self._ws:
            self._ws[key] = _retry(self.book.worksheet, TABS[key])
        return self._ws[key]

    def values(self, key: str) -> list[list[str]]:
        return _retry(self.ws(key).get_all_values)

    def read_all(self) -> dict[str, list[list[str]]]:
        ranges = [f"'{TABS[k]}'" for k in TABS]
        result = _retry(self.book.values_batch_get, ranges)
        out = {}
        for key, vr in zip(TABS, result.get("valueRanges", [])):
            out[key] = vr.get("values", [])
        return out

    def cell(self, key: str, a1: str) -> str:
        v = _retry(self.ws(key).acell, a1).value
        return "" if v is None else str(v)

    def set_cell(self, key: str, a1: str, value: str) -> None:
        _retry(self.ws(key).update, range_name=a1, values=[[value]], value_input_option="RAW")

    def append(self, key: str, rows: list[list[str]]) -> None:
        if rows:
            _retry(self.ws(key).append_rows, rows, value_input_option="RAW", insert_data_option="INSERT_ROWS")

    def set_setting(self, settings_rows: list[list[str]], name: str, value: str) -> None:
        for i, r in enumerate(settings_rows, start=1):
            if r and str(r[0]).strip().lower() == name:
                self.set_cell("settings", f"B{i}", value)
                return
        self.append("settings", [[name, value, ""]])
