from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QComboBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
    QMainWindow, QMessageBox, QPushButton, QVBoxLayout, QWidget, QGroupBox)

from app.core.project import Project, ProjectError


class ProjectLauncher(QMainWindow):
    def __init__(self):
        super().__init__()
        self.workspace = None
        self.setWindowTitle('SUMO Model Preparation — выбор проекта')
        self.resize(710, 470)
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(32, 28, 32, 28)
        root.setSpacing(16)
        heading = QLabel('С чего начнём?')
        heading.setFont(QFont('Segoe UI', 23, QFont.Weight.Bold))
        root.addWidget(heading)
        expl = QLabel('Выберите способ получения дорожной сети и папку проекта. '
                      'Все зоны, матрицы и маршруты будут сохраняться только в ней.')
        expl.setWordWrap(True)
        root.addWidget(expl)
        group = QGroupBox('Настройки проекта')
        form = QVBoxLayout(group)
        form.addWidget(QLabel('Способ работы'))
        self.mode = QComboBox()
        self.mode.addItem('Обычный OSM — построить сеть через netconvert', 'osm')
        self.mode.addItem('OSM Web Wizard — импортировать готовый сценарий', 'wizard')
        form.addWidget(self.mode)
        form.addWidget(QLabel('Папка проекта (можно выбрать существующую)'))
        directory = QHBoxLayout()
        self.folder = QLineEdit()
        self.folder.setPlaceholderText('Например: C:\\Users\\…\\Documents\\SUMO_Projects\\project_1')
        browse = QPushButton('Выбрать папку…')
        browse.clicked.connect(self.browse)
        directory.addWidget(self.folder, 1)
        directory.addWidget(browse)
        form.addLayout(directory)
        hint = QLabel('При открытии существующего проекта выберите тот же способ работы. '
                      'Программа сохранит ранее созданные файлы.')
        hint.setWordWrap(True)
        hint.setStyleSheet('color: #63718a;')
        form.addWidget(hint)
        root.addWidget(group)
        root.addStretch()
        row = QHBoxLayout()
        row.addStretch()
        open_button = QPushButton('ОТКРЫТЬ РАБОЧЕЕ ОКНО  →')
        open_button.setObjectName('primary')
        open_button.clicked.connect(self.open_workspace)
        row.addWidget(open_button)
        root.addLayout(row)
        self.setStyleSheet('''
            QMainWindow, QWidget { background: #f5f7fa; color: #172033; }
            QGroupBox { background: white; border: 1px solid #dce2ea; border-radius: 10px;
                margin-top: 10px; padding: 18px; font-weight: 600; }
            QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 6px; }
            QLineEdit, QComboBox { background: white; border: 1px solid #cfd6e0; border-radius: 7px; padding: 9px; }
            QPushButton { background: white; border: 1px solid #cbd3df; border-radius: 7px; padding: 10px; font-weight: 600; }
            QPushButton#primary { background: #2f64d6; border: none; color: white; }
        ''')

    def browse(self):
        folder = QFileDialog.getExistingDirectory(self, 'Папка рабочего проекта',
                        self.folder.text().strip() or str(Path.home() / 'Documents'))
        if folder:
            self.folder.setText(folder)

    def open_workspace(self):
        selected = self.folder.text().strip()
        if not selected:
            QMessageBox.warning(self, 'Папка проекта', 'Укажите папку проекта. Можно создать новую.')
            return
        try:
            project = Project.open(Path(selected), self.mode.currentData())
        except (OSError, ProjectError) as exc:
            QMessageBox.critical(self, 'Проект не открыт', str(exc))
            return
        from app.ui.main_window import MainWindow
        self.workspace = MainWindow(project, return_to_launcher=self.return_to_launcher)
        self.workspace.show()
        self.hide()

    def return_to_launcher(self):
        self.workspace = None
        self.show()
