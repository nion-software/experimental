"""Metadata-driven experimental Axis Plotter panel.

TODO:
    - Method for storing native image axis in metadata so the user does not have to select it manually and subsequent removal of checkbox
    - Formalisation of the CoordinateTransforms payload in niondata / metadata so that this panel can consume it directly instead of building it from instrument.axis_transformation_matrices metadata.
    - Stream/Mode implementation in stem controller opening the ability to track the control being currently updated and being able to impose the control axis automatically instead of having the user select it manually.

Design intent:

    - UI / plotting code consumes a CoordinateTransforms payload.
    - Today's payload is built from instrument.axis_transformation_matrices metadata.
    - The metadata builder section can later be replaced when metadata / niondata
      supplies CoordinateTransforms directly.

    - Select/focus a display panel.
    - The panel automatically loads coordinate transforms from that display panel's data item.
    - Existing overlays on other data items remain visible.
    - Refresh remains as a manual fallback.
"""

from __future__ import annotations

import traceback
import types
import typing

from dataclasses import dataclass

from nion.swift import DocumentController
from nion.swift import Panel
from nion.swift import Workspace
from nion.swift.model import PlugInManager
from nion.typeshed import API_1_0 as Facade
from nion.ui import Declarative
from nion.utils import Event
from nion.utils import Geometry
from nion.utils import Model
from nion.utils import Stream


PANEL_ID = "experimental-axis-plotter"
PANEL_TITLE = "[Experimental] Axis plotter"

# STEMController.update_instrument_properties calls stem_controller.get_autostem_properties,
# which stores the axis_transformation_matrix_metadata at this metadata path.
AXIS_TRANSFORMATION_MATRICES_METADATA_PATH = "instrument.axis_transformation_matrices"


# --------------------------------------------------------------------------------------
# Shared data types
# --------------------------------------------------------------------------------------

StoredGraphic = tuple[Facade.DataItem, Facade.Graphic]
AxisGraphicKey = tuple[str, str]


@dataclass(frozen=True)
class CoordinateTransform:
    """Parsed coordinate transform used by the UI and overlay renderer."""

    axis_id: str
    display_name: str
    axis_type: tuple[str, str]
    origin: Geometry.FloatPoint
    x_vector: Geometry.FloatPoint
    y_vector: Geometry.FloatPoint
    color: str


@dataclass(frozen=True)
class VisibleAxisOverlay:
    """Currently visible axis overlay graphics on one API data item."""
    data_item_key: str
    axis_id: str
    axis: CoordinateTransform
    graphics_data_item: Facade.DataItem
    color: str
    graphics: tuple[StoredGraphic, ...]


# --------------------------------------------------------------------------------------
# Coordinate transform payload consumed by the plotter
# --------------------------------------------------------------------------------------

@dataclass(frozen=True)
class CoordinateTransforms:
    """Coordinate transform payload consumed by UI / plotting code."""

    transforms: typing.Mapping[str, CoordinateTransform]
    metadata_source: str


# --------------------------------------------------------------------------------------
# UI and plotting code
# --------------------------------------------------------------------------------------

class ExperimentalAxesPlotterHandler(Declarative.Handler):
    name = "Metadata Axis Plotter"

    def __init__(self, rebuild_widget_fn: typing.Callable[[], None]) -> None:
        super().__init__()

        api_broker = PlugInManager.APIBroker()
        facade_api = typing.cast(Facade.API, api_broker.get_api(version="~1.0"))
        self._api = facade_api

        self._rebuild_widget_fn = rebuild_widget_fn
        self._current_display_item: Facade.Display | None = None
        self._current_coordinate_transforms: CoordinateTransforms | None = None

        self.axis_ids: list[str] = []
        self.image_axis_ids: list[str] = []
        self.image_axis_display_names: list[str] = []

        self._axis_id_to_axis: dict[str, CoordinateTransform] = {}
        self._axis_id_to_safeid: dict[str, str] = {}
        self._axis_color_models: dict[str, Model.PropertyModel[str]] = {}
        self._axis_graphics: dict[AxisGraphicKey, VisibleAxisOverlay] = {}
        self._axis_toggle_callback_names: set[str] = set()

        self.full_length_enabled = Model.PropertyModel[bool](False)
        self.full_length_enabled.on_value_changed = self._full_length_enabled_changed
        self.selected_image_axis_index = Model.PropertyModel[int](0)
        self.selected_image_axis_index.on_value_changed = self._selected_image_axis_changed
        self.status_text = Model.PropertyModel[str]("Select a display panel containing a data item.")

        self._ui = Declarative.DeclarativeUI()
        self.ui_view = self._build_ui()

        self._coordinate_transforms_stream = self._create_coordinate_transforms_stream()
        self._coordinate_transforms_listener: Event.EventListener | None = (self._coordinate_transforms_stream.value_stream.listen(self._coordinate_transforms_changed))

    def _full_length_enabled_changed(self, value: bool | None) -> None:
        """Rebuild overlays only on the currently selected display."""

        self._rebuild_existing_axes_for_selected_display()

    def _selected_image_axis_changed(self, value: int | None) -> None:
        """Rebuild overlays only on the currently selected display."""

        self._rebuild_existing_axes_for_selected_display()

    def close(self) -> None:
        """Declarative widget close.

        Do not remove axis overlays here, otherwise overlays disappear on focus changes.
        """

        return

    def close_for_panel(self) -> None:
        """Clean up streams and overlays because the actual panel is closing."""

        if self._coordinate_transforms_listener is not None:
            try:
                self._coordinate_transforms_listener.close()
            except Exception:
                traceback.print_exc()

            self._coordinate_transforms_listener = None

        self._remove_all_axis_graphics()

    def _create_coordinate_transforms_stream(self) -> Stream.ValueStream[CoordinateTransforms | None]:
        """Create the current CoordinateTransforms value stream."""

        try:
            return typing.cast(
                Stream.ValueStream[CoordinateTransforms | None],
                Stream.ValueStream(None)
            )
        except TypeError:
            # Older Nion Stream.ValueStream builds do not accept an initial value
            coordinate_transforms_stream = typing.cast(
                Stream.ValueStream[CoordinateTransforms | None],
                Stream.ValueStream()
            )
            coordinate_transforms_stream.value = None
            return coordinate_transforms_stream

    def _set_coordinate_transforms_value(self, coordinate_transforms: CoordinateTransforms | None) -> None:
        """Set the current coordinate transforms and force the UI state to update."""

        try:
            self._coordinate_transforms_stream.value = coordinate_transforms
        except Exception:
            traceback.print_exc()

    def _coordinate_transforms_changed(self, coordinate_transforms: CoordinateTransforms | None) -> None:
        """Refresh handler state when the current CoordinateTransforms changes."""

        self._current_coordinate_transforms = coordinate_transforms
        self._refresh_axes_from_coordinate_transforms(coordinate_transforms)
        self._rebuild_ui()

    def set_display_item(self, display_item: Facade.Display | None) -> None:
        """Update axes from the selected/focused display item."""

        self._current_display_item = display_item

        if display_item is None:
            self._set_coordinate_transforms_value(None)
            return

        self._set_coordinate_transforms_value(create_coordinate_transforms_from_display_item(display_item))

    def refresh_from_current_selection(self) -> None:
        """Manual fallback refresh using the current display item or API target."""

        self._set_coordinate_transforms_value(self._create_coordinate_transforms_for_current_display_item())

    def _rebuild_ui(self) -> None:
        """Recreate the Declarative UI and ask the owning widget to replace it."""

        self.ui_view = self._build_ui()
        self._rebuild_widget_fn()

    def _build_ui(self) -> Declarative.UIDescriptionResult:
        """Build the Declarative UI """

        u = self._ui

        header = u.create_row(
            u.create_label(text="Axis Plotter", width=220),
            u.create_push_button(text="Refresh", on_clicked="on_refresh_clicked", width=80),
            u.create_push_button(text="Clear All", on_clicked="on_clear_all_clicked", width=90),
            u.create_stretch(),
            spacing=8
        )

        options_row = u.create_row(
            u.create_label(text="Image axis:", width=60),
            u.create_combo_box(
                items=self.image_axis_display_names,
                current_index="@binding(selected_image_axis_index.value)",
                width=150
            ),
            u.create_spacing(20),
            u.create_check_box(
                text="Full length lines",
                checked="@binding(full_length_enabled.value)",
                tool_tip="Draw each axis as a full line centered on the origin."
            ),
            u.create_stretch(),
            spacing=8
        )

        if not self.axis_ids:
            body = u.create_column(
                u.create_label(text="@binding(status_text.value)", width=460),
                spacing=6
            )
        else:
            rows: list[Declarative.UIDescriptionResult] = []

            for axis_id in self.axis_ids:
                axis = self._axis_id_to_axis[axis_id]
                safeid = self._axis_id_to_safeid[axis_id]

                color_attr = f"axis_color_{safeid}"
                toggle_method = f"on_toggle_{safeid}_clicked"

                if not hasattr(self, toggle_method):
                    setattr(self, toggle_method, self._make_toggle_handler(axis_id))
                    self._axis_toggle_callback_names.add(toggle_method)

                axis_label = f"{axis.display_name} ({axis.axis_type[0]}, {axis.axis_type[1]})"

                rows.append(
                    u.create_row(
                        u.create_label(text=axis_label, width=160),
                        u.create_push_button(text="Toggle", on_clicked=toggle_method, width=70),
                        u.create_line_edit(text=f"@binding({color_attr}.value)", width=90),
                        {"type": "nionswift.color_chooser", "color": f"@binding({color_attr}.value)"},
                        u.create_stretch(),
                        spacing=8
                    )
                )

            body = u.create_column(
                u.create_label(text="@binding(status_text.value)", width=460),
                *rows,
                spacing=6
            )

        return u.create_column(header, options_row, body, u.create_stretch(), spacing=10)

    def _make_toggle_handler(self, axis_id: str) -> typing.Callable[[Declarative.UIWidget], None]:
        """Create a direct button callback that toggles one axis."""

        def _handler(widget: Declarative.UIWidget) -> None:
            self._toggle_axis(axis_id)

        return _handler

    def _sanitize_axis_id(self, axis_id: str) -> str:
        """Return an identifier-safe suffix for dynamic Declarative bindings."""

        out: list[str] = []

        for ch in axis_id:
            out.append(ch if ch.isalnum() else "_")

        safe_id = "".join(out)

        if safe_id and safe_id[0].isdigit():
            safe_id = "_" + safe_id

        return safe_id

    def _clear_axis_color_attributes(self) -> None:
        """Remove dynamic colour models and their binding attributes."""

        for color_model in self._axis_color_models.values():
            color_model.on_value_changed = None

        self._axis_color_models.clear()

        for attr_name in list(vars(self)):
            if attr_name.startswith("axis_color_"):
                delattr(self, attr_name)

    def _make_axis_color_changed_handler(self, axis_id: str) -> typing.Callable[[str | None], None]:
        """Create a colour-model callback for one axis."""

        def _handler(value: str | None) -> None:
            if isinstance(value, str):
                self._update_axis_color_on_selected_display(axis_id, value)

        return _handler

    def _update_axis_color_on_selected_display(self, axis_id: str, color: str) -> None:
        """Apply a colour change immediately to the selected display's visible axis."""

        data_item = self._get_active_data_item()

        if data_item is None:
            return

        overlay_key: AxisGraphicKey = (self._get_data_item_key(data_item), axis_id)
        overlay = self._axis_graphics.get(overlay_key)

        if overlay is None:
            return

        for _graphic_data_item, graphic in overlay.graphics:
            try:
                graphic.set_property("stroke_color", color)
            except Exception:
                traceback.print_exc()

        self._axis_graphics[overlay_key] = VisibleAxisOverlay(
            data_item_key=overlay.data_item_key,
            axis_id=overlay.axis_id,
            axis=overlay.axis,
            graphics_data_item=overlay.graphics_data_item,
            color=color,
            graphics=overlay.graphics
        )

    def _clear_axis_toggle_callbacks(self) -> None:
        """Remove dynamic axis toggle callbacks from the handler."""

        for callback_name in list(self._axis_toggle_callback_names):
            if callback_name in self.__dict__:
                delattr(self, callback_name)

        self._axis_toggle_callback_names.clear()

    def _get_target_document_window(self) -> Facade.DocumentWindow | None:
        """Return a usable facade document window, ignoring transient popup windows."""

        try:
            windows = self._api.application.document_windows
        except (AttributeError, RuntimeError):
            return None

        fallback_window: Facade.DocumentWindow | None = None

        for window in windows:
            try:
                target_display = window.target_display
                window.target_data_item
            except (AttributeError, RuntimeError):
                # Workspace/project changes can temporarily expose PopupWindow objects.
                continue

            if fallback_window is None:
                fallback_window = window

            if target_display is not None:
                return window

        return fallback_window

    def _get_active_data_item(self) -> Facade.DataItem | None:
        window = self._get_target_document_window()

        if window is None:
            return None

        try:
            target_data_item = window.target_data_item

            if target_data_item is not None:
                return target_data_item

            target_display = window.target_display

            if target_display is not None:
                return target_display.data_item
        except (AttributeError, RuntimeError):
            return None

        return None

    def _get_data_item_key(self, data_item: Facade.DataItem) -> str:

        return str(data_item.uuid)

    def _create_coordinate_transforms_for_current_display_item(self) -> CoordinateTransforms | None:
        """Create transforms from the current display item, falling back to the active item."""

        if self._current_display_item is not None:
            return create_coordinate_transforms_from_display_item(self._current_display_item)

        data_item = self._get_active_data_item()

        if data_item is None:
            return None

        return create_coordinate_transforms_from_data_item(data_item)

    def _get_coordinate_transforms(self) -> CoordinateTransforms | None:
        """Return the currently selected coordinate-transform payload."""

        return self._current_coordinate_transforms

    def _refresh_axes_from_coordinate_transforms(self, coordinate_transforms: CoordinateTransforms | None) -> None:
        """Refresh axis UI state from the current CoordinateTransforms.

        This clears current UI axis state, removes stale colour/callback bindings,
        sorts the axis ids, and repopulates the axis lookup for the current stream.
        Existing overlay graphics are intentionally preserved.
        """

        current_image_axis_id = self._get_selected_image_axis_id()

        self.axis_ids.clear()
        self.image_axis_ids.clear()
        self.image_axis_display_names.clear()
        self._axis_id_to_axis.clear()
        self._axis_id_to_safeid.clear()
        self._clear_axis_color_attributes()
        self._clear_axis_toggle_callbacks()

        if coordinate_transforms is None:
            self.selected_image_axis_index.value = 0
            self.status_text.value = "Selected display panel has no readable axes metadata."
            return

        axes = coordinate_transforms.transforms
        metadata_source = coordinate_transforms.metadata_source

        if not axes:
            self.selected_image_axis_index.value = 0
            self.status_text.value = "Selected display panel has no readable axes metadata."
            return

        preferred_order = (
            "tv",
            "scan",
            "stageaxis",
            "stagetiltaxis",
            "eels",
            "mc",
            "postsample",
            "correctoraxis",
            "gun"
        )

        ordered_axis_ids: list[str] = []

        for preferred_axis_id in preferred_order:
            if preferred_axis_id in axes:
                ordered_axis_ids.append(preferred_axis_id)

        for axis_id in axes:
            if axis_id not in ordered_axis_ids:
                ordered_axis_ids.append(axis_id)

        self.axis_ids[:] = ordered_axis_ids

        for axis_id in self.axis_ids:
            axis = axes[axis_id]
            safeid = self._sanitize_axis_id(axis_id)

            self._axis_id_to_axis[axis_id] = axis
            self._axis_id_to_safeid[axis_id] = safeid
            self.image_axis_ids.append(axis_id)
            self.image_axis_display_names.append(axis.display_name)

            color_model = Model.PropertyModel[str](axis.color)
            color_model.on_value_changed = self._make_axis_color_changed_handler(axis_id)
            self._axis_color_models[axis_id] = color_model
            setattr(self, f"axis_color_{safeid}", color_model)

        #This is a workaround having the user select the image axis. In future we would look to have the native axis stored in the metadata and use that as the default or remove this entirely
        if current_image_axis_id in self.image_axis_ids:
            self.selected_image_axis_index.value = self.image_axis_ids.index(current_image_axis_id)
        elif "tv" in self.image_axis_ids:
            self.selected_image_axis_index.value = self.image_axis_ids.index("tv")
        else:
            self.selected_image_axis_index.value = 0

        self.status_text.value = f"Loaded {len(self.axis_ids)} axes from {metadata_source}."

    def _get_selected_image_axis_id(self) -> str | None:
        """Return the currently selected image axis id."""

        if not self.image_axis_ids:
            return None

        index = self.selected_image_axis_index.value

        if index is not None and 0 <= index < len(self.image_axis_ids):
            return self.image_axis_ids[index]

        return self.image_axis_ids[0]

    def _get_selected_image_axis(self) -> CoordinateTransform | None:
        """Return the currently selected image axis transform."""

        image_axis_id = self._get_selected_image_axis_id()

        if image_axis_id is None:
            return None

        return self._axis_id_to_axis.get(image_axis_id)

    def _vector_in_image_axis(self, vector: Geometry.FloatPoint, image_axis: CoordinateTransform) -> Geometry.FloatPoint | None:
        """Express a vector in the selected image-axis basis."""

        image_axis_x = image_axis.x_vector
        image_axis_y = image_axis.y_vector
        determinant = image_axis_x.y * image_axis_y.x - image_axis_y.y * image_axis_x.x

        if abs(determinant) <= 1e-12:
            return None

        x_component = (vector.y * image_axis_y.x - image_axis_y.y * vector.x) / determinant
        y_component = (image_axis_x.y * vector.x - vector.y * image_axis_x.x) / determinant

        return Geometry.FloatPoint(y=y_component, x=x_component)

    def _axis_vectors_in_image_axis(self, axis: CoordinateTransform, image_axis: CoordinateTransform) -> tuple[Geometry.FloatPoint, Geometry.FloatPoint] | None:
        """Return source-axis basis vectors expressed in image-axis coordinates."""

        x_vector = self._vector_in_image_axis(axis.x_vector, image_axis)
        y_vector = self._vector_in_image_axis(axis.y_vector, image_axis)

        if x_vector is None or y_vector is None:
            return None

        return x_vector, y_vector

    def _shape_to_height_width(self, shape_value: object) -> tuple[int, int] | None:
        """Convert an xdata value to display height and width."""

        if isinstance(shape_value, (tuple, list)) and len(shape_value) >= 2:
            return int(shape_value[-2]), int(shape_value[-1])

        return None

    def _get_active_data_shape(self, data_item: Facade.DataItem) -> tuple[int, int] | None:
        """Return the active data shape used for normalized overlay vector scaling."""

        return self._shape_to_height_width(data_item.xdata.data_shape)

    def _normalize_vector(self, vector: Geometry.FloatPoint) -> Geometry.FloatPoint:
        """Return a unit vector, preserving zero vectors as zero vectors."""

        length = float(abs(vector))

        if length <= 1e-12:
            return Geometry.FloatPoint(y=0.0, x=0.0)

        return Geometry.FloatPoint(y=vector.y / length, x=vector.x / length)

    def _normalize_vector_for_display(self, vector: Geometry.FloatPoint, data_shape: tuple[int, int] | None) -> Geometry.FloatPoint:
        """Normalize a metadata vector while compensating for non-square displays."""

        if data_shape is None:
            return self._normalize_vector(vector)

        height, width = data_shape

        if height <= 0 or width <= 0:
            return self._normalize_vector(vector)

        pixel_y = vector.y * height
        pixel_x = vector.x * width
        pixel_length = (pixel_y * pixel_y + pixel_x * pixel_x) ** 0.5

        if pixel_length <= 1e-12:
            return Geometry.FloatPoint(y=0.0, x=0.0)

        scale = float(min(height, width))

        return Geometry.FloatPoint(
            y=(pixel_y / pixel_length) * (scale / height),
            x=(pixel_x / pixel_length) * (scale / width)
        )

    def _clamp_point(self, point: Geometry.FloatPoint) -> Geometry.FloatPoint:
        """Clamp a normalized point to the display bounds."""

        return Geometry.FloatPoint(
            y=min(max(point.y, 0.0), 1.0),
            x=min(max(point.x, 0.0), 1.0)
        )

    def _axis_line_points(self, origin: Geometry.FloatPoint, unit_vector: Geometry.FloatPoint, line_length: float, *, forward: bool = True) -> tuple[Geometry.FloatPoint, Geometry.FloatPoint]:
        """Return start/end points for a forward or backward half-axis line."""

        direction_sign = 1.0 if forward else -1.0

        end = self._clamp_point(
            Geometry.FloatPoint(
                y=origin.y + direction_sign * unit_vector.y * line_length,
                x=origin.x + direction_sign * unit_vector.x * line_length
            )
        )

        return origin, end

    def _make_line_region(self, data_item: Facade.DataItem, start: Geometry.FloatPoint, end: Geometry.FloatPoint, color: str, label: str, *, arrow_at_end: bool = True) -> Facade.Graphic:
        """Create and configure one Swift line-region overlay."""

        graphic = data_item.add_line_region(start.y, start.x, end.y, end.x)

        graphic.set_property("label", label)
        graphic.set_property("stroke_color", color)
        graphic.set_property("stroke_width", 2.0)
        graphic.set_property("start_arrow_enabled", False)
        graphic.set_property("end_arrow_enabled", arrow_at_end)

        return graphic

    def _append_axis_line_region(self, graphics_to_add: list[StoredGraphic], graphics_data_item: Facade.DataItem, start: Geometry.FloatPoint, end: Geometry.FloatPoint, color: str, label: str, *, arrow_at_end: bool = True) -> None:
        """Append one new overlay graphic to the mutable per-axis collection.

        The concrete list type is intentional because this helper mutates the caller-owned
        collection while building an overlay atomically.
        """

        graphic = self._make_line_region(
            graphics_data_item,
            start,
            end,
            color,
            label,
            arrow_at_end=arrow_at_end
        )
        graphics_to_add.append((graphics_data_item, graphic))

    def _show_axis_overlay(self, axis: CoordinateTransform, graphics_data_item: Facade.DataItem, color: str) -> tuple[StoredGraphic, ...] | None:
        """Create overlay graphics for one axis on one API data item.

        A tuple is returned intentionally because VisibleAxisOverlay is frozen and stores
        an immutable snapshot of the graphics that were successfully created.
        """

        data_shape = self._get_active_data_shape(graphics_data_item)
        image_axis = self._get_selected_image_axis()

        if image_axis is None:
            self.status_text.value = "No image axis is selected."
            return None

        image_axis_vectors = self._axis_vectors_in_image_axis(axis, image_axis)

        if image_axis_vectors is None:
            self.status_text.value = f"Cannot plot {axis.display_name}: image axis {image_axis.display_name} is singular."
            return None

        x_vector, y_vector = image_axis_vectors
        x_unit_vector = self._normalize_vector_for_display(x_vector, data_shape)
        y_unit_vector = self._normalize_vector_for_display(y_vector, data_shape)

        if abs(x_unit_vector) <= 1e-12 or abs(y_unit_vector) <= 1e-12:
            self.status_text.value = f"Cannot plot {axis.display_name}: metadata vector is near zero in {image_axis.display_name}."
            return None

        line_length = 0.22
        axis_name_0, axis_name_1 = axis.axis_type
        graphics_to_add: list[StoredGraphic] = []

        try:
            x_forward_start, x_forward_end = self._axis_line_points(
                axis.origin,
                x_unit_vector,
                line_length,
                forward=True
            )
            y_forward_start, y_forward_end = self._axis_line_points(
                axis.origin,
                y_unit_vector,
                line_length,
                forward=True
            )

            self._append_axis_line_region(
                graphics_to_add,
                graphics_data_item,
                x_forward_start,
                x_forward_end,
                color,
                axis_name_0,
                arrow_at_end=True
            )
            self._append_axis_line_region(
                graphics_to_add,
                graphics_data_item,
                y_forward_start,
                y_forward_end,
                color,
                axis_name_1,
                arrow_at_end=True
            )

            if bool(self.full_length_enabled.value):
                x_backward_start, x_backward_end = self._axis_line_points(
                    axis.origin,
                    x_unit_vector,
                    line_length,
                    forward=False
                )
                y_backward_start, y_backward_end = self._axis_line_points(
                    axis.origin,
                    y_unit_vector,
                    line_length,
                    forward=False
                )

                self._append_axis_line_region(
                    graphics_to_add,
                    graphics_data_item,
                    x_backward_start,
                    x_backward_end,
                    color,
                    "",
                    arrow_at_end=False
                )
                self._append_axis_line_region(
                    graphics_to_add,
                    graphics_data_item,
                    y_backward_start,
                    y_backward_end,
                    color,
                    "",
                    arrow_at_end=False
                )

        except Exception:
            for graphic_data_item, graphic in graphics_to_add:
                try:
                    graphic_data_item.remove_region(graphic)
                except Exception:
                    traceback.print_exc()

            traceback.print_exc()
            self.status_text.value = f"Failed to plot axis {axis.display_name}."
            return None

        return tuple(graphics_to_add)

    def _toggle_axis(self, axis_id: str) -> None:
        """Toggle one axis overlay on the active API data item.

        The available axis must come from the current CoordinateTransforms. Overlay state is
        keyed by data item and axis id so overlays remain visible when focus moves.
        """

        coordinate_transforms = self._get_coordinate_transforms()

        if coordinate_transforms is None:
            self.status_text.value = "No coordinate transforms available for the selected display panel."
            self._refresh_axes_from_coordinate_transforms(None)
            self._rebuild_ui()
            return

        axis = coordinate_transforms.transforms.get(axis_id)

        if axis is None:
            self.status_text.value = f"Axis {axis_id!r} is not available on the selected display panel."
            self._refresh_axes_from_coordinate_transforms(coordinate_transforms)
            self._rebuild_ui()
            return

        graphics_data_item = self._get_active_data_item()

        if graphics_data_item is None:
            self.status_text.value = "No active API data item available for axis overlay."
            return

        data_item_key = self._get_data_item_key(graphics_data_item)
        overlay_key: AxisGraphicKey = (data_item_key, axis_id)

        if overlay_key in self._axis_graphics:
            self._remove_graphics_for_key(overlay_key)
            self.status_text.value = f"Removed axis {axis.display_name} from active data item."
            return

        color_model = self._axis_color_models.get(axis_id)
        color = color_model.value if color_model is not None else axis.color

        if not isinstance(color, str):
            color = axis.color

        graphics = self._show_axis_overlay(axis, graphics_data_item, color)

        if graphics is None:
            return

        self._axis_graphics[overlay_key] = VisibleAxisOverlay(
            data_item_key=data_item_key,
            axis_id=axis_id,
            axis=axis,
            graphics_data_item=graphics_data_item,
            color=color,
            graphics=graphics
        )

        image_axis = self._get_selected_image_axis()
        image_axis_name = image_axis.display_name if image_axis is not None else "selected image axis"
        self.status_text.value = f"Displayed axis {axis.display_name} in {image_axis_name} on active data item."

    def _remove_graphics_for_key(self, overlay_key: AxisGraphicKey) -> None:
        overlay = self._axis_graphics.pop(overlay_key, None)

        if overlay is None:
            return

        for data_item, graphic in overlay.graphics:
            try:
                data_item.remove_region(graphic)
            except Exception:
                traceback.print_exc()

    def _remove_all_axis_graphics(self) -> None:
        """Remove all overlays from every tracked display during panel shutdown."""

        for overlay_key in list(self._axis_graphics.keys()):
            self._remove_graphics_for_key(overlay_key)

    def _remove_axis_graphics_for_selected_display(self) -> None:
        """Remove managed overlays only from the currently selected display."""

        data_item = self._get_active_data_item()

        if data_item is None:
            return

        data_item_key = self._get_data_item_key(data_item)

        for overlay_key in list(self._axis_graphics.keys()):
            if overlay_key[0] == data_item_key:
                self._remove_graphics_for_key(overlay_key)

    def _rebuild_existing_axes_for_selected_display(self) -> None:
        """Rebuild visible overlays only on the currently selected display."""

        data_item = self._get_active_data_item()

        if data_item is None:
            return

        data_item_key = self._get_data_item_key(data_item)
        overlays = [overlay for overlay in self._axis_graphics.values() if overlay.data_item_key == data_item_key]

        for overlay in overlays:
            self._remove_graphics_for_key((overlay.data_item_key, overlay.axis_id))

        for overlay in overlays:
            color_model = self._axis_color_models.get(overlay.axis_id)
            color = color_model.value if color_model is not None else overlay.color

            if not isinstance(color, str):
                color = overlay.color

            graphics = self._show_axis_overlay(overlay.axis, overlay.graphics_data_item, color)

            if graphics is None:
                continue

            overlay_key: AxisGraphicKey = (overlay.data_item_key, overlay.axis_id)

            self._axis_graphics[overlay_key] = VisibleAxisOverlay(
                data_item_key=overlay.data_item_key,
                axis_id=overlay.axis_id,
                axis=overlay.axis,
                graphics_data_item=overlay.graphics_data_item,
                color=color,
                graphics=graphics
            )

    def on_clear_all_clicked(self, widget: Declarative.UIWidget) -> None:
        self._remove_axis_graphics_for_selected_display()
        self.status_text.value = "Cleared axis overlays from the selected display."

    def on_refresh_clicked(self, widget: Declarative.UIWidget) -> None:
        self.refresh_from_current_selection()


# --------------------------------------------------------------------------------------
# Panel registration
# --------------------------------------------------------------------------------------

class ExperimentalAxesPlotterPanel(Panel.Panel):
    def __init__(self, document_controller: DocumentController.DocumentController, panel_id: str, properties: typing.Mapping[str, object]) -> None:
        """Create the experimental panel."""

        super().__init__(document_controller, panel_id, PANEL_TITLE)

        self.__document_controller = document_controller
        self.__display_item_changed_listeners: list[Event.EventListener] = []

        ui = document_controller.ui

        self.__content_column = ui.create_column_widget()

        self.widget = self.__content_column

        self.__handler = ExperimentalAxesPlotterHandler(
            rebuild_widget_fn=self.__rebuild_widget
        )

        self.__declarative_widget: Declarative.DeclarativeWidget | None = None

        self.__rebuild_widget()
        self.__connect_display_selection_listener()
        self.__load_initial_display_item()

    def __rebuild_widget(self) -> None:
        self.__content_column.remove_all()

        self.__declarative_widget = Declarative.DeclarativeWidget(
            self.__document_controller.ui,
            self.__document_controller.event_loop,
            self.__handler
        )

        self.__content_column.add(self.__declarative_widget)

    def __connect_display_selection_listener(self) -> None:
        focused_listener = self.__document_controller.focused_display_item_changed_event.listen(
            self.__display_item_changed
        )

        self.__display_item_changed_listeners.append(focused_listener)

    def __get_current_display_item(self) -> Facade.Display | None:
        focused_display_item = self.__document_controller.focused_display_item

        if focused_display_item is not None:
            return typing.cast(Facade.Display, focused_display_item)

        selected_display_item = self.__document_controller.selected_display_item

        if selected_display_item is not None:
            return typing.cast(Facade.Display, selected_display_item)

        return None

    def __load_initial_display_item(self) -> None:
        self.__handler.set_display_item(self.__get_current_display_item())

    def __display_item_changed(self, display_item: object | None = None) -> None:
        current_display_item = self.__get_current_display_item()

        if current_display_item is not None:
            self.__handler.set_display_item(current_display_item)
            return

        # None is normal while a workspace or project is closing. Passing it explicitly
        # prevents the handler from probing transient application popup windows.
        self.__handler.set_display_item(None)

    def close(self) -> None:
        for listener in self.__display_item_changed_listeners:
            try:
                listener.close()
            except Exception:
                traceback.print_exc()

        self.__display_item_changed_listeners.clear()

        self.__handler.close_for_panel()
        super().close()


def register_panel() -> None:
    Workspace.WorkspaceManager().register_panel(
        ExperimentalAxesPlotterPanel,
        PANEL_ID,
        PANEL_TITLE,
        ["left", "right"],
        "right",
        {}
    )


def unregister_panel() -> None:
    Workspace.WorkspaceManager().unregister_panel(PANEL_ID)


class ExperimentalAxesPlotterExtension:
    extension_id = "nion.extension.experimental_axes_plotter"

    def __init__(self, api_broker: typing.Any) -> None:
        """Register the panel."""

        api_broker.get_api(version="~1.0")
        register_panel()

    def close(self) -> None:
        unregister_panel()


# --------------------------------------------------------------------------------------
# Metadata-backed CoordinateTransforms construction
#
# This section will be replaced when CoordinateTransforms are supplied directly by metadata / niondata.
# --------------------------------------------------------------------------------------

def _is_mapping(value: typing.Any) -> typing.TypeGuard[typing.Mapping[typing.Any, typing.Any]]:
    return isinstance(value, typing.Mapping)


def _metadata_get_path(metadata: typing.Mapping[typing.Any, typing.Any], path: str) -> typing.Any:
    """Read a dotted metadata path from the known metadata dictionary shape."""

    if path in metadata:
        return metadata[path]

    current: typing.Any = metadata

    for part in path.split("."):
        if not _is_mapping(current):
            return None

        if part not in current:
            return None

        current = current[part]

    return current


def get_data_item_metadata(data_item: Facade.DataItem) -> typing.Mapping[typing.Any, typing.Any]:
    """Return metadata from the known data-item shape."""

    metadata = data_item.metadata

    if _is_mapping(metadata):
        return metadata

    return {}


def get_axis_transformation_matrices_metadata(data_item: Facade.DataItem) -> typing.Mapping[typing.Any, typing.Any] | None:
    """Return instrument axis transformation matrices from metadata."""

    axis_transformation_matrices = _metadata_get_path(
        get_data_item_metadata(data_item),
        AXIS_TRANSFORMATION_MATRICES_METADATA_PATH
    )

    if _is_mapping(axis_transformation_matrices):
        return axis_transformation_matrices

    return None


def _read_float_point(value: typing.Any) -> Geometry.FloatPoint | None:
    """Read a point/vector from sequence, y/x mapping, or 0/1 mapping."""

    if isinstance(value, typing.Sequence) and not isinstance(value, str) and len(value) >= 2:
        try:
            return Geometry.FloatPoint(y=float(value[0]), x=float(value[1]))
        except (TypeError, ValueError):
            return None

    if _is_mapping(value):
        for y_key, x_key in (("y", "x"), (0, 1), ("0", "1")):
            if y_key in value and x_key in value:
                try:
                    return Geometry.FloatPoint(
                        y=float(value[y_key]),
                        x=float(value[x_key])
                    )
                except (TypeError, ValueError):
                    return None

    return None


def _humanize_axis_id(axis_id: str) -> str:
    names = {
        "tv": "TV",
        "scan": "Scan",
        "gun": "Gun",
        "mc": "MC",
        "eels": "EELS",
        "correctoraxis": "Corrector Axis",
        "postsample": "Post Sample",
        "stageaxis": "Stage Axis",
        "stagetiltaxis": "Stage Tilt Axis"
    }

    normalized = axis_id.lower()

    if normalized in names:
        return names[normalized]

    return axis_id.replace("_", " ").replace("-", " ").title()


def _default_axis_color(axis_id: str) -> str:
    normalized = "".join(ch.lower() for ch in axis_id if ch.isalnum())

    match normalized:
        case "tv":
            return "#77FF1C"
        case "correctoraxis" | "corrector":
            return "#FC3A0F"
        case "eels" | "eelsaxis":
            return "#FF7B00"
        case "mc":
            return "#E64DFF"
        case "postsample" | "postsampleaxis" | "post":
            return "#CCB1B1"
        case "scan":
            return "#FF0090"
        case "stageaxis" | "stage":
            return "#00F2FF"
        case "stagetiltaxis" | "stagetilt":
            return "#EEFF00"
        case "gun":
            return "#4DA3FF"
        case _:
            return "#8E8E93"


def _as_float(value: typing.Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _metadata_key_sort_value(key: typing.Any) -> tuple[int, int | str]:
    """Return a stable sort key for scalar metadata entries.

    Numeric-looking keys are sorted numerically so keys such as 0, 1, 2, 10
    are ordered correctly. Non-numeric keys fall back to string ordering.
    """

    if isinstance(key, int) and not isinstance(key, bool):
        return 0, key

    key_text = str(key)

    try:
        return 0, int(key_text)
    except ValueError:
        return 1, key_text


def _read_matrix_from_mapping(value: typing.Mapping[typing.Any, typing.Any]) -> tuple[tuple[str, str], Geometry.FloatPoint, Geometry.FloatPoint] | None:
    """Read named basis vectors from one axis transformation matrix."""

    explicit_pairs: tuple[tuple[typing.Any, typing.Any], ...] = (
        ("x_vector", "y_vector"),
        ("x", "y"),
        ("X", "Y"),
        ("a", "b"),
        ("A", "B"),
        ("0", "1"),
        (0, 1)
    )

    for first_key, second_key in explicit_pairs:
        if first_key in value and second_key in value:
            first_vector = _read_float_point(value[first_key])
            second_vector = _read_float_point(value[second_key])

            if first_vector is not None and second_vector is not None:
                first_name = str(first_key)
                second_name = str(second_key)

                if first_name == "x_vector":
                    first_name = "x"

                if second_name == "y_vector":
                    second_name = "y"

                return (first_name, second_name), first_vector, second_vector

    vector_items: list[tuple[str, typing.Any]] = []

    for key, item_value in value.items():
        if isinstance(item_value, typing.Sequence) and not isinstance(item_value, str):
            vector_items.append((str(key), item_value))
        elif _is_mapping(item_value):
            vector_items.append((str(key), item_value))

    vectors: list[tuple[str, Geometry.FloatPoint]] = []

    for key, item_value in vector_items:
        point = _read_float_point(item_value)

        if point is not None:
            vectors.append((key, point))

    if len(vectors) >= 2:
        first_name, first_vector = vectors[0]
        second_name, second_vector = vectors[1]
        return (first_name, second_name), first_vector, second_vector

    scalar_values: list[float] = []

    for _key, item_value in sorted(value.items(), key=lambda item: _metadata_key_sort_value(item[0])):
        scalar = _as_float(item_value)

        if scalar is not None:
            scalar_values.append(scalar)

    if len(scalar_values) >= 4:
        return (
            ("x", "y"),
            Geometry.FloatPoint(y=scalar_values[0], x=scalar_values[1]),
            Geometry.FloatPoint(y=scalar_values[2], x=scalar_values[3])
        )

    return None


def _read_matrix_from_sequence(value: typing.Sequence[typing.Any]) -> tuple[tuple[str, str], Geometry.FloatPoint, Geometry.FloatPoint] | None:
    """Read basis vectors from one sequence-style axis transformation matrix."""

    if len(value) >= 2:
        first_vector = _read_float_point(value[0])
        second_vector = _read_float_point(value[1])

        if first_vector is not None and second_vector is not None:
            return ("x", "y"), first_vector, second_vector

    if len(value) < 4:
        return None

    y0 = _as_float(value[0])
    x0 = _as_float(value[1])
    y1 = _as_float(value[2])
    x1 = _as_float(value[3])

    if y0 is None or x0 is None or y1 is None or x1 is None:
        return None

    return (
        ("x", "y"),
        Geometry.FloatPoint(y=y0, x=x0),
        Geometry.FloatPoint(y=y1, x=x1)
    )


def _read_matrix_axis_metadata(axis_id: str, value: typing.Any) -> CoordinateTransform | None:
    """Read one axis from instrument.axis_transformation_matrices metadata."""

    if _is_mapping(value):
        read_result = _read_matrix_from_mapping(value)
    elif isinstance(value, typing.Sequence) and not isinstance(value, str):
        read_result = _read_matrix_from_sequence(value)
    else:
        return None

    if read_result is None:
        return None

    axis_type, x_vector, y_vector = read_result

    return CoordinateTransform(
        axis_id=axis_id,
        display_name=_humanize_axis_id(axis_id),
        axis_type=axis_type,
        origin=Geometry.FloatPoint(y=0.5, x=0.5),
        x_vector=x_vector,
        y_vector=y_vector,
        color=_default_axis_color(axis_id)
    )


def get_axes_from_axis_transformation_matrices(data_item: Facade.DataItem) -> typing.Mapping[str, CoordinateTransform]:
    """Return axes from instrument.axis_transformation_matrices metadata."""

    axis_transformation_matrices = get_axis_transformation_matrices_metadata(data_item)

    if axis_transformation_matrices is None:
        return types.MappingProxyType({})

    axes: dict[str, CoordinateTransform] = {}

    for raw_axis_id, axis_metadata in axis_transformation_matrices.items():
        axis_id = str(raw_axis_id)
        axis = _read_matrix_axis_metadata(axis_id, axis_metadata)

        if axis is not None:
            axes[axis.axis_id] = axis

    return types.MappingProxyType(axes)


def create_coordinate_transforms_from_data_item(data_item: Facade.DataItem) -> CoordinateTransforms | None:
    """Construct CoordinateTransforms from the known data-item metadata shape."""

    transforms = get_axes_from_axis_transformation_matrices(data_item)

    if not transforms:
        return None

    return CoordinateTransforms(
        transforms=transforms,
        metadata_source=AXIS_TRANSFORMATION_MATRICES_METADATA_PATH
    )


def create_coordinate_transforms_from_display_item(display_item: Facade.Display) -> CoordinateTransforms | None:
    """Construct CoordinateTransforms from the known display-item shape."""

    data_item = display_item.data_item

    if data_item is None:
        return None

    return create_coordinate_transforms_from_data_item(data_item)
