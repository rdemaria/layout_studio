import copy

import pytest

from layout_studio import Layout, ValidationError


def test_ui_state_roundtrip_and_copy():
    data = {"reference_curves": {}, "types": {}, "objects": {}, "ui_state": {
        "version": 1, "editor": {"selection": None},
        "viewport": {"camera": {"target": [1, 2, 3], "distance": 42}},
        "future_extension": [True, None, "value"],
    }}
    original = copy.deepcopy(data)
    layout = Layout.from_dict(data)
    data["ui_state"]["viewport"]["camera"]["target"][0] = 999
    assert layout.to_dict() == original
    exported = layout.to_dict()
    exported["ui_state"]["viewport"]["camera"]["target"][0] = -999
    assert Layout.from_json(text=layout.to_json()).to_dict() == original
    layout.validate()


def test_legacy_layout_omits_ui_state():
    assert "ui_state" not in Layout().to_dict()


@pytest.mark.parametrize("value", [None, [], "invalid", {"distance": float("inf")}])
def test_invalid_ui_state(value):
    with pytest.raises(ValidationError, match="ui_state"):
        Layout.from_dict({"reference_curves": {}, "types": {}, "objects": {}, "ui_state": value})
