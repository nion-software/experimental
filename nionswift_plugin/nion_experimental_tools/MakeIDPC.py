import gettext
import logging

import numpy
import scipy.optimize

from nion.data import DataAndMetadata
from nion.swift.model import Symbolic
from nion.typeshed import API_1_0 as API
from nion.swift import Facade

_ = gettext.gettext


class MakeIDPC:
    label = _("Make iDPC from DPC")
    inputs = {"src": {"label": _("DPC Data Item")},
              "gradient_x_index": {"label": _("DPC x-slice")},
              "gradient_y_index": {"label": _("DPC y-slice")},
              "rotation": {"label": _("Rotation (deg) or 'None' for automatic")},
              "crop_region": {"label": _("Crop")},
              }
    outputs = {"output": {"label": _("iDPC")}}

    def __init__(self, computation, **kwargs):
        self.computation = computation
        self.__result_xdata = None

    def __calculate_curl(self, rotation, com_x, com_y):
        com_x_rotated = com_x * numpy.cos(rotation) - com_y * numpy.sin(rotation)
        com_y_rotated = com_x * numpy.sin(rotation) + com_y * numpy.cos(rotation)
        curl_com = numpy.gradient(com_y_rotated, axis=1) - numpy.gradient(com_x_rotated, axis=0)
        return numpy.mean(curl_com**2)

    def execute(self, src, gradient_x_index, gradient_y_index, rotation, crop_region, **kwargs) -> None:
        try:
            dpc_xdata = src.xdata
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
            # Don't use "if rotation" here because that would also calculate rotation for a given value of 0
            if rotation is None or rotation == "None" or rotation == "":
                res = scipy.optimize.minimize_scalar(self.__calculate_curl, 0, args=(gradx, grady), bounds=(0, numpy.pi*2), method='bounded')
                if res.success:
                    rotation = res.x
                    logging.info(f'Calculated optimal roation: {rotation*180/numpy.pi:.1f} degree.')
                else:
                    logging.warning(f'Could not find the optimal rotation. Optimize error: {res.message}\nUsing rotation=0 as default.')
                    rotation = 0
            else:
                rotation = float(rotation) / 180.0 * numpy.pi

            gradx_rotated = gradx * numpy.cos(rotation) - grady * numpy.sin(rotation)
            grady_rotated = gradx * numpy.sin(rotation) + grady * numpy.cos(rotation)
            freq_v = numpy.fft.fftfreq(gradx.shape[-2], d=dpc_xdata.dimensional_calibrations[-2].scale)
            freq_u = numpy.fft.fftfreq(gradx.shape[-1], d=dpc_xdata.dimensional_calibrations[-1].scale)
            freqs = numpy.meshgrid(freq_u, freq_v)

            fft_idpc = (numpy.fft.fft2(gradx_rotated) * freqs[0] + numpy.fft.fft2(grady_rotated) * freqs[1]) / (1j * (freqs[0]**2 + freqs[1]**2))
            fft_idpc[numpy.isnan(fft_idpc)] = 0
            self.__result_xdata = DataAndMetadata.new_data_and_metadata(numpy.real(numpy.fft.ifft2(fft_idpc)),
                                                                        intensity_calibration=dpc_xdata.intensity_calibration,
                                                                        dimensional_calibrations=dpc_xdata.dimensional_calibrations[1:])
        except Exception as e:
            import traceback
            print(traceback.format_exc())
            print(e)
            raise

    def commit(self):
        self.computation.set_referenced_xdata("output", self.__result_xdata)


class MakeIDPCMenuItem:
    menu_id = "_processing_menu"
    menu_item_name = _("Make iDPC from DPC")

    def __init__(self, api):
        self.__api = api

    def menu_item_execute(self, window: API.DocumentWindow) -> None:
        document_controller = window._document_controller
        display_item = document_controller.selected_display_item
        data_item = display_item.data_items[0] if display_item and len(display_item.data_items) > 0 else None

        if not data_item:
            return

        api_data_item = Facade.DataItem(data_item)

        if (api_data_item.xdata.is_sequence or api_data_item.xdata.collection_dimension_count == 1) and api_data_item.xdata.datum_dimension_count == 2:
            crop_region = None
            for graphic in api_data_item.graphics:
                if graphic.graphic_type == 'rect-graphic':
                    crop_region = graphic
                    break
            if crop_region is None:
                crop_region = api_data_item.add_rectangle_region(0.5, 0.5, 0.75, 0.75)
            crop_region.label = 'Crop'
            result_data_items = {"output": self.__api.library.create_data_item(title="iDPC of " + data_item.title)}
            self.__api.library.create_computation("nion.make_idpc",
                                                  inputs={"src": api_data_item,
                                                          "gradient_x_index": 0,
                                                          "gradient_y_index": 1,
                                                          "rotation": "None",
                                                          "crop_region": crop_region},
                                                  outputs=result_data_items)
            for data_item in result_data_items.values():
                window.display_data_item(data_item)


class MakeIDPCExtension:

    # required for Swift to recognize this as an extension class.
    extension_id = "nion.extension.make_idpc"

    def __init__(self, api_broker):
        # grab the api object.
        api = api_broker.get_api(version="1", ui_version="1")
        # be sure to keep a reference or it will be closed immediately.
        self.__idpc_menu_item_ref = api.create_menu_item(MakeIDPCMenuItem(api))

    def close(self):
        self.__idpc_menu_item_ref.close()


Symbolic.register_computation_type("nion.make_idpc", MakeIDPC)