import gettext
import typing

import numpy

from nion.data import Core
from nion.data import DataAndMetadata
from nion.swift.model import Symbolic
from nion.swift import Facade
from nion.typeshed import API_1_0

_ = gettext.gettext


class AlignMultiDimensionalSequence(Symbolic.ComputationHandlerLike):
    computation_id = "nion.align_multi_d_sequence"
    label = _("Align Multi-Dimensional Sequence")
    inputs = {"si_sequence_data_item": {"label": _("Multi-dimensional sequence data item"), "data_type": "xdata"},
              "haadf_sequence_data_item": {"label": _("HAADF sequence data item"), "data_type": "xdata"},
              "align_index": {"label": _("Align to this slice")},
              "align_region": {"label": _("Alignment bounds")},
              "align_collection_index": {"label": _("Calculate shifts from this slice")}
              }
    outputs = {"aligned_haadf": {"label": _("Aligned HAADF Sequence")},
               "aligned_si": {"label": _("Aligned Multi-Dimensional Sequence")}}

    def __init__(self, computation: Facade.Computation, **kwargs: typing.Any) -> None:
        self.computation = computation
        self.__aligned_haadf_sequence: typing.Optional[DataAndMetadata.DataAndMetadata] = None
        self.__aligned_si_sequence: typing.Optional[DataAndMetadata.DataAndMetadata] = None

    def execute(self, *,
                si_sequence_data_item: typing.Optional[API_1_0.DataItem] = None,
                haadf_sequence_data_item: typing.Optional[API_1_0.DataItem] = None, align_index: int = 0,
                align_region: typing.Optional[API_1_0.Graphic] = None, align_collection_index: int = 0,
                **kwargs: typing.Any) -> None:
        assert si_sequence_data_item
        assert haadf_sequence_data_item
        assert align_region
        if haadf_sequence_data_item == si_sequence_data_item:
            haadf_xdata = haadf_sequence_data_item.xdata[:, align_collection_index]
            two_items = False
        else:
            haadf_xdata = haadf_sequence_data_item.xdata
            two_items = True
        si_xdata = si_sequence_data_item.xdata
        bounds = align_region.bounds
        translations = Core.function_sequence_measure_relative_translation(haadf_xdata,
                                                                           haadf_xdata[align_index],
                                                                           True, bounds=bounds)
        sequence_shape = haadf_sequence_data_item.xdata.sequence_dimension_shape

        c = int(numpy.prod(sequence_shape))
        haadf_result_data = numpy.empty_like(haadf_xdata.data)
        si_result_data = numpy.empty_like(si_xdata.data)

        align_data_shape = haadf_xdata.datum_dimension_shape
        align_axes_start_index: typing.Optional[int] = None
        # TODO: this algorithm is wrong. it fails for (8,8,8) and (8,8,8,8) shapes.
        for i in range(len(si_xdata.data_shape) - 1):
            if align_data_shape == si_xdata.data_shape[i:i+2]:
                align_axes_start_index = i
                break
        else:
            raise RuntimeError('Could not find axes that match the HAADF shape in SI data item.')

        si_translation = [0.0] * (len(si_xdata.data_shape) - len(sequence_shape))
        align_axes_start_index -= len(sequence_shape)
        assert align_axes_start_index >= 0

        for i in range(c):
            ii = numpy.unravel_index(i, sequence_shape)
            current_xdata = DataAndMetadata.new_data_and_metadata(haadf_xdata.data[ii])
            translation = translations.data[ii]
            haadf_result_data[ii] = Core.function_shift(current_xdata, tuple(translation)).data
            current_xdata = DataAndMetadata.new_data_and_metadata(si_xdata.data[ii])
            si_translation[align_axes_start_index] = translation[0]
            si_translation[align_axes_start_index+1] = translation[1]
            si_result_data[ii] = Core.function_shift(current_xdata, tuple(si_translation)).data
        if two_items:
            self.__aligned_haadf_sequence = DataAndMetadata.new_data_and_metadata(haadf_result_data,
                                                                                  intensity_calibration=haadf_xdata.intensity_calibration,
                                                                                  dimensional_calibrations=haadf_xdata.dimensional_calibrations,
                                                                                  metadata=haadf_xdata.metadata,
                                                                                  data_descriptor=haadf_xdata.data_descriptor)
        else:
            self.__aligned_haadf_sequence = None

        self.__aligned_si_sequence = DataAndMetadata.new_data_and_metadata(si_result_data,
                                                                           intensity_calibration=si_xdata.intensity_calibration,
                                                                           dimensional_calibrations=si_xdata.dimensional_calibrations,
                                                                           metadata=si_xdata.metadata,
                                                                           data_descriptor=si_xdata.data_descriptor)

    def commit(self) -> None:
        if self.__aligned_haadf_sequence:
            self.computation.set_referenced_xdata("aligned_haadf", self.__aligned_haadf_sequence)
        if self.__aligned_si_sequence:
            self.computation.set_referenced_xdata("aligned_si", self.__aligned_si_sequence)


def align_multi_si(api: API_1_0.API, window: API_1_0.DocumentWindow, data_item1: Facade.DataItem, data_item2: Facade.DataItem) -> tuple[Facade.DataItem | None, Facade.DataItem]:
    error_msg = "Select a sequence of spectrum images and a sequence of scanned images in order to use this computation."

    di_1 = data_item1._data_item
    di_2 = data_item2._data_item

    align_collection_index = 0
    aligned_haadf = None

    if di_1 != di_2:
        haadf_footprint = (2, True, 0, True)
        di_1_footprint = (di_1.datum_dimension_count, di_1.is_sequence, di_1.collection_dimension_count,
                          di_1.metadata.get("hardware_source", {}).get("harwdare_source_id", "") == "superscan")
        di_2_footprint = (di_2.datum_dimension_count, di_2.is_sequence, di_2.collection_dimension_count,
                          di_2.metadata.get("hardware_source", {}).get("harwdare_source_id", "") == "superscan")

        di_1_points = 0
        di_2_points = 0
        for i in range(len(haadf_footprint)):
            di_1_points -= abs(haadf_footprint[i] - di_1_footprint[i])
            di_2_points -= abs(haadf_footprint[i] - di_2_footprint[i])
        if di_1_points > di_2_points:
            assert di_1_footprint[:-1] == haadf_footprint[:-1], error_msg
            haadf_sequence_data_item = api._new_api_object(di_1)
            si_sequence_data_item = api._new_api_object(di_2)
        elif di_2_points > di_1_points:
            assert di_2_footprint[:-1] == haadf_footprint[:-1], error_msg
            haadf_sequence_data_item = api._new_api_object(di_2)
            si_sequence_data_item = api._new_api_object(di_1)
        else:
            raise ValueError(error_msg)

        aligned_haadf = api.library.create_data_item()
        aligned_si = api.library.create_data_item()
        outputs = {"aligned_haadf": aligned_haadf,
                   "aligned_si": aligned_si}
    else:
        assert di_1.collection_dimension_count == 1, error_msg
        haadf_sequence_data_item = api._new_api_object(di_1)
        si_sequence_data_item = haadf_sequence_data_item
        align_collection_index = haadf_sequence_data_item.display._display.display_data_channel.collection_index[0]
        aligned_haadf = None
        aligned_si = api.library.create_data_item()
        outputs = {"aligned_si": aligned_si}

    align_region = None
    for graphic in haadf_sequence_data_item.graphics:
        if graphic.graphic_type == 'rect-graphic':
            align_region = graphic
            break
    if align_region is None:
        align_region = haadf_sequence_data_item.add_rectangle_region(0.5, 0.5, 0.75, 0.75)
    align_region.label = 'Alignment bounds'
    align_index = haadf_sequence_data_item.display._display.display_data_channel.sequence_index


    inputs = {"si_sequence_data_item": si_sequence_data_item,
              "haadf_sequence_data_item": haadf_sequence_data_item,
              "align_index": align_index,
              "align_region": align_region,
              "align_collection_index": align_collection_index}

    api.library.create_computation("nion.align_multi_d_sequence",
                                   inputs=inputs,
                                   outputs=outputs)
    window.display_data_item(aligned_si)
    if aligned_haadf is not None:
        window.display_data_item(aligned_haadf)

    return aligned_haadf, aligned_si


Symbolic.register_computation_type(AlignMultiDimensionalSequence.computation_id, AlignMultiDimensionalSequence)


class AlignSequenceMenuItemDelegate:

    def __init__(self, api: Facade.API_1) -> None:
        self.__api = api
        self.menu_id = "processing_menu"  # required, specify menu_id where this item will go
        self.menu_name = _("Processing")  # optional, specify default name if not a standard menu
        self.menu_before_id = "window_menu"  # optional, specify before menu_id if not a standard menu
        self.menu_item_name = _("Align sequence of multi-dimensional data")  # menu item name

    def menu_item_execute(self, window: Facade.DocumentWindow) -> None:
        selected_display_items = window._document_controller._get_two_data_sources()
        error_msg = "Select a sequence of spectrum images and a sequence of scanned images in order to use this computation."
        assert selected_display_items[0][0] is not None, error_msg
        assert selected_display_items[1][0] is not None, error_msg
        assert selected_display_items[0][0].data_item is not None, error_msg
        assert selected_display_items[1][0].data_item is not None, error_msg
        assert selected_display_items[0][0].data_item.is_sequence, error_msg
        assert selected_display_items[1][0].data_item.is_sequence, error_msg
        data_item1 = Facade.DataItem(selected_display_items[0][0].data_item)
        data_item2 = Facade.DataItem(selected_display_items[1][0].data_item)
        align_multi_si(self.__api, window, data_item1, data_item2)


class AlignSequenceExtension:

    # required for Swift to recognize this as an extension class.
    extension_id = "nion.experimental.align_multi_d_sequence"

    def __init__(self, api_broker: typing.Any) -> None:
        # grab the api object.
        api = api_broker.get_api(version="~1.0")
        self.__align_sequence_menu_item_ref = api.create_menu_item(AlignSequenceMenuItemDelegate(api))

    def close(self) -> None:
        # close will be called when the extension is unloaded. in turn, close any references so they get closed. this
        # is not strictly necessary since the references will be deleted naturally when this object is deleted.
        self.__align_sequence_menu_item_ref.close()
        self.__align_sequence_menu_item_ref = None
