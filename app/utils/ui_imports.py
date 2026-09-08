# app/utils/ui_imports.py
"""
Centralized UI imports and styling constants.
All UI files should use: from app.utils.ui_imports import *
"""

# ===== PyQt6 Widget Imports =====
from PyQt6.QtWidgets import (
    QApplication,
    QWidget,
    QMainWindow,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QGroupBox,
    QScrollArea,
    QLabel,
    QLineEdit,
    QSpinBox,
    QComboBox,
    QPushButton,
    QTextEdit,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QListWidget,
    QListWidgetItem,
    QStackedWidget,
    QTabWidget,
    QSplitter,
    QMessageBox,
    QInputDialog,
    QAbstractItemView,
    QFileDialog,
    QMenu,
    QFrame,
    QSizePolicy,
)

# ===== PyQt6 Core Imports =====
from PyQt6.QtCore import (
    Qt,
    QTimer,
    QThreadPool,
    QDateTime,
    pyqtSignal,
    QObject,
)

# ===== PyQt6 GUI Imports =====
from PyQt6.QtGui import (
    QFont,
    QBrush,
    QColor,
    QPainter,
)

# ===== Standard Library =====
import sqlite3
import datetime
import logging
from typing import (
    Optional,
    Dict,
    Any,
    List,
    Tuple,
)

# ===== Color Theme Constants =====
COLOR_PRIMARY = "#1c70b7"
COLOR_PRIMARY_DARK = "#155a8a"
COLOR_PRIMARY_HOVER = "#1668a3"
COLOR_BACKGROUND = "#eae0d5"
COLOR_TEXT = "#2c3e50"
COLOR_TEXT_LIGHT = "#5a6c7d"
COLOR_BORDER = "#d0c7bd"
COLOR_WHITE = "#ffffff"
COLOR_SUCCESS = "#27ae60"
COLOR_WARNING = "#f39c12"
COLOR_ERROR = "#e74c3c"
COLOR_INFO = "#3498db"

# ===== Font Constants =====
FONT_TITLE_LARGE = ("Arial", 18, QFont.Weight.Bold)
FONT_TITLE = ("Arial", 16, QFont.Weight.Bold)
FONT_HEADING = ("Arial", 14, QFont.Weight.Bold)
FONT_SUBHEADING = ("Arial", 12, QFont.Weight.Bold)
FONT_NORMAL = ("Arial", 11, QFont.Weight.Normal)
FONT_SMALL = ("Arial", 10, QFont.Weight.Normal)

# ===== Spacing Constants =====
SPACING_SMALL = 4
SPACING_NORMAL = 8
SPACING_LARGE = 12
SPACING_XLARGE = 20

MARGIN_SMALL = 6
MARGIN_NORMAL = 12
MARGIN_LARGE = 20

# ===== Size Constants =====
SIDEBAR_WIDTH = 240
HEADER_HEIGHT = 60
BUTTON_MIN_WIDTH = 100
BUTTON_MIN_HEIGHT = 36

APP_WIDTH = 1400
APP_HEIGHT = 850

# ===== Stylesheet Templates =====
STYLE_MAIN_WINDOW = f"""
    QWidget {{
        background-color: {COLOR_BACKGROUND};
        font-family: Arial;
    }}
    QLabel {{
        color: {COLOR_TEXT};
    }}
    QPushButton {{
        background-color: {COLOR_PRIMARY};
        color: {COLOR_WHITE};
        padding: 8px 14px;
        border-radius: 6px;
        font-weight: bold;
        border: none;
        min-width: {BUTTON_MIN_WIDTH}px;
        min-height: {BUTTON_MIN_HEIGHT}px;
    }}
    QPushButton:hover {{
        background-color: {COLOR_PRIMARY_DARK};
    }}
    QPushButton:pressed {{
        background-color: {COLOR_PRIMARY_DARK};
    }}
    QLineEdit {{
        padding: 8px;
        border-radius: 4px;
        border: 1px solid {COLOR_BORDER};
        background-color: {COLOR_WHITE};
        color: {COLOR_TEXT};
    }}
    QLineEdit:focus {{
        border: 2px solid {COLOR_PRIMARY};
    }}
    QComboBox {{
        padding: 6px;
        border-radius: 4px;
        border: 1px solid {COLOR_BORDER};
        background-color: {COLOR_WHITE};
        color: {COLOR_TEXT};
    }}
    QComboBox:focus {{
        border: 2px solid {COLOR_PRIMARY};
    }}
    QTextEdit {{
        padding: 6px;
        border-radius: 4px;
        border: 1px solid {COLOR_BORDER};
        background-color: {COLOR_WHITE};
        color: {COLOR_TEXT};
    }}
    QTableWidget {{
        border-radius: 4px;
        border: 1px solid {COLOR_BORDER};
        gridline-color: {COLOR_BORDER};
    }}
    QTableWidget::item {{
        padding: 6px;
    }}
    QHeaderView::section {{
        background-color: {COLOR_PRIMARY};
        color: {COLOR_WHITE};
        padding: 6px;
        border: none;
        font-weight: bold;
    }}
    QListWidget {{
        border-radius: 4px;
        border: 1px solid {COLOR_BORDER};
        background-color: {COLOR_WHITE};
    }}
    QListWidget::item {{
        padding: 8px;
    }}
    QListWidget::item:selected {{
        background-color: {COLOR_PRIMARY};
        color: {COLOR_WHITE};
    }}
    QTabWidget::pane {{
        border: 1px solid {COLOR_BORDER};
    }}
    QTabBar::tab {{
        background-color: {COLOR_BORDER};
        color: {COLOR_TEXT};
        padding: 8px 20px;
        border: none;
    }}
    QTabBar::tab:selected {{
        background-color: {COLOR_PRIMARY};
        color: {COLOR_WHITE};
        font-weight: bold;
    }}
    QTabBar::tab:hover {{
        background-color: {COLOR_PRIMARY_HOVER};
    }}
"""

STYLE_SIDEBAR = f"""
    QListWidget {{
        background-color: {COLOR_PRIMARY};
        color: {COLOR_WHITE};
        border: none;
        font-size: 14px;
        outline: none;
    }}
    QListWidget::item {{
        padding: 15px 12px;
        margin: 0px;
    }}
    QListWidget::item:selected {{
        background-color: {COLOR_PRIMARY_DARK};
        font-weight: bold;
        border-left: 3px solid {COLOR_WHITE};
        margin: 0px;
    }}
    QListWidget::item:hover {{
        background-color: {COLOR_PRIMARY_HOVER};
    }}
"""

STYLE_HEADER = f"""
    QWidget {{
        background-color: {COLOR_WHITE};
        border-bottom: 2px solid {COLOR_BORDER};
    }}
    QLabel {{
        color: {COLOR_TEXT};
    }}
"""

STYLE_DIALOG = f"""
    QDialog {{
        background-color: {COLOR_BACKGROUND};
    }}
    QLabel {{
        color: {COLOR_PRIMARY};
        font-weight: bold;
    }}
    QLineEdit {{
        padding: 8px;
        border-radius: 6px;
        border: 1px solid {COLOR_BORDER};
        background-color: {COLOR_WHITE};
    }}
    QPushButton {{
        background-color: {COLOR_PRIMARY};
        color: {COLOR_WHITE};
        padding: 8px;
        border-radius: 6px;
        font-weight: bold;
        border: none;
    }}
    QPushButton:hover {{
        background-color: {COLOR_PRIMARY_DARK};
    }}
    QPushButton:pressed {{
        background-color: {COLOR_PRIMARY_DARK};
    }}
"""

# ===== Utility Functions =====
def set_font(widget, size: int, bold: bool = False, italic: bool = False):
    """Set font for a widget.
    
    Args:
        widget: Qt widget
        size: Font size
        bold: Whether font should be bold
        italic: Whether font should be italic
    """
    font = QFont("Arial", size)
    if bold:
        font.setBold(True)
    if italic:
        font.setItalic(True)
    widget.setFont(font)


def create_section_header(text: str, parent=None) -> QLabel:
    """Create a styled section header label.
    
    Args:
        text: Header text
        parent: Parent widget
        
    Returns:
        Styled QLabel for section header
    """
    label = QLabel(text, parent)
    font = QFont("Arial", 12)
    font.setBold(True)
    label.setFont(font)
    label.setStyleSheet(f"color: {COLOR_PRIMARY}; margin-top: 10px; margin-bottom: 5px;")
    return label


def create_primary_button(text: str, parent=None) -> QPushButton:
    """Create a styled primary button.
    
    Args:
        text: Button text
        parent: Parent widget
        
    Returns:
        Styled QPushButton
    """
    btn = QPushButton(text, parent)
    btn.setMinimumHeight(BUTTON_MIN_HEIGHT)
    return btn


def create_secondary_button(text: str, parent=None) -> QPushButton:
    """Create a styled secondary button.
    
    Args:
        text: Button text
        parent: Parent widget
        
    Returns:
        Styled QPushButton with secondary styling
    """
    btn = QPushButton(text, parent)
    btn.setMinimumHeight(BUTTON_MIN_HEIGHT)
    btn.setStyleSheet(f"""
        QPushButton {{
            background-color: {COLOR_BORDER};
            color: {COLOR_TEXT};
            padding: 8px 14px;
            border-radius: 6px;
            font-weight: normal;
            border: none;
        }}
        QPushButton:hover {{
            background-color: #c0b8af;
        }}
    """)
    return btn


def create_danger_button(text: str, parent=None) -> QPushButton:
    """Create a styled danger button.
    
    Args:
        text: Button text
        parent: Parent widget
        
    Returns:
        Styled QPushButton with danger styling
    """
    btn = QPushButton(text, parent)
    btn.setMinimumHeight(BUTTON_MIN_HEIGHT)
    btn.setStyleSheet(f"""
        QPushButton {{
            background-color: {COLOR_ERROR};
            color: {COLOR_WHITE};
            padding: 8px 14px;
            border-radius: 6px;
            font-weight: bold;
            border: none;
        }}
        QPushButton:hover {{
            background-color: #c0392b;
        }}
    """)
    return btn


def get_logger(name: str):
    """Get or create a logger for a module.
    
    Args:
        name: Logger name (typically __name__)
        
    Returns:
        Logger instance
    """
    return logging.getLogger(name)
