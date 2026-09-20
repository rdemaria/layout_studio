"""Open a case in the Layout Studio web viewer, controlled by its Python API.

Example: python open_case.py cases/03_rbend_beam.json --repo /path/to/layout_studio
"""
import argparse,sys
from pathlib import Path
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('case',type=Path)
p.add_argument('--repo',type=Path,required=True)
a=p.parse_args()
sys.path.insert(0,str(a.repo/'python_api/src'))
from layout_studio import Layout
layout=Layout.from_json(str(a.case))
with layout.plot_web(mechanical_axis=True,magnetic_axis=True,beam_axis=True,
                     standalone_path=a.repo/'webapp/build/index.html') as viewer:
    viewer.set_mode('pan')
    viewer.set_view('+y')
    viewer.show()
    input('Press Enter to close the viewer... ')
