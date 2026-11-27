"""Safe header sorting helpers for Qt table widgets."""

from contextlib import ExitStack
from datetime import date
from decimal import Decimal, InvalidOperation
import re

from PyQt6.QtCore import QSignalBlocker, Qt
from PyQt6.QtWidgets import QTableWidget, QTableWidgetItem

from app.utils.polish_sort import polish_sort_key


ASCENDING = Qt.SortOrder.AscendingOrder
DESCENDING = Qt.SortOrder.DescendingOrder


def sort_value(value):
    text = str(value if value is not None else "").strip()

    if re.fullmatch(
        r"\d{4}-\d{2}-\d{2}(?:[ T].*)?",
        text,
    ):
        try:
            return (
                0,
                date.fromisoformat(text[:10]).toordinal(),
                text[10:],
            )
        except ValueError:
            pass

    local_date = re.fullmatch(
        r"(\d{1,2})[./](\d{1,2})[./](\d{4})([ T].*)?",
        text,
    )

    if local_date:
        day, month, year = map(
            int,
            local_date.groups()[:3],
        )

        try:
            return (
                0,
                date(year, month, day).toordinal(),
                local_date.group(4) or "",
            )
        except ValueError:
            pass

    if re.fullmatch(
        r"\d+:\d{2}(?::\d{2})?",
        text,
    ):
        parts = [
            int(part)
            for part in text.split(":")
        ]

        seconds = (
            parts[0] * 3600
            + parts[1] * 60
            + (parts[2] if len(parts) == 3 else 0)
        )

        return 1, seconds

    number = text
    number = re.sub(
        r"\s*(?:PLN|zł|%)\s*$",
        "",
        number,
        flags=re.IGNORECASE,
    )
    number = number.replace("\u00a0", "")
    number = number.replace(" ", "")
    number = number.replace(",", ".")

    if re.fullmatch(
        r"[+-]?\d+(?:\.\d+)?",
        number,
    ):
        try:
            return 1, Decimal(number)
        except InvalidOperation:
            pass

    return 2, polish_sort_key(text)


class SortableItem(QTableWidgetItem):
    def __lt__(self, other):
        if not isinstance(other, QTableWidgetItem):
            return NotImplemented

        own_check_state = self.data(
            Qt.ItemDataRole.CheckStateRole
        )
        other_check_state = other.data(
            Qt.ItemDataRole.CheckStateRole
        )

        if (
            own_check_state is not None
            and other_check_state is not None
        ):
            return (
                self.checkState().value
                < other.checkState().value
            )

        own_value = self.data(
            Qt.ItemDataRole.DisplayRole
        )
        other_value = other.data(
            Qt.ItemDataRole.DisplayRole
        )

        return sort_value(own_value) < sort_value(other_value)


def enable_header_sort(
    view,
    *legacy_indicator_headers,
    indicator_headers=(),
):
    """
    Enable guarded sorting on the main table header.

    Additional headers are used only for displaying the current sort
    indicator. They are not connected to the sorting callback.

    The positional additional-header argument remains supported for
    compatibility with existing callers, but those headers are treated
    only as indicator headers.
    """
    main_header = view.horizontalHeader()

    additional_headers = tuple(
        header
        for header in (
            *legacy_indicator_headers,
            *indicator_headers,
        )
        if header is not None and header is not main_header
    )

    unique_additional_headers = []
    seen_header_ids = set()

    for header in additional_headers:
        header_id = id(header)

        if header_id in seen_header_ids:
            continue

        seen_header_ids.add(header_id)
        unique_additional_headers.append(header)

    view._sort_indicator_headers = tuple(
        unique_additional_headers
    )
    view._header_sort = (-1, DESCENDING)
    view._header_sort_in_progress = False

    headers = (
        main_header,
        *view._sort_indicator_headers,
    )

    for header in headers:
        header.setSortIndicator(-1, ASCENDING)
        header.setSortIndicatorShown(False)

    main_header.setSectionsClickable(True)

    def sort_from_main_header(column):
        apply_header_sort(
            view,
            column=column,
            toggle=True,
        )

    # Keep a reference to the callback on the view. This prevents accidental
    # loss of the Python callable and also makes the connection explicit.
    view._header_sort_callback = sort_from_main_header

    main_header.sectionDoubleClicked.connect(
        sort_from_main_header
    )


def apply_header_sort(
    view,
    column=None,
    toggle=False,
):
    """
    Apply the selected sort safely.

    For QTableWidget, sorting rearranges the widget's internal items.
    During that operation, outgoing view and selection signals are blocked
    and painting is paused for every view sharing the table model.
    """
    if getattr(
        view,
        "_header_sort_in_progress",
        False,
    ):
        return

    model = view.model()

    if model is None:
        return

    column_count = model.columnCount()

    if column is not None:
        if column < 0 or column >= column_count:
            return

        previous_column, previous_order = getattr(
            view,
            "_header_sort",
            (-1, DESCENDING),
        )

        if toggle:
            if (
                previous_column == column
                and previous_order == ASCENDING
            ):
                order = DESCENDING
            else:
                order = ASCENDING
        else:
            if previous_column == column:
                order = previous_order
            else:
                order = ASCENDING

        view._header_sort = (column, order)

    selected = getattr(
        view,
        "_header_sort",
        None,
    )

    if selected is None:
        return

    selected_column, selected_order = selected

    if (
        selected_column < 0
        or selected_column >= column_count
    ):
        return

    main_header = view.horizontalHeader()
    indicator_headers = tuple(
        getattr(
            view,
            "_sort_indicator_headers",
            (),
        )
    )

    companion_views = tuple(
        companion
        for companion in getattr(
            view,
            "_sort_companion_views",
            (),
        )
        if companion is not None
    )

    all_views = (view, *companion_views)

    view._header_sort_in_progress = True

    scroll_positions = []

    for table_view in all_views:
        scroll_positions.append(
            (
                table_view,
                table_view.verticalScrollBar().value(),
                table_view.horizontalScrollBar().value(),
            )
        )

    try:
        for table_view in all_views:
            table_view.setUpdatesEnabled(False)

        with ExitStack() as stack:
            blocked_objects = []

            for table_view in all_views:
                blocked_objects.append(table_view)

            selection_model = view.selectionModel()

            if selection_model is not None:
                blocked_objects.append(selection_model)

            seen_object_ids = set()

            for obj in blocked_objects:
                object_id = id(obj)

                if object_id in seen_object_ids:
                    continue

                seen_object_ids.add(object_id)
                stack.enter_context(QSignalBlocker(obj))

            if isinstance(view, QTableWidget):
                view.sortItems(
                    selected_column,
                    selected_order,
                )
            else:
                model.sort(
                    selected_column,
                    selected_order,
                )

        for header in (
            main_header,
            *indicator_headers,
        ):
            header.setSortIndicator(
                selected_column,
                selected_order,
            )
            header.setSortIndicatorShown(True)

    finally:
        for (
            table_view,
            vertical_position,
            horizontal_position,
        ) in scroll_positions:
            table_view.verticalScrollBar().setValue(
                vertical_position
            )
            table_view.horizontalScrollBar().setValue(
                horizontal_position
            )

        for table_view in reversed(all_views):
            table_view.setUpdatesEnabled(True)
            table_view.viewport().update()

        sync_rows = getattr(
            view,
            "_sync_rows",
            None,
        )

        if callable(sync_rows):
            sync_rows()

        position_frozen = getattr(
            view,
            "_position_frozen",
            None,
        )

        if callable(position_frozen):
            position_frozen()

        view._header_sort_in_progress = False