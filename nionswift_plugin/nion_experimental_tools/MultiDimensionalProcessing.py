import typing
import gettext
import copy
import math
import numpy
import numpy.typing

from nion.data import Core
from nion.data import DataAndMetadata
from nion.data import MultiDimensionalProcessing
from nion.swift.model import Symbolic
from nion.swift.model import DataItem
from nion.swift import Inspector
from nion.swift import DocumentController
from nion.swift import Application as ApplicationModule
from nion.ui import Declarative
from nion.utils import Registry
from nion.utils import Observable
from nion.swift import Facade

try:
    import mkl
except ModuleNotFoundError:
    _has_mkl = False
else:
    _has_mkl = True

_ = gettext.gettext

_DataArrayType = numpy.typing.NDArray[typing.Any]


computation_settings: typing.Dict[str, typing.Dict[str, typing.Any]] = {}


class MultiDimensionalProcessingComputation(Symbolic.ComputationHandlerLike):

    @staticmethod
    def guess_starting_axis(xdata: DataAndMetadata.DataAndMetadata, **kwargs: typing.Any) -> str:
        raise NotImplementedError()


def function_crop_along_axis(input_xdata: DataAndMetadata.DataAndMetadata, crop_axis: str, crop_graphic: typing.Optional[Facade.Graphic] = None, **kwargs: typing.Any) -> DataAndMetadata.DataAndMetadata:
    if crop_axis == "collection":
        assert input_xdata.is_collection
        crop_axis_indices = list(input_xdata.collection_dimension_indexes)
    elif crop_axis == "sequence":
        assert input_xdata.is_sequence
        assert input_xdata.sequence_dimension_index is not None
        crop_axis_indices = [input_xdata.sequence_dimension_index]
    else:
        crop_axis_indices = list(input_xdata.datum_dimension_indexes)

    crop_bounds_left = typing.cast(int, None)
    crop_bounds_right = typing.cast(int, None)
    crop_bounds_top = typing.cast(int, None)
    crop_bounds_bottom = typing.cast(int, None)
    if crop_graphic is not None:
        if len(crop_axis_indices) == 2:
            bounds = crop_graphic.bounds
            assert numpy.ndim(bounds) == 2
            crop_bounds_left = int(bounds[0][1] * input_xdata.data_shape[crop_axis_indices[1]])
            crop_bounds_right = int((bounds[0][1] + bounds[1][1]) * input_xdata.data_shape[crop_axis_indices[1]])
            crop_bounds_top = int(bounds[0][0] * input_xdata.data_shape[crop_axis_indices[0]])
            crop_bounds_bottom = int((bounds[0][0] + bounds[1][0]) * input_xdata.data_shape[crop_axis_indices[0]])
        else:
            # Use different name to make typing happy
            bounds_1d = crop_graphic.interval
            assert numpy.ndim(bounds_1d) == 1
            crop_bounds_left = int(bounds_1d[0] * input_xdata.data_shape[crop_axis_indices[0]])
            crop_bounds_right = int(bounds_1d[1] * input_xdata.data_shape[crop_axis_indices[0]])
    else:
        crop_bounds_left = typing.cast(int, kwargs.get("crop_bounds_left"))
        crop_bounds_right = typing.cast(int, kwargs.get("crop_bounds_right"))
        crop_bounds_top = typing.cast(int, kwargs.get("crop_bounds_top"))
        crop_bounds_bottom = typing.cast(int, kwargs.get("crop_bounds_bottom"))

    if len(crop_axis_indices) == 2:
        crop_bounds_left = int(crop_bounds_left)
        crop_bounds_right = int(crop_bounds_right)
        crop_bounds_top = int(crop_bounds_top)
        crop_bounds_bottom = int(crop_bounds_bottom)
        crop_bounds_left = max(0, crop_bounds_left)
        crop_bounds_top = max(0, crop_bounds_top)
        if crop_bounds_right == -1:
            crop_bounds_right = typing.cast(int, None)
        else:
            crop_bounds_right = min(crop_bounds_right, input_xdata.data_shape[crop_axis_indices[1]])
        if crop_bounds_bottom == -1:
            crop_bounds_bottom = typing.cast(int, None)
        else:
            crop_bounds_bottom = min(crop_bounds_bottom, input_xdata.data_shape[crop_axis_indices[0]])
    else:
        crop_bounds_left = int(crop_bounds_left)
        crop_bounds_right = int(crop_bounds_right)
        crop_bounds_left = max(0, crop_bounds_left)
        if crop_bounds_right == -1:
            crop_bounds_right = typing.cast(int, None)
        else:
            crop_bounds_right = min(crop_bounds_right, input_xdata.data_shape[crop_axis_indices[0]])

    crop_slices: typing.Tuple[slice, ...] = tuple()
    for i in range(len(input_xdata.data_shape)):
        if len(crop_axis_indices) == 1 and i == crop_axis_indices[0]:
            crop_slices += (slice(crop_bounds_left, crop_bounds_right),)
        elif len(crop_axis_indices) == 2 and i == crop_axis_indices[0]:
            crop_slices += (slice(crop_bounds_top, crop_bounds_bottom),)
        elif len(crop_axis_indices) == 2 and i == crop_axis_indices[1]:
            crop_slices += (slice(crop_bounds_left, crop_bounds_right),)
        else:
            crop_slices += (slice(None),)

    return input_xdata[crop_slices]


class IntegrateAlongAxis(MultiDimensionalProcessingComputation):
    computation_id = "nion.integrate_along_axis"
    label = _("Integrate")
    inputs = {"input_data_item": {"label": _("Input data item"), "data_type": "xdata"},
              "axes_description": {"label": _("Integrate these axes")},
              # "sub_integration_axes": {"label": _("Select which of the above axes to integrate"), "entity_id": "sub_axis_choice"},
              "integration_graphic": {"label": _("Integration mask")},
              }
    outputs = {"integrated": {"label": _("Integrated")},
               }

    def __init__(self, computation: typing.Any, **kwargs: typing.Any) -> None:
        self.computation = computation

    @staticmethod
    def guess_starting_axis(xdata: DataAndMetadata.DataAndMetadata, *, graphic: typing.Optional[Facade.Graphic] = None, **kwargs: typing.Any) -> str:
        # If we have an integrate graphic we probably want to integrate the displayed dimensions
        if graphic:
            # For collections with 1D data we see the collection dimensions
            if xdata.is_collection and xdata.datum_dimension_count == 1:
                integration_axes = "collection"
            # Otherwise we see the data dimensions
            else:
                integration_axes = "data"
        # If not, use some generic rules
        else:
            if xdata.is_sequence:
                integration_axes = "sequence"
            elif xdata.is_collection and xdata.datum_dimension_count == 1:
                integration_axes = "collection"
            else:
                integration_axes = "data"

        return integration_axes

    def execute(self, *, input_data_item: Facade.DataItem, axes_description: str, integration_graphic: typing.Optional[Facade.Graphic]=None, **kwargs: typing.Any) -> None: # type: ignore
        assert input_data_item.xdata is not None
        input_xdata: DataAndMetadata.DataAndMetadata = input_data_item.xdata
        split_description = axes_description.split("-")
        integration_axes = split_description[0]
        sub_integration_axes = split_description[1] if len(split_description) > 1 else "all"

        if integration_axes == "collection":
            assert input_xdata.is_collection
            integration_axis_indices = list(input_xdata.collection_dimension_indexes)
            if sub_integration_axes != "all" and input_xdata.collection_dimension_count > 1:
                index = ["first", "second"].index(sub_integration_axes)
                integration_axis_indices = [integration_axis_indices[index]]
        elif integration_axes == "sequence":
            assert input_xdata.is_sequence
            assert input_xdata.sequence_dimension_index is not None
            integration_axis_indices = [input_xdata.sequence_dimension_index]
        else:
            integration_axis_indices = list(input_xdata.datum_dimension_indexes)
            if sub_integration_axes != "all" and input_xdata.datum_dimension_count > 1:
                index = ["first", "second"].index(sub_integration_axes)
                integration_axis_indices = [integration_axis_indices[index]]

        integration_mask: typing.Optional[_DataArrayType] = None
        if integration_graphic is not None:
            integration_axis_shape = tuple((input_xdata.data_shape[i] for i in integration_axis_indices))
            integration_mask = integration_graphic.mask_xdata_with_shape(integration_axis_shape).data

        self.__result_xdata = MultiDimensionalProcessing.function_integrate_along_axis(input_xdata, tuple(integration_axis_indices), integration_mask)
        return None


    def commit(self) -> None:
        self.computation.set_referenced_xdata("integrated", self.__result_xdata)
        return None


class MeasureShifts(MultiDimensionalProcessingComputation):
    computation_id = "nion.measure_shifts"
    label = _("Measure Shifts")
    inputs = {"input_data_item": {"label": _("Input data item"), "data_type": "xdata"},
              "axes_description": {"label": _("Measure shift along this axis")},
              "reference_index": {"label": _("Reference index for shifts")},
              "relative_shifts": {"label": _("Measure shifts relative to previous slice")},
              "max_shift": {"label": _("Max shift between consecutive frames (in pixels, <= 0 to disable)")},
              "bounds_graphic": {"label": _("Shift bounds")},
              }
    outputs = {"shifts": {"label": _("Shifts")},
               }

    def __init__(self, computation: typing.Any, **kwargs: typing.Any) -> None:
        self.computation = computation

    @staticmethod
    def guess_starting_axis(xdata: DataAndMetadata.DataAndMetadata, *, graphic: typing.Optional[Facade.Graphic] = None, **kwargs: typing.Any) -> str:
        # If we have a bound graphic we probably want to align the displayed dimensions
        if graphic:
            # For collections with 1D data we see the collection dimensions
            if xdata.is_collection and xdata.datum_dimension_count == 1:
                shift_axis = 'collection'
            # Otherwise we see the data dimensions
            else:
                shift_axis = 'data'
        # If not, use some generic rules
        else:
            shift_axis = 'data'

            if xdata.is_collection and xdata.datum_dimension_count == 1:
                shift_axis = 'collection'

        return shift_axis

    def execute(self, *, input_data_item: Facade.DataItem, axes_description: str, reference_index: typing.Optional[int] = None, relative_shifts: bool=True, max_shift: int=0, bounds_graphic: typing.Optional[Facade.Graphic]=None, **kwargs: typing.Any) -> None: # type: ignore
        input_xdata = input_data_item.xdata
        assert input_xdata is not None
        bounds: typing.Optional[typing.Union[typing.Tuple[float, float], typing.Tuple[typing.Tuple[float, float], typing.Tuple[float, float]]]] = None
        if bounds_graphic is not None:
            if bounds_graphic.graphic_type == "interval-graphic":
                bounds = bounds_graphic.interval
            else:
                bounds = bounds_graphic.bounds
        split_description = axes_description.split("-")
        shift_axis = split_description[0]
        max_shift_ = max_shift if max_shift > 0 else None
        reference_index = reference_index if not relative_shifts else None

        if shift_axis == "collection":
            assert input_xdata.is_collection
            shift_axis_indices = list(input_xdata.collection_dimension_indexes)
        elif shift_axis == "sequence":
            assert input_xdata.is_sequence
            assert input_xdata.sequence_dimension_index is not None
            shift_axis_indices = [input_xdata.sequence_dimension_index]
        elif shift_axis == "data":
            shift_axis_indices = list(input_xdata.datum_dimension_indexes)
        else:
            raise ValueError(f"Unknown shift axis: '{shift_axis}'.")

        self.__shifts_xdata = MultiDimensionalProcessing.function_measure_multi_dimensional_shifts(input_xdata, tuple(shift_axis_indices), reference_index=reference_index, bounds=bounds, max_shift=max_shift_)
        settings_dict = computation_settings.setdefault(self.computation._computation.processing_id, dict())
        settings_dict["axes_description"] = axes_description
        # Reference index cannot be None, otherwise the computation will fail to run the next time
        settings_dict["reference_index"] = reference_index or 0
        settings_dict["relative_shifts"] = relative_shifts
        settings_dict["max_shift"] = max_shift
        return None

    def commit(self) -> None:
        self.computation.set_referenced_xdata("shifts", self.__shifts_xdata)
        return None


def measure_shifts(api: Facade.API_1, window: Facade.DocumentWindow, data_item: Facade.DataItem, bounds_graphic: Facade.Graphic | None, shift_axis: str) -> Facade.DataItem:
    result_data_item = api.library.create_data_item()

    settings_dict = computation_settings.get("nion.measure_shifts", dict())
    inputs = {"input_data_item": {"object": data_item, "type": "data_source"},
              "axes_description": settings_dict.get("axes_description", shift_axis),
              "reference_index": settings_dict.get("reference_index", 0),
              "relative_shifts": settings_dict.get("relative_shifts", False),
              "max_shift": settings_dict.get("max_shift", 0),
              }
    if bounds_graphic:
        inputs["bounds_graphic"] = bounds_graphic

    api.library.create_computation("nion.measure_shifts",
                                   inputs=inputs,
                                   outputs={"shifts": result_data_item})
    window.display_data_item(result_data_item)

    return result_data_item


class MeasureShiftsMenuItemDelegate:
    def __init__(self, api: Facade.API_1) -> None:
        self.__api = api
        self.menu_id = "multi_dimensional_processing_menu"
        self.menu_name = _("Multi-Dimensional Processing")
        self.menu_before_id = "window_menu"

    @property
    def menu_item_name(self) -> str:
        return _("Measure shifts")

    def menu_item_execute(self, window: Facade.DocumentWindow) -> None:
        data_item = window.target_data_item

        if not data_item or not data_item.xdata:
            return None

        bounds_graphic = None
        if data_item.display.selected_graphics:
            for graphic in data_item.display.selected_graphics:
                if graphic.graphic_type in {"rect-graphic", "interval-graphic"}:
                    bounds_graphic = graphic

        shift_axis = MeasureShifts.guess_starting_axis(data_item.xdata, graphic=bounds_graphic)

        measure_shifts(self.__api, window, data_item, bounds_graphic, shift_axis)
        return None


class ApplyShifts(MultiDimensionalProcessingComputation):
    computation_id = "nion.apply_shifts"
    label = _("Apply Shifts")
    inputs = {"input_data_item": {"label": _("Input data item"), "data_type": "xdata"},
              "shifts_data_item": {"label": _("Shifts data item"), "data_type": "xdata"},
              "axes_description": {"label": _("Apply shift along this axis")},
              "crop_to_valid": {"label": _("Crop result to valid area")},
              }
    outputs = {"shifted": {"label": _("Shifted")},
               }

    def __init__(self, computation: typing.Any, **kwargs: typing.Any) -> None:
        self.computation = computation

    @staticmethod
    def guess_starting_axis(xdata: DataAndMetadata.DataAndMetadata, *, shifts_xdata: typing.Optional[DataAndMetadata.DataAndMetadata] = None, **kwargs: typing.Any) -> str:
        assert shifts_xdata is not None
        shifts_shape = shifts_xdata.data.shape
        data_shape = xdata.data.shape
        for i in range(len(data_shape) - len(shifts_shape) + 1):
            if data_shape[i:i+len(shifts_shape)] == shifts_shape:
                shifts_start_axis = i
                shifts_end_axis = i + len(shifts_shape)
                break
            elif data_shape[i:i+len(shifts_shape)-1] == shifts_shape[:-1] and shifts_shape[-1] == 2:
                shifts_start_axis = i
                shifts_end_axis = i + len(shifts_shape) - 1
                break
        else:
            raise ValueError("Did not find any axis matching the shifts shape.")

        shifts_indexes = range(shifts_start_axis, shifts_end_axis)
        shift_axis_points = {"collection": 0, "sequence": 0, "data": 0}
        if xdata.is_collection:
            collection_dimension_indexes = xdata.collection_dimension_indexes
            cond = False
            for ind in collection_dimension_indexes:
                if ind in shifts_indexes:
                    cond = True
            if not cond and (len(collection_dimension_indexes) == 1 or len(collection_dimension_indexes) == shifts_shape[-1]):
                shift_axis_points["collection"] += 1

        if xdata.is_sequence:
            sequence_dimension_index = xdata.sequence_dimension_index
            if not sequence_dimension_index in shifts_indexes:
                shift_axis_points["sequence"] += 1

        datum_dimension_indexes = xdata.datum_dimension_indexes
        cond = False
        for ind in datum_dimension_indexes:
            if ind in shifts_indexes:
                cond = True
        if not cond and (len(datum_dimension_indexes) == 1 or len(datum_dimension_indexes) == shifts_shape[-1]):
            shift_axis_points["data"] += 1

        if shift_axis_points["collection"] > 0:
            shift_axis = "collection"
        elif shift_axis_points["data"] > 0:
            shift_axis = "data"
        elif shift_axis_points["sequence"] > 0:
            shift_axis = "sequence"
        else:
            shift_axis = "data"

        return shift_axis

    def execute(self, *, input_data_item: Symbolic.DataSource, shifts_data_item: Symbolic.DataSource, axes_description: str, crop_to_valid: bool) -> None: # type: ignore
        input_xdata = input_data_item.xdata
        assert input_xdata is not None
        assert shifts_data_item.xdata is not None
        assert shifts_data_item.data is not None
        shifts_shape = shifts_data_item.xdata.data_shape
        # Handle the special case of shifts created by "AlignImageSequence" here: This computation calculates the shifts
        # for a 1D collection or a sequence of 2D data and transposes the result so that Swift can display it as a
        # line plot. Try to detect that case and transpose back.
        if len(shifts_shape) == 2 and shifts_shape[0] == 2 and shifts_shape[-1] == input_xdata.data_shape[0]:
            # HDF5 datasets don't implement .T, so convert to real numpy array first
            shifts = numpy.asarray(shifts_data_item.data).T
        else:
            shifts  = shifts_data_item.data
        split_description = axes_description.split("-")
        shift_axis = split_description[0]
        if shift_axis == "collection":
            assert input_xdata.is_collection
            if input_xdata.collection_dimension_count == 2:
                assert shifts.shape[-1] == 2
            shift_axis_indices = list(input_xdata.collection_dimension_indexes)
        elif shift_axis == "sequence":
            assert input_xdata.is_sequence
            assert input_xdata.sequence_dimension_index is not None
            shift_axis_indices = [input_xdata.sequence_dimension_index]
        elif shift_axis == "data":
            if input_xdata.datum_dimension_count == 2:
                assert shifts.shape[-1] == 2
            shift_axis_indices = list(input_xdata.datum_dimension_indexes)
        else:
            raise ValueError(f"Unknown shift axis: '{shift_axis}'.")
        # Like this we directly write to the underlying storage and don't have to cache everything in memory first
        result_data_item = self.computation.get_result('shifted')
        if result_data_item.xdata.data_shape == input_xdata.data_shape:
            MultiDimensionalProcessing.function_apply_multi_dimensional_shifts(input_xdata, shifts, tuple(shift_axis_indices), out=result_data_item.xdata)
            result_xdata = result_data_item.xdata
        else: # But if the shape in the data item does not match the input's shape we cannot do that
            result_xdata = MultiDimensionalProcessing.function_apply_multi_dimensional_shifts(input_xdata, shifts, tuple(shift_axis_indices))
        if crop_to_valid:
            shift_axis_shape = [input_xdata.data_shape[i] for i in range(len(input_xdata.data_shape)) if i in shift_axis_indices]
            valid_area = calculate_valid_area_from_shifts(tuple(shift_axis_shape), shifts)
            slice_tuple: typing.Tuple[slice, ...] = tuple()
            k = 0
            for i in range(len(input_xdata.data_shape)):
                if i in shift_axis_indices:
                    slice_tuple += (slice(valid_area[k], valid_area[k+2]),)
                    k += 1
                else:
                    slice_tuple += (slice(0, None),)
            self.__result_xdata = result_xdata[slice_tuple]
        else:
            self.__result_xdata = result_xdata
        settings_dict = computation_settings.setdefault(self.computation._computation.processing_id, dict())
        settings_dict["crop_to_valid"] = crop_to_valid
        return None

    def commit(self) -> None:
        self.computation.set_referenced_xdata("shifted", self.__result_xdata)
        return None


def apply_shifts(api: Facade.API_1, window: Facade.DocumentWindow, input_di: Facade.DataItem, shifts_di: Facade.DataItem, shift_axis: str) -> Facade.DataItem:
    data_item = DataItem.DataItem(large_format=True)
    window._document_controller.document_model.append_data_item(data_item)
    input_xdata = input_di.xdata
    assert input_xdata
    data_item.reserve_data(data_shape=input_xdata.data_shape, data_dtype=input_xdata.data_dtype, data_descriptor=input_xdata.data_descriptor)
    data_item.dimensional_calibrations = input_xdata.dimensional_calibrations
    data_item.intensity_calibration = input_xdata.intensity_calibration
    data_item.metadata = copy.deepcopy(input_xdata.metadata)
    result_data_item = Facade.DataItem(data_item)

    settings_dict = computation_settings.get("nion.align_and_integrate_image_sequence", dict())
    inputs = {"input_data_item": {"object": input_di, "type": "data_source"},
              "shifts_data_item": {"object": shifts_di, "type": "data_source"},
              "axes_description": shift_axis,
              "crop_to_valid": settings_dict.get("crop_to_valid", False)
              }

    api.library.create_computation("nion.apply_shifts",
                                   inputs=inputs,
                                   outputs={"shifted": result_data_item})
    window.display_data_item(result_data_item)
    return result_data_item


class ApplyShiftsMenuItemDelegate:
    def __init__(self, api: Facade.API_1) -> None:
        self.__api = api
        self.menu_id = "multi_dimensional_processing_menu"
        self.menu_name = _("Multi-Dimensional Processing")
        self.menu_before_id = "window_menu"

    @property
    def menu_item_name(self) -> str:
        return _("Apply shifts")

    def menu_item_execute(self, window: Facade.DocumentWindow) -> None:
        selected_display_items = window._document_controller._get_two_data_sources()
        error_msg = "Select a multi-dimensional data item and another one that contains shifts that can be broadcast to the shape of the first one."
        assert selected_display_items[0][0] is not None, error_msg
        assert selected_display_items[1][0] is not None, error_msg
        display_1 = selected_display_items[0][0]
        display_2 = selected_display_items[1][0]
        di_1 = display_1.data_item
        di_2 = display_2.data_item
        assert di_1 is not None, error_msg
        assert di_2 is not None, error_msg
        assert di_1.xdata is not None, error_msg
        assert di_2.xdata is not None, error_msg
        assert di_1.data is not None, error_msg
        assert di_2.data is not None, error_msg

        if len(di_1.data.shape) < len(di_2.data.shape):
            shifts_di = self.__api._new_api_object(di_1)
            shifts_display = display_1
            input_di = self.__api._new_api_object(di_2)
        elif len(di_2.data.shape) < len(di_1.data.shape):
            shifts_di = self.__api._new_api_object(di_2)
            shifts_display = display_2
            input_di = self.__api._new_api_object(di_1)
        else:
            raise ValueError(error_msg)

        # Handle the special case of shifts created by "AlignImageSequence" here: This computation calculates the shifts
        # for a 1D collection or a sequence of 2D data and transposes the result so that Swift can display it as a
        # line plot. Try to detect that case and transpose back for guessing the starting axis.
        shifts_shape = shifts_di.xdata.data_shape
        if shifts_display.display_type == "line_plot" and len(shifts_shape) == 2 and shifts_shape[0] == 2 and shifts_shape[-1] == input_di.xdata.data_shape[0]:
            shifts_xdata = Core.function_transpose_flip(shifts_di.xdata, transpose=True, flip_v=False, flip_h=False)
        else:
            shifts_xdata = shifts_di.xdata

        shift_axis = ApplyShifts.guess_starting_axis(input_di.xdata, shifts_xdata=shifts_xdata)

        apply_shifts(self.__api, window, input_di, shifts_di, shift_axis)

        return None


class IntegrateAlongAxisMenuItemDelegate:
    def __init__(self, api: Facade.API_1) -> None:
        self.__api = api
        self.menu_id = "multi_dimensional_processing_menu"
        self.menu_name = _("Multi-Dimensional Processing")
        self.menu_before_id = "window_menu"

    @property
    def menu_item_name(self) -> str:
        return _("Integrate axis")

    def menu_item_execute(self, window: Facade.DocumentWindow) -> None:
        selected_data_item = window.target_data_item

        if not selected_data_item or not selected_data_item.xdata:
            return None

        integrate_graphic = None
        if selected_data_item.display.selected_graphics:
            integrate_graphic = selected_data_item.display.selected_graphics[0]

        integration_axes = IntegrateAlongAxis.guess_starting_axis(selected_data_item.xdata, graphic=integrate_graphic)

        result_data_item = self.__api.library.create_data_item()

        inputs: typing.MutableMapping[str, typing.Any]
        inputs = {"input_data_item": {"object": selected_data_item, "type": "data_source"},
                  "axes_description": integration_axes + "-all"
                  }
        if integrate_graphic:
            inputs["integration_graphic"] = integrate_graphic

        self.__api.library.create_computation("nion.integrate_along_axis",
                                              inputs=inputs,
                                              outputs={"integrated": result_data_item})
        window.display_data_item(result_data_item)
        return None


class Crop(MultiDimensionalProcessingComputation):
    computation_id = "nion.crop_multi_dimensional"
    label = _("Multidimensional Crop")
    inputs = {"input_data_item": {"label": _("Input data item"), "data_type": "xdata"},
              "axes_description": {"label": _("Crop along this axis")},
              "crop_graphic": {"label": _("Crop bounds")},
              "crop_bounds_left": {"label": _("Crop bound left")},
              "crop_bounds_right": {"label": _("Crop bound right")},
              "crop_bounds_top": {"label": _("Crop bound top")},
              "crop_bounds_bottom": {"label": _("Crop bound bottom")}}
    outputs = {"cropped": {"label": _("Cropped")}}

    def __init__(self, computation: typing.Any, **kwargs: typing.Any) -> None:
        self.computation = computation

    @staticmethod
    def guess_starting_axis(xdata: DataAndMetadata.DataAndMetadata, graphic: typing.Optional[Facade.Graphic] = None, **kwargs: typing.Any) -> str:
        # If we have a crop graphic we probably want to crop the displayed dimensions
        if graphic:
            # For collections with 1D data we see the collection dimensions
            if xdata.is_collection and xdata.datum_dimension_count == 1:
                crop_axes = "collection"
            # Otherwise we see the data dimensions
            else:
                crop_axes = "data"
        # If not, use some generic rules
        else:
            if xdata.is_collection and xdata.datum_dimension_count == 1:
                crop_axes = "collection"
            else:
                crop_axes = "data"

        return crop_axes

    def execute(self, *, input_data_item: Facade.DataItem, axes_description: str, crop_graphic: typing.Optional[Facade.Graphic]=None, **kwargs: typing.Any) -> None: # type: ignore
        assert input_data_item.xdata is not None
        input_xdata: DataAndMetadata.DataAndMetadata = input_data_item.xdata
        split_description = axes_description.split("-")
        crop_axis = split_description[0]
        self.__result_xdata = function_crop_along_axis(input_xdata, crop_axis, crop_graphic=crop_graphic, **kwargs)
        return None

    def commit(self) -> None:
        self.computation.set_referenced_xdata("cropped", self.__result_xdata)
        return None


def crop_multi_dimensional(api: Facade.API_1, window: Facade.DocumentWindow, data_item: Facade.DataItem, crop_graphic: Facade.Graphic | None, crop_axes: str) -> Facade.DataItem:
    result_data_item = api.library.create_data_item()

    inputs: typing.MutableMapping[str, typing.Any]
    inputs = {"input_data_item": {"object": data_item, "type": "data_source"},
              "axes_description": crop_axes
              }
    if crop_graphic:
        inputs["crop_graphic"] = crop_graphic
    else:
        inputs["crop_bounds_left"] = 0
        inputs["crop_bounds_right"] = -1
        inputs["crop_bounds_top"] = 0
        inputs["crop_bounds_bottom"] = -1

    api.library.create_computation("nion.crop_multi_dimensional",
                                   inputs=inputs,
                                   outputs={"cropped": result_data_item})
    window.display_data_item(result_data_item)

    return result_data_item


class CropMenuItemDelegate:
    def __init__(self, api: Facade.API_1) -> None:
        self.__api = api
        self.menu_id = "multi_dimensional_processing_menu"
        self.menu_name = _("Multi-Dimensional Processing")
        self.menu_before_id = "window_menu"

    @property
    def menu_item_name(self) -> str:
        return _("Crop")

    def menu_item_execute(self, window: Facade.DocumentWindow) -> None:
        selected_data_item = window.target_data_item

        if not selected_data_item or not selected_data_item.xdata:
            return None

        crop_graphic = None
        if selected_data_item.display.selected_graphics:
            for graphic in selected_data_item.display.selected_graphics:
                if graphic.graphic_type in {"rect-graphic", "interval-graphic"}:
                    crop_graphic = graphic
                    break

        crop_axes = Crop.guess_starting_axis(selected_data_item.xdata, graphic=crop_graphic)

        crop_multi_dimensional(self.__api, window, selected_data_item, crop_graphic, crop_axes)

        return None


class MakeTableau(Symbolic.ComputationHandlerLike):
    computation_id = "nion.make_tableau_image"
    label = _("Tableau")
    inputs = {"input_data_item": {"label": _("Input data item"), "data_type": "xdata"},
              "scale": {"label": _("Scale")}}
    outputs = {"tableau": {"label": "Tableau"}}

    def __init__(self, computation: typing.Any, **kwargs: typing.Any) -> None:
        self.computation = computation
        self.__result_xdata: typing.Optional[DataAndMetadata.DataAndMetadata] = None

    def execute(self, *, input_data_item: Facade.DataItem, scale: float) -> None: # type: ignore
        assert input_data_item.xdata is not None
        self.__result_xdata = MultiDimensionalProcessing.function_make_tableau_image(input_data_item.xdata, scale)
        return None

    def commit(self) -> None:
        self.computation.set_referenced_xdata("tableau", self.__result_xdata)
        self.__result_xdata = None
        return None


def tableau(api: Facade.API_1, window: Facade.DocumentWindow, data_item: Facade.DataItem, scale: float) -> Facade.DataItem:
    inputs = {"input_data_item": {"object": data_item, "type": "data_source"}, "scale": scale}

    result_data_item = api.library.create_data_item()

    api.library.create_computation("nion.make_tableau_image",
                                   inputs=inputs,
                                   outputs={"tableau": result_data_item})

    window.display_data_item(result_data_item)

    return result_data_item


class MakeTableauMenuItemDelegate:
    def __init__(self, api: Facade.API_1) -> None:
        self.__api = api
        self.menu_id = "multi_dimensional_processing_menu"
        self.menu_name = _("Multi-Dimensional Processing")
        self.menu_before_id = "window_menu"

    @property
    def menu_item_name(self) -> str:
        return _("Make tableau image")

    def menu_item_execute(self, window: Facade.DocumentWindow) -> None:
        selected_data_item = window.target_data_item
        error_msg = "Select one data item that contains a sequence or collection of two-dimensional data."
        assert selected_data_item is not None, error_msg
        assert selected_data_item.xdata is not None, error_msg
        assert selected_data_item.xdata.is_sequence or selected_data_item.xdata.is_collection, error_msg
        assert selected_data_item.xdata.datum_dimension_count == 2, error_msg

        # Limit the maximum size of the result to something sensible:
        max_result_pixels = 8192
        scale = 1.0
        if selected_data_item.xdata.is_collection:
            scale = min(1.0, max_result_pixels / (numpy.sqrt(numpy.prod(selected_data_item.xdata.collection_dimension_shape)) *
                                                  numpy.sqrt(numpy.prod(selected_data_item.xdata.datum_dimension_shape))))
        elif selected_data_item.xdata.is_sequence:
            scale = min(1.0, max_result_pixels / (numpy.sqrt(numpy.prod(selected_data_item.xdata.sequence_dimension_shape)) *
                                                  numpy.sqrt(numpy.prod(selected_data_item.xdata.datum_dimension_shape))))

        tableau(self.__api, window, selected_data_item, scale)

        return None


def calculate_valid_area_from_shifts(input_shape: typing.Tuple[int, ...], shifts: numpy.typing.NDArray[numpy.float64]) -> typing.Tuple[int, int, int, int]:
    if len(input_shape) == 2:
        min_y = numpy.amin(shifts[..., 0])
        max_y = numpy.amax(shifts[..., 0])
        min_x = numpy.amin(shifts[..., 1])
        max_x = numpy.amax(shifts[..., 1])
        valid_area_tlbr = [0.0, 0.0, input_shape[0], input_shape[1]]
        if min_y < 0:
            valid_area_tlbr[2] += min_y
        if max_y > 0:
            valid_area_tlbr[0] = max_y
        if min_x < 0:
            valid_area_tlbr[3] += min_x
        if max_x > 0:
            valid_area_tlbr[1] = max_x

        top = min(input_shape[0], max(0, math.ceil(valid_area_tlbr[0])))
        left = min(input_shape[1], max(0, math.ceil(valid_area_tlbr[1])))
        bottom = min(input_shape[0], max(top, math.floor(valid_area_tlbr[2])))
        right = min(input_shape[1], max(left, math.floor(valid_area_tlbr[3])))
    elif len(input_shape) == 1:
        min_y = numpy.amin(shifts)
        max_y = numpy.amax(shifts)

        valid_area_t_b = [0.0, input_shape[0]]

        if min_y < 0:
            valid_area_t_b[1] += min_y
        if max_y > 0:
            valid_area_t_b[0] = max_y

        top = min(input_shape[0], max(0, math.ceil(valid_area_t_b[0])))
        left = None
        bottom = min(input_shape[0], max(top, math.floor(valid_area_t_b[1])))
        right = None
    else:
        raise ValueError("Only shifts with one or two axis are supported.")

    return top, typing.cast(int, left), bottom, typing.cast(int, right)


class AlignImageSequence(Symbolic.ComputationHandlerLike):
    computation_id = "nion.align_and_integrate_image_sequence"
    label = _("Align/Integrate")
    inputs = {"input_data_item": {"label": _("Input data item"), "data_type": "xdata"},
              "reference_index": {"label": _("Reference index for shifts")},
              "relative_shifts": {"label": _("Measure shifts relative to previous slice")},
              "max_shift": {"label": _("Max shift between consecutive frames (in pixels, <= 0 to disable)")},
              "show_shifted_output": {"label": _("Show shifted output")},
              "crop_to_valid": {"label": _("Crop result to valid area")},
              "bounds_graphic": {"label": _("Shift bounds")},
              }
    outputs = {"shifts": {"label": _("Shifts")},
               "integrated_sequence": {"label": _("Integrated Sequence")},
               "shifted_data": {"label": _("Aligned sequence")}
               }

    def __init__(self, computation: typing.Any, **kwargs: typing.Any) -> None:
        self.computation = computation

    def execute(self, *, input_data_item: Symbolic.DataSource, reference_index: typing.Optional[int] = None, relative_shifts: bool=True, max_shift: int=0, show_shifted_output: bool = False, crop_to_valid: bool = True, bounds_graphic: typing.Optional[Facade.Graphic]=None) -> None: # type: ignore
        input_xdata = input_data_item.xdata
        assert input_xdata is not None
        bounds = None
        if bounds_graphic is not None:
            bounds = bounds_graphic.bounds
        max_shift_ = max_shift if max_shift > 0 else None
        reference_index = reference_index if not relative_shifts else None
        shifts_axes = tuple(input_xdata.datum_dimension_indexes)
        assert len(shifts_axes) == 2, "This computation only works for sequences and collections of 2D data."
        shifts_xdata = MultiDimensionalProcessing.function_measure_multi_dimensional_shifts(input_xdata, shifts_axes, reference_index=reference_index, bounds=bounds, max_shift=max_shift_)
        self.__valid_area_tlbr: typing.Optional[typing.Tuple[int, int, int, int]] = calculate_valid_area_from_shifts(input_xdata.datum_dimension_shape, shifts_xdata.data)
        self.__shifts_xdata = Core.function_transpose_flip(shifts_xdata, transpose=True, flip_v=False, flip_h=False)
        aligned_input_xdata = MultiDimensionalProcessing.function_apply_multi_dimensional_shifts(input_xdata, shifts_xdata.data, shifts_axes)
        assert aligned_input_xdata is not None
        aligned_input_xdata._set_metadata(input_xdata.metadata)
        if crop_to_valid:
            top, left, bottom, right = self.__valid_area_tlbr
            aligned_input_xdata = aligned_input_xdata[..., top:bottom, left:right]
            self.__valid_area_tlbr = None
        self.__integrated_input_xdata = Core.function_sum(aligned_input_xdata, axis=0)
        self.__integrated_input_xdata._set_metadata(input_xdata.metadata)
        if show_shifted_output:
            self.__shifted_xdata = aligned_input_xdata
        settings_dict = computation_settings.setdefault(self.computation._computation.processing_id, dict())
        # Reference index cannot be None, otherwise the computation will fail to run the next time we start it
        settings_dict["reference_index"] = reference_index or 0
        settings_dict["relative_shifts"] = relative_shifts
        settings_dict["max_shift"] = max_shift
        settings_dict["show_shifted_output"] = show_shifted_output
        settings_dict["crop_to_valid"] = crop_to_valid
        return None

    def commit(self) -> None:
        self.computation.set_referenced_xdata("shifts", self.__shifts_xdata)
        self.computation.set_referenced_xdata("integrated_sequence", self.__integrated_input_xdata)
        integrated_data_item = self.computation.get_result("integrated_sequence")
        for graphic in integrated_data_item.graphics:
            if graphic.label == "Valid Area":
                integrated_data_item.remove_region(graphic)
                break
        shape = integrated_data_item.data.shape
        valid_area = self.__valid_area_tlbr
        if valid_area is not None:
            rectangle_bounds = (max(0.0, min(1.0, (valid_area[0] + (valid_area[2] - valid_area[0]) * 0.5) / shape[0])),
                                max(0.0, min(1.0, (valid_area[1] + (valid_area[3] - valid_area[1]) * 0.5) / shape[1])),
                                max(0.0, min(1.0, (valid_area[2] - valid_area[0]) / shape[0])),
                                max(0.0, min(1.0, (valid_area[3] - valid_area[1]) / shape[1])))
            rect = integrated_data_item.add_rectangle_region(*rectangle_bounds)
            rect.label = "Valid Area"
        try:
            shifted_xdata = self.__shifted_xdata
        except AttributeError:
            shifted_result_data_item = self.computation.get_result("shifted_data")
            if shifted_result_data_item:
                self.computation.set_result("shifted_data", None)
                api = Facade.API_1(None, ApplicationModule.app)
                api.library._document_model.remove_data_item(shifted_result_data_item._data_item)
        else:
            shifted_result_data_item = self.computation.get_result("shifted_data")
            if not shifted_result_data_item:
                api = Facade.API_1(None, ApplicationModule.app)
                shifted_result_data_item = api.library.create_data_item()
                api.application.document_windows[0].display_data_item(shifted_result_data_item)
                self.computation.set_result("shifted_data", shifted_result_data_item)
            self.computation.set_referenced_xdata("shifted_data", shifted_xdata)
            for graphic in shifted_result_data_item.graphics:
                if graphic.label == "Valid Area":
                    integrated_data_item.remove_region(graphic)
                    break
            if valid_area is not None:
                rect = shifted_result_data_item.add_rectangle_region(*rectangle_bounds)
                rect.label = "Valid Area"
        return None


def align_image_sequence(api: Facade.API_1, window: Facade.DocumentWindow, data_item: Facade.DataItem,
                         reference_index: int, relative_shifts: bool, max_shift: int, show_shifted_output: bool,
                         crop_to_valid: bool, bounds_graphic: Facade.Graphic | None) -> tuple[Facade.DataItem, Facade.DataItem, Facade.DataItem | None]:
    result_data_item = api.library.create_data_item()
    shifts = api.library.create_data_item_from_data(numpy.zeros((2, 2)))  # create real data so we can update the display below
    inputs = {"input_data_item": {"object": data_item, "type": "data_source"},
              "reference_index": reference_index,
              "relative_shifts": relative_shifts,
              "max_shift": max_shift,
              "show_shifted_output": show_shifted_output,
              "crop_to_valid": crop_to_valid
              }
    if bounds_graphic:
        inputs["bounds_graphic"] = bounds_graphic

    outputs = {"shifts": shifts, "integrated_sequence": result_data_item}
    if show_shifted_output:
        shifted_result_data_item = api.library.create_data_item()
        outputs["shifted_data"] = shifted_result_data_item
    else:
        shifted_result_data_item = None

    api.library.create_computation("nion.align_and_integrate_image_sequence",
                                   inputs=inputs,
                                   outputs=outputs)
    window.display_data_item(result_data_item)
    window.display_data_item(shifts)
    if shifted_result_data_item:
        window.display_data_item(shifted_result_data_item)

    display_item = api.library._document_model.get_display_item_for_data_item(shifts._data_item)
    assert display_item is not None
    display_item.display_type = "line_plot"
    display_item._set_display_layer_properties(0, stroke_color='#1E90FF', stroke_width=2, fill_color=None, label="y")
    display_item._set_display_layer_properties(1, stroke_color='#F00', stroke_width=2, fill_color=None, label="x")

    return result_data_item, shifts, shifted_result_data_item


class AlignImageSequenceMenuItemDelegate:

    def __init__(self, api: Facade.API_1) -> None:
        self.__api = api
        self.menu_id = "processing_menu"  # required, specify menu_id where this item will go
        self.menu_name = _("Processing")  # optional, specify default name if not a standard menu
        self.menu_before_id = "window_menu"  # optional, specify before menu_id if not a standard menu

    @property
    def menu_item_name(self) -> str:
        return _("[EXPERIMENTAL] Align image sequence")  # menu item name

    def menu_item_execute(self, window: Facade.DocumentWindow) -> None:
        try:
            selected_data_item = window.target_data_item
            error_msg = "Select one data item that contains a sequence or 1D-collection of two-dimensional data."
            assert selected_data_item is not None, error_msg
            assert selected_data_item.xdata is not None, error_msg
            assert selected_data_item.xdata.is_sequence or selected_data_item.xdata.is_collection, error_msg
            assert not (selected_data_item.xdata.is_sequence and selected_data_item.xdata.is_collection), error_msg
            if selected_data_item.xdata.is_collection:
                assert selected_data_item.xdata.collection_dimension_count == 1, error_msg
            assert selected_data_item.xdata.datum_dimension_count == 2, error_msg

            bounds_graphic = None
            if selected_data_item.display.selected_graphics:
                for graphic in selected_data_item.display.selected_graphics:
                    if graphic.graphic_type in {"rect-graphic", "interval-graphic"}:
                        bounds_graphic = graphic

            settings_dict = computation_settings.get("nion.align_and_integrate_image_sequence", dict())

            align_image_sequence(self.__api, window,
                                 selected_data_item,
                                 settings_dict.get("reference_index", 0),
                                 settings_dict.get("relative_shifts", False),
                                 settings_dict.get("max_shift", 0),
                                 settings_dict.get("show_shifted_output", False),
                                 settings_dict.get("crop_to_valid", True),
                                 bounds_graphic)
        except Exception as e:
            import traceback
            traceback.print_exc()
            from nion.swift.model import Notification
            Notification.notify(Notification.Notification("nion.computation.error", "\N{WARNING SIGN} Computation", "Align sequence of images failed", str(e)))

        return None


class MultiDimensionalProcessingExtension:

    extension_id = "nion.experimental.multi_dimensional_processing"

    def __init__(self, api_broker: typing.Any) -> None:
        api = typing.cast(Facade.API_1, api_broker.get_api(version="~1.0"))
        self.__integrate_menu_item_ref = api.create_menu_item(IntegrateAlongAxisMenuItemDelegate(api))
        self.__measure_shifts_menu_item_ref = api.create_menu_item(MeasureShiftsMenuItemDelegate(api))
        self.__apply_shifts_menu_item_ref = api.create_menu_item(ApplyShiftsMenuItemDelegate(api))
        self.__crop_menu_item_ref = api.create_menu_item(CropMenuItemDelegate(api))
        self.__tableau_menu_item_ref = api.create_menu_item(MakeTableauMenuItemDelegate(api))
        self.__align_image_sequence_menu_item_ref = api.create_menu_item(AlignImageSequenceMenuItemDelegate(api))

    def close(self) -> None:
        self.__integrate_menu_item_ref.close()
        self.__integrate_menu_item_ref = typing.cast(Facade.API_1.MenuItemReference, None)
        self.__measure_shifts_menu_item_ref.close()
        self.__measure_shifts_menu_item_ref = typing.cast(Facade.API_1.MenuItemReference, None)
        self.__apply_shifts_menu_item_ref.close()
        self.__apply_shifts_menu_item_ref = typing.cast(Facade.API_1.MenuItemReference, None)
        self.__crop_menu_item_ref.close()
        self.__crop_menu_item_ref = typing.cast(Facade.API_1.MenuItemReference, None)
        self.__tableau_menu_item_ref.close()
        self.__tableau_menu_item_ref = typing.cast(Facade.API_1.MenuItemReference, None)
        self.__align_image_sequence_menu_item_ref.close()
        self.__align_image_sequence_menu_item_ref = typing.cast(Facade.API_1.MenuItemReference, None)
        return None


class AxisChoiceVariableHandler(Observable.Observable):
    def __init__(self, computation: Symbolic.Computation, computation_variable: Symbolic.ComputationVariable, variable_model: Inspector.VariableValueModel, sub_axes_enabled: bool):
        super().__init__()
        self.computation = computation
        self.computation_variable = computation_variable
        self.variable_model = variable_model
        self.sub_axes_enabled = sub_axes_enabled

        self.__axes_index = 0
        self.__sub_axes_visible = False
        self.__sub_axes_index = 0

        self.initialize()

        u = Declarative.DeclarativeUI()
        label = u.create_label(text="@binding(computation_variable.display_label)")
        axes_combo_box = u.create_combo_box(items_ref="@binding(axes)", current_index="@binding(axes_index)")
        sub_axes_combo_box = u.create_combo_box(items_ref="@binding(sub_axes)", current_index="@binding(sub_axes_index)", visible="@binding(sub_axes_visible)")
        self.ui_view = u.create_column(label, u.create_row(axes_combo_box, sub_axes_combo_box, u.create_stretch(), spacing=8))

        def handle_item_inserted(*args: typing.Any, **kwargs: typing.Any) -> None:
            self.property_changed_event.fire("axes")
            self.property_changed_event.fire("sub_axes")
            input_data_item = self.computation.get_input("input_data_item")
            new_value = None
            if self.computation.processing_id == "nion.apply_shifts":
                shifts_data_item = self.computation.get_input("shifts_data_item")
                if input_data_item and shifts_data_item:
                    compute_class = typing.cast(MultiDimensionalProcessingComputation, Symbolic._computation_types.get(self.computation.processing_id))
                    if compute_class:
                        new_value = compute_class.guess_starting_axis(input_data_item.xdata, shifts_xdata=shifts_data_item.xdata)
            else:
                if input_data_item:
                    assert self.computation.processing_id is not None
                    compute_class = typing.cast(MultiDimensionalProcessingComputation, Symbolic._computation_types.get(self.computation.processing_id))
                    if compute_class:
                        new_value = compute_class.guess_starting_axis(input_data_item.xdata)
            if new_value is not None:
                self.variable_model.value = new_value
            self.initialize()
            return None

        self.__item_inserted_listener = self.computation.item_inserted_event.listen(handle_item_inserted)

    def initialize(self) -> None:
        axes_description = self.variable_model.value
        split_description = axes_description.split("-")
        self.axes_index = self.__get_available_axis_choices().index(split_description[0])
        choices = self.__get_available_sub_axis_choices(self.current_axis)
        self.sub_axes_visible = bool(choices)
        if choices and len(split_description) > 1:
            self.sub_axes_index = choices.index(split_description[1])
        return None

    def close(self) -> None:
        self.__item_inserted_listener = typing.cast(typing.Any, None)
        return None

    def update(self) -> None:
        current_axis = self.current_axis
        current_sub_axis = self.current_sub_axis
        self.sub_axes_visible = bool(current_sub_axis)
        axes_description = ""
        if current_axis:
            axes_description += current_axis
            if current_sub_axis:
                axes_description += "-" + current_sub_axis
        self.variable_model.value = axes_description
        self.property_changed_event.fire("sub_axes")
        return None

    @property
    def __axes_labels(self) -> typing.Mapping[str, str]:
        return {"sequence": _("Sequence"),
                "collection": _("Collection"),
                "data": _("Data")}

    @property
    def __sub_axes_labels(self) -> typing.Mapping[str, str]:
        return {"first": _("First"),
                "second": _("Second"),
                "all": _("All")}

    def __get_available_axis_choices(self) -> typing.List[str]:
        axis_choices = []
        input_data_item = self.computation.get_input("input_data_item")
        if input_data_item and input_data_item.xdata:
            if input_data_item.xdata.is_sequence:
                axis_choices.append("sequence")
            if input_data_item.xdata.is_collection:
                axis_choices.append("collection")
            axis_choices.append("data")
        return axis_choices

    def __get_available_sub_axis_choices(self, axis: typing.Optional[str]) -> typing.List[str]:
        sub_axis_choices = []
        input_data_item = self.computation.get_input("input_data_item")
        if axis and input_data_item and input_data_item.xdata:
            dimension_count = 0
            if axis == "collection":
                dimension_count = input_data_item.xdata.collection_dimension_count
            elif axis == "data":
                dimension_count = input_data_item.xdata.datum_dimension_count
            if dimension_count > 1:
                sub_axis_choices = ["all", "first", "second"]
        return sub_axis_choices

    @property
    def current_axis(self) -> typing.Optional[str]:
        choices = self.__get_available_axis_choices()
        if choices:
            return choices[min(self.axes_index, len(choices) - 1)]
        return None

    @property
    def current_sub_axis(self) -> typing.Optional[str]:
        choices = self.__get_available_sub_axis_choices(self.current_axis)
        if choices:
            return choices[min(self.sub_axes_index, len(choices) - 1)]
        return None

    @property
    def axes(self) -> typing.List[str]:
        return [self.__axes_labels[entry] for entry in self.__get_available_axis_choices()]

    @axes.setter
    def axes(self, axes: typing.List[str]) -> None:
        ...

    @property
    def sub_axes(self) -> typing.List[str]:
        return self.__get_available_sub_axis_choices(self.current_axis)

    @sub_axes.setter
    def sub_axes(self, sub_axes: typing.List[str]) -> None:
        ...

    @property
    def axes_index(self) -> int:
        return self.__axes_index

    @axes_index.setter
    def axes_index(self, axes_index: int) -> None:
        self.__axes_index = axes_index
        self.update()

    @property
    def sub_axes_index(self) -> int:
        return self.__sub_axes_index

    @sub_axes_index.setter
    def sub_axes_index(self, sub_axes_index: int) -> None:
        self.__sub_axes_index = sub_axes_index
        self.update()

    @property
    def sub_axes_visible(self) -> bool:
        return self.__sub_axes_visible

    @sub_axes_visible.setter
    def sub_axes_visible(self, visible: bool) -> None:
        self.__sub_axes_visible = visible if self.sub_axes_enabled else False
        self.property_changed_event.fire("sub_axes_visible")


class AxisChoiceVariableHandlerFactory(Inspector.VariableHandlerComponentFactory):
    def make_variable_handler(self, document_controller: DocumentController.DocumentController, computation: Symbolic.Computation, computation_variable: Symbolic.ComputationVariable, variable_model: Inspector.VariableValueModel) -> typing.Optional[Declarative.HandlerLike]:
        if computation.processing_id == "nion.integrate_along_axis" and computation_variable.name == "axes_description":
            return AxisChoiceVariableHandler(computation, computation_variable, variable_model, True)
        if computation.processing_id in {"nion.measure_shifts", "nion.apply_shifts", "nion.crop_multi_dimensional"} and computation_variable.name == "axes_description":
            return AxisChoiceVariableHandler(computation, computation_variable, variable_model, False)
        return None


Registry.register_component(AxisChoiceVariableHandlerFactory(), {"variable-handler-component-factory"})


for computation in [IntegrateAlongAxis, MeasureShifts, ApplyShifts, Crop, MakeTableau, AlignImageSequence]:
    Symbolic.register_computation_type(getattr(computation, "computation_id"), computation)
