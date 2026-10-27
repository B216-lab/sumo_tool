from __future__ import annotations

import gzip
import hashlib
import json
import math
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from .auto_taz import membership_path


class WizardODError(RuntimeError):
    pass


@dataclass(frozen=True)
class WizardODStats:
    total: int
    matched: int
    missing: int
    interval_end: int
    missing_edges: tuple[str, ...]


def build_wizard_od(trips_path: Path, taz_path: Path, destination: Path) -> WizardODStats:
    trips_path, taz_path, destination = map(Path, (trips_path, taz_path, destination))
    if not trips_path.is_file() or not taz_path.is_file():
        raise WizardODError('Не найдены исходные поездки Wizard или файл TAZ.')
    try:
        zones = ET.parse(taz_path).getroot()
        edge_to_zone: dict[str, str] = {}
        for taz in zones.iter('taz'):
            zid = taz.get('id', '').strip()
            if not zid:
                continue
            edges = taz.get('edges', '').split()
            edges += [el.get('id') for el in taz.findall('tazSource') + taz.findall('tazSink') if el.get('id')]
            for eid in edges:
                if eid in edge_to_zone and edge_to_zone[eid] != zid:
                    raise WizardODError(f'Дорога {eid} включена в несколько TAZ: '
                                        f'{edge_to_zone[eid]} и {zid}. Исправьте зоны.')
                edge_to_zone[eid] = zid

        sidecar = membership_path(taz_path)
        if sidecar.is_file():
            try:
                meta = json.loads(sidecar.read_text(encoding='utf-8'))
                if meta.get('taz_sha256') == hashlib.sha256(taz_path.read_bytes()).hexdigest():
                    ids = {t.get('id') for t in zones.iter('taz')}
                    for zid, edge_ids in meta.get('members', {}).items():
                        if zid not in ids or not isinstance(edge_ids, list):
                            raise WizardODError('TAZ: неверная карта принадлежности дорог.')
                        for eid in edge_ids:
                            if not isinstance(eid, str):
                                raise WizardODError('TAZ: неверный идентификатор дороги.')
                            if eid in edge_to_zone and edge_to_zone[eid] != zid:
                                raise WizardODError(f'Дорога {eid} относится к нескольким TAZ.')
                            edge_to_zone[eid] = zid
            except (OSError, ValueError, TypeError) as exc:
                raise WizardODError(f'Некорректные метаданные автоматических TAZ: {exc}') from exc
        if not edge_to_zone:
            raise WizardODError('TAZ не содержат дорог. Сначала создайте/исправьте зоны.')
        with (gzip.open(trips_path, 'rb') if trips_path.name.endswith('.gz') else open(trips_path, 'rb')) as src:
            root = ET.parse(src).getroot()
    except (OSError, ET.ParseError) as exc:
        raise WizardODError(f'Не удалось прочитать данные Wizard/TAZ: {exc}') from exc

    pairs = Counter()
    missed = Counter()
    total = 0
    latest = 0.0
    for trip in root.iter('trip'):
        total += 1
        depart = trip.get('depart', '0')
        try:
            latest = max(latest, float(depart))
        except ValueError:
            raise WizardODError(f'Некорректное время отправления поездки {trip.get("id", "?")}.')
        start, end = trip.get('from'), trip.get('to')
        z_from, z_to = edge_to_zone.get(start), edge_to_zone.get(end)
        if z_from is None or z_to is None:
            if z_from is None:
                missed[start or '<нет from>'] += 1
            if z_to is None:
                missed[end or '<нет to>'] += 1
            continue
        pairs[(z_from, z_to)] += 1
    if not total:
        raise WizardODError('В файле Wizard отсутствуют отдельные поездки <trip>.')
    if not pairs:
        raise WizardODError(f'Ни одна из {total} поездок Wizard не сопоставилась с TAZ. '
                            'Проверьте, что зоны построены для той же сети.')

    interval_end = max(3600, math.ceil(latest + 1))
    result = ET.Element('odMatrix')
    interval = ET.SubElement(result, 'interval', {'begin': '0', 'end': str(interval_end)})
    for (origin, target), count in sorted(pairs.items()):
        ET.SubElement(interval, 'tazRelation', {'from': origin, 'to': target, 'count': str(count)})
    destination.parent.mkdir(parents=True, exist_ok=True)
    tree = ET.ElementTree(result)
    ET.indent(tree, space='    ')
    tree.write(destination, encoding='utf-8', xml_declaration=True)
    return WizardODStats(total, sum(pairs.values()), total - sum(pairs.values()),
                         interval_end, tuple(eid for eid, _ in missed.most_common(10)))
