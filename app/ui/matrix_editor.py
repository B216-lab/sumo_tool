
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)


class MatrixEditorDialog(QDialog):
    """Создание, загрузка, редактирование и сохранение матрицы TAZ."""

    def __init__(
        self,
        parent=None,
        taz_path: str = "",
        matrix_path: str = "",
    ):
        super().__init__(parent)

        self.setWindowTitle("Матрица корреспонденций")
        self.resize(1000, 650)

        self.zone_ids: list[str] = []
        self.begin = 0
        self.end = 3600
        self.saved_path: Path | None = None

        self.taz_path_edit = QLineEdit(taz_path)
        self.matrix_path_edit = QLineEdit(matrix_path)

        self.table = QTableWidget()
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeToContents
        )
        self.table.verticalHeader().setSectionResizeMode(
            QHeaderView.ResizeToContents
        )

        self.status_label = QLabel(
            "Выберите файл зон, чтобы создать новую матрицу, "
            "или загрузите существующую."
        )
        self.status_label.setWordWrap(True)

        choose_taz_button = QPushButton("Обзор…")
        choose_taz_button.clicked.connect(self.choose_taz)

        choose_matrix_button = QPushButton("Обзор…")
        choose_matrix_button.clicked.connect(self.choose_matrix)

        create_button = QPushButton("Создать новую")
        create_button.clicked.connect(self.create_new)

        load_button = QPushButton("Загрузить матрицу")
        load_button.clicked.connect(self.load_matrix)

        save_button = QPushButton("Сохранить как…")
        save_button.clicked.connect(self.save_matrix)

        close_button = QPushButton("Закрыть")
        close_button.clicked.connect(self.reject)

        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("Файл зон TAZ:"))

        taz_row = QHBoxLayout()
        taz_row.addWidget(self.taz_path_edit)
        taz_row.addWidget(choose_taz_button)
        layout.addLayout(taz_row)

        layout.addWidget(QLabel("Существующая матрица XML:"))

        matrix_row = QHBoxLayout()
        matrix_row.addWidget(self.matrix_path_edit)
        matrix_row.addWidget(choose_matrix_button)
        layout.addLayout(matrix_row)

        actions = QHBoxLayout()
        actions.addWidget(create_button)
        actions.addWidget(load_button)
        actions.addStretch()
        actions.addWidget(save_button)
        actions.addWidget(close_button)
        layout.addLayout(actions)

        layout.addWidget(self.status_label)
        layout.addWidget(self.table, 1)

    def choose_taz(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Выберите файл зон SUMO",
            self.taz_path_edit.text(),
            "XML-файлы (*.xml);;Все файлы (*)",
        )
        if path:
            self.taz_path_edit.setText(path)

    def choose_matrix(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Выберите матрицу корреспонденций",
            self.matrix_path_edit.text(),
            "XML-файлы (*.xml);;Все файлы (*)",
        )
        if path:
            self.matrix_path_edit.setText(path)

    def _read_zone_ids(self) -> list[str]:
        path_text = self.taz_path_edit.text().strip()

        if not path_text:
            raise ValueError("Сначала выберите файл зон TAZ.")

        path = Path(path_text)

        if not path.is_file():
            raise FileNotFoundError(
                f"Файл зон не найден:\n{path}"
            )

        try:
            root = ET.parse(path).getroot()
        except ET.ParseError as exc:
            raise ValueError(
                f"Не удалось прочитать XML файла зон.\n{exc}"
            ) from exc

        zone_ids = [
            element.get("id", "").strip()
            for element in root.iter("taz")
            if element.get("id", "").strip()
        ]

        if not zone_ids:
            raise ValueError(
                "В выбранном файле не найдены элементы <taz id=\"...\">."
            )

        if len(zone_ids) != len(set(zone_ids)):
            raise ValueError(
                "В файле зон обнаружены повторяющиеся идентификаторы TAZ."
            )

        return zone_ids

    def create_new(self):
        try:
            self.zone_ids = self._read_zone_ids()
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, "Не удалось создать матрицу", str(exc))
            return

        self.begin = 0
        self.end = 3600
        self._build_table({})

        self.status_label.setText(
            f"Создана новая матрица: {len(self.zone_ids)} зон, "
            f"{len(self.zone_ids) ** 2} ячеек. "
            "Все значения первоначально равны нулю."
        )
        self.saved_path = None

    def load_matrix(self):
        try:
            zone_ids = self._read_zone_ids()

            matrix_text = self.matrix_path_edit.text().strip()
            if not matrix_text:
                raise ValueError(
                    "Выберите XML существующей матрицы."
                )

            matrix_path = Path(matrix_text)
            if not matrix_path.is_file():
                raise FileNotFoundError(
                    f"Файл матрицы не найден:\n{matrix_path}"
                )

            root = ET.parse(matrix_path).getroot()
            interval = root.find(".//interval")

            if interval is None:
                raise ValueError(
                    "В матрице не найден элемент <interval>."
                )

            begin = int(interval.get("begin", "0"))
            end = int(interval.get("end", "3600"))

            values: dict[tuple[str, str], int] = {}

            for relation in interval.findall("tazRelation"):
                origin = relation.get("from")
                destination = relation.get("to")
                count_text = relation.get("count")

                if not origin or not destination or count_text is None:
                    raise ValueError(
                        "У элемента <tazRelation> отсутствует "
                        "from, to или count."
                    )

                if origin not in zone_ids or destination not in zone_ids:
                    raise ValueError(
                        "Матрица содержит ссылки на зоны, которых "
                        "нет в выбранном файле TAZ.\n"
                        f"Отправление: {origin}\n"
                        f"Назначение: {destination}"
                    )

                count = int(count_text)
                if count < 0:
                    raise ValueError(
                        "Количество поездок не может быть отрицательным."
                    )

                key = (origin, destination)
                if key in values:
                    raise ValueError(
                        "В матрице найдены повторные записи для пары "
                        f"{origin} → {destination}."
                    )

                values[key] = count

            self.zone_ids = zone_ids
            self.begin = begin
            self.end = end
            self._build_table(values)

            self.status_label.setText(
                f"Матрица загружена. Зон: {len(zone_ids)}. "
                f"Интервал: {begin}–{end} секунд. "
                "Отсутствующие пары имеют значение 0."
            )
            self.saved_path = None

        except (ValueError, OSError, ET.ParseError) as exc:
            QMessageBox.warning(
                self, "Не удалось загрузить матрицу", str(exc)
            )

    def _build_table(self, values: dict[tuple[str, str], int]):
        size = len(self.zone_ids)
        self.table.clear()
        self.table.setRowCount(size)
        self.table.setColumnCount(size)
        self.table.setVerticalHeaderLabels(self.zone_ids)
        self.table.setHorizontalHeaderLabels(self.zone_ids)

        for row, origin in enumerate(self.zone_ids):
            for column, destination in enumerate(self.zone_ids):
                value = values.get((origin, destination), 0)
                item = QTableWidgetItem(str(value))
                self.table.setItem(row, column, item)

    def save_matrix(self):
        if not self.zone_ids:
            QMessageBox.warning(
                self,
                "Нет данных",
                "Сначала создайте новую матрицу или загрузите существующую.",
            )
            return

        values: list[tuple[str, str, int]] = []

        for row, origin in enumerate(self.zone_ids):
            for column, destination in enumerate(self.zone_ids):
                item = self.table.item(row, column)
                text = item.text().strip() if item else ""

                if not text:
                    count = 0
                else:
                    try:
                        count = int(text)
                    except ValueError:
                        QMessageBox.warning(
                            self,
                            "Некорректное значение",
                            f"Ячейка {origin} → {destination} "
                            "должна содержать целое неотрицательное число.",
                        )
                        self.table.setCurrentCell(row, column)
                        return

                if count < 0:
                    QMessageBox.warning(
                        self,
                        "Некорректное значение",
                        "Количество поездок не может быть отрицательным.",
                    )
                    self.table.setCurrentCell(row, column)
                    return

                values.append((origin, destination, count))

        default_path = str(
            Path(self.taz_path_edit.text()).parent
            / "matrix_taz.xml"
        )

        path_text, _ = QFileDialog.getSaveFileName(
            self,
            "Сохранить матрицу",
            default_path,
            "XML-файлы (*.xml)",
        )

        if not path_text:
            return

        output_path = Path(path_text)
        if output_path.suffix.lower() != ".xml":
            output_path = output_path.with_suffix(".xml")

        root = ET.Element("odMatrix")
        interval = ET.SubElement(
            root,
            "interval",
            {"begin": str(self.begin), "end": str(self.end)},
        )

        for origin, destination, count in values:
            if count == 0:
                continue

            ET.SubElement(
                interval,
                "tazRelation",
                {
                    "from": origin,
                    "to": destination,
                    "count": str(count),
                },
            )

        try:
            tree = ET.ElementTree(root)
            ET.indent(tree, space="    ")
            tree.write(
                output_path,
                encoding="UTF-8",
                xml_declaration=True,
            )
        except OSError as exc:
            QMessageBox.critical(
                self,
                "Ошибка сохранения",
                str(exc),
            )
            return

        self.saved_path = output_path
        self.matrix_path_edit.setText(str(output_path))

        QMessageBox.information(
            self,
            "Матрица сохранена",
            f"Файл сохранён:\n{output_path}",
        )