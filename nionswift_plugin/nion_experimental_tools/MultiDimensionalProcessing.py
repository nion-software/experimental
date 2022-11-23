import typing
import gettext
import copy
import numpy
import numpy.typing

from nion.data import Core
from nion.data import DataAndMetadata
from nion.data import MultiDimensionalProcessing
from nion.swift.model import Symbolic
from nion.swift.model import DataItem
from nion.swift import Inspector
from nion.swift import DocumentController
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


_ImageDataType = DataAndMetadata._ImageDataType


class MultiDimensionalProcessingComputation(Symbolic.ComputationHandlerLike):

    @staticmethod
    def guess_starting_axis(xdata: DataAndMetadata.DataAndMetadata, **kwargs: typing.Any) -> str:
        ...


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
    label = _("Integrate")
    inputs = {"input_data_item": {"label": _("Input data item")},
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

        integration_mask: typing.Optional[_ImageDataType] = None
        if integration_graphic is not None:
            integration_axis_shape = tuple((input_xdata.data_shape[i] for i in integration_axis_indices))
            integration_mask = integration_graphic.mask_xdata_with_shape(integration_axis_shape).data

        self.__result_xdata = MultiDimensionalProcessing.function_integrate_along_axis(input_xdata, tuple(integration_axis_indices), integration_mask)
        return None


    def commit(self) -> None:
        self.computation.set_referenced_xdata("integrated", self.__result_xdata)
        return None


class MeasureShifts(MultiDimensionalProcessingComputation):
    label = _("Measure shifts")
    inputs = {"input_data_item": {"label": _("Input data item")},
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
        return None

    def commit(self) -> None:
        self.computation.set_referenced_xdata("shifts", self.__shifts_xdata)
        return None


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
        selected_data_item = window.target_data_item

        if not selected_data_item or not selected_data_item.xdata:
            return None

        bounds_graphic = None
        if selected_data_item.display.selected_graphics:
            for graphic in selected_data_item.display.selected_graphics:
                if graphic.graphic_type in {"rect-graphic", "interval-graphic"}:
                    bounds_graphic = graphic

        shift_axis = MeasureShifts.guess_starting_axis(selected_data_item.xdata, graphic=bounds_graphic)

        # Make a result data item with 3 dimensions to ensure we get a large_format data item
        result_data_item = self.__api.library.create_data_item_from_data(numpy.zeros((1,1,1)), title="Shifts of {}".format(selected_data_item.title))

        # shift_axis_structure = DataStructure.DataStructure(structure_type=shift_axis)
        # self.__api.library._document_model.append_data_structure(shift_axis_structure)
        # shift_axis_structure.source = result_data_item._data_item

        inputs = {"input_data_item": {"object": selected_data_item, "type": "data_source"},
                  "axes_description": shift_axis,
                  "reference_index": 0,
                  "relative_shifts": True,
                  "max_shift": 0,
                  }
        if bounds_graphic:
            inputs["bounds_graphic"] = bounds_graphic

        self.__api.library.create_computation("nion.measure_shifts",
                                              inputs=inputs,
                                              outputs={"shifts": result_data_item})
        window.display_data_item(result_data_item)
        return None


class ApplyShifts(MultiDimensionalProcessingComputation):
    label = _("Apply shifts")
    inputs = {"input_data_item": {"label": _("Input data item")},
              "shifts_data_item": {"label": _("Shifts data item")},
              "axes_description": {"label": _("Apply shift along this axis")},
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

    def execute(self, *, input_data_item: Facade.DataItem, shifts_data_item: Facade.DataItem, axes_description: str) -> None: # type: ignore
        input_xdata = input_data_item.xdata
        assert input_xdata is not None
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
        MultiDimensionalProcessing.function_apply_multi_dimensional_shifts(input_xdata, shifts, tuple(shift_axis_indices), out=result_data_item.xdata)
        return None

    def commit(self) -> None:
        # self.computation.set_referenced_xdata("shifted", self.__result_xdata)
        # self.__result_xdata = None
        # Still call "set_referenced_xdata" to notify Swift that the data has been updated.
        self.computation.set_referenced_xdata("shifted", self.computation.get_result("shifted").xdata)
        return None


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
        di_1 = selected_display_items[0][0].data_item
        di_2 = selected_display_items[1][0].data_item
        assert di_1 is not None, error_msg
        assert di_2 is not None, error_msg
        assert di_1.xdata is not None, error_msg
        assert di_2.xdata is not None, error_msg
        assert di_1.data is not None, error_msg
        assert di_2.data is not None, error_msg

        if len(di_1.data.shape) < len(di_2.data.shape):
            shifts_di = self.__api._new_api_object(di_1)
            input_di = self.__api._new_api_object(di_2)
        elif len(di_2.data.shape) < len(di_1.data.shape):
            shifts_di = self.__api._new_api_object(di_2)
            input_di = self.__api._new_api_object(di_1)
        else:
            raise ValueError(error_msg)

        shift_axis = ApplyShifts.guess_starting_axis(input_di.xdata, shifts_xdata=shifts_di.xdata)

        data_item = DataItem.DataItem(large_format=True)
        data_item.title="Shifted {}".format(input_di.title)
        window._document_controller.document_model.append_data_item(data_item)
        data_item.reserve_data(data_shape=input_di.xdata.data_shape, data_dtype=input_di.xdata.data_dtype, data_descriptor=input_di.xdata.data_descriptor)
        data_item.dimensional_calibrations = input_di.xdata.dimensional_calibrations
        data_item.intensity_calibration = input_di.xdata.intensity_calibration
        data_item.metadata = copy.deepcopy(input_di.xdata.metadata)
        result_data_item = self.__api._new_api_object(data_item)

        # shift_axis_structure = DataStructure.DataStructure(structure_type=shift_axis)
        # self.__api.library._document_model.append_data_structure(shift_axis_structure)
        # shift_axis_structure.source = result_data_item._data_item

        inputs = {"input_data_item": {"object": input_di, "type": "data_source"},
                  "shifts_data_item": {"object": shifts_di, "type": "data_source"},
                  "axes_description": shift_axis
                  }

        self.__api.library.create_computation("nion.apply_shifts",
                                              inputs=inputs,
                                              outputs={"shifted": result_data_item})
        window.display_data_item(result_data_item)
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

        # Make a result data item with 3 dimensions to ensure we get a large_format data item
        result_data_item = self.__api.library.create_data_item_from_data(numpy.zeros((1,1,1)), title="Integrated {}".format(selected_data_item.title))

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
    label = _("Crop")
    inputs = {"input_data_item": {"label": _("Input data item")},
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

        # Make a result data item with 3 dimensions to ensure we get a large_format data item
        result_data_item = self.__api.library.create_data_item_from_data(numpy.zeros((1,1,1)), title="Cropped {}".format(selected_data_item.title))

        inputs: typing.MutableMapping[str, typing.Any]
        inputs = {"input_data_item": {"object": selected_data_item, "type": "data_source"},
                  "axes_description": crop_axes
                  }
        if crop_graphic:
            inputs["crop_graphic"] = crop_graphic
        else:
            inputs["crop_bounds_left"] = 0
            inputs["crop_bounds_right"] = -1
            inputs["crop_bounds_top"] = 0
            inputs["crop_bounds_bottom"] = -1

        self.__api.library.create_computation("nion.crop_multi_dimensional",
                                              inputs=inputs,
                                              outputs={"cropped": result_data_item})
        window.display_data_item(result_data_item)
        return None


class MakeTableau(Symbolic.ComputationHandlerLike):
    label = _("Display Tableau")
    inputs = {"input_data_item": {"label": _("Input data item")},
              "scale": {"label": _("Scale")}}
    outputs = {"tableau": {"label": "Tableau"}}

    def __init__(self, computation: typing.Any, **kwargs: typing.Any) -> None:
        self.computation = computation
        self.__result_xdata: typing.Optional[DataAndMetadata.DataAndMetadata] = None

    def execute(self, *, input_data_item: Facade.DataItem, scale: float) -> None: # type: ignore
        assert input_data_item.xdata is not None
        try:
            self.__result_xdata = MultiDimensionalProcessing.function_make_tableau_image(input_data_item.xdata, scale)
        except:
            import traceback
            traceback.print_exc()
            raise
        return None

    def commit(self) -> None:
        self.computation.set_referenced_xdata("tableau", self.__result_xdata)
        self.__result_xdata = None
        return None


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

        inputs = {"input_data_item": {"object": selected_data_item, "type": "data_source"},
                  "scale": scale}

        # Make a result data item with 3 dimensions to ensure we get a large_format data item
        result_data_item = self.__api.library.create_data_item_from_data(numpy.zeros((1,1,1)), title="Tableau of {}".format(selected_data_item.title))

        self.__api.library.create_computation("nion.make_tableau_image",
                                              inputs=inputs,
                                              outputs={"tableau": result_data_item})

        window.display_data_item(result_data_item)
        return None


class AlignImageSequence(Symbolic.ComputationHandlerLike):
    label = _("Align and integrate image sequence")
    inputs = {"input_data_item": {"label": _("Input data item")},
              "reference_index": {"label": _("Reference index for shifts")},
              "relative_shifts": {"label": _("Measure shifts relative to previous slice")},
              "max_shift": {"label": _("Max shift between consecutive frames (in pixels, <= 0 to disable)")},
              "bounds_graphic": {"label": _("Shift bounds")},
              }
    outputs = {"shifts": {"label": _("Shifts")},
               "integrated_sequence": {"label": _("Integrated sequence")},
               }

    def __init__(self, computation: typing.Any, **kwargs: typing.Any) -> None:
        self.computation = computation

    def execute(self, *, input_data_item: Facade.DataItem, reference_index: typing.Optional[int] = None, relative_shifts: bool=True, max_shift: int=0, bounds_graphic: typing.Optional[Facade.Graphic]=None) -> None: # type: ignore
        input_xdata = input_data_item.xdata
        assert input_xdata is not None
        bounds = None
        if bounds_graphic is not None:
            bounds = bounds_graphic.bounds
        max_shift_ = max_shift if max_shift > 0 else None
        reference_index = reference_index if not relative_shifts else None
        shifts_axes = tuple(input_xdata.datum_dimension_indexes)
        shifts_xdata = MultiDimensionalProcessing.function_measure_multi_dimensional_shifts(input_xdata, shifts_axes, reference_index=reference_index, bounds=bounds, max_shift=max_shift_)
        self.__shifts_xdata = Core.function_transpose_flip(shifts_xdata, transpose=True, flip_v=False, flip_h=False)
        aligned_input_xdata = MultiDimensionalProcessing.function_apply_multi_dimensional_shifts(input_xdata, shifts_xdata.data, shifts_axes)
        assert aligned_input_xdata is not None
        self.__integrated_input_xdata = Core.function_sum(aligned_input_xdata, axis=0)
        return None

    def commit(self) -> None:
        self.computation.set_referenced_xdata("shifts", self.__shifts_xdata)
        self.computation.set_referenced_xdata("integrated_sequence", self.__integrated_input_xdata)
        return None


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

            # Make a result data item with 3 dimensions to ensure we get a large_format data item
            result_data_item = self.__api.library.create_data_item_from_data(numpy.zeros((1,1,1)), title=f"{selected_data_item.title} aligned and integrated")
            shifts = self.__api.library.create_data_item_from_data(numpy.zeros((2, 2)), title=f"{selected_data_item.title} measured shifts")

            inputs = {"input_data_item": {"object": selected_data_item, "type": "data_source"},
                      "reference_index": 0,
                      "relative_shifts": False,
                      "max_shift": 0,
                      }
            if bounds_graphic:
                inputs["bounds_graphic"] = bounds_graphic

            self.__api.library.create_computation("nion.align_and_integrate_image_sequence",
                                                  inputs=inputs,
                                                  outputs={"shifts": shifts,
                                                           "integrated_sequence": result_data_item})
            window.display_data_item(result_data_item)
            window.display_data_item(shifts)

            display_item = self.__api.library._document_model.get_display_item_for_data_item(shifts._data_item)
            assert display_item is not None
            display_item.display_type = "line_plot"
            display_item._set_display_layer_properties(0, stroke_color='#1E90FF', stroke_width=2, fill_color=None, label="y")
            display_item._set_display_layer_properties(1, stroke_color='#F00', stroke_width=2, fill_color=None, label="x")

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

Symbolic.register_computation_type("nion.integrate_along_axis", IntegrateAlongAxis)
Symbolic.register_computation_type("nion.measure_shifts", MeasureShifts)
Symbolic.register_computation_type("nion.apply_shifts", ApplyShifts)
Symbolic.register_computation_type("nion.crop_multi_dimensional", Crop)
Symbolic.register_computation_type("nion.make_tableau_image", MakeTableau)
Symbolic.register_computation_type("nion.align_and_integrate_image_sequence", AlignImageSequence)
