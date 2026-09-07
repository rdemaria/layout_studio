"""The public, frozen corpus is also usable by independent implementations."""
import json
from pathlib import Path

import numpy as np
import pytest

from layout_studio import Layout, Resolver
from layout_studio.errors import AmbiguousStationError, NoStationSolutionError, EvaluationError, LayoutError


CORPUS = json.loads((Path(__file__).resolve().parents[2] / 'specifications/conformance/cases.json').read_text())


@pytest.mark.parametrize('case', CORPUS['cases'], ids=lambda case: case['id'])
def test_conformance(case):
    def parse():
        return Layout.from_json(text=case['json_text']) if 'json_text' in case else Layout.from_dict(case['layout'])

    error = case.get('error')
    if error and error['stage'] == 'parse':
        with pytest.raises(LayoutError):
            parse()
        return
    layout = parse()
    resolver = Resolver(layout)
    resolver.validate()

    def solve():
        with resolver:
            for curve in layout.curves.values():
                resolver.curve_frame(curve, 0)
                resolver.curve_frame(curve, sum(segment.length for segment in curve.segments))
            for obj in layout.objects.values():
                for frame in [*obj.implicit_frames, *obj.type.frames]:
                    resolver.object_frame(obj, frame)

    if error:
        exception = {'ambiguous': AmbiguousStationError, 'no_station': NoStationSolutionError,
                     'numeric_range': EvaluationError}[error['category']]
        with np.errstate(over='ignore', invalid='ignore'), pytest.raises(exception):
            solve()
        return
    solve()
    with resolver:
        for query in case.get('frames', []):
            actual = resolver.object_frame(query['object'], query['frame']).matrix
            np.testing.assert_allclose(actual, query['matrix'], atol=CORPUS['matrix_atol'], rtol=CORPUS['matrix_rtol'])
        for query in case.get('curve_frames', []):
            actual = resolver.curve_frame(query['curve'], query['s']).matrix
            np.testing.assert_allclose(actual, query['matrix'], atol=CORPUS['matrix_atol'], rtol=CORPUS['matrix_rtol'])
        for query in case.get('stations', []):
            expected = query['expected']
            if expected['kind'] == 'ambiguous':
                with pytest.raises(AmbiguousStationError):
                    resolver.infer_station(query['curve'], query['point'])
            else:
                assert resolver.infer_station(query['curve'], query['point']) == pytest.approx(expected['s'], abs=1e-10)


def test_dictionary_order_does_not_change_solution():
    case = next(c for c in CORPUS['cases'] if c['id'] == 'anchored-features')
    def reverse(value):
        if isinstance(value, dict):
            return {key: reverse(value[key]) for key in reversed(value)}
        return [reverse(v) for v in value] if isinstance(value, list) else value
    test_conformance({**case, 'layout': reverse(case['layout'])})
