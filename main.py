import sys
import os
import random
import logging
import sqlite3
from datetime import date, time, timedelta
from pathlib import Path
from typing import Optional

from PyQt5 import uic
from PyQt5.Qt import *
from PyQt5.QtCore import QTimer, QSettings
from PyQt5.QtGui import QColor


# =============================================================================
# КОНФИГУРАЦИЯ ПУТЕЙ И НАСТРОЕК
# =============================================================================

def get_resource_path(relative_path):
    """Определяет корректный путь к ресурсам для собранного приложения"""
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)


class Config:
    """Хранилище конфигурационных параметров приложения"""
    ALARM_PATH = get_resource_path("compress.mp3")
    LOG_PATH = "timeflow.log"
    DB_PATH = "Rtime.db"
    UI_PATH = get_resource_path("untitled.ui")
    ICON_PATH = get_resource_path("icon.ico")

    WORK_MINUTES = 25
    BREAK_MINUTES = 5
    LONG_BREAK_MINUTES = 15
    ROUNDS_BEFORE_LONG_BREAK = 4


# =============================================================================
# ИНИЦИАЛИЗАЦИЯ ЛОГИРОВАНИЯ И ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# =============================================================================

def setup_logging():
    """Настраивает систему логирования приложения"""
    logging.basicConfig(
        filename=Config.LOG_PATH,
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    logging.info("Запуск приложения TimeFlow")


MOTIVATIONAL_PHRASES = [
    'Единственный способ хорошо работать — это любить то, что вы делаете.',
    'Успех никогда не бывает окончательным; неудача никогда не бывает фатальной.',
    'Лучшая подготовка к завтрашнему дню — сделать все возможное сегодня',
    'Сосредоточьте все свои мысли на предстоящей работе.',
    'Люди редко добиваются успеха, если они не получают удовольствия от того, что делают',
    'Маленькие ежедневные улучшения со временем приводят к большим результатам',
    'Дисциплина — это мост между целями и их достижением',
    'Не откладывай на завтра то, что можно сделать сегодня'
]


def format_seconds_to_mmss(seconds: int) -> str:
    """Конвертирует секунды в формат MM:SS"""
    if seconds < 0:
        seconds = 0
    minutes = seconds // 60
    seconds = seconds % 60
    return f"{minutes:02d}:{seconds:02d}"


def validate_time_string(time_str: str) -> Optional[time]:
    """Проверяет валидность строки времени формата HH:MM"""
    try:
        return time.fromisoformat(time_str)
    except ValueError:
        return None


def get_week_dates(start_date: date = None) -> list[date]:
    """Генерирует список дат на неделю вперед от указанной даты"""
    if start_date is None:
        start_date = date.today()
    return [start_date + timedelta(days=i) for i in range(7)]


# =============================================================================
# РАБОТА С БАЗОЙ ДАННЫХ
# =============================================================================

class DatabaseManager:
    """Управляет операциями с базой данных SQLite"""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.init_database()

    def init_database(self):
        """Создает необходимые таблицы в базе данных"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                CREATE TABLE IF NOT EXISTS Time (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT NOT NULL,
                    task TEXT NOT NULL,
                    importance INTEGER DEFAULT 0,
                    timeStart TEXT,
                    timeEnd TEXT,
                    color TEXT DEFAULT '#e6e6e6'
                );
                """)
                conn.commit()
            logging.info("База данных инициализирована")
        except Exception as e:
            logging.exception("Ошибка при инициализации БД: %s", e)
            raise

    def execute_query(self, query: str, params: tuple = ()) -> list:
        """Выполняет SQL запрос и возвращает результат"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(query, params)
                if query.strip().upper().startswith('SELECT'):
                    return cursor.fetchall()
                conn.commit()
                return []
        except Exception as e:
            logging.exception("Ошибка выполнения запроса: %s", e)
            return []

    def cleanup_old_records(self, valid_dates: tuple):
        """Удаляет записи за пределами текущей недели"""
        placeholders = ','.join('?' for _ in valid_dates)
        query = f"DELETE FROM Time WHERE date NOT IN ({placeholders})"
        self.execute_query(query, valid_dates)
        logging.info("Очистка старых записей выполнена")


# =============================================================================
# POMODORO ТАЙМЕР
# =============================================================================

class PomodoroTimer:
    """Реализует технику Pomodoro для управления временем"""

    def __init__(self):
        self.work_seconds = Config.WORK_MINUTES * 60
        self.break_seconds = Config.BREAK_MINUTES * 60
        self.long_break_seconds = Config.LONG_BREAK_MINUTES * 60
        self.rounds_before_long_break = Config.ROUNDS_BEFORE_LONG_BREAK

        self.remaining_seconds = 0
        self.current_phase = None
        self.completed_rounds = 0
        self.total_rounds = 0

        self.timer = QTimer()
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self._tick)

        self.on_phase_changed = None
        self.on_timer_finished = None

    def start(self, total_rounds: int):
        """Запускает сессию Pomodoro с указанным количеством раундов"""
        self.total_rounds = total_rounds
        self.completed_rounds = 0
        self._start_work_phase()

    def stop(self):
        """Полностью останавливает таймер"""
        self.timer.stop()
        self.current_phase = None

    def _start_work_phase(self):
        """Инициирует рабочую фазу таймера"""
        self.current_phase = 'work'
        self.remaining_seconds = self.work_seconds
        self.timer.start()
        if self.on_phase_changed:
            self.on_phase_changed('work', self.remaining_seconds)

    def _start_break_phase(self):
        """Инициирует фазу перерыва"""
        if self.completed_rounds % self.rounds_before_long_break == 0:
            self.current_phase = 'long_break'
            self.remaining_seconds = self.long_break_seconds
        else:
            self.current_phase = 'break'
            self.remaining_seconds = self.break_seconds

        if self.on_phase_changed:
            self.on_phase_changed(self.current_phase, self.remaining_seconds)

    def _tick(self):
        """Обрабатывает каждый тик таймера (1 секунда)"""
        self.remaining_seconds -= 1

        if self.remaining_seconds <= 0:
            self._phase_finished()
        else:
            if self.on_phase_changed:
                self.on_phase_changed(self.current_phase, self.remaining_seconds)

    def _phase_finished(self):
        """Обрабатывает завершение текущей фазы"""
        self.timer.stop()

        if self.current_phase == 'work':
            self.completed_rounds += 1

            # Визуальное уведомление без звука
            if self.completed_rounds >= self.total_rounds:
                if self.on_timer_finished:
                    self.on_timer_finished()
            else:
                self._start_break_phase()
        else:
            self._start_work_phase()


# =============================================================================
# ДИАЛОГ РЕДАКТИРОВАНИЯ ЗАДАЧ
# =============================================================================

class TaskDialog(QDialog):
    """Диалоговое окно для добавления и редактирования задач"""

    def __init__(self, parent=None, mode="add", task_data=None, dates=None):
        super().__init__(parent)
        self.mode = mode
        self.task_data = task_data
        self.dates = dates or []

        self.setWindowTitle("Добавление задачи" if mode == "add" else "Редактирование задачи")
        self.setModal(True)
        self.setMinimumSize(500, 400)
        self.setup_ui()

        if mode == "edit" and task_data:
            self.load_task_data()

    def setup_ui(self):
        """Создает и настраивает элементы интерфейса диалога"""
        layout = QVBoxLayout(self)

        # Заголовок диалога
        title_label = QLabel("Добавление новой задачи" if self.mode == "add" else "Редактирование задачи")
        title_label.setStyleSheet("font-size: 16px; font-weight: bold; margin-bottom: 10px;")
        title_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(title_label)

        # Выбор даты
        date_layout = QHBoxLayout()
        date_label = QLabel("Дата:")
        date_label.setMinimumWidth(100)
        date_layout.addWidget(date_label)

        self.date_combo = QComboBox()
        self.date_combo.addItems([d.strftime('%d.%m.%Y') for d in self.dates])
        self.date_combo.setStyleSheet("QComboBox { padding: 5px; }")
        date_layout.addWidget(self.date_combo)
        layout.addLayout(date_layout)

        # Описание задачи
        task_label = QLabel("Описание задачи:")
        task_label.setMinimumWidth(100)
        layout.addWidget(task_label)

        self.task_edit = QTextEdit()
        self.task_edit.setMaximumHeight(100)
        self.task_edit.setPlaceholderText("Введите описание вашей задачи...")
        self.task_edit.setStyleSheet("QTextEdit { padding: 5px; border: 1px solid #ccc; }")
        layout.addWidget(self.task_edit)

        # Время выполнения
        time_group = QGroupBox("Время выполнения")
        time_layout = QHBoxLayout(time_group)

        start_time_layout = QVBoxLayout()
        start_time_layout.addWidget(QLabel("Начало:"))
        self.start_time_edit = QTimeEdit()
        self.start_time_edit.setTime(QTime(9, 0))
        self.start_time_edit.setDisplayFormat("HH:mm")
        self.start_time_edit.setStyleSheet("QTimeEdit { padding: 5px; }")
        start_time_layout.addWidget(self.start_time_edit)
        time_layout.addLayout(start_time_layout)

        end_time_layout = QVBoxLayout()
        end_time_layout.addWidget(QLabel("Окончание:"))
        self.end_time_edit = QTimeEdit()
        self.end_time_edit.setTime(QTime(10, 0))
        self.end_time_edit.setDisplayFormat("HH:mm")
        self.end_time_edit.setStyleSheet("QTimeEdit { padding: 5px; }")
        end_time_layout.addWidget(self.end_time_edit)
        time_layout.addLayout(end_time_layout)

        layout.addWidget(time_group)

        # Дополнительные настройки
        settings_group = QGroupBox("Настройки задачи")
        settings_layout = QVBoxLayout(settings_group)

        self.importance_check = QCheckBox("⭐ Важная задача")
        self.importance_check.setStyleSheet("QCheckBox { font-weight: bold; }")
        settings_layout.addWidget(self.importance_check)

        color_layout = QHBoxLayout()
        color_layout.addWidget(QLabel("Цвет задачи:"))
        self.color_button = QPushButton()
        self.color_button.setFixedSize(50, 30)
        self.color_button.clicked.connect(self.choose_color)
        self.current_color = QColor('#e6e6e6')
        self.update_color_button()
        color_layout.addWidget(self.color_button)
        color_layout.addStretch()
        settings_layout.addLayout(color_layout)

        layout.addWidget(settings_group)

        # Кнопки действий
        button_layout = QHBoxLayout()
        button_layout.addStretch()

        self.cancel_button = QPushButton("Отмена")
        self.cancel_button.setStyleSheet("QPushButton { padding: 8px 15px; background-color: #f0f0f0; }")
        self.cancel_button.clicked.connect(self.reject)
        button_layout.addWidget(self.cancel_button)

        self.ok_button = QPushButton("Сохранить" if self.mode == "add" else "Обновить")
        self.ok_button.setStyleSheet("QPushButton { padding: 8px 15px; background-color: #4CAF50; color: white; }")
        self.ok_button.clicked.connect(self.accept)
        button_layout.addWidget(self.ok_button)

        layout.addLayout(button_layout)

        self.task_edit.setFocus()

    def choose_color(self):
        """Открывает диалог выбора цвета для задачи"""
        color = QColorDialog.getColor(self.current_color, self, "Выберите цвет задачи")
        if color.isValid():
            self.current_color = color
            self.update_color_button()

    def update_color_button(self):
        """Обновляет внешний вид кнопки выбора цвета"""
        self.color_button.setStyleSheet(f"""
            QPushButton {{
                background-color: {self.current_color.name()};
                border: 1px solid #ccc;
                border-radius: 3px;
            }}
            QPushButton:hover {{
                border: 2px solid #0078d7;
            }}
        """)

    def load_task_data(self):
        """Загружает данные задачи для редактирования"""
        if not self.task_data:
            return

        try:
            date_str = self.task_data[1]
            index = self.date_combo.findText(date_str)
            if index >= 0:
                self.date_combo.setCurrentIndex(index)

            self.task_edit.setPlainText(self.task_data[2])

            if self.task_data[4]:
                start_time = QTime.fromString(self.task_data[4], "HH:mm")
                if start_time.isValid():
                    self.start_time_edit.setTime(start_time)

            if self.task_data[5]:
                end_time = QTime.fromString(self.task_data[5], "HH:mm")
                if end_time.isValid():
                    self.end_time_edit.setTime(end_time)

            self.importance_check.setChecked(bool(self.task_data[3]))

            if self.task_data[6] and self.task_data[6] != '#e6e6e6':
                self.current_color = QColor(self.task_data[6])
                self.update_color_button()

        except Exception as e:
            logging.exception("Ошибка при загрузке данных задачи: %s", e)
            QMessageBox.warning(self, "Ошибка", "Не удалось загрузить данные задачи")

    def get_task_data(self):
        """Возвращает собранные данные задачи в виде словаря"""
        return {
            'date': self.date_combo.currentText(),
            'task': self.task_edit.toPlainText().strip(),
            'importance': 1 if self.importance_check.isChecked() else 0,
            'timeStart': self.start_time_edit.time().toString("HH:mm"),
            'timeEnd': self.end_time_edit.time().toString("HH:mm"),
            'color': self.current_color.name()
        }

    def validate(self):
        """Проверяет корректность введенных данных"""
        data = self.get_task_data()

        if not data['task']:
            QMessageBox.warning(self, "Ошибка", "Введите описание задачи")
            self.task_edit.setFocus()
            return False

        if len(data['task']) < 3:
            QMessageBox.warning(self, "Ошибка", "Описание задачи должно содержать не менее 3 символов")
            self.task_edit.setFocus()
            return False

        start_time = self.start_time_edit.time()
        end_time = self.end_time_edit.time()

        if start_time >= end_time:
            QMessageBox.warning(self, "Ошибка", "Время начала должно быть раньше времени окончания")
            self.start_time_edit.setFocus()
            return False

        start_minutes = start_time.hour() * 60 + start_time.minute()
        end_minutes = end_time.hour() * 60 + end_time.minute()

        if (end_minutes - start_minutes) < 5:
            reply = QMessageBox.question(
                self,
                "Подтверждение",
                "Задача длится менее 5 минут. Вы уверены, что хотите сохранить?",
                QMessageBox.Yes | QMessageBox.No
            )
            if reply == QMessageBox.No:
                return False

        return True

    def accept(self):
        """Обрабатывает подтверждение диалога с валидацией"""
        if self.validate():
            super().accept()


# =============================================================================
# ГЛАВНОЕ ОКНО ПРИЛОЖЕНИЯ
# =============================================================================

class TimeManagement(QMainWindow):
    """Основной класс приложения - главное окно"""

    def __init__(self):
        super().__init__()

        self.setup_ui()
        self.setup_database()
        self.setup_pomodoro()
        self.setup_connections()

        self.settings_app = QSettings("TimeFlow", "TimeManagement")
        self.load_settings()

        self.show_day_view()
        self.cleanup_old_records()

    def setup_ui(self):
        """Инициализирует пользовательский интерфейс"""
        try:
            uic.loadUi(Config.UI_PATH, self)
        except Exception as e:
            logging.exception("Ошибка загрузки UI файла: %s", e)
            self.setup_basic_ui()

        self.setWindowTitle("TimeFlow - Управление временем")
        self.setMinimumSize(900, 600)

        self.setup_tooltips()
        self.hide_pomodoro_elements()

    def setup_basic_ui(self):
        """Создает базовый интерфейс при отсутствии UI файла"""
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)
        layout.addWidget(QLabel("Ошибка загрузки интерфейса. Проверьте файл untitled.ui"))

    def setup_tooltips(self):
        """Настраивает всплывающие подсказки для элементов интерфейса"""
        tooltips = {
            'concentrathion': 'Режим концентрации (Pomodoro)',
            'back': 'Вернуться к списку задач',
            'settings': 'Настройки Pomodoro',
            'day': 'Расписание на сегодня',
            'week': 'Расписание на неделю',
            'add': 'Добавить задачу',
            'minus': 'Удалить задачу',
            'change': 'Изменить задачу'
        }

        for widget_name, tooltip in tooltips.items():
            widget = getattr(self, widget_name, None)
            if widget:
                widget.setToolTip(tooltip)

    def setup_database(self):
        """Инициализирует подключение к базе данных"""
        self.db = DatabaseManager(Config.DB_PATH)
        self.current_week = get_week_dates()

    def setup_pomodoro(self):
        """Настраивает Pomodoro таймер"""
        self.pomodoro = PomodoroTimer()
        self.pomodoro.on_phase_changed = self.on_pomodoro_phase_changed
        self.pomodoro.on_timer_finished = self.on_pomodoro_finished

    def setup_connections(self):
        """Устанавливает связи между сигналами и слотами"""
        self.concentrathion.clicked.connect(self.show_pomodoro_dialog)
        self.back.clicked.connect(self.show_day_view)
        self.settings.clicked.connect(self.show_settings)
        self.day.clicked.connect(self.show_day_view)
        self.week.clicked.connect(self.show_week_view)
        self.add.clicked.connect(self.add_task)
        self.minus.clicked.connect(self.delete_task)
        self.change.clicked.connect(self.edit_task)

        self.motivathion.setReadOnly(True)
        self.motivathion.setAlignment(Qt.AlignCenter)
        self.show_random_phrase()

    def load_settings(self):
        """Загружает сохраненные настройки интерфейса"""
        self.restoreGeometry(self.settings_app.value("geometry", b""))
        self.restoreState(self.settings_app.value("windowState", b""))

    def save_settings(self):
        """Сохраняет текущие настройки интерфейса"""
        self.settings_app.setValue("geometry", self.saveGeometry())
        self.settings_app.setValue("windowState", self.saveState())

    def cleanup_old_records(self):
        """Очищает устаревшие записи из базы данных"""
        week_dates = tuple(d.strftime('%d.%m.%Y') for d in self.current_week)
        self.db.cleanup_old_records(week_dates)

    def show_random_phrase(self):
        """Показывает случайную мотивационную фразу"""
        self.motivathion.setText(random.choice(MOTIVATIONAL_PHRASES))

    # =========================================================================
    # УПРАВЛЕНИЕ РЕЖИМАМИ ОТОБРАЖЕНИЯ
    # =========================================================================

    def show_day_view(self):
        """Активирует режим просмотра задач на день"""
        self.update_day_view()
        self.daysTasks.setHidden(False)
        self.weekTasks.setHidden(True)
        self.hide_pomodoro_elements()
        self.update_button_styles('day')

    def show_week_view(self):
        """Активирует режим просмотра задач на неделю"""
        self.update_week_view()
        self.daysTasks.setHidden(True)
        self.weekTasks.setHidden(False)
        self.hide_pomodoro_elements()
        self.update_button_styles('week')

    def show_pomodoro_view(self):
        """Активирует режим Pomodoro таймера"""
        self.daysTasks.setHidden(True)
        self.weekTasks.setHidden(True)
        self.show_pomodoro_elements()
        self.update_button_styles('pomodoro')

    def hide_pomodoro_elements(self):
        """Скрывает элементы интерфейса Pomodoro"""
        self.motivathion.setHidden(True)
        self.tttimer.setHidden(True)
        self.back.setHidden(True)
        self.settings.setHidden(True)

        self.day.setHidden(False)
        self.week.setHidden(False)
        self.add.setHidden(False)
        self.minus.setHidden(False)
        self.change.setHidden(False)

    def show_pomodoro_elements(self):
        """Показывает элементы интерфейса Pomodoro"""
        self.motivathion.setHidden(False)
        self.tttimer.setHidden(False)
        self.back.setHidden(False)
        self.settings.setHidden(False)

        self.day.setHidden(True)
        self.week.setHidden(True)
        self.add.setHidden(True)
        self.minus.setHidden(True)
        self.change.setHidden(True)

    def update_button_styles(self, active_view):
        """Обновляет стили кнопок переключения режимов"""
        base_style = 'QPushButton { background-color: %s; color: black; }'

        day_color = '#f0caa3' if active_view == 'day' else '#826d58'
        week_color = '#f0caa3' if active_view == 'week' else '#826d58'
        pomodoro_color = '#f0caa3' if active_view == 'pomodoro' else '#826d58'

        self.day.setStyleSheet(base_style % day_color)
        self.week.setStyleSheet(base_style % week_color)
        self.concentrathion.setStyleSheet(base_style % pomodoro_color)

    # =========================================================================
    # УПРАВЛЕНИЕ ЗАДАЧАМИ
    # =========================================================================

    def update_day_view(self):
        """Обновляет отображение задач на текущий день"""
        today_str = self.current_week[0].strftime('%d.%m.%Y')
        tasks = self.db.execute_query(
            "SELECT id, task, importance, timeStart, timeEnd, color FROM Time WHERE date = ? ORDER BY timeStart, timeEnd",
            (today_str,)
        )
        self.display_tasks_in_scroll_area(self.daysTasks, tasks, today_str, is_week_view=False)

    def update_week_view(self):
        """Обновляет отображение задач на всю неделю"""
        layout = QGridLayout()

        for i, day_date in enumerate(self.current_week):
            date_str = day_date.strftime('%d.%m.%Y')
            tasks = self.db.execute_query(
                "SELECT id, task, importance, timeStart, timeEnd, color FROM Time WHERE date = ? ORDER BY timeStart, timeEnd",
                (date_str,)
            )

            day_label = QLabel(date_str)
            day_label.setStyleSheet("QLabel { background-color: #e6e6e6; color: black; font-weight: bold; }")
            day_label.setAlignment(Qt.AlignCenter)
            day_label.setMinimumHeight(30)
            layout.addWidget(day_label, 0, i)

            if tasks:
                for j, task in enumerate(tasks):
                    task_widget = self.create_task_widget(task, is_week_view=True)
                    layout.addWidget(task_widget, j + 1, i)
            else:
                no_tasks_label = QLabel("Нет задач")
                no_tasks_label.setStyleSheet("QLabel { background-color: #f8f8f8; color: #666; }")
                no_tasks_label.setAlignment(Qt.AlignCenter)
                layout.addWidget(no_tasks_label, 1, i)

        container = QWidget()
        container.setLayout(layout)
        self.weekTasks.setWidget(container)

    def display_tasks_in_scroll_area(self, scroll_area, tasks, date_str, is_week_view=False):
        """Отображает список задач в области прокрутки"""
        layout = QVBoxLayout()

        title = QLabel(f"Задачи на {date_str}")
        title.setStyleSheet("QLabel { background-color: #e6e6e6; color: black; font-weight: bold; }")
        title.setAlignment(Qt.AlignCenter)
        title.setMinimumHeight(30)
        layout.addWidget(title)

        if tasks:
            for task in tasks:
                task_widget = self.create_task_widget(task, is_week_view)
                layout.addWidget(task_widget)
        else:
            no_tasks = QLabel("На этот день задач нет")
            no_tasks.setStyleSheet("QLabel { background-color: #f8f8f8; color: #666; }")
            no_tasks.setAlignment(Qt.AlignCenter)
            layout.addWidget(no_tasks)

        layout.addStretch()
        container = QWidget()
        container.setLayout(layout)
        scroll_area.setWidget(container)

    def create_task_widget(self, task, is_week_view=False):
        """Создает виджет для отображения отдельной задачи"""
        task_id, task_text, importance, time_start, time_end, color = task

        widget = QFrame()
        widget.setFrameStyle(QFrame.Box)
        widget.setStyleSheet(f"QFrame {{ background-color: {color}; padding: 5px; }}")

        layout = QVBoxLayout(widget)

        time_importance_layout = QHBoxLayout()
        time_text = f"{time_start} - {time_end}" if time_start and time_end else "Время не указано"
        time_label = QLabel(time_text)
        time_label.setStyleSheet("font-weight: bold;")
        time_importance_layout.addWidget(time_label)

        if importance:
            importance_label = QLabel("⭐ Важная")
            importance_label.setStyleSheet("color: #d4af37; font-weight: bold;")
            time_importance_layout.addWidget(importance_label)

        time_importance_layout.addStretch()
        layout.addLayout(time_importance_layout)

        task_label = QLabel(task_text)
        task_label.setWordWrap(True)
        layout.addWidget(task_label)

        if not is_week_view:
            id_label = QLabel(f"ID: {task_id}")
            id_label.setStyleSheet("color: #666; font-size: 10px;")
            layout.addWidget(id_label)

        return widget

    def add_task(self):
        """Добавляет новую задачу через диалоговое окно"""
        dialog = TaskDialog(self, "add", dates=self.current_week)
        if dialog.exec_() == QDialog.Accepted:
            task_data = dialog.get_task_data()
            self.db.execute_query(
                "INSERT INTO Time (date, task, importance, timeStart, timeEnd, color) VALUES (?, ?, ?, ?, ?, ?)",
                (task_data['date'], task_data['task'], task_data['importance'],
                 task_data['timeStart'], task_data['timeEnd'], task_data['color'])
            )
            QMessageBox.information(self, "Успех", "Задача успешно добавлена!")
            logging.info("Добавлена задача: %s", task_data['task'])
            self.update_day_view()
            self.update_week_view()

    def delete_task(self):
        """Удаляет выбранную задачу"""
        date_str, ok = QInputDialog.getItem(
            self, "Выбор даты", "Выберите дату:",
            [d.strftime('%d.%m.%Y') for d in self.current_week], 0, False
        )
        if not ok:
            return

        tasks = self.db.execute_query(
            "SELECT id, task, timeStart, timeEnd FROM Time WHERE date = ? ORDER BY timeStart",
            (date_str,)
        )
        if not tasks:
            QMessageBox.information(self, "Информация", "На выбранную дату задач нет")
            return

        task_items = [f"{task[0]}: {task[2]}-{task[3]} - {task[1][:50]}..." for task in tasks]
        task_str, ok = QInputDialog.getItem(
            self, "Выбор задачи", "Выберите задачу для удаления:", task_items, 0, False
        )
        if ok and task_str:
            task_id = int(task_str.split(':')[0])
            reply = QMessageBox.question(
                self, "Подтверждение",
                "Вы уверены, что хотите удалить эту задачу?",
                QMessageBox.Yes | QMessageBox.No
            )
            if reply == QMessageBox.Yes:
                self.db.execute_query("DELETE FROM Time WHERE id = ?", (task_id,))
                QMessageBox.information(self, "Успех", "Задача удалена")
                logging.info("Удалена задача id=%d", task_id)
                self.update_day_view()
                self.update_week_view()

    def edit_task(self):
        """Редактирует выбранную задачу"""
        date_str, ok = QInputDialog.getItem(
            self, "Выбор даты", "Выберите дату:",
            [d.strftime('%d.%m.%Y') for d in self.current_week], 0, False
        )
        if not ok:
            return

        tasks = self.db.execute_query(
            "SELECT * FROM Time WHERE date = ? ORDER BY timeStart",
            (date_str,)
        )
        if not tasks:
            QMessageBox.information(self, "Информация", "На выбранную дату задач нет")
            return

        task_items = [f"{task[0]}: {task[4]}-{task[5]} - {task[2][:50]}..." for task in tasks]
        task_str, ok = QInputDialog.getItem(
            self, "Выбор задачи", "Выберите задачу для редактирования:", task_items, 0, False
        )
        if ok and task_str:
            task_id = int(task_str.split(':')[0])
            task_data = next((task for task in tasks if task[0] == task_id), None)
            if task_data:
                dialog = TaskDialog(self, "edit", task_data, self.current_week)
                if dialog.exec_() == QDialog.Accepted:
                    new_data = dialog.get_task_data()
                    self.db.execute_query(
                        """UPDATE Time SET date=?, task=?, importance=?, timeStart=?, timeEnd=?, color=?
                         WHERE id=?""",
                        (new_data['date'], new_data['task'], new_data['importance'],
                         new_data['timeStart'], new_data['timeEnd'], new_data['color'], task_id)
                    )
                    QMessageBox.information(self, "Успех", "Задача обновлена!")
                    logging.info("Обновлена задача id=%d", task_id)
                    self.update_day_view()
                    self.update_week_view()

    # =========================================================================
    # POMODORO ТАЙМЕР
    # =========================================================================

    def show_pomodoro_dialog(self):
        """Показывает диалог настройки параметров Pomodoro"""
        rounds, ok = QInputDialog.getItem(
            self, "Настройка Pomodoro", "Количество рабочих сессий:",
            ['1', '2', '3', '4', '5', '6', '7', '8'], 0, False
        )
        if ok:
            work_time, ok1 = QInputDialog.getItem(
                self, "Настройка Pomodoro", "Длительность работы (минуты):",
                ['15', '20', '25', '30', '45', '60'], 2, False
            )
            break_time, ok2 = QInputDialog.getItem(
                self, "Настройка Pomodoro", "Длительность перерыва (минуты):",
                ['5', '10', '15', '20'], 0, False
            )
            if ok1 and ok2:
                self.pomodoro.work_seconds = int(work_time) * 60
                self.pomodoro.break_seconds = int(break_time) * 60
                self.start_pomodoro(int(rounds))

    def start_pomodoro(self, rounds: int):
        """Запускает сессию Pomodoro таймера"""
        self.show_pomodoro_view()
        self.show_random_phrase()
        self.pomodoro.start(rounds)
        logging.info("Запущен Pomodoro: %d сессий", rounds)

    def on_pomodoro_phase_changed(self, phase: str, seconds: int):
        """Обрабатывает смену фаз Pomodoro таймера"""
        time_text = format_seconds_to_mmss(seconds)
        self.tttimer.setText(time_text)

        if phase == 'work':
            self.motivathion.setText("Время работать! 💪")
        elif phase == 'break':
            self.motivathion.setText("Время отдохнуть! ☕")
        elif phase == 'long_break':
            self.motivathion.setText("Время для длинного перерыва! 🌴")

    def on_pomodoro_finished(self):
        """Обрабатывает завершение сессии Pomodoro"""
        self.motivathion.setText("Отличная работа! Сессия завершена! 🎉")
        self.tttimer.setText("00:00")
        QMessageBox.information(self, "Pomodoro", "Все сессии завершены! Отличная работа!")

    def show_settings(self):
        """Показывает текущие настройки Pomodoro"""
        QMessageBox.information(self, "Настройки",
                                "Текущие настройки Pomodoro:\n"
                                f"- Работа: {self.pomodoro.work_seconds // 60} мин\n"
                                f"- Перерыв: {self.pomodoro.break_seconds // 60} мин\n"
                                f"- Длинный перерыв: {self.pomodoro.long_break_seconds // 60} мин\n\n"
                                "Для изменения настроек перезапустите Pomodoro сессию.")

    def closeEvent(self, event):
        """Обрабатывает закрытие приложения"""
        self.pomodoro.stop()
        self.save_settings()
        logging.info("Приложение закрыто")
        super().closeEvent(event)


# =============================================================================
# ТОЧКА ВХОДА В ПРИЛОЖЕНИЕ
# =============================================================================

if __name__ == '__main__':
    setup_logging()

    app = QApplication(sys.argv)
    app.setApplicationName("TimeFlow")
    app.setApplicationVersion("2.0")

    if not Path(Config.ALARM_PATH).exists():
        logging.warning("Звуковой файл не найден: %s", Config.ALARM_PATH)

    try:
        window = TimeManagement()
        window.show()

        exit_code = app.exec_()
        logging.info("Приложение завершено с кодом: %d", exit_code)
        sys.exit(exit_code)

    except Exception as e:
        logging.exception("Критическая ошибка при запуске приложения: %s", e)
        QMessageBox.critical(None, "Ошибка", f"Не удалось запустить приложение:\n{str(e)}")
        sys.exit(1)