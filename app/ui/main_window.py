from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal, Slot
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QFrame, QGridLayout, QGroupBox, QHBoxLayout,
    QLabel, QLineEdit, QMainWindow, QMessageBox, QPushButton,
    QPlainTextEdit, QProgressBar, QScrollArea, QSpinBox, QVBoxLayout, QWidget,
)

from app.core.project import Project
from app.core.pipeline import ModelPipeline, PipelineInputs, PipelineResult
from app.core.sumo_runner import SumoRunner
from app.core.wizard_import import import_wizard, wizard_configuration
from app.core.auto_taz import generate_grid_taz, membership_path
from app.core.od_from_wizard import build_wizard_od
from app.core.taz_editor import TazEditorLauncher
from app.ui.matrix_editor import MatrixEditorDialog


class PipelineWorker(QObject):
    finished = Signal(object)
    failed = Signal(str)
    log = Signal(str)

    def __init__(self, inputs: PipelineInputs, sumo_bin: str | None):
        super().__init__()
        self.inputs, self.sumo_bin = inputs, sumo_bin

    @Slot()
    def run(self):
        try:
            runner = SumoRunner(self.sumo_bin, self.log.emit)
            result = ModelPipeline(runner, self.log.emit).prepare(self.inputs)
            self.finished.emit(result)
        except Exception as exc:
            self.failed.emit(str(exc) or repr(exc))


class MainWindow(QMainWindow):
    def __init__(self, project: Project, return_to_launcher=None):
        super().__init__()
        self.project = project
        self.return_to_launcher = return_to_launcher
        self.worker_thread: QThread | None = None
        self.worker: PipelineWorker | None = None
        self.last_result: PipelineResult | None = None
        self._busy = False
        self._build_ui()
        self._apply_style()
        self.refresh_status()
        self.append_log('Открыт проект: ' + str(project.root))
        self.append_log('Режим: ' + ('OSM Web Wizard' if project.mode == 'wizard' else 'Обычный OSM → netconvert'))

    def _build_ui(self):
        self.setWindowTitle('SUMO Model Preparation — ' + self.project.root.name)
        self.resize(1110, 880)
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(20, 15, 20, 15)
        root.setSpacing(10)

        heading = QHBoxLayout()
        title_box = QVBoxLayout()
        title = QLabel('Подготовка транспортной модели')
        title.setFont(QFont('Segoe UI', 19, QFont.Weight.Bold))
        title_box.addWidget(title)
        self.project_label = QLabel(f'Проект: {self.project.root}  •  ' +
            ('Источник: OSM Web Wizard' if self.project.mode == 'wizard' else 'Источник: обычный OSM'))
        self.project_label.setObjectName('subtitle')
        self.project_label.setWordWrap(True)
        title_box.addWidget(self.project_label)
        heading.addLayout(title_box, 1)
        self.back_button = QPushButton('← Выбор проекта')
        self.back_button.clicked.connect(self.back_to_start)
        heading.addWidget(self.back_button)
        root.addLayout(heading)

        scrolling = QScrollArea()
        scrolling.setWidgetResizable(True)
        scrolling.setFrameShape(QFrame.NoFrame)
        content = QWidget()
        section = QVBoxLayout(content)
        section.setContentsMargins(3, 3, 10, 3)
        section.setSpacing(11)

        source = QGroupBox('1. Дорожная сеть')
        source_layout = QVBoxLayout(source)
        if self.project.mode == 'osm':
            row = QHBoxLayout()
            self.osm_field = QLineEdit(str(self.project.osm) if self.project.osm.is_file() else '')
            self.osm_field.setReadOnly(True)
            self.osm_field.setPlaceholderText('OSM ещё не выбран')
            self.select_osm_button = QPushButton('Выбрать OSM…')
            self.select_osm_button.clicked.connect(self.select_osm)
            row.addWidget(QLabel('Файл карты:'))
            row.addWidget(self.osm_field, 1)
            row.addWidget(self.select_osm_button)
            source_layout.addLayout(row)
            examples = QHBoxLayout()
            self.practice_button = QPushButton('Загрузить пример OSM из исходного проекта')
            self.practice_button.clicked.connect(self.load_example_osm)
            examples.addWidget(self.practice_button)
            examples.addStretch()
            source_layout.addLayout(examples)
            toolbar = QHBoxLayout()
            self.export_network_button = QPushButton('СОЗДАТЬ СЕТЬ (netconvert)')
            self.export_network_button.clicked.connect(self.export_network)
            toolbar.addWidget(self.export_network_button)
            self.open_network_button = QPushButton('Открыть сеть в SUMO')
            self.open_network_button.clicked.connect(self.launch_network)
            toolbar.addWidget(self.open_network_button)
            toolbar.addStretch()
            source_layout.addLayout(toolbar)
        else:
            toolbar = QHBoxLayout()
            self.wizard_start_button = QPushButton('Запустить OSM Web Wizard')
            self.wizard_start_button.clicked.connect(self.start_wizard)
            self.wizard_folder_button = QPushButton('Импорт папки Wizard…')
            self.wizard_folder_button.clicked.connect(self.import_wizard_folder)
            self.wizard_zip_button = QPushButton('Импорт ZIP Wizard…')
            self.wizard_zip_button.clicked.connect(self.import_wizard_zip)
            for widget in (self.wizard_start_button, self.wizard_folder_button, self.wizard_zip_button):
                toolbar.addWidget(widget)
            toolbar.addStretch()
            source_layout.addLayout(toolbar)
            toolbar2 = QHBoxLayout()
            self.open_network_button = QPushButton('Открыть сеть в SUMO')
            self.open_network_button.clicked.connect(self.launch_network)
            self.wizard_original_button = QPushButton('Открыть ОРИГИНАЛЬНЫЙ сценарий Wizard')
            self.wizard_original_button.clicked.connect(self.launch_original_wizard)
            toolbar2.addWidget(self.open_network_button)
            toolbar2.addWidget(self.wizard_original_button)
            toolbar2.addStretch()
            source_layout.addLayout(toolbar2)
        self.network_info = QLabel('Сеть не загружена')
        self.network_info.setObjectName('subtitle')
        self.network_info.setWordWrap(True)
        source_layout.addWidget(self.network_info)
        section.addWidget(source)

        zones = QGroupBox('2. Транспортные зоны TAZ и матрица корреспонденций')
        zones_layout = QVBoxLayout(zones)
        self.taz_field = QLineEdit(str(self.project.taz))
        self.matrix_field = QLineEdit(str(self.project.matrix))
        for label, field in (('Зоны TAZ:', self.taz_field), ('OD-матрица:', self.matrix_field)):
            line = QHBoxLayout()
            line.addWidget(QLabel(label))
            field.setReadOnly(True)
            line.addWidget(field, 1)
            zones_layout.addLayout(line)
        taz_buttons = QHBoxLayout()
        self.auto_taz_button = QPushButton('СОЗДАТЬ TAZ АВТОМАТИЧЕСКИ')
        self.auto_taz_button.clicked.connect(self.create_auto_taz)
        taz_buttons.addWidget(self.auto_taz_button)
        taz_buttons.addWidget(QLabel('Сетка:'))
        self.taz_cols = QSpinBox()
        self.taz_cols.setRange(1, 8)
        self.taz_cols.setValue(2)
        self.taz_rows = QSpinBox()
        self.taz_rows.setRange(1, 8)
        self.taz_rows.setValue(2)
        taz_buttons.addWidget(self.taz_cols)
        taz_buttons.addWidget(QLabel('×'))
        taz_buttons.addWidget(self.taz_rows)
        self.edit_taz_button = QPushButton('Исправить TAZ в NetEdit')
        self.edit_taz_button.clicked.connect(self.edit_taz_zones)
        taz_buttons.addWidget(self.edit_taz_button)
        self.import_taz_button = QPushButton('Импорт TAZ…')
        self.import_taz_button.clicked.connect(self.import_taz)
        taz_buttons.addWidget(self.import_taz_button)
        taz_buttons.addStretch()
        zones_layout.addLayout(taz_buttons)
        od_buttons = QHBoxLayout()
        self.matrix_editor_button = QPushButton('ОТКРЫТЬ / СОЗДАТЬ OD-МАТРИЦУ')
        self.matrix_editor_button.clicked.connect(self.open_matrix_editor)
        od_buttons.addWidget(self.matrix_editor_button)
        if self.project.mode == 'wizard':
            self.od_from_wizard_button = QPushButton('Заполнить OD по поездкам Wizard')
            self.od_from_wizard_button.setToolTip(
                'Агрегировать случайные поездки Wizard по текущим TAZ. '
                'Это не реальные данные транспортного обследования.')
            self.od_from_wizard_button.clicked.connect(self.create_wizard_od)
            od_buttons.addWidget(self.od_from_wizard_button)
        self.data_info = QLabel('TAZ: —  •  Матрица: —')
        self.data_info.setObjectName('subtitle')
        od_buttons.addWidget(self.data_info, 1)
        zones_layout.addLayout(od_buttons)
        section.addWidget(zones)

        modeling = QGroupBox('3. Подготовка и запуск своей модели')
        ml = QVBoxLayout(modeling)
        actions = QHBoxLayout()
        self.prepare_button = QPushButton('ПОДГОТОВИТЬ МОДЕЛЬ')
        self.prepare_button.setObjectName('primary')
        self.prepare_button.clicked.connect(self.prepare)
        self.prepare_button.setMinimumHeight(43)
        self.sumo_button = QPushButton('ЗАПУСТИТЬ SUMO')
        self.sumo_button.clicked.connect(self.launch_sumo)
        actions.addWidget(self.prepare_button, 2)
        actions.addWidget(self.sumo_button, 1)
        ml.addLayout(actions)
        info = QLabel('После изменения матрицы нажимайте «Подготовить модель» заново. '
                      'Запуск исходного сценария Wizard независим от этих шагов.')
        info.setObjectName('subtitle')
        info.setWordWrap(True)
        ml.addWidget(info)
        stats = QGridLayout()
        self.stat_trip, self.stat_route, self.stat_warn, self.stat_time = (QLabel('—') for _ in range(4))
        for col, (name, counter) in enumerate((('Поездок', self.stat_trip),
                       ('Маршрутов', self.stat_route), ('Предупреждений', self.stat_warn),
                       ('Время', self.stat_time))):
            box = QFrame()
            box.setObjectName('stat')
            layout = QVBoxLayout(box)
            layout.addWidget(QLabel(name))
            counter.setObjectName('statValue')
            layout.addWidget(counter)
            stats.addWidget(box, 0, col)
        ml.addLayout(stats)
        section.addWidget(modeling)

        settings = QGroupBox('Путь к установленному SUMO')
        row = QHBoxLayout(settings)
        row.addWidget(QLabel('SUMO/bin:'))
        self.sumo_bin = QLineEdit(self.project.get_setting('sumo_bin'))
        self.sumo_bin.editingFinished.connect(self.save_sumo_bin)
        self.sumo_bin.setPlaceholderText('Например: C:\\Program Files (x86)\\Eclipse\\Sumo\\bin — или оставить пустым при PATH')
        row.addWidget(self.sumo_bin, 1)
        self.sumo_bin_button = QPushButton('Выбрать…')
        self.sumo_bin_button.clicked.connect(self.choose_sumo_bin)
        row.addWidget(self.sumo_bin_button)
        section.addWidget(settings)
        section.addStretch()
        scrolling.setWidget(content)
        root.addWidget(scrolling, 1)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.hide()
        root.addWidget(self.progress)
        log_group = QGroupBox('Журнал выполнения')
        log_layout = QVBoxLayout(log_group)
        self.log_box = QPlainTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setMinimumHeight(125)
        log_layout.addWidget(self.log_box)
        root.addWidget(log_group)
        self.statusBar().showMessage('Готово')

    def _apply_style(self):
        self.setStyleSheet('''
            QMainWindow, QWidget { background: #f5f7fa; color: #172033; }
            QGroupBox { background: white; border: 1px solid #dce2ea; border-radius: 10px;
                margin-top: 10px; padding: 13px; font-weight: 600; }
            QGroupBox::title { subcontrol-origin: margin; left: 14px; padding: 0 6px; }
            QLineEdit, QPlainTextEdit { background: white; border: 1px solid #cfd6e0;
                border-radius: 7px; padding: 7px; }
            QPushButton { background: white; border: 1px solid #cbd3df; border-radius: 7px;
                padding: 8px 12px; font-weight: 600; }
            QPushButton:hover { background: #edf3fc; }
            QPushButton:disabled { color: #9aa3af; }
            QPushButton#primary { background: #2f64d6; border: none; color: white; }
            QLabel#subtitle { color: #63718a; font-size: 12px; }
            QFrame#stat { background: #fbfcfe; border: 1px solid #e1e6ed; border-radius: 8px; }
            QLabel#statValue { font-size: 20px; font-weight: 700; }
        ''')

    def _runner(self):
        return SumoRunner(self.sumo_bin.text().strip() or None, self.append_log)

    def _current_network(self) -> Path:
        network = self.project.network
        if network is None:
            raise ValueError('Дорожная сеть ещё не подготовлена. '
                + ('Выберите и экспортируйте OSM.' if self.project.mode == 'osm'
                   else 'Импортируйте папку или ZIP OSM Web Wizard.'))
        return network

    def _model_is_current(self) -> bool:
        config = self.project.root / 'sumo.sumocfg'
        routes = self.project.root / 'routes.rou.xml'
        network = self.project.network
        deps = [network, self.project.taz, self.project.matrix, routes]
        return (config.is_file() and all(x is not None and x.is_file() for x in deps)
                and all(config.stat().st_mtime >= x.stat().st_mtime for x in deps))

    def refresh_status(self):
        network = self.project.network
        self.network_info.setText(str(network) if network else 'Сеть ещё не подготовлена')
        self.open_network_button.setEnabled(network is not None and not self._busy)
        if self.project.mode == 'osm':
            self.osm_field.setText(str(self.project.osm) if self.project.osm.is_file() else '')
        else:
            self.wizard_original_button.setEnabled(self.project.original_wizard_config.is_file() and not self._busy)
        self.auto_taz_button.setEnabled(bool(network) and not self._busy)
        self.edit_taz_button.setEnabled(bool(network) and self.project.taz.is_file() and not self._busy)
        self.import_taz_button.setEnabled(bool(network) and not self._busy)
        self.matrix_editor_button.setEnabled(self.project.taz.is_file() and not self._busy)
        if self.project.mode == 'wizard':
            self.od_from_wizard_button.setEnabled(
                self.project.taz.is_file()
                and (self.project.root / 'wizard' / 'osm.passenger.trips.xml').is_file()
                and not self._busy)
        self.data_info.setText('TAZ: ' + ('готовы' if self.project.taz.is_file() else 'нет') +
                               '  •  Матрица: ' + ('готова' if self.project.matrix.is_file() else 'нет'))
        self.prepare_button.setEnabled(bool(network) and self.project.taz.is_file()
                                       and self.project.matrix.is_file() and not self._busy)
        self.sumo_button.setEnabled(self._model_is_current() and not self._busy)

    def _invalidate_model(self):
        self.last_result = None
        (self.project.root / 'sumo.sumocfg').unlink(missing_ok=True)
        self.refresh_status()

    @Slot(str)
    def append_log(self, message: str):
        self.log_box.appendPlainText(message)
        scrollbar = self.log_box.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
        if message.startswith('=== '):
            self.statusBar().showMessage(message.strip('= ').strip())

    def save_sumo_bin(self):
        try:
            self.project.set_setting('sumo_bin', self.sumo_bin.text().strip())
        except Exception as exc:
            self.append_log('Не удалось сохранить путь SUMO: ' + str(exc))

    def choose_sumo_bin(self):
        selected = QFileDialog.getExistingDirectory(self, 'Выберите SUMO/bin')
        if selected:
            self.sumo_bin.setText(selected)
            self.save_sumo_bin()

    def load_example_osm(self):
        source = Path(__file__).resolve().parents[2] / 'sample_data' / 'kedr.osm'
        if not source.is_file():
            QMessageBox.warning(self, 'Пример отсутствует', f'Не найден пример: {source}')
            return
        try:
            self.project.select_osm(source)
            self.refresh_status()
            self.append_log('Пример OSM скопирован в проект. Теперь нажмите «Создать сеть».')
        except Exception as exc:
            QMessageBox.critical(self, 'Ошибка примера', str(exc))

    def import_taz(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Импорт зон TAZ',
            str(self.project.root), 'XML (*.xml);;Все файлы (*)')
        if not path:
            return
        try:
            source = Path(path).resolve()
            if not self.project.network:
                raise ValueError('Сначала создайте или импортируйте дорожную сеть.')
            root = ET.parse(source).getroot()
            zones = list(root.iter('taz'))
            if not zones or any(not z.get('id') or not (
                    z.get('edges') or (z.findall('tazSource') and z.findall('tazSink')))
                    for z in zones):
                raise ValueError('TAZ должны содержать id и дороги: edges либо '
                                 'отдельные tazSource и tazSink.')
            if source == self.project.taz.resolve():
                self.append_log('TAZ уже находятся в папке проекта.')
                return
            backup = self.project.backup_dependents()
            if backup:
                self.append_log(f'Прежние зоны, матрица и модель сохранены в {backup}')
            shutil.copy2(source, self.project.taz)
            self._invalidate_model()
            self.append_log(f'Импортировано зон TAZ: {len(zones)}. Создайте/загрузите OD-матрицу.')
        except Exception as exc:
            QMessageBox.critical(self, 'Ошибка импорта TAZ', str(exc))

    def select_osm(self):
        selected, _ = QFileDialog.getOpenFileName(self, 'Выберите данные OSM',
            str(self.project.root), 'OpenStreetMap (*.osm *.xml);;Все файлы (*)')
        if not selected:
            return
        try:
            changed = self.project.select_osm(Path(selected))
            self.refresh_status()
            self.append_log('Исходный OSM ' + ('скопирован в проект. Создайте сеть.' if changed else 'не изменился.'))
            if changed:
                self.last_result = None
                self.append_log('Связанные старые файлы сети/TAZ/матрицы сохранены в backup_before_network_change_* (если существовали).')
        except Exception as exc:
            QMessageBox.critical(self, 'Ошибка выбора OSM', str(exc))

    def export_network(self):
        if not self.project.osm.is_file():
            QMessageBox.warning(self, 'Нет OSM', 'Сначала выберите OSM-файл.')
            return
        network = self.project.root / 'network.net.xml'
        try:
            runner = self._runner()
            digest = hashlib.sha256(self.project.osm.read_bytes()).hexdigest()
            stored = self.project.root / 'network.source.sha256'
            previous = stored.read_text(encoding='utf-8').strip() if stored.is_file() else None
            if network.is_file() and previous is not None and previous != digest:
                backup = self.project.backup_dependents(include_network=True)
                if backup:
                    self.append_log(f'Предыдущие результаты перемещены в {backup}')
            self.progress.show()
            self.export_network_button.setEnabled(False)
            self.append_log('=== Создание сети с помощью netconvert ===')
            result = runner.run([runner.executable('netconvert'), '--osm-files',
                str(self.project.osm), '--output-file', str(network)], cwd=self.project.root)
            if result.returncode != 0:
                raise RuntimeError(f'netconvert завершился с кодом {result.returncode}:\n{result.stderr or result.stdout}')
            if not network.is_file():
                raise RuntimeError('netconvert не создал network.net.xml')
            stored.write_text(digest, encoding='utf-8')
            self.append_log(f'Сеть создана: {network}')
            if not self.project.taz.is_file():
                counts = generate_grid_taz(network, self.project.taz,
                    self.taz_cols.value(), self.taz_rows.value())
                self.append_log(f'Автоматически созданы TAZ-зоны: {len(counts)}. Откройте OD-матрицу.')
            else:
                self.append_log('Существующие зоны сохранены. При изменении OSM проверьте их соответствие сети.')
            self._invalidate_model()
            QMessageBox.information(self, 'Сеть готова', 'Сеть создана в папке проекта.\n'
                                    'TAZ-зоны доступны; откройте OD-матрицу и задайте спрос.')
        except Exception as exc:
            self.append_log('ОШИБКА СОЗДАНИЯ СЕТИ: ' + str(exc))
            QMessageBox.critical(self, 'Ошибка netconvert', str(exc))
        finally:
            self.progress.hide()
            self.export_network_button.setEnabled(True)
            self.refresh_status()

    def launch_network(self):
        try:
            self._runner().launch_network(self._current_network())
            self.append_log('Сеть открыта в SUMO.')
        except Exception as exc:
            QMessageBox.critical(self, 'Не удалось открыть сеть', str(exc))

    def import_wizard_folder(self):
        source = QFileDialog.getExistingDirectory(self, 'Папка, созданная OSM Web Wizard')
        if source:
            self._import_wizard(Path(source))

    def import_wizard_zip(self):
        source, _ = QFileDialog.getOpenFileName(self, 'ZIP OSM Web Wizard', '', 'ZIP (*.zip)')
        if source:
            self._import_wizard(Path(source))

    def _import_wizard(self, source: Path):
        try:
            network, changed = import_wizard(source, self.project.root)
            self.append_log(f'Импортирован сценарий Wizard: {network}')
            if changed:
                self.append_log('Сеть изменилась: предыдущие TAZ/матрица и модель перенесены в резервную папку.')
            if not self.project.taz.is_file():
                counts = generate_grid_taz(network, self.project.taz, self.taz_cols.value(), self.taz_rows.value())
                self.append_log(f'Создано черновых зон TAZ: {len(counts)}.')
            else:
                self.append_log('Существующие TAZ оставлены без изменений.')
            self.last_result = None
            self.refresh_status()
            QMessageBox.information(self, 'Сценарий импортирован',
                'Сценарий сохранён в текущем проекте.\n\n'
                'Можно сразу открыть оригинальный Wizard с его движением.\n'
                'Для своей модели: откройте OD-матрицу, задайте числа, сохраните и нажмите «Подготовить модель».')
        except Exception as exc:
            self.append_log('ОШИБКА ИМПОРТА: ' + str(exc))
            QMessageBox.critical(self, 'Ошибка импорта Wizard', str(exc))
            self.refresh_status()

    def launch_original_wizard(self):
        try:
            config = wizard_configuration(self.project.root)
            ModelPipeline(self._runner(), self.append_log).launch_sumo(config)
            self.append_log('Запущен оригинальный сценарий Wizard (его собственные случайные поездки).')
        except Exception as exc:
            QMessageBox.critical(self, 'Ошибка запуска оригинального Wizard', str(exc))

    def start_wizard(self):
        candidates = []
        bin_text = self.sumo_bin.text().strip()
        if bin_text:
            home = Path(bin_text).expanduser().resolve()
            candidates += [home.parent/'tools'/'osmWebWizard.py', home/'osmWebWizard.py']
        sumo_home = os.environ.get('SUMO_HOME')
        if sumo_home:
            candidates.append(Path(sumo_home)/'tools'/'osmWebWizard.py')
        for home in (Path('C:/Program Files (x86)/Eclipse/Sumo'),
                     Path('C:/Program Files/Eclipse/Sumo')):
            candidates.append(home/'tools'/'osmWebWizard.py')
        script = next((candidate for candidate in candidates if candidate.is_file()), None)
        if script is None:
            QMessageBox.warning(self, 'Wizard не найден',
                'Укажите SUMO/bin. Программа ищет SUMO/tools/osmWebWizard.py.\n'
                'OSM Web Wizard можно запустить и через меню «Пуск».')
            return
        try:
            cmd = ['py', '-3', str(script)] if os.name == 'nt' and shutil.which('py') else [sys.executable, str(script)]
            env = os.environ.copy()
            env.setdefault('SUMO_HOME', str(script.parent.parent))
            subprocess.Popen(cmd, cwd=str(script.parent), env=env)
            self.append_log('OSM Web Wizard открыт. После создания сценария импортируйте папку или ZIP.')
        except OSError as exc:
            QMessageBox.critical(self, 'Ошибка запуска Wizard', str(exc))

    def create_auto_taz(self):
        staged = self.project.root / '.sumo.add.pending.xml'
        staged_metadata = membership_path(staged)
        try:
            network = self._current_network()
            counts = generate_grid_taz(network, staged, self.taz_cols.value(), self.taz_rows.value())
            if self.project.taz.is_file():
                answer = QMessageBox.question(self, 'Заменить зоны?',
                    'Пересоздать TAZ? Текущие зоны, матрица и результаты маршрутизации '
                    'будут сохранены в резервной папке. После этого матрицу нужно заполнить заново.',
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No)
                if answer != QMessageBox.StandardButton.Yes:
                    return
                backup = self.project.backup_dependents()
                if backup:
                    self.append_log('Резервная копия предыдущих TAZ/матрицы: ' + str(backup))
            staged.replace(self.project.taz)
            staged_metadata.replace(membership_path(self.project.taz))
            self._invalidate_model()
            self.append_log('Созданы TAZ: ' + ', '.join(f'{k} ({n} дорог)' for k, n in counts.items()))
            self.append_log('Направления Source и Sink назначены раздельно по соединениям '
                            'дорог SUMO: предпочтение тупиковым краевым въездам и выездам; '
                            'если их нет — переходам между зонами.')
            self.append_log('Одна и та же направленная дорога не назначается одновременно '
                            'Source и Sink. Два направления одной физической дороги '
                            '(например, 123 и -123) могут выполнять разные роли.')
            self.append_log('Это черновая автоматическая разметка; проверьте въезды/выезды '
                            'в NetEdit. Исходная матрица при пересоздании зон сохранена в backup.')
            self.append_log('Теперь откройте OD-матрицу — зоны появятся автоматически.')
        except Exception as exc:
            QMessageBox.critical(self, 'Ошибка создания TAZ', str(exc))
            self.append_log('ОШИБКА TAZ: ' + str(exc))
        finally:
            staged.unlink(missing_ok=True)
            staged_metadata.unlink(missing_ok=True)
            self.refresh_status()

    def create_wizard_od(self):
        source = self.project.root / 'wizard' / 'osm.passenger.trips.xml'
        staged = self.project.root / '.matrix_taz.from_wizard.pending.xml'
        try:
            if not self.project.taz.is_file():
                raise ValueError('Сначала создайте или импортируйте TAZ.')
            if not source.is_file():
                raise ValueError('В Wizard нет исходного osm.passenger.trips.xml.')
            stats = build_wizard_od(source, self.project.taz, staged)
            note = (f'Поездки Wizard: {stats.total}. Распределено по TAZ: {stats.matched}. '
                    f'Не удалось привязать: {stats.missing}.\n'
                    f'Интервал матрицы: 0–{stats.interval_end} секунд.\n\n'
                    'Это случайный транспортный спрос Wizard, а не реальная статистика.\n'
                    'Для существующей модели поездки будут заново сгенерированы через od2trips.')
            if stats.missing:
                note += ('\n\nВНИМАНИЕ: часть поездок будет исключена. Примеры '
                         'непривязанных дорог: ' + ', '.join(stats.missing_edges))
            if self.project.matrix.is_file():
                note += ('\n\nСуществующая матрица будет сохранена в отдельной '
                         'резервной папке перед заменой.')
            answer = QMessageBox.question(
                self, 'Создать матрицу из Wizard?', note + '\n\nПродолжить?',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return
            if self.project.matrix.is_file():
                backup = self.project.root / ('backup_before_wizard_od_' +
                    datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
                backup.mkdir(parents=True)
                shutil.copy2(self.project.matrix, backup / 'matrix_taz.xml')
                self.append_log('Предыдущая матрица сохранена: ' + str(backup / 'matrix_taz.xml'))
            staged.replace(self.project.matrix)
            self._invalidate_model()
            self.append_log(f'Создана OD-матрица из Wizard: {stats.matched}/{stats.total} поездок '
                            f'за {stats.interval_end} с. Файл: {self.project.matrix}')
            if stats.missing:
                self.append_log('ПРЕДУПРЕЖДЕНИЕ: ' + str(stats.missing) +
                                ' поездок не привязаны к текущим TAZ.')
            QMessageBox.information(self, 'OD-матрица создана',
                f'Успешно перенесено в матрицу {stats.matched} поездок.\n'
                'Теперь откройте редактор матрицы, при необходимости измените значения '
                'и нажмите «ПОДГОТОВИТЬ МОДЕЛЬ».')
        except Exception as exc:
            self.append_log('ОШИБКА OD WIZARD: ' + str(exc))
            QMessageBox.critical(self, 'Ошибка создания OD из Wizard', str(exc))
        finally:
            staged.unlink(missing_ok=True)
            self.refresh_status()

    def edit_taz_zones(self):
        try:
            network = self._current_network()
            if not self.project.taz.is_file():
                raise ValueError('Сначала создайте зоны автоматически.')
            TazEditorLauncher(self._runner(), self.append_log).open_editor(network, self.project.taz)
            self.append_log('После сохранения TAZ в NetEdit заново проверьте матрицу и подготовьте модель.')
            self._invalidate_model()
        except Exception as exc:
            QMessageBox.critical(self, 'Ошибка редактирования зон', str(exc))

    def open_matrix_editor(self):
        if not self.project.taz.is_file():
            QMessageBox.warning(self, 'TAZ не найдены', 'Сначала создайте TAZ.')
            return
        dialog = MatrixEditorDialog(self, taz_path=str(self.project.taz),
                                    matrix_path=str(self.project.matrix), project_path=str(self.project.root))
        dialog.exec()
        if dialog.saved_path is not None:
            self._invalidate_model()
            self.append_log('Матрица сохранена в проект: ' + str(self.project.matrix))
        self.refresh_status()

    def prepare(self):
        if self._busy:
            return
        try:
            network = self._current_network()
            if not self.project.taz.is_file():
                raise ValueError('Сначала создайте транспортные зоны TAZ.')
            if not self.project.matrix.is_file():
                raise ValueError('Матрица ещё не сохранена. Откройте редактор и нажмите «Сохранить в проект».')
            inputs = PipelineInputs(self.project.osm if self.project.mode == 'osm' else None,
                self.project.taz, self.project.matrix, self.project.root,
                prepared_network=network if self.project.mode == 'wizard' else None)
        except Exception as exc:
            QMessageBox.warning(self, 'Не хватает данных', str(exc))
            return
        self._busy = True
        self._set_busy(True)
        self.last_result = None
        self.log_box.clear()
        self.append_log('Начата подготовка индивидуальных поездок и маршрутов…')
        thread = QThread(self)
        worker = PipelineWorker(inputs, self.sumo_bin.text().strip() or None)
        self.worker_thread, self.worker = thread, worker
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.log.connect(self.append_log)
        worker.finished.connect(self.on_finished)
        worker.failed.connect(self.on_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(self.thread_done)
        thread.start()

    def _set_busy(self, busy: bool):
        self.progress.setVisible(busy)
        controls = [self.back_button, self.open_network_button, self.auto_taz_button, self.import_taz_button,
                    self.edit_taz_button, self.matrix_editor_button, self.prepare_button,
                    self.sumo_button, self.sumo_bin, self.sumo_bin_button]
        if self.project.mode == 'osm':
            controls += [self.select_osm_button, self.export_network_button, self.practice_button]
        else:
            controls += [self.wizard_start_button, self.wizard_folder_button,
                         self.wizard_zip_button, self.wizard_original_button,
                         self.od_from_wizard_button]
        for control in controls:
            control.setEnabled(not busy)
        if not busy:
            self.refresh_status()

    @Slot(object)
    def on_finished(self, result: PipelineResult):
        self.last_result = result
        self.stat_trip.setText(str(result.trip_count))
        self.stat_route.setText(str(result.route_count))
        self.stat_warn.setText(str(result.warning_count))
        self.stat_time.setText(f'{result.elapsed_seconds:.1f} с')
        self.append_log(f'Готовая модель: {result.config}')
        self.statusBar().showMessage('Модель подготовлена')
        QMessageBox.information(self, 'Готово',
            f'Поездок: {result.trip_count}; маршрутов: {result.route_count}.\n'
            'Теперь нажмите «ЗАПУСТИТЬ SUMO».')

    @Slot(str)
    def on_failed(self, message: str):
        self.append_log('ОШИБКА: ' + message)
        self.statusBar().showMessage('Ошибка подготовки')
        QMessageBox.critical(self, 'Ошибка построения модели', message)

    @Slot()
    def thread_done(self):
        thread = self.worker_thread
        self.worker_thread = None
        self.worker = None
        self._busy = False
        self._set_busy(False)
        if thread is not None:
            thread.deleteLater()

    def launch_sumo(self):
        try:
            if not self._model_is_current():
                raise RuntimeError('Модель устарела или не подготовлена. Нажмите «ПОДГОТОВИТЬ МОДЕЛЬ».')
            config = self.project.root / 'sumo.sumocfg'
            ModelPipeline(self._runner(), self.append_log).launch_sumo(config)
            self.append_log('Открыта собственная модель из OD-матрицы: ' + str(config))
        except Exception as exc:
            QMessageBox.critical(self, 'Не удалось запустить модель', str(exc))

    def back_to_start(self):
        if self._busy:
            QMessageBox.warning(self, 'Идёт подготовка', 'Дождитесь завершения построения маршрутов.')
            return
        self.close()

    def closeEvent(self, event):
        if self._busy:
            QMessageBox.warning(self, 'Идёт подготовка', 'Завершите подготовку модели перед выходом.')
            event.ignore()
            return
        event.accept()
        if self.return_to_launcher is not None:
            self.return_to_launcher()
