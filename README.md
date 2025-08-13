# napari-orthogonal-views

[![License BSD-3](https://img.shields.io/pypi/l/napari-orthogonal-views.svg?color=green)](https://github.com/AnniekStok/napari-orthogonal-views/raw/main/LICENSE)
[![PyPI](https://img.shields.io/pypi/v/napari-orthogonal-views.svg?color=green)](https://pypi.org/project/napari-orthogonal-views)
[![Python Version](https://img.shields.io/pypi/pyversions/napari-orthogonal-views.svg?color=green)](https://python.org)
[![tests](https://github.com/AnniekStok/napari-orthogonal-views/workflows/tests/badge.svg)](https://github.com/AnniekStok/napari-orthogonal-views/actions)
[![codecov](https://codecov.io/gh/AnniekStok/napari-orthogonal-views/branch/main/graph/badge.svg)](https://codecov.io/gh/AnniekStok/napari-orthogonal-views)
[![napari hub](https://img.shields.io/endpoint?url=https://api.napari-hub.org/shields/napari-orthogonal-views)](https://napari-hub.org/plugins/napari-orthogonal-views)
[![npe2](https://img.shields.io/badge/plugin-npe2-blue?link=https://napari.org/stable/plugins/index.html)](https://napari.org/stable/plugins/index.html)
[![Copier](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/copier-org/copier/master/img/badge/badge-grayscale-inverted-border-purple.json)](https://github.com/copier-org/copier)

A plugin for displaying the XZ and YZ views in separate windows, and syncing (paint) events between the different views. 

----------------------------------

This [napari] plugin was generated with [copier] using the [napari-plugin-template].


![napari-orthogonal-views](https://github.com/user-attachments/assets/dc4333c6-c801-42d1-9ad5-8f753ef47942)

<!--
Don't miss the full getting started guide to set up your new package:
https://github.com/napari/napari-plugin-template#getting-started

and review the napari docs for plugin developers:
https://napari.org/stable/plugins/index.html
-->

## Installation

You can install `napari-orthogonal-views` via [pip]:

```
pip install git+https://github.com/AnniekStok/napari-orthogonal-views.git
```
## Usage
Commands are available in Views>Commands Palette (CMD+SHIFT+P):
  - Show Orthogonal Views
  - Hide Orthogonal Views
  - Toggle Orthogonal Views
  - Remove Orthogonal Views

Once shown, it can also be popped up or collapsed using the checkbox in the bottom right corner 'Show orthogonal views'. 
Alternatively, you can access the 'OrthoViewManager' via the console:

```
from napari_orthogonal_views.ortho_view_manager import show_orthogonal_views, hide_orthogonal_views, _get_manager
m = _get_manager(viewer)
m.show()
m.is_shown()
Out[6]: True
```
or 
```
show_orthogonal_views(viewer)
```

The checkboxes in the bottom right corner can be used to show cross hairs or for more control over camera zoom and axis center syncing.

By default, all events (including label editing such as painting) are synced across all views. The different views share the same underlying data array and undo/redo history. 

## Known issues and ongoing work
- Deprecation warnings on 'Window._qt_window', 'LayerList._get_step_size', 'LayerList._get_extent_world'.
- After removing the OrthoViewManager with `delete_and_cleanup` (Remove Orthogonal Views command), the canvas may become temporarily unresponsive. Clicking outside of Napari and then back on the Napari window usually fixes this.
- Ongoing: more finegrained control over event syncing between the different viewers. 

## Contributing

Contributions are very welcome. Tests can be run with [tox], please ensure
the coverage at least stays the same before you submit a pull request.

## License

Distributed under the terms of the [BSD-3] license,
"napari-orthogonal-views" is free and open source software

## Issues

If you encounter any problems, please [file an issue] along with a detailed description.

[napari]: https://github.com/napari/napari
[copier]: https://copier.readthedocs.io/en/stable/
[@napari]: https://github.com/napari
[MIT]: http://opensource.org/licenses/MIT
[BSD-3]: http://opensource.org/licenses/BSD-3-Clause
[GNU GPL v3.0]: http://www.gnu.org/licenses/gpl-3.0.txt
[GNU LGPL v3.0]: http://www.gnu.org/licenses/lgpl-3.0.txt
[Apache Software License 2.0]: http://www.apache.org/licenses/LICENSE-2.0
[Mozilla Public License 2.0]: https://www.mozilla.org/media/MPL/2.0/index.txt
[napari-plugin-template]: https://github.com/napari/napari-plugin-template

[napari]: https://github.com/napari/napari
[tox]: https://tox.readthedocs.io/en/latest/
[pip]: https://pypi.org/project/pip/
[PyPI]: https://pypi.org/
