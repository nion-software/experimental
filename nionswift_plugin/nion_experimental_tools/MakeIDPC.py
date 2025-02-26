import gettext
import logging
import typing
import warnings

import numpy
import scipy.optimize

from nion.data import DataAndMetadata
from nion.swift import Facade
from nion.swift.model import Symbolic
from nion.typeshed import API_1_0


_ = gettext.gettext


class MakeIDPC(Symbolic.ComputationHandlerLike):
    computation_id = "nion.make_idpc"
    label = _("Make iDPC from DPC")
    inputs = {"src": {"label": _("DPC Data Item"), "data_type": "xdata"},
              "gradient_x_index": {"label": _("DPC x-slice")},
              "gradient_y_index": {"label": _("DPC y-slice")},
              "flip_x": {"label":_("Flip x-axis")},
              "flip_y": {"label":_("Flip y-axis")},
              "rotation_str": {"label": _("Rotation (deg) or 'None' for automatic")},
              "crop_region": {"label": _("Crop")},
              }
    outputs = {"output": {"label": _("iDPC")}}

    def __init__(self, computation: Facade.Computation, **kwargs: typing.Any) -> None:
        self.computation = computation
        self.__result_xdata: typing.Optional[DataAndMetadata.DataAndMetadata] = None

    def __calculate_curl(self, rotation: float, com_x: float, com_y: float) -> float:
        com_x_rotated = com_x * numpy.cos(rotation) - com_y * numpy.sin(rotation)
        com_y_rotated = com_x * numpy.sin(rotation) + com_y * numpy.cos(rotation)
        curl_com = numpy.gradient(com_y_rotated, axis=1) - numpy.gradient(com_x_rotated, axis=0)
        return float(numpy.mean(curl_com**2))

    def execute(self, *,
                src: typing.Optional[Facade.DataItem] = None, gradient_x_index: int = 0, gradient_y_index: int = 0,
                flip_x: bool = False, flip_y: bool = False, rotation_str: typing.Optional[str] = None,
                crop_region: typing.Optional[Facade.Graphic] = None,
                **kwargs: typing.Any) -> None:
        assert src
        assert crop_region
        dpc_xdata = src.xdata
        assert dpc_xdata
        assert dpc_xdata.is_datum_2d
        assert dpc_xdata.is_sequence or dpc_xdata.collection_dimension_count == 1
        gradx = dpc_xdata.data[gradient_x_index]
        grady = dpc_xdata.data[gradient_y_index]
        top_x = crop_region.bounds[0][1] * gradx.shape[1]
        top_y = crop_region.bounds[0][0] * gradx.shape[0]
        crop_slices = (slice(int(top_y), int(top_y + crop_region.bounds[1][0] * gradx.shape[0])),
                       slice(int(top_x), int(top_x + crop_region.bounds[1][1] * gradx.shape[1])))
        # Subtract the mean of each component so that we remove any global offset
        gradx = gradx[crop_slices] - numpy.mean(gradx[crop_slices])
        grady = grady[crop_slices] - numpy.mean(grady[crop_slices])
        if flip_x:
            gradx *= -1.0
        if flip_y:
            grady *= -1.0
        # Don't use "if rotation" here because that would also calculate rotation for a given value of 0
        if not rotation_str or rotation_str == "None":
            res = scipy.optimize.minimize_scalar(self.__calculate_curl, 0, args=(gradx, grady), bounds=(0, numpy.pi*2), method='bounded')
            if res.success:
                rotation = res.x
                logging.debug(f'Calculated optimal rotation: {rotation*180/numpy.pi:.1f} degree.')
            else:
                logging.warning(f'Could not find the optimal rotation. Optimize error: {res.message}\nUsing rotation=0 as default.')
                rotation = 0
        else:
            rotation = float(rotation_str) / 180.0 * numpy.pi

        gradx_rotated = gradx * numpy.cos(rotation) - grady * numpy.sin(rotation)
        grady_rotated = gradx * numpy.sin(rotation) + grady * numpy.cos(rotation)
        freq_v = numpy.fft.fftfreq(gradx.shape[-2], d=dpc_xdata.dimensional_calibrations[-2].scale)
        freq_u = numpy.fft.fftfreq(gradx.shape[-1], d=dpc_xdata.dimensional_calibrations[-1].scale)
        freqs = numpy.meshgrid(freq_u, freq_v)
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            fft_idpc = (numpy.fft.fft2(gradx_rotated) * freqs[0] + numpy.fft.fft2(grady_rotated) * freqs[1]) / (1j * (freqs[0]**2 + freqs[1]**2))
        fft_idpc[numpy.isnan(fft_idpc)] = 0
        self.__result_xdata = DataAndMetadata.new_data_and_metadata(numpy.real(numpy.fft.ifft2(fft_idpc)),
                                                                    intensity_calibration=dpc_xdata.intensity_calibration,
                                                                    dimensional_calibrations=dpc_xdata.dimensional_calibrations[1:])

    def commit(self) -> None:
        if self.__result_xdata:
            self.computation.set_referenced_xdata("output", self.__result_xdata)


def iDPC(api: Facade.API_1, window: API_1_0.DocumentWindow, data_item: Facade.DataItem) -> Facade.DataItem:
    assert data_item.xdata
    assert data_item.xdata.is_sequence or data_item.xdata.collection_dimension_count == 1
    assert data_item.xdata.datum_dimension_count == 2

    crop_region = None
    for graphic in data_item.graphics:
        if graphic.graphic_type == 'rect-graphic':
            crop_region = graphic
            break
    if crop_region is None:
        crop_region = data_item.add_rectangle_region(0.5, 0.5, 0.75, 0.75)
    crop_region.label = 'Crop'
    result_data_item = api.library.create_data_item()
    api.library.create_computation("nion.make_idpc",
                                   inputs={"src": data_item,
                                           "gradient_x_index": 0,
                                           "gradient_y_index": 1,
                                           "flip_x": False,
                                           "flip_y": False,
                                           "rotation_str": "None",
                                           "crop_region": crop_region},
                                   outputs={"output": result_data_item})
    window.display_data_item(result_data_item)
    return result_data_item


class MakeIDPCMenuItem:
    menu_id = "_processing_menu"
    menu_item_name = _("Make iDPC from DPC")

    def __init__(self, api: Facade.API_1) -> None:
        self.__api = api

    def menu_item_execute(self, window: API_1_0.DocumentWindow) -> None:
        document_controller = window._document_controller
        display_item = document_controller.selected_display_item
        data_item = display_item.data_items[0] if display_item and len(display_item.data_items) > 0 else None
        if not data_item:
            return
        if data_item.xdata and (data_item.xdata.is_sequence or data_item.xdata.collection_dimension_count == 1) and data_item.xdata.datum_dimension_count == 2:
            iDPC(self.__api, window, Facade.DataItem(data_item))


class MakeIDPCExtension:

    # required for Swift to recognize this as an extension class.
    extension_id = "nion.extension.make_idpc"

    def __init__(self, api_broker: typing.Any) -> None:
        # grab the api object.
        api = api_broker.get_api(version="1", ui_version="1")
        # be sure to keep a reference or it will be closed immediately.
        self.__idpc_menu_item_ref = api.create_menu_item(MakeIDPCMenuItem(api))

    def close(self) -> None:
        self.__idpc_menu_item_ref.close()


Symbolic.register_computation_type(MakeIDPC.computation_id, MakeIDPC)
