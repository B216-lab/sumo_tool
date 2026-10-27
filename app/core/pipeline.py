from __future__ import annotations

import hashlib
import os
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
    osm: Path | None
    taz: Path
    matrix: Path
    output_dir: Path
    begin: int = 0
    end: int = 3600
    prepared_network: Path | None = None


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

        self.log('=== 1. Подготовка проекта ===')
        if inputs.prepared_network is not None:
            network = Path(inputs.prepared_network).resolve()
            self.log(f'Используется готовая сеть OSM Web Wizard: {network}')
        else:
            assert inputs.osm is not None
            osm = self._copy_input(inputs.osm, out / 'source.osm')
            network = out / 'network.net.xml'
            self.log('=== 2. Проверка сети SUMO ===')
            digest = hashlib.sha256(osm.read_bytes()).hexdigest()
            digest_file = out / 'network.source.sha256'
            recorded = digest_file.read_text(encoding='utf-8').strip() if digest_file.is_file() else None
            needs_conversion = not network.is_file() or (recorded is not None and recorded != digest)
            if not needs_conversion:
                self.log(f'Используется существующая сеть проекта: {network}')
                if recorded is None:
                    self.log('Сеть создана в старой версии; соответствие OSM автоматически не проверить.')
                    self.log('Если OSM менялся, нажмите «ЭКСПОРТИРОВАТЬ СЕТЬ OSM».')
            else:
                self.log('Создание/обновление дорожной сети через netconvert...')
                result = self.runner.run([
                    self.runner.executable('netconvert'), '--osm-files', str(osm), '-o', str(network)
                ], cwd=out)
                self._require_success(result, 'netconvert')
                if not network.is_file():
                    raise PipelineError(f'netconvert не создал файл сети: {network}')
                digest_file.write_text(digest, encoding='utf-8')
                self.log('Если идентификаторы дорог изменились, проверьте TAZ перед маршрутизацией.')

        taz = self._copy_input(inputs.taz, out / 'sumo.add.xml')
        matrix = self._copy_input(inputs.matrix, out / 'matrix_taz.xml')
        begin, end = self._matrix_bounds(matrix, inputs.begin, inputs.end)
        expected_demand = self._matrix_demand(matrix)
        demand_hours = (end - begin) / 3600
        hourly = expected_demand / demand_hours
        self.log(f'OD-матрица: {expected_demand} поездок за {(end - begin) / 60:.1f} мин; '
                 f'средняя интенсивность {hourly:.1f} авто/ч.')
        sparse_demand = 0 < hourly < 60
        if sparse_demand:
            self.log('ПРЕДУПРЕЖДЕНИЕ: Менее 60 поездок в час — движение на экране '
                     'может выглядеть очень редким. Это ориентир визуальной плотности, '
                     'а не норматив дорожного движения.')
        trips = out / 'trips.trips.xml'
        routes = out / 'routes.rou.xml'
        config = out / 'sumo.sumocfg'

        for target in (trips, routes, config):
            target.unlink(missing_ok=True)

        self.log('=== 3. Матрица TAZ → отдельные поездки (od2trips) ===')
        result = self.runner.run([
            self.runner.executable('od2trips'),
            '--taz-files', str(taz),
            '--tazrelation-files', str(matrix),
            '-o', str(trips),
            '--begin', str(begin), '--end', str(end),
        ], cwd=out)
        self._require_success(result, 'od2trips')
        if not trips.is_file():
            raise PipelineError('od2trips не создал файл поездок.')
        trip_count = self._count_xml_elements(trips, 'trip')
        if trip_count == 0:
            raise PipelineError('Матрица не создала поездок. Проверьте зоны и ненулевой спрос.')

        self.log('=== 4. Поездки → индивидуальные маршруты (duarouter) ===')
        result = self.runner.run([
            self.runner.executable('duarouter'),
            '-n', str(network), '--route-files', str(trips),
            '-o', str(routes), '--ignore-errors',
        ], cwd=out)
        self._require_success(result, 'duarouter')
        if not routes.is_file():
            raise PipelineError('duarouter не создал файл маршрутов.')
        warning_count = self._count_warnings(result.stdout + '\n' + result.stderr) + int(sparse_demand)
        route_count = self._count_xml_elements(routes, 'vehicle')
        self.log(f'Сформировано поездок: {trip_count}; маршрутов: {route_count}')
        if trip_count != expected_demand:
            self.log(f'ПРЕДУПРЕЖДЕНИЕ: в OD-матрице задано {expected_demand}, '
                     f'однако od2trips сформировал {trip_count}. Проверьте настройки/логи.')
            warning_count += 1
        self.log('Отправления по шести равным интервалам: ' +
                 self._departure_histogram(trips, begin, end))
        if route_count == 0:
            raise PipelineError('Нет построенных маршрутов; проверьте соединения дорог и состав TAZ.')
        if route_count < trip_count:
            self.log(f'ПРЕДУПРЕЖДЕНИЕ: {trip_count - route_count} поездок не удалось маршрутизировать.')
            warning_count += 1
        elif route_count == trip_count:
            self.log('Все созданные поездки получили маршруты (прибытие ещё не проверено).')

        self.log('=== 5. Создание конфигурации SUMO ===')

        sim_end = end + 900
        additional = [taz]
        if inputs.prepared_network is not None:
            poly = out / 'wizard' / 'osm.poly.xml.gz'
            if poly.is_file():
                additional.append(poly)
        self._write_config(config, network, routes, additional, begin, sim_end)
        self.log(f'Симуляция: {begin}–{sim_end} с (включая 900 с после конца спроса).')

        elapsed = time.perf_counter() - started
        self.log('=== Готово ===')
        self.log(f'Поездок: {trip_count}; маршрутов: {route_count}; предупреждений: {warning_count}')
        self.log(f'Время подготовки: {elapsed:.2f} с')
        return PipelineResult(out, network, trips, routes, config,
                              trip_count, route_count, warning_count, elapsed)

    def launch_sumo(self, config: Path) -> None:
        config = Path(config).resolve()
        if not config.is_file():
            raise PipelineError(f'Не найдена конфигурация SUMO: {config}')
        self.log(f'Запуск SUMO: {config}')
        subprocess.Popen([self.runner.executable('sumo-gui'), '-c', str(config)], cwd=str(config.parent))

    @staticmethod
    def _validate_inputs(inputs: PipelineInputs) -> None:
        required = [('TAZ', inputs.taz), ('матрица', inputs.matrix)]
        if inputs.prepared_network is not None:
            required.append(('сеть Wizard', inputs.prepared_network))
        else:
            if inputs.osm is None:
                raise PipelineError('Выберите OSM-файл или импортируйте сценарий Wizard.')
            required.append(('OSM', inputs.osm))
        for label, p in required:
            if not Path(p).is_file():
                raise PipelineError(f'Не найден файл {label}: {p}')
        if inputs.begin >= inputs.end:
            raise PipelineError('Время начала должно быть меньше времени окончания.')

    @staticmethod
    def _matrix_bounds(path: Path, fallback_begin: int, fallback_end: int) -> tuple[int,int]:
        try:
            root = ET.parse(path).getroot()
            intervals = list(root.iter('interval'))
            if not intervals:
                return fallback_begin, fallback_end
            begins = [int(float(i.get('begin', fallback_begin))) for i in intervals]
            ends = [int(float(i.get('end', fallback_end))) for i in intervals]
            begin, end = min(begins), max(ends)
            if begin < 0 or end <= begin:
                raise ValueError('Неверное время в матрице.')
            return begin, end
        except (ET.ParseError, ValueError) as exc:
            raise PipelineError(f'Некорректная матрица корреспонденций: {exc}') from exc

    @staticmethod
    def _copy_input(source: Path, destination: Path) -> Path:
        source, destination = Path(source).resolve(), Path(destination).resolve()
        if not source.is_file():
            raise PipelineError(f'Исходный файл не найден: {source}')
        if source != destination:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        return destination

    @staticmethod
    def _require_success(result, tool: str) -> None:
        if result.returncode != 0:
            details = (result.stderr or result.stdout or 'Нет дополнительного сообщения.').strip()
            raise PipelineError(f'{tool} завершился с кодом {result.returncode}.\n{details}')

    @staticmethod
    def _matrix_demand(path: Path) -> int:
        try:
            root = ET.parse(path).getroot()
            total = 0
            for relation in root.iter('tazRelation'):
                count = float(relation.get('count', '0'))
                if count < 0 or not count.is_integer():
                    raise ValueError('Количество поездок должно быть целым неотрицательным.')
                total += int(count)
            return total
        except (ET.ParseError, ValueError) as exc:
            raise PipelineError(f'Невозможно подсчитать спрос в OD-матрице: {exc}') from exc

    @staticmethod
    def _departure_histogram(path: Path, begin: int, end: int) -> str:
        counts = [0] * 6
        width = (end - begin) / 6
        try:
            for _, el in ET.iterparse(path, events=('end',)):
                if el.tag == 'trip':
                    depart = float(el.get('depart', str(begin)))
                    bucket = max(0, min(5, int((depart - begin) / width)))
                    counts[bucket] += 1
                el.clear()
        except (ET.ParseError, ValueError):
            return 'не удалось получить времена отправления'
        labels = [f'{(begin+width*i)/60:.0f}–{(begin+width*(i+1))/60:.0f} мин: {n}'
                  for i, n in enumerate(counts)]
        return '; '.join(labels)

    @staticmethod
    def _count_xml_elements(path: Path, tag: str) -> int:
        count = 0
        for _, elem in ET.iterparse(path, events=('end',)):
            if elem.tag == tag:
                count += 1
            elem.clear()
        return count

    @staticmethod
    def _count_warnings(text: str) -> int:
        return len(re.findall(r'(?:^|\n)(?:Warning:|Error:)', text, flags=re.IGNORECASE))

    @staticmethod
    def _write_config(path: Path, network: Path, routes: Path, additionals: list[Path], begin: int, end: int) -> None:
        root = ET.Element('configuration')
        inp = ET.SubElement(root, 'input')
        rel = lambda p: Path(os.path.relpath(p, path.parent)).as_posix()
        ET.SubElement(inp, 'net-file', {'value': rel(network)})
        ET.SubElement(inp, 'route-files', {'value': rel(routes)})
        ET.SubElement(inp, 'additional-files', {'value': ','.join(map(rel, additionals))})
        time_root = ET.SubElement(root, 'time')
        ET.SubElement(time_root, 'begin', {'value': str(begin)})
        ET.SubElement(time_root, 'end', {'value': str(end)})
        ET.SubElement(time_root, 'step-length', {'value': '1'})
        tree = ET.ElementTree(root)
        ET.indent(tree, space='    ')
        tree.write(path, encoding='utf-8', xml_declaration=True)
