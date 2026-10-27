from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime
from pathlib import Path


class WizardImportError(RuntimeError):
    pass


def _scenario_folder(source: Path) -> Path:
    if (source / 'osm.sumocfg').is_file():
        return source
    matches = [p for p in source.iterdir() if p.is_dir() and (p / 'osm.sumocfg').is_file()]
    if len(matches) == 1:
        return matches[0]
    raise WizardImportError('Выберите папку, содержащую osm.sumocfg, или её родительскую папку.')


def _read_scenario_files(source: Path) -> dict[str, bytes]:
    if source.is_dir():
        folder = _scenario_folder(source)
        candidates = [(p.name, p.read_bytes()) for p in folder.iterdir() if p.is_file()
                      and (p.name.startswith('osm.') or p.name in ('output.add.xml', 'edgeData.xml'))]
        return dict(candidates)
    if not source.is_file() or source.suffix.lower() != '.zip':
        raise WizardImportError('Выберите папку сценария или ZIP-архив OSM Web Wizard.')
    with zipfile.ZipFile(source) as archive:
        configs = [i for i in archive.infolist() if not i.is_dir() and Path(i.filename).name == 'osm.sumocfg']
        if len(configs) != 1:
            raise WizardImportError('В архиве должна находиться одна конфигурация osm.sumocfg.')
        prefix = configs[0].filename.rpartition('/')[0]
        files = {}
        for info in archive.infolist():
            if info.is_dir():
                continue
            rel = info.filename[len(prefix) + 1:] if prefix else info.filename
            if prefix and not info.filename.startswith(prefix + '/'):
                continue
            if not rel or '/' in rel or '\\' in rel or rel in {'.','..'}:
                continue
            if not (rel.startswith('osm.') or rel in ('output.add.xml', 'edgeData.xml')):
                continue
            if info.file_size > 100_000_000 or sum(len(v) for v in files.values()) + info.file_size > 400_000_000:
                raise WizardImportError('Архив слишком большой для импорта.')
            files[rel] = archive.read(info)
        return files


def _network_filename(files: dict[str, bytes]) -> str:
    name = next((x for x in ('osm.net.xml.gz', 'osm.net.xml') if x in files), None)
    if name is None or 'osm.sumocfg' not in files:
        raise WizardImportError('Не найдены osm.net.xml(.gz) и osm.sumocfg.')
    try:
        import gzip
        raw = gzip.decompress(files[name]) if name.endswith('.gz') else files[name]
        root = ET.fromstring(raw)
        cfg = ET.fromstring(files['osm.sumocfg'])
        if root.tag != 'net' or cfg.tag != 'sumoConfiguration':
            raise ValueError('Некорректный формат сети или конфигурации')
        for key in ('net-file', 'route-files', 'additional-files'):
            for elem in cfg.findall(f'./input/{key}'):
                for fname in elem.get('value','').split(','):
                    fname = fname.strip()
                    if not fname:
                        continue
                    if Path(fname).name != fname or fname not in files:
                        raise ValueError(f'Отсутствует обязательный файл сценария: {fname}')
    except (ValueError, ET.ParseError, OSError) as exc:
        raise WizardImportError(f'Сценарий Wizard повреждён или неполон: {exc}') from exc
    return name


def _unique_backup(project: Path) -> Path:
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup = project / f'backup_before_wizard_change_{stamp}'
    index = 1
    while backup.exists():
        index += 1
        backup = project / f'backup_before_wizard_change_{stamp}_{index}'
    backup.mkdir()
    return backup


def import_wizard(source: Path, project: Path) -> tuple[Path, bool]:
    source, project = Path(source).resolve(), Path(project).resolve()
    if not source.exists():
        raise WizardImportError(f'Источник не найден: {source}')
    if source.is_dir() and ((project in source.parents) or (source == project) or (source in project.parents)):
        raise WizardImportError('Исходную папку Wizard и папку проекта нужно выбирать отдельно.')
    if source.is_file() and project in source.parents:
        raise WizardImportError('ZIP должен находиться вне рабочей папки проекта.')
    files = _read_scenario_files(source)
    net_name = _network_filename(files)
    project.mkdir(parents=True, exist_ok=True)
    destination = project / 'wizard'
    old_net = next((destination / n for n in ('osm.net.xml.gz', 'osm.net.xml') if (destination / n).is_file()), None)
    changed = old_net is not None and hashlib.sha256(old_net.read_bytes()).digest() != hashlib.sha256(files[net_name]).digest()
    switching_from_osm = old_net is None and (project / 'network.net.xml').is_file()

    with tempfile.TemporaryDirectory(prefix='wizard_stage_', dir=project) as temp:
        staged = Path(temp)
        for name, payload in files.items():
            (staged / name).write_bytes(payload)
        if changed or switching_from_osm:
            backup = _unique_backup(project)
            for name in ('sumo.add.xml', 'sumo.add.xml.membership.json', 'matrix_taz.xml', 'trips.trips.xml', 'routes.rou.xml',
                         'sumo.sumocfg', 'network.net.xml', 'network.net.xml.gz', 'network.source.sha256'):
                p = project / name
                if p.exists():
                    shutil.move(str(p), str(backup / name))
            if destination.exists():
                shutil.move(str(destination), str(backup / 'wizard'))
        else:
            if destination.exists():
                shutil.rmtree(destination)
        shutil.copytree(staged, destination)
    return destination / net_name, changed


def wizard_configuration(project: Path) -> Path:
    path = Path(project) / 'wizard' / 'osm.sumocfg'
    if not path.is_file():
        raise WizardImportError('Исходный сценарий Wizard не импортирован.')
    return path
