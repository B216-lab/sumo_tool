from __future__ import annotations

import subprocess
import traceback
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal, Slot
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QFrame, QGridLayout, QGroupBox, QHBoxLayout,
    QLabel, QLineEdit, QMainWindow, QMessageBox, QPushButton, QPlainTextEdit,
    QProgressBar, QVBoxLayout, QWidget,
)

from app.core.pipeline import ModelPipeline, PipelineInputs, PipelineResult, PipelineError
from app.core.sumo_runner import SumoRunner

from app.ui.matrix_editor import MatrixEditorDialog

import shutil
from pathlib import Path

from PySide6.QtWidgets import QMessageBox

from app.core.taz_editor import TazEditorLauncher, TazEditorError

class PipelineWorker(QObject):
    finished = Signal(object)
    failed = Signal(str)
    log = Signal(str)

    def __init__(self, inputs: PipelineInputs, sumo_bin: str | None):
        super().__init__()
        self.inputs = inputs
        self.sumo_bin = sumo_bin

    @Slot()
    def run(self):
        try:
            runner = SumoRunner(self.sumo_bin, self.log.emit)
            pipeline = ModelPipeline(runner, self.log.emit)
            result = pipeline.prepare(self.inputs)
            self.finished.emit(result)
        except Exception as exc:
            self.failed.emit(str(exc) or repr(exc))


class FileRow(QWidget):
    def __init__(self, label: str, placeholder: str, parent=None):
        super().__init__(parent)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText(placeholder)
        button = QPushButton("Выбрать…")
        button.setFixedWidth(110)
        button.clicked.connect(self.choose)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QLabel(label))
        layout.addWidget(self.edit, 1)
        layout.addWidget(button)

    def choose(self):
        path, _ = QFileDialog.getOpenFileName(self, "Выберите файл")
        if path:
            self.edit.setText(path)

    def path(self) -> Path:
        return Path(self.edit.text().strip())


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SUMO Model Preparation")
        self.resize(980, 720)
        self.worker_thread: QThread | None = None
        self.worker: PipelineWorker | None = None
        self.last_result: PipelineResult | None = None
        self.exported_network: Path | None = None
        self._build_ui()
        self._apply_style()
        self._load_practice_defaults()

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(18)

        title = QLabel("Подготовка транспортной модели")
        title.setFont(QFont("Segoe UI", 22, QFont.Weight.Bold))
        subtitle = QLabel("OSM → SUMO network → TAZ/OD → trips → routes → SUMO")
        subtitle.setObjectName("subtitle")
        root.addWidget(title)
        root.addWidget(subtitle)

        files = QGroupBox("Исходные данные")
        fl = QVBoxLayout(files)
        fl.setSpacing(12)
        self.osm_row = FileRow("OSM-файл", "map2.osm")
        self.taz_row = FileRow("TAZ-файл", "sumo.add.xml")
        self.matrix_row = FileRow("Матрица", "matrix_taz.xml")
        fl.addWidget(self.osm_row)
        fl.addWidget(self.taz_row)
        fl.addWidget(self.matrix_row)
        root.addWidget(files)

        settings = QGroupBox("Настройки")
        grid = QGridLayout(settings)
        grid.addWidget(QLabel("Папка SUMO (необязательно)"), 0, 0)
        self.sumo_bin = QLineEdit()
        self.sumo_bin.setPlaceholderText("Например: C:\\Program Files (x86)\\Eclipse\\Sumo\\bin")
        browse = QPushButton("Выбрать…")
        browse.clicked.connect(self.choose_sumo_bin)
        grid.addWidget(self.sumo_bin, 0, 1)
        grid.addWidget(browse, 0, 2)
        grid.addWidget(QLabel("Папка результата"), 1, 0)
        self.output_dir = QLineEdit()
        self.output_dir.setPlaceholderText("Если пусто — рядом с OSM создастся папка sumo_project")
        out_button = QPushButton("Выбрать…")
        out_button.clicked.connect(self.choose_output)
        grid.addWidget(self.output_dir, 1, 1)
        grid.addWidget(out_button, 1, 2)
        root.addWidget(settings)

        actions = QHBoxLayout() 
        
        practice_button = QPushButton("Загрузить дефолтные файлы") 
        practice_button.clicked.connect(self._load_practice_defaults) 
        
        self.export_network_button = QPushButton("ЭКСПОРТИРОВАТЬ СЕТЬ OSM") 
        self.export_network_button.setMinimumHeight(46) 
        self.export_network_button.clicked.connect(self.export_network) 

        self.open_network_button = QPushButton("ОТКРЫТЬ СЕТЬ В SUMO")
        self.open_network_button.setMinimumHeight(46)
        self.open_network_button.setEnabled(False)
        self.open_network_button.clicked.connect(
            self.launch_exported_network
        )

        self.matrix_editor_button = QPushButton("МАТРИЦА КОРРЕСПОНДЕНЦИЙ")
        self.matrix_editor_button.setMinimumHeight(46)
        self.matrix_editor_button.clicked.connect(
            self.open_matrix_editor
        )
        
        self.edit_taz_button = QPushButton("РЕДАКТИРОВАТЬ TAZ-ЗОНЫ")
        self.edit_taz_button.clicked.connect(self.edit_taz_zones)

        self.prepare_button = QPushButton("ПОДГОТОВИТЬ МОДЕЛЬ")
        
        self.prepare_button.setObjectName("primary")
        self.prepare_button.setMinimumHeight(46)
        self.prepare_button.clicked.connect(self.prepare)
        self.sumo_button = QPushButton("ЗАПУСТИТЬ SUMO")
        self.sumo_button.setMinimumHeight(46)
        self.sumo_button.setEnabled(False)
        self.sumo_button.clicked.connect(self.launch_sumo)
        
        actions.addWidget(practice_button, 1) 
        actions.addWidget(self.export_network_button, 2) 
        actions.addWidget(self.open_network_button, 2)
        actions.addWidget(self.matrix_editor_button, 2)
        actions.addWidget(self.edit_taz_button, 2)
        actions.addWidget(self.prepare_button, 2) 
        actions.addWidget(self.sumo_button, 1) 
        root.addLayout(actions)

        stats = QGroupBox("Результат")
        sg = QGridLayout(stats)
        self.stat_trip = QLabel("—")
        self.stat_route = QLabel("—")
        self.stat_warn = QLabel("—")
        self.stat_time = QLabel("—")
        for i, (name, value) in enumerate([
            ("Поездок", self.stat_trip),
            ("Маршрутов", self.stat_route),
            ("Предупреждений", self.stat_warn),
            ("Время", self.stat_time),
        ]):
            card = QFrame()
            card.setObjectName("stat")
            cl = QVBoxLayout(card)
            cl.addWidget(QLabel(name))
            value.setObjectName("statValue")
            cl.addWidget(value)
            sg.addWidget(card, 0, i)
        root.addWidget(stats)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.hide()
        root.addWidget(self.progress)

        log_group = QGroupBox("Журнал выполнения")
        ll = QVBoxLayout(log_group)
        self.log_box = QPlainTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setMinimumHeight(220)
        ll.addWidget(self.log_box)
        root.addWidget(log_group, 1)

        self.statusBar().showMessage("Готово")

    def edit_taz_zones(self):
        osm_text = self.osm_row.edit.text().strip()
        taz_text = self.taz_row.edit.text().strip()
        matrix_text = self.matrix_row.edit.text().strip()

        if not osm_text:
            QMessageBox.warning(
                self,
                "Не выбран OSM",
                "Сначала загрузите файл OpenStreetMap."
            )
            return

        osm_file = Path(osm_text)

        if not osm_file.is_file():
            QMessageBox.warning(
                self,
                "Файл не найден",
                f"Не найден файл OSM:\n{osm_file}"
            )
            return

        # Используем папку результатов, заданную пользователем.
        # Если она не указана, создаём проект рядом с исходным OSM.
        output_text = self.output_dir.text().strip()
        if output_text:
            workspace = Path(output_text)
        else:
            workspace = osm_file.parent / "sumo_network_export"

        working_taz = workspace / "sumo.add.xml"

        # Если рабочего файла зон ещё нет и исходный файл не выбран,
        # сначала спрашиваем разрешение создать его.
        if not working_taz.exists() and not taz_text:
            answer = QMessageBox.question(
                self,
                "Создать TAZ-зоны?",
                "В рабочей папке нет файла зон.\n\n"
                "Создать новый файл и открыть NetEdit для "
                "редактирования зон?",
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return

        original_taz = Path(taz_text) if taz_text else None

        try:
            runner = SumoRunner(
                self.sumo_bin.text().strip() or None,
                self.append_log,
            )

            launcher = TazEditorLauncher(
                runner,
                log=self.append_log,
            )

            network, taz_file = launcher.prepare_workspace(
                osm_file=osm_file,
                original_taz_file=original_taz,
                workspace=workspace,
            )

            # При первом создании проекта переносим исходную матрицу
            # в рабочую папку, чтобы все данные проекта лежали вместе.
            working_matrix = workspace / "matrix_taz.xml"

            self.taz_row.edit.setText(str(taz_file))

            if working_matrix.is_file():
                self.matrix_row.edit.setText(str(working_matrix))
            else:
                self.matrix_row.edit.clear()

            # Обновляем поля: дальше приложение работает с копиями проекта.
            self.taz_row.edit.setText(str(taz_file))
            if working_matrix.exists():
                self.matrix_row.edit.setText(str(working_matrix))

            launcher.open_editor(network, taz_file)

        except (TazEditorError, OSError, ValueError) as exc:
            QMessageBox.critical(
                self,
                "Ошибка редактирования TAZ",
                str(exc),
            )

    def _apply_style(self):
        self.setStyleSheet("""
            QMainWindow, QWidget { background: #f5f7fa; color: #172033; }
            QGroupBox { background: white; border: 1px solid #dce2ea; border-radius: 10px; margin-top: 10px; padding: 16px; font-weight: 600; }
            QGroupBox::title { subcontrol-origin: margin; left: 14px; padding: 0 6px; }
            QLineEdit, QPlainTextEdit { background: white; border: 1px solid #cfd6e0; border-radius: 7px; padding: 8px; }
            QLineEdit:focus { border: 1px solid #5277d8; }
            QPushButton { background: white; border: 1px solid #cbd3df; border-radius: 7px; padding: 9px 14px; font-weight: 600; }
            QPushButton:hover { background: #f0f4fb; }
            QPushButton:disabled { color: #9aa3af; }
            QPushButton#primary { background: #2f64d6; color: white; border: none; }
            QPushButton#primary:hover { background: #2758bf; }
            QLabel#subtitle { color: #6b7585; font-size: 13px; }
            QFrame#stat { background: #fbfcfe; border: 1px solid #e1e6ed; border-radius: 8px; }
            QLabel#statValue { font-size: 21px; font-weight: 700; }
            QProgressBar { border: 0; border-radius: 4px; background: #e8ecf2; height: 8px; }
            QProgressBar::chunk { background: #5277d8; border-radius: 4px; }
        """)

    def _load_practice_defaults(self):
        base = Path(__file__).resolve().parents[2] / "sample_data"
        self.osm_row.edit.setText(str(base / "map2.osm"))
        self.taz_row.edit.setText(str(base / "sumo.add.xml"))
        self.matrix_row.edit.setText(str(base / "matrix_taz.xml"))
        self.output_dir.clear()

    def choose_sumo_bin(self):
        path = QFileDialog.getExistingDirectory(self, "Выберите папку SUMO/bin")
        if path:
            self.sumo_bin.setText(path)

    def choose_output(self):
        path = QFileDialog.getExistingDirectory(self, "Папка результата")
        if path:
            self.output_dir.setText(path)

    def get_project_dir(self, osm_file: Path) -> Path:
        output_text = self.output_dir.text().strip()

        # Если пользователь указал папку вручную — используем её.
        if output_text:
            return Path(output_text).expanduser().resolve()

        # Если сеть уже экспортирована — используем её папку.
        exported_network = getattr(self, "exported_network", None)
        if exported_network and Path(exported_network).is_file():
            return Path(exported_network).resolve().parent

        # Папка по умолчанию для нового проекта.
        return Path(osm_file).resolve().parent / "sumo_network_export"

    def export_network(self):
        osm_text = self.osm_row.edit.text().strip()

        if not osm_text:
            QMessageBox.warning(
                self,
                "Не выбран OSM-файл",
                "Сначала выберите файл OSM."
            )
            return

        osm = Path(osm_text)

        if not osm.is_file():
            QMessageBox.warning(
                self,
                "Файл не найден",
                f"Не удалось найти OSM-файл:\n{osm}"
            )
            return

        output_dir = self.get_project_dir(osm)

        try:
            output_dir.mkdir(parents=True, exist_ok=True)

            network_file = output_dir / "network.net.xml"

            sumo_bin_text = self.sumo_bin.text().strip()

            if sumo_bin_text:
                netconvert = Path(sumo_bin_text) / (
                    "netconvert.exe" if __import__("os").name == "nt"
                    else "netconvert"
                )

                if not netconvert.is_file():
                    raise FileNotFoundError(
                        f"Не найден netconvert:\n{netconvert}"
                    )

                command = str(netconvert)
            else:
                command = "netconvert"

            args = [
                command,
                "--osm-files", str(osm),
                "--output-file", str(network_file),
            ]

            self.log_box.clear()
            self.append_log("=== Экспорт дорожной сети ===")
            self.append_log("$ " + subprocess.list2cmdline(args))

            self.export_network_button.setEnabled(False)
            self.prepare_button.setEnabled(False)
            self.sumo_button.setEnabled(False)
            self.progress.show()

            try:
                process = subprocess.run(
                    args,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=300,
                    check=False,
                )

                if process.stdout:
                    self.append_log(process.stdout.strip())

                if process.stderr:
                    self.append_log(process.stderr.strip())

                if process.returncode != 0:
                    raise RuntimeError(
                        f"netconvert завершился с кодом "
                        f"{process.returncode}."
                    )

                if not network_file.is_file():
                    raise RuntimeError(
                        "netconvert завершился, но файл сети не создан."
                    )

                self.exported_network = network_file
                
                self.open_network_button.setEnabled(True)

                self.append_log(
                    f"Сеть экспортирована: {network_file}"
                )
                self.statusBar().showMessage(
                    "Экспорт дорожной сети завершён"
                )

                QMessageBox.information(
                    self,
                    "Экспорт завершён",
                    f"Дорожная сеть сохранена:\n{network_file}"
                )

            finally:
                self.progress.hide()
                self.export_network_button.setEnabled(True)
                self.prepare_button.setEnabled(True)

        except Exception as exc:
            self.append_log(f"ОШИБКА ЭКСПОРТА: {exc}")
            self.statusBar().showMessage("Ошибка экспорта сети")
            QMessageBox.critical(
                self,
                "Ошибка экспорта",
                str(exc)
            )

    @Slot(str)
    def append_log(self, message: str):
        self.log_box.appendPlainText(message)
        self.log_box.verticalScrollBar().setValue(self.log_box.verticalScrollBar().maximum())
        if message.startswith("=== "):
            self.statusBar().showMessage(message.replace("=== ", "").replace(" ===", "…"))
        elif message.startswith("$ "):
            self.statusBar().showMessage("Выполняется команда SUMO…")

    def open_matrix_editor(self):
        taz_text = self.taz_row.edit.text().strip()

        if not taz_text:
            QMessageBox.warning(
                self,
                "Не выбраны TAZ-зоны",
                "Сначала загрузите файл зон или создайте их через NetEdit.",
            )
            return

        taz_path = Path(taz_text)

        if not taz_path.is_file():
            QMessageBox.warning(
                self,
                "Файл зон не найден",
                f"Не найден файл:\n{taz_path}",
            )
            return

        # Рабочая матрица хранится рядом с рабочим файлом TAZ.
        working_matrix = taz_path.parent / "matrix_taz.xml"

        # Если рабочей матрицы пока нет, используем выбранную
        # пользователем матрицу как исходную.
        selected_matrix = self.matrix_row.edit.text().strip()

        if not working_matrix.exists() and selected_matrix:
            source = Path(selected_matrix)

            if source.is_file() and source.resolve() != working_matrix.resolve():
                shutil.copy2(source, working_matrix)

        dialog = MatrixEditorDialog(
            self,
            taz_path=str(taz_path),
            matrix_path=str(working_matrix),
        )

        dialog.exec()

        if dialog.saved_path is not None:
            self.matrix_row.edit.setText(str(dialog.saved_path))
            self.append_log(
                f"Выбрана матрица корреспонденций: {dialog.saved_path}"
            )

    def prepare(self):
        osm = self.osm_row.path()
        taz = self.taz_row.path()
        matrix = self.matrix_row.path()
        if not all((osm, taz, matrix)):
            QMessageBox.warning(self, "Не хватает файлов", "Выберите OSM, TAZ и матрицу.")
            return
        if not all(p.exists() for p in (osm, taz, matrix)):
            QMessageBox.warning(self, "Файл не найден", "Проверьте пути к исходным файлам.")
            return

        out_text = self.output_dir.text().strip()

        if out_text:
            output = Path(out_text).resolve()
        else:
            taz_text = self.taz_row.edit.text().strip()

            if taz_text:
                taz_path = Path(taz_text).resolve()
                # Если TAZ уже находится в рабочей папке проекта,
                # используем эту же папку.
                if taz_path.name == "sumo.add.xml":
                    output = taz_path.parent
                else:
                    output = osm.parent / f"{osm.stem}_sumo_project"
            else:
                output = osm.parent / f"{osm.stem}_sumo_project"

        sumo_bin = self.sumo_bin.text().strip() or None

        self.log_box.clear()
        self.append_log("Запуск подготовки модели…")
        self.prepare_button.setEnabled(False)
        self.sumo_button.setEnabled(False)
        self.progress.show()
        self.statusBar().showMessage("Подготовка модели…")

        inputs = PipelineInputs(osm, taz, matrix, output)
        self.worker_thread = QThread(self)
        self.worker = PipelineWorker(inputs, sumo_bin)
        worker = self.worker
        worker.moveToThread(self.worker_thread)
        self.worker_thread.started.connect(worker.run)
        worker.log.connect(self.append_log)
        worker.finished.connect(self.on_finished)
        worker.failed.connect(self.on_failed)
        worker.finished.connect(self.worker_thread.quit)
        worker.failed.connect(self.worker_thread.quit)
        self.worker_thread.finished.connect(worker.deleteLater)
        self.worker_thread.finished.connect(self.thread_done)
        self.worker_thread.start()

    @Slot(object)
    def on_finished(self, result: PipelineResult):
        self.last_result = result
        self.stat_trip.setText(str(result.trip_count))
        self.stat_route.setText(str(result.route_count))
        self.stat_warn.setText(str(result.warning_count))
        self.stat_time.setText(f"{result.elapsed_seconds:.1f} с")
        self.sumo_button.setEnabled(True)
        self.statusBar().showMessage(f"Модель готова: {result.output_dir}")
        QMessageBox.information(self, "Модель подготовлена", f"Файлы сохранены в:\n{result.output_dir}")

    @Slot(str)
    def on_failed(self, message: str):
        self.append_log("\nОШИБКА: " + message)
        self.statusBar().showMessage("Ошибка подготовки")
        QMessageBox.critical(self, "Ошибка", message)

    def thread_done(self):
        self.progress.hide()
        self.prepare_button.setEnabled(True)
        self.worker = None
        self.worker_thread = None

    def launch_exported_network(self):
        if not self.exported_network:
            QMessageBox.warning(
                self,
                "Сеть не экспортирована",
                "Сначала нажмите «ЭКСПОРТИРОВАТЬ СЕТЬ OSM»."
            )
            return

        try:
            runner = SumoRunner(
                self.sumo_bin.text().strip() or None,
                self.append_log,
            )
            runner.launch_network(self.exported_network)
            self.statusBar().showMessage(
                "Дорожная сеть открыта в SUMO"
            )
        except Exception as exc:
            self.append_log(f"ОШИБКА ЗАПУСКА SUMO: {exc}")
            QMessageBox.critical(
                self,
                "Не удалось открыть сеть",
                str(exc),
            )

    
    def launch_sumo(self):
        if not self.last_result:
            QMessageBox.warning(
                self,
                "Модель не подготовлена",
                "Сначала нажмите «ПОДГОТОВИТЬ МОДЕЛЬ»."
            )
            return

        try:
            runner = SumoRunner(
                self.sumo_bin.text().strip() or None,
                self.append_log,
            )

            config = self.last_result.config.resolve()

            if not config.is_file():
                raise FileNotFoundError(
                    f"Конфигурация модели не найдена:\n{config}"
                )

            self.append_log(f"Запуск модели из проекта: {config.parent}")

            ModelPipeline(
                runner,
                self.append_log
            ).launch_sumo(config)

            self.statusBar().showMessage(
                f"SUMO запущен: {config.parent}"
            )

        except Exception as exc:
            self.append_log(f"ОШИБКА ЗАПУСКА SUMO: {exc}")
            QMessageBox.critical(
                self,
                "Не удалось запустить SUMO",
                str(exc)
            )


    def closeEvent(self, event):
        if self.worker_thread and self.worker_thread.isRunning():
            QMessageBox.warning(self, "Идёт подготовка", "Дождитесь завершения подготовки модели.")
            event.ignore()
            return
        event.accept()


def run():
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    return app.exec()
