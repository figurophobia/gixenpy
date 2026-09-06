"""
Generic <form> parsing from HTML, using the standard library (no new
dependencies). Knows nothing about Gixen: it only extracts forms and their
fields (name → value), replicating what a browser would send if the form
were submitted as-is.

Gixen-specific logic (which form is the "add snipe" one, which field is the
bid, etc.) lives in `client.py`, which uses this module as its parsing
engine.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser


@dataclass
class _Form:
    action: str = ""
    method: str = "post"
    # <form name="..."> (used to tell Gixen's settings forms apart: each of
    # them has a distinct name like changecountry, changeebaysite, ...).
    name: str = ""
    # field name -> default value (fields with no value get "")
    fields: dict[str, str] = field(default_factory=dict)


class _FormParser(HTMLParser):
    """
    Extracts every <form> along with its fields (name→value).

    Important: for <select> elements, it stores the value of the option
    marked 'selected' or, if none is marked, the FIRST option's (which is
    what a real browser would send). Without this, fields like
    'newbidoffset' would go out empty and Gixen would reject the snipe.
    """

    def __init__(self) -> None:
        super().__init__()
        self.forms: list[_Form] = []
        self._cur: _Form | None = None
        self._select: str | None = None   # name of the <select> in progress
        self._sel_locked = False          # a 'selected' option was already seen

    def handle_starttag(self, tag: str, attrs_list):
        attrs = {k.lower(): (v if v is not None else "") for k, v in attrs_list}
        if tag == "form":
            self._cur = _Form(
                action=attrs.get("action", ""),
                method=(attrs.get("method", "post") or "post").lower(),
                name=attrs.get("name", ""),
            )
            self.forms.append(self._cur)
            self._select = None
            self._sel_locked = False
        elif self._cur is None:
            return
        elif tag == "select":
            name = attrs.get("name")
            if name:
                self._select = name
                self._sel_locked = False
                self._cur.fields.setdefault(name, "")  # filled in by the option
        elif tag == "option" and self._select:
            val = attrs.get("value", "")
            if "selected" in attrs:
                self._cur.fields[self._select] = val
                self._sel_locked = True
            elif not self._sel_locked and not self._cur.fields.get(self._select):
                self._cur.fields[self._select] = val  # 1st option = default
        elif tag in ("input", "textarea"):
            name = attrs.get("name")
            if not name:
                return
            if attrs.get("type", "").lower() == "submit":
                self._cur.fields.setdefault(name, attrs.get("value", ""))
            else:
                self._cur.fields[name] = attrs.get("value", "")

    def handle_endtag(self, tag: str):
        if tag == "select":
            self._select = None
            self._sel_locked = False
        elif tag == "form":
            self._cur = None
            self._select = None


def _parse_forms(html: str) -> list[_Form]:
    p = _FormParser()
    p.feed(html or "")
    return p.forms
