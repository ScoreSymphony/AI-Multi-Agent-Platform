"""HTML parsing and current-page state for the stdlib browser reference provider."""

from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser
from http.cookiejar import CookieJar
from typing import cast

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue

from .models import BrowserSessionRef
from .reference_capabilities import CONTENT_TRUST


@dataclass(slots=True)
class Link:
    href: str
    text_parts: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(" ".join(self.text_parts).split())


@dataclass(slots=True)
class Form:
    action: str
    method: str
    fields: dict[str, str] = field(default_factory=dict)


class PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []
        self.links: list[Link] = []
        self.forms: list[Form] = []
        self._in_title = False
        self._current_link: Link | None = None
        self._current_form: Form | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {name.lower(): value for name, value in attrs}
        lowered = tag.lower()
        if lowered == "title":
            self._in_title = True
        elif lowered == "a":
            href = attributes.get("href")
            if href:
                link = Link(href=href)
                self.links.append(link)
                self._current_link = link
        elif lowered == "form":
            form = Form(
                action=attributes.get("action") or "",
                method=(attributes.get("method") or "GET").upper(),
            )
            self.forms.append(form)
            self._current_form = form
        elif lowered == "input" and self._current_form is not None:
            name = attributes.get("name")
            if name:
                input_type = (attributes.get("type") or "text").lower()
                if input_type not in {"file", "submit", "button", "image"}:
                    self._current_form.fields[name] = attributes.get("value") or ""

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered == "title":
            self._in_title = False
        elif lowered == "a":
            self._current_link = None
        elif lowered == "form":
            self._current_form = None

    def handle_data(self, data: str) -> None:
        if not data.strip():
            return
        self.text_parts.append(data)
        if self._in_title:
            self.title_parts.append(data)
        if self._current_link is not None:
            self._current_link.text_parts.append(data)

    @property
    def title(self) -> str:
        return " ".join(" ".join(self.title_parts).split())

    @property
    def text(self) -> str:
        return " ".join(" ".join(self.text_parts).split())


@dataclass(slots=True)
class SessionState:
    ref: BrowserSessionRef
    cookies: CookieJar = field(default_factory=CookieJar)
    current_url: str | None = None
    body: bytes | None = None
    content_type: str | None = None
    charset: str = "utf-8"
    status_code: int | None = None


def store_page(
    state: SessionState,
    *,
    final_url: str,
    data: bytes,
    content_type: str | None,
    charset: str,
    status_code: int,
) -> None:
    state.current_url = final_url
    state.body = data
    state.content_type = content_type
    state.charset = charset
    state.status_code = status_code


def parse_page(state: SessionState) -> PageParser:
    if state.current_url is None or state.body is None:
        raise ContractError(ErrorCode.NOT_FOUND, "browser session has no current page")
    parser = PageParser()
    parser.feed(decode_page(state.body, state.charset))
    parser.close()
    return parser


def page_summary(state: SessionState) -> dict[str, JsonValue]:
    parser = parse_page(state)
    return {
        "session_id": state.ref.session_id,
        "url": cast(str, state.current_url),
        "status_code": cast(int, state.status_code),
        "title": parser.title,
        "content_type": state.content_type,
        "size_bytes": len(state.body or b""),
        "content_trust": CONTENT_TRUST,
    }


def decode_page(data: bytes, charset: str) -> str:
    try:
        return data.decode(charset, errors="replace")
    except LookupError:
        return data.decode("utf-8", errors="replace")
