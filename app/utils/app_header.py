"""UI utilities for header with user info and time tracker."""

import logging
from typing import Optional, Callable
import datetime

from app.utils.ui_imports import *
from app.db.dao_employees import get_employee_info

logger = logging.getLogger(__name__)


class TimeTracker(QWidget):
    """Time tracking widget for accounting/HR tasks."""
    
    time_updated = pyqtSignal(str, int)  # Signal: (category, seconds)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.is_tracking = False
        self.current_category: Optional[str] = None
        self.start_time: Optional[datetime] = None
        self.elapsed_seconds = 0
        self.timer = None
        self.categories = ["Księgowanie", "Kadry", "Czynności dodatkowe"]
        
        self._build_ui()
    
    def _build_ui(self):
        """Build time tracker UI."""
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACING_NORMAL)
        
        # Time display
        self.lbl_time = QLabel("⏱️ 00:00:00")
        self.lbl_time.setStyleSheet(f"font-size: 13px; font-weight: bold; color: {COLOR_PRIMARY};")
        self.lbl_time.setMinimumWidth(100)
        layout.addWidget(self.lbl_time)
        
        # Category selector
        self.combo_category = QComboBox()
        self.combo_category.addItems(self.categories)
        self.combo_category.setMaximumWidth(150)
        self.combo_category.setEnabled(False)
        layout.addWidget(self.combo_category)
        
        # Start/Stop button
        self.btn_toggle = create_primary_button("▶️ Start")
        self.btn_toggle.setMaximumWidth(100)
        self.btn_toggle.clicked.connect(self._toggle_tracking)
        layout.addWidget(self.btn_toggle)
        
        # Reset button
        self.btn_reset = create_secondary_button("🔄 Reset")
        self.btn_reset.setMaximumWidth(100)
        self.btn_reset.setEnabled(False)
        self.btn_reset.clicked.connect(self._reset_tracking)
        layout.addWidget(self.btn_reset)
    
    def _toggle_tracking(self):
        """Start/stop time tracking."""
        if not self.is_tracking:
            self._start_tracking()
        else:
            self._stop_tracking()
    
    def _start_tracking(self):
        """Start tracking time."""
        self.current_category = self.combo_category.currentText()
        self.start_time = datetime.datetime.now()
        self.is_tracking = True
        self.combo_category.setEnabled(False)
        self.btn_toggle.setText("⏸️ Stop")
        self.btn_toggle.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLOR_WARNING};
                color: white;
                padding: 6px 12px;
                border-radius: 4px;
                font-weight: bold;
                border: none;
            }}
            QPushButton:hover {{ background-color: #e67e22; }}
        """)
        self.btn_reset.setEnabled(True)
        
        # Start timer to update display
        if not self.timer:
            self.timer = QTimer()
            self.timer.timeout.connect(self._update_time_display)
        self.timer.start(1000)  # Update every second
        
        logger.info(f"Time tracking started: {self.current_category}")
    
    def _stop_tracking(self):
        """Stop tracking time."""
        if self.timer:
            self.timer.stop()
        
        if self.start_time:
            elapsed = (datetime.datetime.now() - self.start_time).total_seconds()
            self.elapsed_seconds += int(elapsed)
            self.time_updated.emit(self.current_category, self.elapsed_seconds)
        
        self.is_tracking = False
        self.combo_category.setEnabled(True)
        self.btn_toggle.setText("▶️ Start")
        self.btn_toggle.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLOR_PRIMARY};
                color: white;
                padding: 6px 12px;
                border-radius: 4px;
                font-weight: bold;
                border: none;
            }}
            QPushButton:hover {{ background-color: {COLOR_PRIMARY_DARK}; }}
        """)
        
        logger.info(f"Time tracking stopped: {self.current_category} - {self.elapsed_seconds}s")
    
    def _update_time_display(self):
        """Update time display."""
        if self.start_time:
            elapsed = (datetime.datetime.now() - self.start_time).total_seconds() + self.elapsed_seconds
            hours = int(elapsed // 3600)
            minutes = int((elapsed % 3600) // 60)
            seconds = int(elapsed % 60)
            self.lbl_time.setText(f"⏱️ {hours:02d}:{minutes:02d}:{seconds:02d}")
    
    def _reset_tracking(self):
        """Reset tracking."""
        if self.is_tracking:
            self._stop_tracking()
        
        self.elapsed_seconds = 0
        self.lbl_time.setText("⏱️ 00:00:00")
        self.current_category = None
        self.start_time = None
        self.btn_reset.setEnabled(False)
        
        logger.info("Time tracking reset")
    
    def get_tracked_time(self) -> int:
        """Get total tracked seconds."""
        return self.elapsed_seconds
    
    def get_category(self) -> Optional[str]:
        """Get current tracking category."""
        return self.current_category


class AppHeader(QWidget):
    """Application header with user info and time tracker."""
    
    def __init__(self, pracownik_id: int, parent=None):
        super().__init__(parent)
        self.pracownik_id = pracownik_id
        self.emp_info = None
        self.time_tracker = None
        
        self._build_ui()
    
    def _build_ui(self):
        """Build header UI."""
        self.setStyleSheet(STYLE_HEADER)
        self.setFixedHeight(HEADER_HEIGHT)
        
        layout = QHBoxLayout(self)
        layout.setContentsMargins(MARGIN_LARGE, 8, MARGIN_LARGE, 8)
        layout.setSpacing(SPACING_LARGE)
        
        # Left: User info
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(0)
        
        try:
            self.emp_info = get_employee_info(self.pracownik_id)
            if self.emp_info:
                name = f"{self.emp_info.get('imie', '')} {self.emp_info.get('nazwisko', '')}".strip()
            else:
                name = "Nieznany"
        except Exception as e:
            logger.error(f"Failed to get employee info: {e}")
            name = "Błąd"
        
        # User name
        lbl_user = QLabel(f"<b>Zalogowany użytkownik:</b> {name}")
        lbl_user.setStyleSheet(f"font-size: 12px; color: {COLOR_TEXT}; font-weight: bold;")
        left_layout.addWidget(lbl_user)
        
        # User role
        role_text = "Administrator" if self.pracownik_id == 1 else "Pracownik"  # Simple check
        lbl_role = QLabel(f"<b>Rola:</b> {role_text}")
        lbl_role.setStyleSheet(f"font-size: 11px; color: {COLOR_TEXT_LIGHT};")
        left_layout.addWidget(lbl_role)
        
        layout.addWidget(left_widget, 1)
        
        # Right: Time tracker
        self.time_tracker = TimeTracker()
        layout.addWidget(self.time_tracker, 2)
        
        # Stretch
        layout.addStretch()
    
    def get_time_tracker(self) -> TimeTracker:
        """Get time tracker widget."""
        return self.time_tracker
