# napari-orthogonal-views

[![License BSD-3](https://img.shields.io/pypi/l/napari-orthogonal-views.svg?color=green)](https://github.com/AnniekStok/napari-orthogonal-views/raw/main/LICENSE)
[![PyPI](https://img.shields.io/pypi/v/napari-orthogonal-views.svg?color=green)](https://pypi.org/project/napari-orthogonal-views)
[![Python Version](https://img.shields.io/pypi/pyversions/napari-orthogonal-views.svg?color=green)](https://python.org)
[![tests](https://github.com/AnniekStok/napari-orthogonal-views/workflows/tests/badge.svg)](https://github.com/AnniekStok/napari-orthogonal-views/actions)
[![codecov](https://codecov.io/gh/AnniekStok/napari-orthogonal-views/branch/main/graph/badge.svg)](https://codecov.io/gh/AnniekStok/napari-orthogonal-views)
[![napari hub](https://img.shields.io/endpoint?url=https://api.napari-hub.org/shields/napari-orthogonal-views)](https://napari-hub.org/plugins/napari-orthogonal-views)
[![npe2](https://img.shields.io/badge/plugin-npe2-blue?link=https://napari.org/stable/plugins/index.html)](https://napari.org/stable/plugins/index.html)
[![Copier](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/copier-org/copier/master/img/badge/badge-grayscale-inverted-border-purple.json)](https://github.com/copier-org/copier)

A napari plugin for dynamically displaying orthogonal views and syncing events between the different viewers.

----------------------------------

This [napari] plugin was generated with [copier] using the [napari-plugin-template].

![orthoviews](https://github.com/user-attachments/assets/9d1ea326-866d-4af7-9ea6-8e56046cf6f2)

<!--
Don't miss the full getting started guide to set up your new package:
https://github.com/napari/napari-plugin-template#getting-started

and review the napari docs for plugin developers:
https://napari.org/stable/plugins/index.html
-->

This plugin is based on this [example](https://napari.org/dev/gallery/multiple_viewer_widget.html), with extra display and synchronization functionalities. The crosshair overlay is based on [this](https://github.com/napari/napari/pull/8017) and [this](https://github.com/napari/napari/blob/9d0c449553eaf1acc3be1bf9bc0c8b3eec05afc6/examples/dev/overlays.py) example.

## Installation

You can install the latest development version of `napari-orthogonal-views` via [pip]:

```
pip install git+https://github.com/AnniekStok/napari-orthogonal-views.git
```
## Usage
This plugin is not discoverable as a widget, but commands are available in Views>Commands Palette (CMD+SHIFT+P):
  - Show Orthogonal Views
  - Hide Orthogonal Views
  - Toggle Orthogonal Views
  - Remove Orthogonal Views

Once shown, it can also be popped up or collapsed using the checkbox in the bottom right corner 'Show orthogonal views'.
Alternatively, you can show the orthogonal views via the console:

```
from napari_orthogonal_views.ortho_view_manager import show_orthogonal_views
show_orthogonal_views(viewer)
```

And access the OrthoViewManager via _get_manager:

```
from napari_orthogonal_views.ortho_view_manager import _get_manager
m = _get_manager(viewer)
m.is_shown()
Out[6]: True
```

The size of the orthogonal view windows can be adjusted by clicking and dragging the small dot in between the views, optionally one or two views can be hidden entirely. The checkboxes in the bottom right corner can be used to show the crosshair overlay or for more control over camera zoom and axis center syncing.
Pressing `T` on the keyboard will center all views to the current mouse location.

By default, all events (including label editing such as painting) are synced across all views. The different views share the same underlying data array and undo/redo history.

## Syncing properties
By default, all layer properties should be synced between the layer on the main viewer and the orthoviews. However, it is possible to have more finegrained control over the synced properties via the `set_sync_filters` function, as long as it is specified *before* the orthogonal views are activated.

For example, to disable syncing of all properties on Tracks layers and specifically the contour property on Labels layers:

```
from napari_orthogonal_views.ortho_view_manager import _get_manager
from napari.layers import Tracks, Labels
m = _get_manager(viewer)
sync_filters = {
    Tracks: {
        "forward_exclude": "*",  # disable all forward sync
        "reverse_exclude": "*",  # disable all reverse sync
    },
    Labels: {
        "forward_exclude": "contour" # exclude contour from forward syncing
    },
}
m.set_sync_filters(sync_filters)

```
Then add 3D data (e.g. File > Open Sample > napari builtins > Balls (3D)). Activate the labels layer and change the contour value. You should see that the contour property is not synced from main viewer to orthoviews now.

## Layer hooks
Some syncing cannot be expressed as a property copy: painting on a Labels layer, undo/redo, and the point selection each need their own wiring. These are implemented as *layer hooks* in `layer_sync_hooks.py`, and are installed automatically — no setup needed to get the default behavior.

A hook is called once per layer, per orthogonal view:

```python
def my_hook(orig_layer, copied_layer):
    ...
```

Layers can be removed while the orthogonal views stay open, so a hook has to report what it did. It returns an iterable mixing `(signal, handler)` pairs it connected and zero-argument callables that undo anything else; returning `None` means it left nothing behind.

Most syncing needs nothing more than the right event. `data` is synced like any other layer property, so an edit napari makes *without* emitting `data` (painting, undo/redo) only has to be announced — that is all the built-in Labels hooks do:

```python
from napari_orthogonal_views.layer_sync_hooks import emit_data

emit_data(layer)  # the property syncing takes it from here, to every other view
```

A hook that instead writes to the other layer itself has to keep its own write from coming straight back at it; `sync_points_selection` shows that pattern.

### Adding behavior
`register_layer_hook` attaches an extra hook to a layer type, and runs after the built-in ones:

```python
from napari_orthogonal_views.ortho_view_manager import _get_manager
from napari.layers import Labels

m = _get_manager(viewer)

def report_clicks(orig_layer, copied_layer):
    def click(layer, event):
        orig_layer.selected_label = layer.get_value(
            event.position,
            view_direction=event.view_direction,
            dims_displayed=event.dims_displayed,
            world=True,
        )

    copied_layer.mouse_drag_callbacks.append(click)
    return [lambda: copied_layer.mouse_drag_callbacks.remove(click)]

m.register_layer_hook(Labels, report_clicks)
```

### Overriding the built-in behavior
The built-in hooks are keyed by name (`labels_undo_redo`, `labels_paint`, `points_selection`) so a single one can be replaced without disturbing the others. Use this to keep the default behavior and add to it:

```python
from napari_orthogonal_views.layer_sync_hooks import sync_labels_paint

def paint_and_recount(orig_layer, copied_layer):
    cleanup = sync_labels_paint(orig_layer, copied_layer)

    def on_paint(_event):
        my_app.recount_labels(orig_layer)

    copied_layer.events.paint.connect(on_paint)
    return [*cleanup, (copied_layer.events.paint, on_paint)]

m.set_layer_hook("labels_paint", paint_and_recount)
```

Passing `None` disables a built-in entirely. `set_layer_hook` and `register_layer_hook` may be called after the orthogonal views are shown; the change then applies to layers added from that point on.

## Screen recording
The 'Screen recording' tab offers a quick way to save a stitched image of the viewer with its orthogonal views. It is also possible to slide along a given axis and record a movie that is saved as a .avi file.

## Known issues and ongoing work
- Deprecation warnings on `Window._qt_window`, `LayerList._get_step_size`, `LayerList._get_extent_world` (suppressed for now).
- After removing the OrthoViewManager with `delete_and_cleanup` (Remove Orthogonal Views command), the canvas may become temporarily unresponsive. Clicking outside of Napari and then back on the Napari window usually fixes this.

## Contributing

Contributions are very welcome. Tests can be run with [tox], please ensure
the coverage at least stays the same before you submit a pull request.

## License

Distributed under the terms of the [BSD-3] license,
"napari-orthogonal-views" is free and open source software

## Issues

If you encounter any problems, please [file an issue](https://github.com/AnniekStok/napari-orthogonal-views/issues/) along with a detailed description.

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
