from __future__ import annotations

import re
import shutil
import subprocess
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .sumo_runner import SumoRunner


LogCallback = Callable[[str], None]


@dataclass
class PipelineInputs:
    osm: Path
    taz: Path
    matrix: Path
    output_dir: Path
    begin: int = 0
    end: int = 3600


@dataclass
class PipelineResult:
    output_dir: Path
    network: Path
    trips: Path
    routes: Path
    config: Path
    trip_count: int
    route_count: int
    warning_count: int
    elapsed_seconds: float


class PipelineError(RuntimeError):
    pass


class ModelPipeline:
    def __init__(self, runner: SumoRunner, log: LogCallback | None = None):
        self.runner = runner
        self.log = log or (lambda _: None)

    def prepare(self, inputs: PipelineInputs) -> PipelineResult:
        self._validate_inputs(inputs)
        started = time.perf_counter()
        out = inputs.output_dir.resolve()
        out.mkdir(parents=True, exist_ok=True)

        self.log("=== 1. Подготовка проекта ===")
        osm = self._copy_input(inputs.osm, out / "source.osm")
        taz = self._copy_input(inputs.taz, out / "sumo.add.xml")
        matrix = self._copy_input(inputs.matrix, out / "matrix_taz.xml")

        network = out / "network.net.xml"
        trips = out / "trips.trips.xml"
        routes = out / "routes.rou.xml"
        config = out / "sumo.sumocfg"

        self.log("=== 2. OSM → SUMO network (netconvert) ===")
        result = self.runner.run([
            self.runner.executable("netconvert"),
            "--osm-files", str(osm),
            "-o", str(network),
        ], cwd=out)
        self._require_success(result, "netconvert")

        self.log("=== 3. Матрица TAZ → поездки (od2trips) ===")
        result = self.runner.run([
            self.runner.executable("od2trips"),
            "--taz-files", str(taz),
            "--tazrelation-files", str(matrix),
            "-o", str(trips),
            "--begin", str(inputs.begin),
            "--end", str(inputs.end),
        ], cwd=out)
        self._require_success(result, "od2trips")

        self.log("=== 4. Поездки → маршруты (duarouter) ===")
        result = self.runner.run([
            self.runner.executable("duarouter"),
            "-n", str(network),
            "--route-files", str(trips),
            "-o", str(routes),
            "--ignore-errors",
        ], cwd=out)
        if result.returncode != 0:
            raise PipelineError(self._format_command_error("duarouter", result))

        warnings = self._count_warnings(result.stdout + "\n" + result.stderr)
        trip_count = self._count_xml_elements(trips, "trip")
        route_count = self._count_xml_elements(routes, "vehicle")

        self.log("=== 5. Создание конфигурации SUMO ===")
        self._write_config(config, network, routes, taz, inputs.begin, 200)

        elapsed = time.perf_counter() - started
        self.log("=== Готово ===")
        self.log(f"Поездок: {trip_count}")
        self.log(f"Маршрутов: {route_count}")
        self.log(f"Предупреждений/ошибок маршрутизации: {warnings}")
        self.log(f"Время подготовки: {elapsed:.2f} с")

        return PipelineResult(
            out, network, trips, routes, config,
            trip_count, route_count, warnings, elapsed,
        )

    def launch_sumo(self, config: Path) -> None:
        self.log("=== Запуск SUMO ===")
        subprocess.Popen(
            [self.runner.executable("sumo-gui"), "-c", str(config)],
            cwd=str(config.parent),
        )

    @staticmethod
    def _validate_inputs(inputs: PipelineInputs) -> None:
        for name, path in (("OSM", inputs.osm), ("TAZ", inputs.taz), ("матрица", inputs.matrix)):
            if not path.exists():
                raise PipelineError(f"Не найден файл {name}: {path}")
        if inputs.begin >= inputs.end:
            raise PipelineError("Время начала должно быть меньше времени окончания.")

    @staticmethod
    def _copy_input(source: Path, destination: Path) -> Path:
        shutil.copy2(source, destination)
        return destination

    @staticmethod
    def _require_success(result, tool: str) -> None:
        if result.returncode != 0:
            raise PipelineError(ModelPipeline._format_command_error(tool, result))

    @staticmethod
    def _format_command_error(tool: str, result) -> str:
        details = (result.stderr or result.stdout or "Нет дополнительного сообщения.").strip()
        return f"{tool} завершился с кодом {result.returncode}.\n\n{details}"

    @staticmethod
    def _count_xml_elements(path: Path, tag: str) -> int:
        count = 0
        for _, elem in ET.iterparse(path, events=("end",)):
            if elem.tag == tag:
                count += 1
            elem.clear()
        return count

    @staticmethod
    def _count_warnings(text: str) -> int:
        return len(re.findall(r"(?:^|\n)Warning:", text, flags=re.IGNORECASE))

    @staticmethod
    def _write_config(path: Path, network: Path, routes: Path, taz: Path, begin: int, end: int) -> None:
        root = ET.Element("sumoConfiguration", {
            "xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
            "xsi:noNamespaceSchemaLocation": "http://sumo.dlr.de/xsd/sumoConfiguration.xsd",
        })
        inp = ET.SubElement(root, "input")
        ET.SubElement(inp, "net-file", {"value": network.name})
        ET.SubElement(inp, "route-files", {"value": routes.name})
        ET.SubElement(inp, "additional-files", {"value": taz.name})
        timing = ET.SubElement(root, "time")
        ET.SubElement(timing, "begin", {"value": str(begin)})
        ET.SubElement(timing, "end", {"value": str(end)})
        ET.SubElement(timing, "step-length", {"value": "0.1"})
        tree = ET.ElementTree(root)
        ET.indent(tree, space="    ")
        tree.write(path, encoding="UTF-8", xml_declaration=True)
