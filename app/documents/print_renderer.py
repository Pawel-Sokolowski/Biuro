"""Qt print operations delivered to the GUI thread from the export worker."""
from pathlib import Path
from io import BytesIO
import threading

from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot, QTimer, QMarginsF, QSizeF, QPointF
from PyQt6.QtGui import QFont, QFontMetricsF, QPageSize, QPageLayout, QPainter, QPdfWriter


def trim_empty_browser_tail(content):
    """Remove only empty trailing pages emitted by the trusted HTML print job."""
    from pypdf import PdfReader, PdfWriter
    reader = PdfReader(BytesIO(content))
    end = len(reader.pages)
    while end:
        page = reader.pages[end - 1]
        stream = page.get_contents()
        if page.get('/Annots') or (stream is not None and stream.get_data().strip()):
            break
        end -= 1
    if not end:
        raise ValueError('Wizualizacja formularza nie zawiera treści do wydruku.')
    if end == len(reader.pages):
        return content
    writer, output = PdfWriter(), BytesIO()
    try:
        for page in reader.pages[:end]:
            writer.add_page(page)
        writer.write(output)
        return output.getvalue()
    finally:
        writer.close()


def paint_overlay(pages, destination):
    """Vector text overlay in PDF points; called on the application's GUI thread."""
    writer = QPdfWriter(str(destination))
    writer.setResolution(72)
    writer.setTitle('Dane pełnomocnictwa')
    painter = QPainter()
    try:
        for index, page in enumerate(pages):
            width, height = page['size']
            layout = QPageLayout(QPageSize(QSizeF(width, height), QPageSize.Unit.Point),
                                 QPageLayout.Orientation.Portrait, QMarginsF(0, 0, 0, 0), QPageLayout.Unit.Point)
            layout.setMode(QPageLayout.Mode.FullPageMode)
            writer.setPageLayout(layout)
            if index == 0:
                if not painter.begin(writer):
                    raise RuntimeError('Nie można utworzyć warstwy PDF.')
            elif not writer.newPage():
                raise RuntimeError('Nie można utworzyć kolejnej strony PDF.')
            for command in page['text']:
                x, y, text, max_width = command
                text = str(text or '')
                if not text:
                    continue
                font = QFont('Arial')
                size = 12.0
                while True:
                    font.setPointSizeF(size)
                    metrics = QFontMetricsF(font, writer)
                    if metrics.horizontalAdvance(text) <= max_width:
                        break
                    size -= 0.25
                    if size < 7.5:
                        raise ValueError('Wartość nie mieści się w polu formularza: ' + text[:45])
                painter.setFont(font)
                painter.drawText(QPointF(x, height - y), text)
        if not pages:
            raise ValueError('Brak stron formularza.')
    finally:
        if painter.isActive():
            painter.end()


class PrintRenderer(QObject):
    requested = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.requested.connect(self._start)
        self._pages = set()

    def render(self, operation, source, destination):
        """Worker-thread entry point; Qt work remains on the GUI thread."""
        request = dict(operation=operation, source=source, destination=str(destination),
                       done=threading.Event(), error=None)
        self.requested.emit(request)
        if not request['done'].wait(65):
            raise RuntimeError('Przekroczono czas generowania formularza PDF.')
        if request['error'] is not None:
            raise request['error']
        return str(destination)

    @pyqtSlot(object)
    def _start(self, request):
        try:
            if request['operation'] == 'overlay':
                paint_overlay(request['source'], request['destination'])
                request['done'].set()
                return
            if request['operation'] != 'html':
                raise ValueError('Nieznana operacja wydruku.')
            from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings, QWebEngineUrlRequestInterceptor

            class LocalOnly(QWebEngineUrlRequestInterceptor):
                def interceptRequest(self, info):
                    if info.requestUrl().scheme() not in ('data', 'about'):
                        info.block(True)

            profile = QWebEngineProfile(self)
            interceptor = LocalOnly(profile)
            profile._interceptor = interceptor
            profile.setUrlRequestInterceptor(interceptor)
            page = QWebEnginePage(profile, self)
            self._pages.add(page)
            page.settings().setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, False)
            page.settings().setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, False)
            timer = QTimer(page)
            timer.setSingleShot(True)
            finished = False

            def finish(error=None):
                nonlocal finished
                if finished:
                    return
                finished = True
                timer.stop()
                request['error'] = error
                self._pages.discard(page)
                page.deleteLater()
                # Delete the profile after its page releases it.
                page.destroyed.connect(profile.deleteLater)
                request['done'].set()

            def printed(content):
                if finished:
                    return
                try:
                    if not content:
                        raise RuntimeError('Nie powstał PDF formularza.')
                    Path(request['destination']).write_bytes(trim_empty_browser_tail(bytes(content)))
                    finish()
                except Exception as error:
                    finish(error)

            def loaded(ok):
                if finished:
                    return
                if not ok:
                    finish(RuntimeError('Nie można załadować wizualizacji formularza.'))
                    return
                layout = QPageLayout(QPageSize(QPageSize.PageSizeId.A4), QPageLayout.Orientation.Portrait, QMarginsF(8, 8, 8, 8))
                page.printToPdf(printed, layout)

            page.loadFinished.connect(loaded)
            timer.timeout.connect(lambda: finish(RuntimeError('Przekroczono czas drukowania formularza.')))
            timer.start(60000)
            page.setHtml(request['source'])
        except Exception as error:
            request['error'] = error
            request['done'].set()
