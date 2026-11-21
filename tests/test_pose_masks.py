"""Authored mask entries outside the primary skeleton do not alter its weights."""
from copy import deepcopy

import numpy as np
import pytest

from agr.vendor.pose_math import feather_mask


@pytest.fixture
def skeleton():
    return {'m_boneIDs':['root', 'mid', 'tip'], 'm_parentIndices':[-1, 0, 1]}


def definition(names, weights, mask_id='upper_body'):
    return {'m_ID':mask_id,
            'm_primaryWeightList':{'m_boneIDs':names, 'm_weights':weights}}


def test_missing_authored_bone_preserves_feathering_and_reports_omission(skeleton):
    clean = definition(['root', 'tip'], [0.0, 1.0])
    with_missing = definition(['root', 'absent_wing', 'tip'], [0.0, 1.0, 1.0])
    before = deepcopy((skeleton, clean, with_missing))
    diagnostics = []
    expected = feather_mask(skeleton, clean)
    actual = feather_mask(skeleton, with_missing, missing_bones=diagnostics)
    np.testing.assert_array_equal(expected, [0.0, 0.5, 1.0])
    np.testing.assert_array_equal(actual, expected)
    assert diagnostics == [{'mask':'upper_body', 'bone':'absent_wing',
                            'weight':1.0, 'reason':'not_in_primary_skeleton'}]
    assert (skeleton, clean, with_missing) == before


def test_all_missing_mask_retains_zero_weights_at_original_mask_index(skeleton):
    definitions = [definition(['root'], [1.0], 'full_body'),
                   definition(['absent_wing'], [1.0], 'wing_only'),
                   definition(['root', 'tip'], [0.0, 1.0], 'feathered')]
    before = deepcopy(definitions)
    diagnostics = []
    masks = [feather_mask(skeleton, item, missing_bones=diagnostics)
             for item in definitions]
    assert len(masks) == 3
    np.testing.assert_array_equal(masks[0], [1.0, 1.0, 1.0])
    np.testing.assert_array_equal(masks[1], [0.0, 0.0, 0.0])
    np.testing.assert_array_equal(masks[2], [0.0, 0.5, 1.0])
    assert [item['m_ID'] for item in definitions] == ['full_body', 'wing_only', 'feathered']
    assert definitions == before
    assert diagnostics == [{'mask':'wing_only', 'bone':'absent_wing',
                            'weight':1.0, 'reason':'not_in_primary_skeleton'}]


def test_minus_one_remains_unset_weight_for_feathering(skeleton):
    np.testing.assert_array_equal(
        feather_mask(skeleton, definition(['root', 'mid', 'tip'], [0.0, -1.0, 1.0])),
        [0.0, 0.5, 1.0])


def test_malformed_mask_length_is_not_silently_truncated(skeleton):
    with pytest.raises(ValueError):
        feather_mask(skeleton, definition(['root', 'tip'], [0.0]))


@pytest.mark.parametrize(('bone', 'weight'), [('absent_wing', 1.01)])
def test_invalid_authored_weight_is_rejected_even_for_missing_bone(skeleton, bone, weight):
    with pytest.raises(ValueError):
        feather_mask(skeleton, definition([bone], [weight]))
