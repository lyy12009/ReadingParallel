"""Pure target-setting helpers; no learned-word or tokenization decisions."""
import json
import re


def combine_targets(selected, manual):
    values = list(selected) + re.split(r'[,，、;；\s]+', manual)
    return list(dict.fromkeys(word.strip() for word in values if word.strip()))


def minimum_matrix(targets, text_count, saved=None):
    """Active cells only; the caller retains inactive cells for reversible edits."""
    saved = saved or {}
    return {word: {piece: saved.get(word, {}).get(piece, 1)
                   for piece in range(1, text_count + 1)} for word in targets}


def configuration_signature(config):
    """Immutable snapshot, including context and actual selected/manual sources."""
    return json.dumps(config, ensure_ascii=False, sort_keys=True)


def invalidate_audit(state):
    """Revoke current validity without deleting any teacher-authored content."""
    state['approved'] = False
    for key in ('last_audit', 'audited_text', 'audited_names', 'audited_guides',
                'audited_target_settings'):
        state.pop(key, None)
