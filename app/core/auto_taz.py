from __future__ import annotations

import gzip
import hashlib
import json
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path


class AutoTazError(RuntimeError):
    pass


def _root(net: Path):
    with (gzip.open(net, 'rb') if str(net).lower().endswith('.gz') else open(net, 'rb')) as source:
        return ET.parse(source).getroot()


def membership_path(taz_file: Path) -> Path:
    p = Path(taz_file)
    return p.with_name(p.name + '.membership.json')


def _chunks(items, count):
    count = min(count, len(items))
    if count == 0:
        return []
    q, rem = divmod(len(items), count)
    parts, start = [], 0
    for i in range(count):
        size = q + (i < rem)
        parts.append(items[start:start + size])
        start += size
    return parts


def _passenger_lane(lane: ET.Element) -> bool:
    allowed = lane.get('allow', '').split()
    denied = lane.get('disallow', '').split()
    return 'passenger' not in denied and (not allowed or 'passenger' in allowed)


def _geometry(lane: ET.Element):
    try:
        return [tuple(map(float, p.split(','))) for p in lane.get('shape', '').split()]
    except ValueError:
        return []


def _classify_portals(group_keys, groups, edges, by_zone, outgoing, incoming, node_groups):
    zone = by_zone[groups[group_keys[0]]['edges'][0]]
    terminals = []
    connectors = []
    fallback = []

    for key in group_keys:
        ids = groups[key]['edges']
        options = []
        is_terminal = False
        crosses_boundary = False
        for eid in ids:
            edge = edges[eid]
            src_terminal = len(node_groups[edge['from']]) == 1
            dst_terminal = len(node_groups[edge['to']]) == 1
            is_terminal |= src_terminal != dst_terminal
            out_other = sum(by_zone.get(x) != zone for x in outgoing[eid]
                            if x in by_zone and x.lstrip('-') != key)
            in_other = sum(by_zone.get(x) != zone for x in incoming[eid]
                           if x in by_zone and x.lstrip('-') != key)
            crosses_boundary |= bool(out_other or in_other)

            score = (12 if src_terminal and not dst_terminal else
                     -12 if dst_terminal and not src_terminal else 0)
            score += 3 * (out_other - in_other)
            options.append((eid, score, out_other, in_other,
                            src_terminal, dst_terminal))
        if len(options) == 2 and {x[0] for x in options} == {key, '-' + key}:
            sorted_opts = sorted(options, key=lambda x: (-x[1], x[0]))
            src, dst = sorted_opts[0][0], sorted_opts[1][0]
            role = (src, dst)
        else:
            src = next((x[0] for x in sorted(options, key=lambda x: (-x[1], x[0]))
                        if x[1] > 0 and (outgoing[x[0]] or x[4])), None)
            dst = next((x[0] for x in sorted(options, key=lambda x: (x[1], x[0]))
                        if x[1] < 0 and (incoming[x[0]] or x[5]) and x[0] != src), None)
            role = (src, dst)
        entry = (key, role)
        if is_terminal:
            terminals.append(entry)
        elif crosses_boundary:
            connectors.append(entry)
        else:
            fallback.append(entry)

    selected = terminals if terminals else connectors
    sources = set(src for _, (src, _) in selected if src)
    sinks = set(dst for _, (_, dst) in selected if dst)

    for _, (src, dst) in connectors + fallback:
        if not sources and src and src not in sinks:
            sources.add(src)
        if not sinks and dst and dst not in sources:
            sinks.add(dst)
        if sources and sinks:
            break

    if not sources or not sinks:
        raise AutoTazError(
            f'Зона {zone}: невозможно надёжно подобрать и вход, и выход '
            'по направлению доступных дорог. Проверьте односторонние улицы '
            'и соединения или назначьте Source/Sink вручную в NetEdit.')
    assert not (sources & sinks)
    return sorted(sources), sorted(sinks)


def generate_grid_taz(net: Path, dest: Path, cols: int = 2, rows: int = 2) -> dict[str, int]:
    if not 1 <= cols <= 8 or not 1 <= rows <= 8:
        raise AutoTazError('Число строк и столбцов должно быть от 1 до 8.')
    net, dest = Path(net), Path(dest)
    if not net.is_file():
        raise AutoTazError(f'Не найдена дорожная сеть: {net}')
    network = _root(net)
    if network.tag != 'net':
        raise AutoTazError('Ожидался файл сети SUMO (net.xml или net.xml.gz).')

    groups = {}
    edges = {}
    node_groups = defaultdict(set)
    lane_indices = defaultdict(set)
    for edge in network.findall('edge'):
        eid = edge.get('id', '')
        if not eid or eid.startswith(':') or edge.get('function') == 'internal':
            continue
        fid, tid = edge.get('from'), edge.get('to')
        if not fid or not tid:
            continue
        usable = [(str(lane.get('index', '0')), lane) for lane in edge.findall('lane')
                  if _passenger_lane(lane)]
        if not usable:
            continue
        coords = []
        for _, lane in usable:
            coords = _geometry(lane)
            if coords:
                break
        if not coords:
            continue
        key = eid.lstrip('-')
        group = groups.setdefault(key, {'edges': [], 'xs': [], 'ys': []})
        group['edges'].append(eid)
        group['xs'].extend(p[0] for p in coords)
        group['ys'].extend(p[1] for p in coords)
        edges[eid] = {'from': fid, 'to': tid}
        node_groups[fid].add(key)
        node_groups[tid].add(key)
        lane_indices[eid].update(idx for idx, _ in usable)
    if not groups:
        raise AutoTazError('В сети не найдены дороги, доступные легковым автомобилям.')

    entries = [(key, sum(g['xs']) / len(g['xs']), sum(g['ys']) / len(g['ys']), g)
               for key, g in groups.items()]
    col_count = min(cols, max(1, len(entries) // rows)) if len(entries) >= rows else 1
    col_groups = _chunks(sorted(entries, key=lambda item: (item[1], item[2], item[0])), col_count)
    result = ET.Element('additional')
    counts = {}
    zone_groups = {}
    members = {}
    by_zone = {}

    for c, group_col in enumerate(col_groups):
        row_groups = _chunks(sorted(group_col, key=lambda item: (item[2], item[1], item[0])), rows)
        for r, group_row in enumerate(row_groups):
            keyset = [key for key, _, _, _ in group_row]
            edge_ids = sorted(eid for key in keyset for eid in groups[key]['edges'])
            xs = [x for key in keyset for x in groups[key]['xs']]
            ys = [y for key in keyset for y in groups[key]['ys']]
            if not edge_ids:
                continue
            pad = 1.0
            x0, x1 = min(xs)-pad, max(xs)+pad
            y0, y1 = min(ys)-pad, max(ys)+pad
            shape = f'{x0:.2f},{y0:.2f} {x1:.2f},{y0:.2f} {x1:.2f},{y1:.2f} {x0:.2f},{y1:.2f}'
            zid = f'Z{r+1}_{c+1}'
            ET.SubElement(result, 'taz', {'id': zid, 'shape': shape})
            zone_groups[zid] = keyset
            members[zid] = edge_ids
            by_zone.update({eid: zid for eid in edge_ids})
            counts[zid] = len(edge_ids)

    outgoing = defaultdict(set)
    incoming = defaultdict(set)
    for conn in network.findall('connection'):
        src, dst = conn.get('from'), conn.get('to')
        if src not in edges or dst not in edges:
            continue
        if conn.get('fromLane', '0') not in lane_indices[src] or conn.get('toLane', '0') not in lane_indices[dst]:
            continue
        outgoing[src].add(dst)
        incoming[dst].add(src)

    for taz in result.findall('taz'):
        zid = taz.get('id')
        srcs, dsts = _classify_portals(zone_groups[zid], groups, edges,
                                        by_zone, outgoing, incoming, node_groups)
        for eid in srcs:
            ET.SubElement(taz, 'tazSource', {'id': eid, 'weight': '1.00'})
        for eid in dsts:
            ET.SubElement(taz, 'tazSink', {'id': eid, 'weight': '1.00'})

    dest.parent.mkdir(parents=True, exist_ok=True)
    tree = ET.ElementTree(result)
    ET.indent(tree, space='  ')

    import io
    buf = io.BytesIO()
    tree.write(buf, encoding='utf-8', xml_declaration=True)
    payload = buf.getvalue()
    metadata = {
        'version': 1,
        'taz_sha256': hashlib.sha256(payload).hexdigest(),
        'members': members,
    }
    dest.write_bytes(payload)
    membership_path(dest).write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
    return counts
