from collections import Counter, defaultdict
from statistics import mode as pymode

import numpy as np

from tqdm import tqdm

from .subpage import Subpage
from .packet import Packet


def check_buffer(mb, pages, subpages, min_rows=0):
    if (len(mb) > min_rows) and mb[0].type == 'header':
        page = int(mb[0].header.page) | (int(mb[0].mrag.magazine) * 0x100)
        if page in pages or (page & 0x7ff) in pages:
            if mb[0].header.subpage in subpages:
                yield sorted(mb, key=lambda p: p.mrag.row)


def packet_squash(packets):
    return Packet(_mode_axis0(np.stack([p._array for p in packets])).astype(np.uint8))


def bsdp_squash_format1(packets):
    date = pymode([p.broadcast.format1.date for p in packets])
    hour = min(pymode([p.broadcast.format1.hour for p in packets]), 99)
    minute = min(pymode([p.broadcast.format1.minute for p in packets]), 99)
    second = min(pymode([p.broadcast.format1.second for p in packets]), 99)
    return f'{date} {hour:02d}:{minute:02d}:{second:02d}'


def bsdp_squash_format2(packets):
    day = min(pymode([p.broadcast.format2.day for p in packets]), 99)
    month = min(pymode([p.broadcast.format2.month for p in packets]), 99)
    hour = min(pymode([p.broadcast.format1.hour for p in packets]), 99)
    minute = min(pymode([p.broadcast.format1.minute for p in packets]), 99)
    return f'{month:02d}-{day:02d} {hour:02d}:{minute:02d}'

def paginate(packets, pages=range(0x900), subpages=range(0x3f80), drop_empty=False):

    """Yields packet lists containing contiguous rows."""

    magbuffers = [[],[],[],[],[],[],[],[]]
    for packet in packets:
        mag = packet.mrag.magazine & 0x7
        if packet.type == 'header':
            yield from check_buffer(magbuffers[mag], pages, subpages, 1 if drop_empty else 0)
            magbuffers[mag] = []
        magbuffers[mag].append(packet)
    for mb in magbuffers:
        yield from check_buffer(mb, pages, subpages, 1 if drop_empty else 0)


def _subpages_from_packet_lists(packet_lists, ignore_empty):
    for pl in packet_lists:
        if len(pl) > 1:
            yield Subpage.from_packets(pl, ignore_empty=ignore_empty)


def _page_key(subpage):
    return (int(subpage.mrag.magazine), int(subpage.header.page))


def _group_subpages_v3_for_page(subpages, threshold):
    grouped = defaultdict(list)
    for subpage in subpages:
        subcode_groups = grouped[int(subpage.header.subpage)]
        for existing in subcode_groups:
            if threshold == -1:
                existing.append(subpage)
                break
            if subpage.diff(existing[0]) < threshold:
                existing.append(subpage)
                break
        else:
            subcode_groups.append([subpage])
    groups = []
    for bucket in grouped.values():
        groups.extend(bucket)
    return sorted(groups, key=len, reverse=True)


def _v1_similarity_cache(subpage):
    cache = getattr(subpage, '_v1_similarity_cache', None)
    if cache is None:
        display = np.bitwise_and(np.array(subpage.displayable._array, copy=True), 0x7f)
        no_double_on_prev = np.ones((display.shape[0],), dtype=np.bool_)
        if display.shape[0] > 1:
            no_double_on_prev[1:] = (display[:-1] != 0x0d).all(axis=1)
        threshold = (display != 0x20).sum(axis=1).astype(np.float64, copy=False)
        threshold *= ((threshold > 5) & no_double_on_prev)
        threshold *= 0.5
        cache = {
            'display': display,
            'threshold': threshold,
            'threshold_sum': float(threshold.sum() * 1.5),
        }
        setattr(subpage, '_v1_similarity_cache', cache)
    return cache


def _subpage_matches_v1(subpage, other):
    cache = _v1_similarity_cache(subpage)
    other_display = _v1_similarity_cache(other)['display']
    matches = ((cache['display'] != 0x20) & (cache['display'] == other_display)).sum(axis=1)
    return bool((matches >= cache['threshold']).all() and float(matches.sum()) >= cache['threshold_sum'])


def _group_subpages_v1_once(subpages):
    groups = []
    for subpage in subpages:
        for group in groups:
            if _subpage_matches_v1(subpage, group[0]):
                group.append(subpage)
                break
        else:
            groups.append([subpage])
    return sorted(groups, key=len, reverse=True)


def _group_subpages_v1_for_page(subpages, iterations=3):
    subpages = list(subpages)
    if not subpages:
        return []
    groups = _group_subpages_v1_once(subpages)
    for _ in range(max(int(iterations), 0)):
        centroids = [_squash_subpage_list(group) for group in groups]
        regrouped = [[] for _ in centroids]
        extras = []
        for subpage in subpages:
            for index, centroid in enumerate(centroids):
                if _subpage_matches_v1(subpage, centroid):
                    regrouped[index].append(subpage)
                    break
            else:
                extras.append([subpage])
        groups = [group for group in regrouped if group] + extras
        groups = sorted(groups, key=len, reverse=True)
    return groups


def _auto_mode_prefers_v1(subpages, v3_groups, v1_groups):
    if not subpages:
        return False
    code_counts = Counter(int(subpage.header.subpage) for subpage in subpages)
    dominant = max(code_counts.values())
    dominant_code = max(code_counts.items(), key=lambda item: item[1])[0]
    dominant_ratio = float(dominant) / float(len(subpages))
    suspicious_codes = (
        len(code_counts) == 1
        or dominant_code in (0x0000, 0x0001)
        or (dominant_ratio >= 0.8 and len(code_counts) <= 2)
    )
    if suspicious_codes and len(v1_groups) > len(v3_groups):
        return True
    if suspicious_codes and len(v1_groups) == len(v3_groups) == 1 and len(code_counts) == 1:
        return True
    return False


def subpage_group(packet_lists, threshold, ignore_empty, squash_mode='v3', v1_iterations=3):

    """Group similar subpages."""
    squash_mode = str(squash_mode).lower()
    if squash_mode not in {'v1', 'v3', 'auto'}:
        raise ValueError(f'Unknown squash mode {squash_mode!r}.')

    page_groups = defaultdict(list)
    for subpage in _subpages_from_packet_lists(packet_lists, ignore_empty):
        page_groups[_page_key(subpage)].append(subpage)

    for subpages in page_groups.values():
        v3_groups = _group_subpages_v3_for_page(subpages, threshold)
        if squash_mode == 'v3':
            yield from v3_groups
            continue

        v1_groups = _group_subpages_v1_for_page(subpages, iterations=v1_iterations)
        if squash_mode == 'v1' or _auto_mode_prefers_v1(subpages, v3_groups, v1_groups):
            yield from v1_groups
        else:
            yield from v3_groups


def _weighted_mode_columns(arr, weights):
    result = np.empty((arr.shape[1],), dtype=arr.dtype)
    weights = np.asarray(weights, dtype=np.float64)
    for column in range(arr.shape[1]):
        values, inverse = np.unique(arr[:, column], return_inverse=True)
        totals = np.zeros((values.shape[0],), dtype=np.float64)
        np.add.at(totals, inverse, weights)
        result[column] = values[int(np.argmax(totals))]
    return result


def _mode_columns(arr):
    result = np.empty((arr.shape[1],), dtype=arr.dtype)
    for column in range(arr.shape[1]):
        values, counts = np.unique(arr[:, column], return_counts=True)
        result[column] = values[int(np.argmax(counts))]
    return result


def _mode_axis0(arr):
    arr = np.asarray(arr)
    if arr.ndim < 2:
        raise ValueError('Expected an array with at least 2 dimensions for axis-0 mode.')
    flat = arr.reshape(arr.shape[0], -1)
    return _mode_columns(flat).reshape(arr.shape[1:])


def _squash_subpage_list(splist, use_confidence=False):
    numbers = _mode_axis0(np.stack([np.clip(sp.numbers, -100, -1) for sp in splist])).astype(np.int64)
    s = Subpage(numbers=numbers)
    for row in range(29):
        if row in [26, 27, 28]:
            for dc in range(16):
                if s.has_packet(row, dc):
                    packets = [sp.packet(row, dc) for sp in splist if sp.has_packet(row, dc)]
                    if not packets:
                        continue
                    confidences = np.asarray(
                        [max(sp.packet_confidence(row, dc), 1.0) for sp in splist if sp.has_packet(row, dc)],
                        dtype=np.float64,
                    )
                    s.packet(row, dc)[:3] = packets[0][:3]
                    slot = s._slot(row, dc)
                    s._confidences[slot] = float(np.mean(confidences)) if confidences.size else -1.0
                    arr = np.stack([p[3:] for p in packets])
                    if row == 27:
                        if use_confidence and len(packets) > 1:
                            s.packet(row, dc)[3:] = _weighted_mode_columns(arr.astype(np.uint8, copy=False), confidences)
                        else:
                            s.packet(row, dc)[3:] = _mode_axis0(arr).astype(np.uint8)
                    else:
                        t = arr.astype(np.uint32)
                        t = t[:, 0::3] | (t[:, 1::3] << 8) | (t[:, 2::3] << 16)
                        if use_confidence and len(packets) > 1:
                            result = _weighted_mode_columns(t, confidences).astype(np.uint32, copy=False)
                        else:
                            result = _mode_axis0(t).astype(np.uint32)
                        s.packet(row, dc)[3::3] = result & 0xff
                        s.packet(row, dc)[4::3] = (result >> 8) & 0xff
                        s.packet(row, dc)[5::3] = (result >> 16) & 0xff
        else:
            if s.has_packet(row):
                packets = [sp.packet(row) for sp in splist if sp.has_packet(row)]
                if not packets:
                    continue
                confidences = np.asarray(
                    [max(sp.packet_confidence(row), 1.0) for sp in splist if sp.has_packet(row)],
                    dtype=np.float64,
                )
                slot = s._slot(row, 0)
                s._confidences[slot] = float(np.mean(confidences)) if confidences.size else -1.0
                arr = np.stack([p[2:] for p in packets])
                s.packet(row)[:2] = packets[0][:2]
                if use_confidence and len(packets) > 1:
                    s.packet(row)[2:] = _weighted_mode_columns(arr.astype(np.uint8, copy=False), confidences)
                else:
                    s.packet(row)[2:] = _mode_axis0(arr).astype(np.uint8)
    return s


def subpage_squash(packet_lists, threshold=-1, min_duplicates=3, ignore_empty=False, best_of_n=None, use_confidence=False, squash_mode='v3', v1_iterations=3):

    """Yields squashed subpages."""

    for splist in tqdm(
        subpage_group(packet_lists, threshold, ignore_empty, squash_mode=squash_mode, v1_iterations=v1_iterations),
        unit=' Groups',
        desc='Squashing groups',
        dynamic_ncols=True,
    ):
        if len(splist) >= min_duplicates:
            working = list(splist)
            if best_of_n is not None and int(best_of_n) > 0 and len(working) > int(best_of_n):
                working = sorted(working, key=lambda sp: sp.average_confidence, reverse=True)[:int(best_of_n)]
            yield _squash_subpage_list(working, use_confidence=use_confidence)


def to_file(packets, f, format):

    """Write packets to f as format."""

    if format == 'auto':
        format = 'debug' if f.isatty() else 'bytes'
    if f.isatty():
        for p in packets:
            with tqdm.external_write_mode():
                f.write(getattr(p, format))
            yield p
    else:
        for p in packets:
            f.write(getattr(p, format))
            yield p
