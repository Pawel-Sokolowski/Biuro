"""Podgląd treści wiadomości w Chromium: blokada zdalnych zasobów i linki kontaktów."""
from __future__ import annotations

import base64
import re
from html import escape as html_escape
from typing import Any, Dict
from urllib.parse import unquote

from PyQt6.QtCore import QCoreApplication, QUrl, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QDesktopServices

# QtWebEngine wymaga tej flagi przed utworzeniem QApplication. Ustawiamy ją przy imporcie
# modułu, ale nie nadpisujemy wcześniejszej decyzji aplikacji (np. w testach).
QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)

from PyQt6.QtWebEngineCore import (
    QWebEnginePage,
    QWebEngineProfile,
    QWebEngineSettings,
    QWebEngineUrlRequestInterceptor,
)
from PyQt6.QtWebEngineWidgets import QWebEngineView


class _RemoteContentBlocker(QWebEngineUrlRequestInterceptor):
    """Blokuje zdalne zasoby (piksele śledzące, obrazy, CSS), dopóki użytkownik ich nie zaakceptuje."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.allow_remote = False

    def interceptRequest(self, info) -> None:
        if not self.allow_remote and info.requestUrl().scheme().lower() in ("http", "https", "ftp"):
            info.block(True)


class _MailWebPage(QWebEnginePage):
    def acceptNavigationRequest(self, url, navigation_type, is_main_frame):
        scheme = url.scheme().lower()
        if navigation_type == QWebEnginePage.NavigationType.NavigationTypeLinkClicked:
            if scheme == "biuro-images":
                view = self.parent()
                if hasattr(view, "load_remote_images"):
                    view.load_remote_images()
            elif scheme == 'biuro-contact':
                view = self.parent()
                target = getattr(view, 'contact_links', {}).get(url.path())
                if target is not None:
                    view.address_clicked.emit(*target)
            elif scheme in ("http", "https", "mailto"):
                QDesktopServices.openUrl(url)
            return False
        return scheme in ("about", "data")


class MailBrowser(QWebEngineView):
    """Podgląd HTML wiadomości w Chromium; JavaScript, pliki i zdalne zasoby są domyślnie blokowane."""
    address_clicked = pyqtSignal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.resources: Dict[str, Any] = {}
        self.contact_links = {}
        self._last_html = ""
        self._last_header = ""
        # Osobny profil bez zapisu na dysku, żeby blokada nie dotyczyła innych widoków aplikacji.
        self._profile = QWebEngineProfile(self)
        self._blocker = _RemoteContentBlocker(self)
        self._profile.setUrlRequestInterceptor(self._blocker)
        self.setPage(_MailWebPage(self._profile, self))
        self.page().setBackgroundColor(QColor("white"))
        settings = self.settings()
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.PluginsEnabled, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.AutoLoadImages, True)

    def load_remote_images(self) -> None:
        self._blocker.allow_remote = True
        self.setHtml(self.prepare_html(self._last_html, self.resources, self._last_header))

    def show_html(self, html: str, header: str = "") -> None:
        self._blocker.allow_remote = False
        self._last_html, self._last_header = html, header
        if re.search(r"(?i)(https?:)?//[^\s\"'<>)]+", html or "") and re.search(
            r"(?i)<img\b|url\(|<link\b", html or ""
        ):
            header += (
                "<div style='margin:4px 0'><a href='biuro-images:load'>"
                "Zdalne obrazy zablokowane – kliknij, aby załadować</a></div>"
            )
        self.setHtml(self.prepare_html(html, self.resources, header))

    def setPlainText(self, text: str) -> None:
        self.setHtml(
            "<!doctype html><html><head><meta charset='utf-8'></head>"
            "<body><pre style='white-space:pre-wrap'>"
            + html_escape(text)
            + "</pre></body></html>"
        )

    def clear(self) -> None:
        self.contact_links = {}
        self.setHtml("")

    def contextMenuEvent(self, event):
        request = self.lastContextMenuRequest()
        url = request.linkUrl() if request is not None else QUrl()
        target = self.contact_links.get(url.path()) if url.scheme() == 'biuro-contact' else None
        if target is not None:
            self.address_clicked.emit(*target)
            event.accept()
            return
        super().contextMenuEvent(event)

    @staticmethod
    def prepare_html(html: str, resources: Dict[str, Any], header: str = "") -> str:
        resource_map = {key.strip("<> ").casefold(): value for key, value in resources.items()}

        def replace_cid(match):
            key = unquote(match.group(1)).strip("<> ").casefold()
            entry = resource_map.get(key)
            if not entry:
                return match.group(0)
            payload, mime_type = entry
            encoded = base64.b64encode(payload).decode("ascii")
            return f"data:{mime_type};base64,{encoded}"

        html = re.sub(r"(?i)cid:([^\"'\s)>]+)", replace_cid, html)
        header_html = (
            "<div style='font:12px Arial,sans-serif;color:#444;padding:8px;"
            "border-bottom:1px solid #bbb;margin-bottom:8px'>"
            + header
            + "</div>"
            if header
            else (
                "<div style='font:12px Arial,sans-serif;color:#444;padding:8px;"
                "border-bottom:1px solid #bbb;margin-bottom:8px'></div>"
            )
        )
        # Keep complete email documents intact and place the app's message header in their body.
        body_open = re.search(r"<body\b[^>]*>", html, re.IGNORECASE)
        if body_open:
            html = html[:body_open.end()] + header_html + html[body_open.end():]
            return html
        elif re.search(r"<html\b", html, re.IGNORECASE):
            head_end = re.search(r"</head\s*>", html, re.IGNORECASE)
            if head_end:
                html = html[:head_end.end()] + "<body>" + header_html + html[head_end.end():]
            else:
                html_open = re.search(r"<html\b[^>]*>", html, re.IGNORECASE)
                html = html[:html_open.end()] + "<body>" + header_html + html[html_open.end():]
            if not re.search(r"</body\s*>", html, re.IGNORECASE):
                close_html = re.search(r"</html\s*>", html, re.IGNORECASE)
                if close_html:
                    return html[:close_html.start()] + "</body>" + html[close_html.start():]
                return html + "</body>"
            return html
        else:
            return (
                "<!doctype html><html><head><meta charset='utf-8'>"
                "<meta name='viewport' content='width=device-width, initial-scale=1'>"
                "</head><body>" + header_html + html + "</body></html>"
            )
