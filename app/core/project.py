from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

PROJECT_FILE = 'project.json'
MODES = {'osm', 'wizard'}

class ProjectError(ValueError):
    pass


def _fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as src:
        for chunk in iter(lambda: src.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class Project:
    root: Path
    mode: str

    @classmethod
    def open(cls, root: Path, mode: str) -> 'Project':
        if mode not in MODES:
            raise ProjectError(f'Неподдерживаемый тип проекта: {mode}')
        root = Path(root).expanduser().resolve()
        if root.exists() and not root.is_dir():
            raise ProjectError(f'Указанный путь не является папкой: {root}')
        meta = root / PROJECT_FILE
        if meta.exists():
            try:
                payload = json.loads(meta.read_text(encoding='utf-8'))
            except (json.JSONDecodeError, OSError) as exc:
                raise ProjectError(f'Не удалось прочитать {meta}: {exc}') from exc
            saved_mode = payload.get('mode')
            if saved_mode != mode:
                raise ProjectError('Эта папка уже содержит проект другого типа: '
                    f'{"OSM Web Wizard" if saved_mode == "wizard" else "обычный OSM"}. '
                    'Выберите соответствующий способ работы или другую папку.')
        else:
            detected = 'wizard' if (root/'wizard'/'osm.sumocfg').is_file() else (
                'osm' if (root/'network.net.xml').is_file() else None)
            if detected is not None and detected != mode:
                raise ProjectError('В этой папке уже есть модель другого типа. '
                    'Выберите подходящий режим, чтобы не смешивать дорожные сети.')
            root.mkdir(parents=True, exist_ok=True)
            meta.write_text(json.dumps({'version': 1, 'mode': mode}, indent=2, ensure_ascii=False),
                            encoding='utf-8')
        return cls(root, mode)

    @property
    def taz(self) -> Path:
        return self.root / 'sumo.add.xml'

    @property
    def matrix(self) -> Path:
        return self.root / 'matrix_taz.xml'

    @property
    def osm(self) -> Path:
        return self.root / 'source.osm'

    @property
    def original_wizard_config(self) -> Path:
        return self.root / 'wizard' / 'osm.sumocfg'

    @property
    def network(self) -> Path | None:
        if self.mode == 'osm':
            path = self.root / 'network.net.xml'
            return path if path.is_file() else None
        for name in ('osm.net.xml.gz', 'osm.net.xml'):
            path = self.root / 'wizard' / name
            if path.is_file():
                return path
        return None

    def backup_dependents(self, include_network: bool = False) -> Path | None:
        names = ['sumo.add.xml', 'sumo.add.xml.membership.json', 'matrix_taz.xml', 'trips.trips.xml', 'routes.rou.xml', 'sumo.sumocfg']
        if include_network:
            names += ['network.net.xml', 'network.source.sha256']
        existing = [self.root / name for name in names if (self.root / name).is_file()]
        if not existing:
            return None
        stamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        backup = self.root / f'backup_before_network_change_{stamp}'
        backup.mkdir(parents=True)
        for item in existing:
            shutil.move(str(item), str(backup / item.name))
        return backup


    def get_setting(self, name: str, fallback: str = '') -> str:
        try:
            info = json.loads((self.root / PROJECT_FILE).read_text(encoding='utf-8'))
            value = info.get('settings', {}).get(name, fallback)
            return value if isinstance(value, str) else fallback
        except (OSError, ValueError):
            return fallback

    def set_setting(self, name: str, value: str) -> None:
        meta = self.root / PROJECT_FILE
        info = json.loads(meta.read_text(encoding='utf-8'))
        info.setdefault('settings', {})[name] = value
        temp = self.root / '.project.json.tmp'
        temp.write_text(json.dumps(info, indent=2, ensure_ascii=False), encoding='utf-8')
        temp.replace(meta)

    def select_osm(self, source: Path) -> bool:
        if self.mode != 'osm':
            raise ProjectError('Загружать OSM можно только в проект обычного OSM.')
        source = Path(source).expanduser().resolve()
        if not source.is_file():
            raise ProjectError(f'OSM-файл не найден: {source}')
        if source.suffix.lower() not in ('.osm', '.xml'):
            raise ProjectError('Выберите дорожные данные .osm или .xml.')
        if self.osm.is_file() and _fingerprint(source) == _fingerprint(self.osm):
            return False
        self.backup_dependents(include_network=True)
        if source != self.osm:
            shutil.copy2(source, self.osm)
        return True
