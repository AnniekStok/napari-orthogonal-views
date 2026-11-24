"""Patches for vispy (and napari's vispy layers) for the orthoslices tab (experimental).

The default napari and vispy settings do not support viewing clipping planes as a Union instead of Intersection.
The functions here include a patch (_union_of_slabs_glsl) to switch between Union and Intersection mode,
which is how pairs of clipping planes can visualize ortho slice views simultaneously (Slice mode), while
still allowing the default Intersection display for the 'Range' mode.
In the normal situation, vispy computes the signed distance of a position to each clipping plane.
It is kept if the minimum distance is larger than 0 for all planes (intersection).
To take the Union instead, the trick is to take the maximum over consecutive pairs of planes of their minimum,
inside any of the slabs. Normally, a negative distance would be filtered out (it is outside the clipping range),
but in this specific case it is kept if any of the other clipping planes have a positive distance and thus include it in their range.
In the case of volumes (labels and image layers) the position is in texture coordinates mapped to the clipping coordinates.
All other layers use a clipping filter that gets scene coordinates directly.

Additional patches:
- 'clip_in_3d_only': only clip layers with a clipping filter (points, shapes, vectors,
  ...) where they are rendered in 3D. By default napari applies the planes in 2D views as well,
  where these 3D planes would clip everything, and we want to see the 'normal' non clipped view in any (ortho) viewer that is showing a 2D view.
- 'whole_points': clip points by their centers instead of per pixel, so they are drawn
  whole instead of as cut fragments.
  Points are drawn as squares facing the camera, and their pixels gets clipped off by the clipping filter.
  To prevent this, store the position of the point center when the square is built (apply_offset function)
  and give all these pixels to the clipping filter, so that they are all included and not cut off.
- 'fast_volume': update the clipping planes of volumes without recompiling the shader,
  which vispy does for every change (every slice step), which is slow.

Because these patches touch private vispy and napari code, the state of these features is only experimental.
If any of the patches cannot be installed/errors because it is not supported by the installed vispy/napari version, that
feature is turned off while the rest of the plugin stays functional.

"""

import logging
import traceback
from collections.abc import Iterator
from contextlib import contextmanager
from functools import partial

logger = logging.getLogger(__name__)

_PLANES_HANDLER = (
    "_on_experimental_clipping_planes_change"  # napari vispy layers
)


def _check_versions() -> dict[str, bool]:
    """Whether the vispy and napari code that each patch relies on is unchanged."""

    try:
        from napari._vispy.layers.base import VispyBaseLayer
        from vispy.visuals import markers
        from vispy.visuals.filters.clipping_planes import PlanesClipper
        from vispy.visuals.volume import VolumeVisual

        volume_clip = VolumeVisual._build_clipping_planes_glsl(1)
        filter_clip = PlanesClipper._build_clipping_planes_glsl(1)
        return {
            "union": "float clip_planes(vec3 loc, vec3 vol_shape)"
            in volume_clip
            and "$clip_transform(vec4(loc * vol_shape" in volume_clip
            and "float clip_planes(vec3 loc)" in filter_clip
            and "$clip_with_planes($itransform($v_position).xyz)"
            in PlanesClipper.FRAG_CODE,
            "clip_in_3d_only": hasattr(VispyBaseLayer, _PLANES_HANDLER),
            "whole_points": "$v_position = gl_Position"
            in PlanesClipper.VERT_CODE
            and "$apply_offset(fb_pos, total_size)" in markers._VERTEX_SHADER
            and "$framebuffer_to_render(final_fb_pos)"
            in markers._VERTEX_SHADER,
            "fast_volume": isinstance(VolumeVisual.clipping_planes, property),
        }
    except Exception:  # noqa: BLE001
        logger.warning(
            "Orthoslices: vispy patches unavailable:\n%s",
            traceback.format_exc(),
        )
        return dict.fromkeys(
            ("union", "clip_in_3d_only", "whole_points", "fast_volume"), False
        )


SUPPORTED = _check_versions()
for _feature, _ok in SUPPORTED.items():
    if not _ok:
        logger.warning(
            "Orthoslices: '%s' is turned off, it does not support this vispy or napari version",
            _feature,
        )


@contextmanager
def _failsafe(feature: str) -> Iterator[None]:
    """Turn a feature off, with a warning, if its patch raises an error."""

    try:
        yield
    except Exception:  # noqa: BLE001
        SUPPORTED[feature] = False
        logger.warning(
            "Orthoslices: '%s' is turned off after an error:\n%s",
            feature,
            traceback.format_exc(),
        )


def can_clip(node) -> bool:
    """Whether the orthoslices can clip a vispy node: volumes always (napari's own
    clipping), nodes with a clipping filter only if they can be kept unclipped in 2D.
    """

    return not hasattr(node, "_clip_filter") or SUPPORTED["clip_in_3d_only"]


_UNION_TEMPLATE = """
    float clip_planes({args}) {{
        vec3 loc_transf = {loc};
        float distance_from_clip = {start};
        {slabs}
        return distance_from_clip;
    }}
"""
_SLAB_TEMPLATE = """
        distance_from_clip = max(distance_from_clip, min(
            dot(loc_transf - $clipping_plane_pos{a}, $clipping_plane_norm{a}),
            dot(loc_transf - $clipping_plane_pos{b}, $clipping_plane_norm{b})));
"""
_VOLUME_LOC = (
    "vec3 loc, vec3 vol_shape",
    "$clip_transform(vec4(loc * vol_shape - 0.5, 1)).xyz",
)
_FILTER_LOC = ("vec3 loc", "loc")


def _union_of_slabs_glsl(n_planes: int, volume: bool = True) -> str:
    """Clipping function drawing the union of slabs, from consecutive pairs of planes,
    for a volume visual or for a clipping filter."""

    args, loc = _VOLUME_LOC if volume else _FILTER_LOC
    if n_planes == 0:  # clip nothing
        return _UNION_TEMPLATE.format(
            args=args, loc=loc, start="3.4e38", slabs=""
        )
    slabs = "".join(
        _SLAB_TEMPLATE.format(a=i, b=min(i + 1, n_planes - 1))
        for i in range(0, n_planes, 2)
    )  # start from nothing inside, then add the slabs
    return _UNION_TEMPLATE.format(
        args=args, loc=loc, start="-3.4e38", slabs=slabs
    )


def _rebuild_clip_function(clipper) -> None:
    """Rebuild the clipping function of a volume or filter with its current planes (the
    filter only rebuilds it when the number of planes changes, so clear them first).
    """

    planes = clipper.clipping_planes
    clipper.clipping_planes = None
    clipper.clipping_planes = planes


def set_slab_union(node, union: bool) -> None:
    """Switch a vispy node between intersecting its clipping planes (default) and
    drawing the union of the slabs formed by pairs of planes."""

    clipper = getattr(node, "_clip_filter", node)
    # the builder of the clipping function is overridden on the instance (writing to
    # its __dict__ directly, as vispy visuals do not accept new attributes)
    overrides = vars(clipper)
    is_union = "_build_clipping_planes_glsl" in overrides
    if union == is_union or (union and not SUPPORTED["union"]):
        return  # do not recompile the shader if nothing changes
    with _failsafe("union"):
        try:
            if union:
                overrides["_build_clipping_planes_glsl"] = partial(
                    _union_of_slabs_glsl, volume=clipper is node
                )
            else:
                del overrides["_build_clipping_planes_glsl"]
            _rebuild_clip_function(clipper)
        except Exception:
            overrides.pop(
                "_build_clipping_planes_glsl", None
            )  # vispy's clipping
            _rebuild_clip_function(clipper)
            raise


def clip_in_3d_only(vispy_layer) -> None:
    """Make a napari vispy layer with a clipping filter apply the clipping planes of its
    layer only when it is rendered in 3D."""

    if not SUPPORTED["clip_in_3d_only"] or _PLANES_HANDLER in vars(
        vispy_layer
    ):
        return
    with _failsafe("clip_in_3d_only"):
        napari_handler = getattr(vispy_layer, _PLANES_HANDLER)

        def on_planes_change(event=None) -> None:
            # runs inside napari's events, so errors fall back to napari's handler
            if SUPPORTED["clip_in_3d_only"]:
                with _failsafe("clip_in_3d_only"):
                    if vispy_layer.layer._slice_input.ndisplay != 3:
                        vispy_layer.node.clipping_planes = None
                        vispy_layer.node.update()  # the filter does not redraw
                        return
            napari_handler()
            vispy_layer.node.update()

        setattr(vispy_layer, _PLANES_HANDLER, on_planes_change)
        on_planes_change()


def restore_napari_handler(vispy_layer) -> None:
    """Undo clip_in_3d_only."""

    vars(vispy_layer).pop(_PLANES_HANDLER, None)


_STORE_CENTER = """
    vec4 store_center(vec4 fb_pos, float total_size) {
        $v_center = $framebuffer_to_render(fb_pos);
        return $apply_offset(fb_pos, total_size);
    }
"""
# runs after the clipping filter has set the position to clip by (the pixel position)
_CLIP_BY_CENTER = """
    void clip_by_center() {
        $v_position = $v_center;
    }
"""


def clip_points_whole(node) -> None:
    """Clip the markers of a napari points node by their centers (once per node)."""

    if not SUPPORTED["whole_points"]:
        return
    with _failsafe("whole_points"):
        from vispy.visuals.shaders import Function, Variable

        v_position = node._clip_filter.vshader["v_position"]
        for markers in (node.points_markers, node.selection_markers):
            vert = markers.shared_program.vert
            apply_offset = vert["apply_offset"]
            if (
                markers._method != "instanced"
                or apply_offset.name == "store_center"
            ):
                continue  # GL points are clipped by their center already
            v_center = Variable("varying vec4 v_center")
            store_center = Function(_STORE_CENTER)
            store_center["v_center"] = v_center
            store_center["apply_offset"] = apply_offset
            store_center["framebuffer_to_render"] = markers.get_transform(
                "framebuffer", "render"
            )
            vert["apply_offset"] = store_center
            clip_by_center = Function(_CLIP_BY_CENTER)
            clip_by_center["v_center"] = v_center
            clip_by_center["v_position"] = v_position
            markers._get_hook("vert", "post").add(clip_by_center(), position=2)


_FAST_VOLUME_CLASSES: dict[type, type] = {}


def _fast_volume_class(cls: type) -> type:
    """Subclass of a vispy volume class that updates just the plane uniforms and skips the vispy setter
    if the number of planes is unchanged (which is most of the time the case when moving the sliders).
    By default, vispy builds a new shader function on every change, which recompiles the shader.
    """

    vispy_planes = cls.clipping_planes

    def set_planes(node, value) -> None:
        # runs inside napari's events, so errors fall back to vispy's setter
        if SUPPORTED["fast_volume"] and value is not None:
            with _failsafe("fast_volume"):
                if len(value) == len(node._clipping_planes):
                    node._clipping_planes = value
                    for idx, plane in enumerate(value):
                        node._clip_func[f"clipping_plane_pos{idx}"] = tuple(
                            plane[0]
                        )
                        node._clip_func[f"clipping_plane_norm{idx}"] = tuple(
                            plane[1]
                        )
                    node.update()
                    return  # values are updated, return early and skip the vispy setter
        vispy_planes.fset(
            node, value
        )  # run the original vispy function from /vispy/visuals/volume.py

    return type(
        cls.__name__,
        (cls,),
        {"clipping_planes": vispy_planes.setter(set_planes)},
    )


def fast_volume_shader(node) -> None:
    """Switch a vispy volume node to a subclass with the faster clipping planes setter."""

    cls = type(node)
    if not SUPPORTED["fast_volume"] or cls in _FAST_VOLUME_CLASSES.values():
        return
    with _failsafe("fast_volume"):
        if cls not in _FAST_VOLUME_CLASSES:
            _FAST_VOLUME_CLASSES[cls] = _fast_volume_class(cls)
        node.__class__ = _FAST_VOLUME_CLASSES[cls]
