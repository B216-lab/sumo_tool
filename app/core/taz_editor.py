from __future__ import annotations

import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Callable

from .sumo_runner import SumoRunner


LogCallback = Callable[[str], None]


class TazEditorError(RuntimeError):
    pass


class TazEditorLauncher:
    """Подготавливает рабочие файлы проекта и запускает NetEdit для редактирования TAZ."""

    def __init__(
        self,
        runner: SumoRunner,
        log: LogCallback | None = None,
    ):
        self.runner = runner
        self.log = log or (lambda _: None)

    
    def prepare_workspace(
        self,
        osm_file: Path,
        original_taz_file: Path | None,
        workspace: Path,
    ) -> tuple[Path, Path]:
        osm_file = Path(osm_file).resolve()
        workspace = Path(workspace).resolve()

        if not osm_file.is_file():
            raise TazEditorError(f"Не найден файл OSM: {osm_file}")

        workspace.mkdir(parents=True, exist_ok=True)

        # Исходный OSM сохраняем в рабочей папке.
        source_osm = workspace / "source.osm"

        if osm_file != source_osm.resolve() and not source_osm.exists():
            shutil.copy2(osm_file, source_osm)

        if not source_osm.is_file():
            raise TazEditorError(
                f"Не найден исходный файл карты: {source_osm}"
            )

        # Сеть должна быть предварительно экспортирована
        # отдельной кнопкой в интерфейсе.
        network = workspace / "network.net.xml"

        if not network.is_file():
            raise TazEditorError(
                "Сначала экспортируйте дорожную сеть OSM "
                "в эту рабочую папку.\n\n"
                f"Ожидаемый файл:\n{network}"
            )

        # Не копируем старые зоны из sample_data.
        # Если зон ещё нет, создаём пустой файл для NetEdit.
        taz_file = workspace / "sumo.add.xml"

        if not taz_file.is_file():
            root = ET.Element(
                "additional",
                {
                    "xmlns:xsi": (
                        "http://www.w3.org/2001/XMLSchema-instance"
                    ),
                    "xsi:noNamespaceSchemaLocation": (
                        "http://sumo.dlr.de/xsd/additional_file.xsd"
                    ),
                },
            )

            tree = ET.ElementTree(root)
            ET.indent(tree, space="    ")
            tree.write(
                taz_file,
                encoding="UTF-8",
                xml_declaration=True,
            )

            self.log(
                "Создан новый пустой файл TAZ для текущей сети. "
                "Добавьте зоны в NetEdit."
            )

        self.log(f"Рабочая сеть: {network}")
        self.log(f"Рабочий файл TAZ: {taz_file}")

        return network, taz_file


    def open_editor(
        self,
        network_file: Path,
        taz_file: Path,
    ) -> None:
        executable = self.runner.executable("netedit")

        # Если указан каталог SUMO, проверяем наличие NetEdit в нём.
        if self.runner.sumo_bin:
            executable_path = Path(executable)
            if not executable_path.is_file():
                raise TazEditorError(
                    f"Не найден NetEdit: {executable_path}"
                )

        self.log("Запуск NetEdit для редактирования TAZ-зон...")
        try:
            subprocess.Popen(
                [
                    executable,
                    "--net-file",
                    str(Path(network_file).resolve()),
                    "--additional-files",
                    str(Path(taz_file).resolve()),
                ],
                cwd=str(Path(network_file).resolve().parent),
            )
        except OSError as exc:
            raise TazEditorError(
                "Не удалось запустить NetEdit. Проверьте путь к SUMO "
                f"и наличие netedit.exe.\n{exc}"
            ) from exc