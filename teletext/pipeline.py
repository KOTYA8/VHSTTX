import json
from collections import Counter, defaultdict
from statistics import mode as pymode

import numpy as np

from tqdm import tqdm

from .subpage import Subpage
from .packet import Packet


class _AutoGroup(list):
    def __init__(self, primary_group, alternate_group=None, match_score=0.0):
        super().__init__(primary_group)
        self.auto_alternate = list(alternate_group) if alternate_group else []
        self.auto_match_score = float(match_score)


DEFAULT_SQUASH_PROFILE = {
    'match_threshold': 0.74,
    'header_weight': 0.55,
    'body_weight': 1.0,
    'footer_weight': 0.45,
    'subcode_match_bonus': 0.12,
    'subcode_mismatch_penalty': 0.04,
    'iterations': 3,
}

BUILTIN_SQUASH_PROFILES = {
    'balanced': dict(DEFAULT_SQUASH_PROFILE),
    'aggressive': {
        'match_threshold': 0.66,
        'header_weight': 0.40,
        'body_weight': 1.0,
        'footer_weight': 0.25,
        'subcode_match_bonus': 0.06,
        'subcode_mismatch_penalty': 0.01,
        'iterations': 4,
    },
    'conservative': {
        'match_threshold': 0.84,
        'header_weight': 0.72,
        'body_weight': 1.0,
        'footer_weight': 0.60,
        'subcode_match_bonus': 0.18,
        'subcode_mismatch_penalty': 0.09,
        'iterations': 2,
    },
    'broken-subcodes': {
        'match_threshold': 0.70,
        'header_weight': 0.50,
        'body_weight': 1.0,
        'footer_weight': 0.35,
        'subcode_match_bonus': 0.00,
        'subcode_mismatch_penalty': 0.00,
        'iterations': 4,
    },
}


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


def normalise_squash_profile(profile=None):
    merged = dict(DEFAULT_SQUASH_PROFILE)
    if profile:
        merged.update(profile)

    return {
        'match_threshold': float(merged['match_threshold']),
        'header_weight': max(float(merged['header_weight']), 0.0),
        'body_weight': max(float(merged['body_weight']), 0.0),
        'footer_weight': max(float(merged['footer_weight']), 0.0),
        'subcode_match_bonus': float(merged['subcode_match_bonus']),
        'subcode_mismatch_penalty': max(float(merged['subcode_mismatch_penalty']), 0.0),
        'iterations': max(int(merged['iterations']), 0),
    }


def builtin_squash_profile_names():
    return tuple(BUILTIN_SQUASH_PROFILES.keys())


def get_builtin_squash_profile(name):
    key = str(name).strip().lower()
    if key == 'default':
        key = 'balanced'
    try:
        return normalise_squash_profile(BUILTIN_SQUASH_PROFILES[key])
    except KeyError as exc:
        raise KeyError(f'Unknown built-in squash profile {name!r}.') from exc


def load_squash_profile(path):
    with open(path, 'r', encoding='utf-8') as handle:
        return normalise_squash_profile(json.load(handle))


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


def _auto_mode_code_health(subpages):
    code_counts = Counter(int(subpage.header.subpage) for subpage in subpages)
    if not code_counts:
        return True, False
    dominant = max(code_counts.values())
    dominant_code = max(code_counts.items(), key=lambda item: item[1])[0]
    dominant_ratio = float(dominant) / float(len(subpages))
    suspicious_codes = (
        len(code_counts) == 1
        or (dominant_code in (0x0000, 0x0001) and dominant_ratio >= 0.8)
        or (dominant_ratio >= 0.8 and len(code_counts) <= 2)
    )
    healthy_codes = not suspicious_codes
    return suspicious_codes, healthy_codes


def _auto_row_weights(row_count):
    weights = np.ones((row_count,), dtype=np.float64)
    if row_count:
        weights[0] = 0.5
    if row_count >= 2:
        weights[-1] = 0.6
    if row_count >= 3:
        weights[-2] = 0.6
    return weights


def _custom_row_weights(row_count, profile):
    weights = np.full((row_count,), fill_value=float(profile['body_weight']), dtype=np.float64)
    if row_count:
        weights[0] = float(profile['header_weight'])
    if row_count >= 2:
        weights[-1] = float(profile['footer_weight'])
    if row_count >= 3:
        weights[-2] = float(profile['footer_weight'])
    return weights


def _weighted_similarity(left, right, weights):
    active = (left != 0x20) | (right != 0x20)
    if not np.any(active):
        return 0.0
    total_weight = float(np.sum(weights * active))
    if total_weight <= 0.0:
        return 0.0
    matches = (left == right) & active
    return float(np.sum(weights * matches) / total_weight)


def _custom_similarity_score(left, right, profile):
    left_header = np.asarray(left.header.displayable[:], dtype=np.uint8)
    right_header = np.asarray(right.header.displayable[:], dtype=np.uint8)
    header_weights = np.full(left_header.shape, fill_value=float(profile['header_weight']), dtype=np.float64)
    header_score = _weighted_similarity(left_header, right_header, header_weights)

    left_display = _v1_similarity_cache(left)['display']
    right_display = _v1_similarity_cache(right)['display']
    body_row_weights = _custom_row_weights(left_display.shape[0], profile)[:, np.newaxis]
    body_score = _weighted_similarity(left_display, right_display, body_row_weights)

    header_weight = max(float(profile['header_weight']), 0.0)
    body_weight = max(float(profile['body_weight']), 0.0)
    footer_weight = max(float(profile['footer_weight']), 0.0)
    total_weight = header_weight + body_weight + footer_weight
    if total_weight <= 0.0:
        total_weight = 1.0

    score = ((header_score * header_weight) + (body_score * (body_weight + footer_weight))) / total_weight
    if int(left.header.subpage) == int(right.header.subpage):
        score += float(profile['subcode_match_bonus'])
    else:
        score -= float(profile['subcode_mismatch_penalty'])
    return float(score)


def _best_custom_group_match(subpage, centroids, profile):
    best_index = None
    best_score = float('-inf')
    for index, centroid in enumerate(centroids):
        score = _custom_similarity_score(subpage, centroid, profile)
        if score > best_score:
            best_index = index
            best_score = score
    return best_index, best_score


def _group_subpages_custom_for_page(subpages, profile):
    subpages = list(subpages)
    if not subpages:
        return []

    profile = normalise_squash_profile(profile)
    threshold = float(profile['match_threshold'])
    groups = []
    centroids = []
    for subpage in subpages:
        if not centroids:
            groups.append([subpage])
            centroids.append(subpage)
            continue
        best_index, best_score = _best_custom_group_match(subpage, centroids, profile)
        if best_index is not None and best_score >= threshold:
            groups[best_index].append(subpage)
            centroids[best_index] = _squash_subpage_list(groups[best_index])
        else:
            groups.append([subpage])
            centroids.append(subpage)

    for _ in range(int(profile['iterations'])):
        centroids = [_squash_subpage_list(group) for group in groups]
        regrouped = [[] for _ in centroids]
        extras = []
        for subpage in subpages:
            best_index, best_score = _best_custom_group_match(subpage, centroids, profile)
            if best_index is not None and best_score >= threshold:
                regrouped[best_index].append(subpage)
            else:
                extras.append([subpage])
        groups = [group for group in regrouped if group] + extras
        groups = sorted(groups, key=len, reverse=True)
    return groups


def _group_quality_score(group):
    if not group:
        return 0.0

    displays = np.stack([_v1_similarity_cache(subpage)['display'] for subpage in group])
    flat = displays.reshape(displays.shape[0], -1)
    row_weights = _auto_row_weights(displays.shape[1])
    weights = np.repeat(row_weights, displays.shape[2])
    active = np.any(flat != 0x20, axis=0)
    if not np.any(active):
        return 0.0

    weighted_consensus = 0.0
    weighted_coverage = float(weights[active].sum())
    sample_count = float(flat.shape[0])
    for column in np.flatnonzero(active):
        _, counts = np.unique(flat[:, column], return_counts=True)
        weighted_consensus += float(weights[column]) * (float(np.max(counts)) / sample_count)

    support = 1.0 + (0.35 * min(np.log2(max(len(group), 1)), 3.0))
    confidence_bonus = 1.0 + (min(np.mean([subpage.average_confidence for subpage in group]), 100.0) / 500.0)
    fragmentation_bias = 0.75
    return (weighted_consensus * support * confidence_bonus) - (weighted_coverage * fragmentation_bias)


def _grouping_quality_score(groups):
    return float(sum(_group_quality_score(group) for group in groups))


def _eligible_groups(groups, min_duplicates):
    return [group for group in groups if len(group) >= int(min_duplicates)]


def _subpage_similarity_score(left, right):
    left_display = _v1_similarity_cache(left)['display']
    right_display = _v1_similarity_cache(right)['display']
    active = (left_display != 0x20) | (right_display != 0x20)
    if not np.any(active):
        return 0.0
    row_weights = _auto_row_weights(left_display.shape[0])[:, np.newaxis]
    matches = (left_display == right_display) & active
    total_weight = float(np.sum(row_weights * active))
    if total_weight <= 0.0:
        return 0.0
    return float(np.sum(row_weights * matches) / total_weight)


def _match_auto_groups(primary_groups, alternate_groups):
    if not alternate_groups:
        return [_AutoGroup(group) for group in primary_groups]

    primary_squashed = [_squash_subpage_list(group) for group in primary_groups]
    alternate_squashed = [_squash_subpage_list(group) for group in alternate_groups]
    matched_groups = []
    for index, group in enumerate(primary_groups):
        best_group = None
        best_score = 0.0
        for alt_group, alt_squashed in zip(alternate_groups, alternate_squashed):
            score = _subpage_similarity_score(primary_squashed[index], alt_squashed)
            if score > best_score:
                best_group = alt_group
                best_score = score
        matched_groups.append(
            _AutoGroup(group, alternate_group=best_group if best_score >= 0.20 else None, match_score=best_score)
        )
    return matched_groups


def _refine_auto_groups(groups, threshold, v1_iterations, prefer_mode, min_duplicates):
    refined = []
    for group in groups:
        if len(group) < 2:
            refined.append(group)
            continue
        current_score = _group_quality_score(group)
        if prefer_mode == 'v3':
            alternate = _group_subpages_v1_for_page(group, iterations=v1_iterations)
        else:
            alternate = _group_subpages_v3_for_page(group, threshold)
        if len(alternate) <= 1:
            refined.append(group)
            continue
        if len(group) >= int(min_duplicates) and not _eligible_groups(alternate, min_duplicates):
            refined.append(group)
            continue
        alternate_score = _grouping_quality_score(alternate)
        if alternate_score > (current_score * 1.15):
            refined.extend(alternate)
        else:
            refined.append(group)
    return sorted(refined, key=len, reverse=True)


def _choose_auto_groups_for_page(subpages, threshold, v1_iterations, min_duplicates):
    v3_groups = _group_subpages_v3_for_page(subpages, threshold)
    v1_groups = _group_subpages_v1_for_page(subpages, iterations=v1_iterations)
    v3_score = _grouping_quality_score(v3_groups)
    v1_score = _grouping_quality_score(v1_groups)
    v3_eligible = _eligible_groups(v3_groups, min_duplicates)
    v1_eligible = _eligible_groups(v1_groups, min_duplicates)
    suspicious_codes, healthy_codes = _auto_mode_code_health(subpages)

    if v3_eligible and not v1_eligible:
        prefer_mode = 'v3'
    elif v1_eligible and not v3_eligible:
        prefer_mode = 'v1'
    elif suspicious_codes:
        prefer_mode = 'v1' if v1_score >= (v3_score * 0.98) else 'v3'
    elif healthy_codes:
        prefer_mode = 'v3'
    else:
        prefer_mode = 'v1' if v1_score > v3_score else 'v3'

    chosen = v1_groups if prefer_mode == 'v1' else v3_groups
    alternate = v3_groups if prefer_mode == 'v1' else v1_groups
    refined = _refine_auto_groups(chosen, threshold, v1_iterations, prefer_mode, min_duplicates)
    return _match_auto_groups(refined, alternate)


def subpage_group(packet_lists, threshold, ignore_empty, squash_mode='v3', v1_iterations=3, min_duplicates=1, squash_profile=None):

    """Group similar subpages."""
    squash_mode = str(squash_mode).lower()
    if squash_mode not in {'v1', 'v3', 'auto', 'custom', 'profile'}:
        raise ValueError(f'Unknown squash mode {squash_mode!r}.')
    if squash_mode in {'custom', 'profile'}:
        squash_profile = normalise_squash_profile(squash_profile)

    page_groups = defaultdict(list)
    for subpage in _subpages_from_packet_lists(packet_lists, ignore_empty):
        page_groups[_page_key(subpage)].append(subpage)

    for subpages in page_groups.values():
        if squash_mode == 'v3':
            yield from _group_subpages_v3_for_page(subpages, threshold)
            continue

        if squash_mode == 'v1':
            yield from _group_subpages_v1_for_page(subpages, iterations=v1_iterations)
        elif squash_mode == 'auto':
            yield from _choose_auto_groups_for_page(subpages, threshold, v1_iterations, min_duplicates)
        elif squash_mode in {'custom', 'profile'}:
            yield from _group_subpages_custom_for_page(subpages, squash_profile)
        else:
            yield from _group_subpages_v3_for_page(subpages, threshold)


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


def _subpage_row_display(subpage, row):
    if row == 0:
        return np.asarray(subpage.header.displayable[:], dtype=np.uint8)
    if 1 <= row <= 24 and subpage.has_packet(row):
        return np.asarray(subpage.packet(row).displayable[:], dtype=np.uint8)
    return None


def _row_samples_for_group(group, row):
    arrays = []
    confidences = []
    for subpage in group:
        row_display = _subpage_row_display(subpage, row)
        if row_display is None:
            continue
        arrays.append(row_display)
        confidences.append(max(subpage.packet_confidence(row), 1.0))
    return arrays, np.asarray(confidences, dtype=np.float64)


def _group_row_quality_score(group, row):
    arrays, confidences = _row_samples_for_group(group, row)
    if not arrays:
        return 0.0
    arr = np.stack(arrays)
    active = np.any(arr != 0x20, axis=0)
    if not np.any(active):
        return 0.0

    sample_count = float(arr.shape[0])
    weighted_consensus = 0.0
    for column in np.flatnonzero(active):
        _, counts = np.unique(arr[:, column], return_counts=True)
        weighted_consensus += float(np.max(counts)) / sample_count

    coverage = float(np.count_nonzero(active)) / float(arr.shape[1])
    support = 1.0 + (0.35 * min(np.log2(max(len(group), 1)), 3.0))
    confidence_bonus = 1.0 + (min(float(np.mean(confidences)), 100.0) / 500.0)
    return weighted_consensus * (0.5 + coverage) * support * confidence_bonus


def _squashed_row_similarity(primary, alternate, row):
    primary_row = _subpage_row_display(primary, row)
    alternate_row = _subpage_row_display(alternate, row)
    if primary_row is None or alternate_row is None:
        return 0.0
    active = (primary_row != 0x20) | (alternate_row != 0x20)
    if not np.any(active):
        return 1.0
    return float(np.mean(primary_row[active] == alternate_row[active]))


def _copy_squashed_row(target, source, row):
    if row == 0:
        target.header.displayable[:] = source.header.displayable[:]
        target._confidences[target._slot(0, 0)] = source.packet_confidence(0)
        return

    if not source.has_packet(row):
        return
    if not target.has_packet(row):
        target.init_packet(row, magazine=source.mrag.magazine)
    target.packet(row)[2:] = source.packet(row)[2:]
    slot = target._slot(row, 0)
    source_slot = source._slot(row, 0)
    target._numbers[slot] = source.numbers[source_slot]
    target._confidences[slot] = source.packet_confidence(row)


def _merge_auto_group_rows(primary_group, alternate_group, use_confidence=False, match_score=0.0):
    primary = _squash_subpage_list(primary_group, use_confidence=use_confidence)
    if not alternate_group or match_score < 0.20:
        return primary

    alternate = _squash_subpage_list(alternate_group, use_confidence=use_confidence)
    if _subpage_similarity_score(primary, alternate) < 0.18:
        return primary

    for row in range(25):
        primary_score = _group_row_quality_score(primary_group, row)
        alternate_score = _group_row_quality_score(alternate_group, row)
        if alternate_score <= 0.0:
            continue
        if primary_score > 0.0:
            min_similarity = 0.25 if row == 0 else 0.10
            if _squashed_row_similarity(primary, alternate, row) < min_similarity:
                continue
        if alternate_score > (primary_score * 1.08) or (primary_score == 0.0 and alternate_score > 0.0):
            _copy_squashed_row(primary, alternate, row)
    return primary


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


def _prepare_best_of_n_group(group, best_of_n):
    working = list(group)
    if best_of_n is not None and int(best_of_n) > 0 and len(working) > int(best_of_n):
        working = sorted(working, key=lambda sp: sp.average_confidence, reverse=True)[:int(best_of_n)]
    return working


def subpage_squash(packet_lists, threshold=-1, min_duplicates=3, ignore_empty=False, best_of_n=None, use_confidence=False, squash_mode='v3', v1_iterations=3, squash_profile=None):

    """Yields squashed subpages."""

    for splist in tqdm(
        subpage_group(
            packet_lists,
            threshold,
            ignore_empty,
            squash_mode=squash_mode,
            v1_iterations=v1_iterations,
            min_duplicates=min_duplicates,
            squash_profile=squash_profile,
        ),
        unit=' Groups',
        desc='Squashing groups',
        dynamic_ncols=True,
    ):
        if len(splist) >= min_duplicates:
            working = _prepare_best_of_n_group(splist, best_of_n)
            if isinstance(splist, _AutoGroup):
                alternate = _prepare_best_of_n_group(splist.auto_alternate, best_of_n)
                yield _merge_auto_group_rows(
                    working,
                    alternate,
                    use_confidence=use_confidence,
                    match_score=splist.auto_match_score,
                )
            else:
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
