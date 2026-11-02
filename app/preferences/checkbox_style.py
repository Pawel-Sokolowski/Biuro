"""Paint checkbox indicators without platform theme assets (also in item views)."""
from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QPainter, QPainterPath, QPalette, QPen
from PyQt6.QtWidgets import QProxyStyle, QStyle


class CheckboxStyle(QProxyStyle):
    def __init__(self):
        super().__init__("Fusion")

    def drawPrimitive(self, element, option, painter, widget=None):
        if element not in (QStyle.PrimitiveElement.PE_IndicatorCheckBox,
                           QStyle.PrimitiveElement.PE_IndicatorItemViewItemCheck):
            return super().drawPrimitive(element, option, painter, widget)
        state = option.state
        enabled = bool(state & QStyle.StateFlag.State_Enabled)
        checked = bool(state & QStyle.StateFlag.State_On)
        partial = bool(state & QStyle.StateFlag.State_NoChange)
        group = QPalette.ColorGroup.Active if enabled else QPalette.ColorGroup.Disabled
        palette = option.palette
        background = palette.color(group, QPalette.ColorRole.Base)
        foreground = palette.color(group, QPalette.ColorRole.Text)
        if checked or partial:
            background = palette.color(group, QPalette.ColorRole.Highlight)
            foreground = palette.color(group, QPalette.ColorRole.HighlightedText)
        # Keep a contrasting outline even on a selected table row.
        outline = palette.color(group, QPalette.ColorRole.Text)
        if state & QStyle.StateFlag.State_Selected:
            outline = palette.color(group, QPalette.ColorRole.HighlightedText)
        size = min(option.rect.width(), option.rect.height(), 16)
        rect = QRectF(0, 0, size - 2, size - 2)
        rect.moveCenter(QPointF(option.rect.center()))
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(outline, 1.2))
        painter.setBrush(background)
        painter.drawRoundedRect(rect, 2, 2)
        painter.setPen(QPen(foreground, 2, Qt.PenStyle.SolidLine,
                            Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        if partial:
            painter.drawLine(QPointF(rect.left() + size * .23, rect.center().y()),
                             QPointF(rect.right() - size * .23, rect.center().y()))
        elif checked:
            path = QPainterPath(QPointF(rect.left() + size * .2, rect.top() + size * .45))
            path.lineTo(rect.left() + size * .38, rect.top() + size * .65)
            path.lineTo(rect.left() + size * .7, rect.top() + size * .25)
            painter.drawPath(path)
        painter.restore()
