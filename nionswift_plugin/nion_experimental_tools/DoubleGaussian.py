# standard libraries
import gettext
import math
import typing

# third party libraries
import numpy

# local libraries
from nion.data import DataAndMetadata
from nion.data import Calibration
from nion.swift import Facade
from nion.swift.model import Symbolic
from nion.swift.model import Graphics
from nion.typeshed import API_1_0

_ = gettext.gettext


class DoubleGaussian(Symbolic.ComputationHandlerLike):
    computation_id = "nion.extension.doublegaussian"
    label = _("Double Gaussian")
    inputs = {"src": {"label": _("Source"), "data_type": "xdata"},
              "weight2": {"label": _("Weight")},
              "ring_graphic": {"label": _("Change the radii of the ring graphic in the FFT\nto adjust the Gaussian's sigmas.")}}
    outputs = {"target": {"label": _("Result")},
               "filtered_fft": {"label": _("Filtered FFT")}}

    def __init__(self, computation: Facade.Computation, **kwargs: typing.Any) -> None:
        self.computation = computation

    def execute(self, *, src: typing.Optional[API_1_0.DataItem] = None, weight2: float = 1.0,
                ring_graphic: typing.Optional[API_1_0.Graphic] = None, **kwargs: typing.Any) -> None:
        assert src
        assert ring_graphic

        sigma1 = ring_graphic.get_property("radius_2") * 2.0
        sigma2 = ring_graphic.get_property("radius_1") * 2.0
        # get the data
        data = src.data

        # first calculate the FFT
        shifted_fft_data = numpy.fft.fft2(data)
        fft_data = numpy.fft.fftshift(shifted_fft_data)

        # next, set up xx, yy arrays to be linear indexes for x and y coordinates ranging
        # from -width/2 to width/2 and -height/2 to height/2.
        yy_min = int(math.floor(-data.shape[0] / 2))
        yy_max = int(math.floor(data.shape[0] / 2))
        xx_min = int(math.floor(-data.shape[1] / 2))
        xx_max = int(math.floor(data.shape[1] / 2))
        xx, yy = numpy.meshgrid(numpy.linspace(yy_min, yy_max, data.shape[0]),
                                numpy.linspace(xx_min, xx_max, data.shape[1]))

        # calculate the pixel distance from the center
        rr = numpy.sqrt(numpy.square(xx) + numpy.square(yy)) / (data.shape[0] * 0.5)

        # finally, apply a filter to the Fourier space data.
        filter = numpy.exp(-0.5 * numpy.square(rr / sigma1)) - (1.0 - weight2) * numpy.exp(-0.5 * numpy.square(rr / sigma2))
        filtered_fft_data = fft_data * filter

        # and then do invert FFT and take the real value.
        shifted_filtered_fft_data = numpy.fft.ifftshift(filtered_fft_data)
        # make sure the filtered image has the same mean as the unfiltered image
        shifted_filtered_fft_data[numpy.unravel_index(0, data.shape)] = shifted_fft_data[numpy.unravel_index(0, data.shape)]
        result = numpy.fft.ifft2(shifted_filtered_fft_data).real

        intensity_calibration = src.xdata.intensity_calibration
        dimensional_calibrations = src.xdata.dimensional_calibrations
        self.__filtered_xdata = DataAndMetadata.new_data_and_metadata(result, intensity_calibration, dimensional_calibrations)
        fft_dimensional_calibrations = [Calibration.Calibration((-0.5 - 0.5 * data_shape_n) / (dimensional_calibration.scale * data_shape_n), 1.0 / (dimensional_calibration.scale * data_shape_n),
                                                        "1/" + dimensional_calibration.units) for
                                        dimensional_calibration, data_shape_n in zip(dimensional_calibrations, data.shape)]
        self.__filtered_fft_xdata = DataAndMetadata.new_data_and_metadata(filtered_fft_data, dimensional_calibrations=fft_dimensional_calibrations)

    def commit(self) -> None:
        self.computation.set_referenced_xdata("target", self.__filtered_xdata)
        self.computation.set_referenced_xdata("filtered_fft", self.__filtered_fft_xdata)


def double_gaussian(api: API_1_0.API, window: API_1_0.DocumentWindow, data_item: Facade.DataItem) -> tuple[Facade.DataItem, Facade.DataItem]:
    result_data_item = api.library.create_data_item()
    fft_data_item = api.library.create_data_item()
    graphic = Graphics.RingGraphic()
    graphic.radius_1 = 0.15
    graphic.radius_2 = 0.25
    fft_data_item.display._display_item.add_graphic(graphic)
    api.library.create_computation("nion.extension.doublegaussian",
                                   inputs={"src": data_item,
                                           "weight2": 0.3,
                                           "ring_graphic": Facade.Graphic(graphic)},
                                   outputs={"target": result_data_item,
                                            "filtered_fft": fft_data_item})
    window.display_data_item(result_data_item)
    window.display_data_item(fft_data_item)

    return result_data_item, fft_data_item


class DoubleGaussianMenuItem:

    menu_id = "_processing_menu"  # required, specify menu_id where this item will go
    menu_item_name = _("Double Gaussian")  # menu item name#

    def __init__(self, api: Facade.API_1) -> None:
        self.__api = api

    def menu_item_execute(self, window: API_1_0.DocumentWindow) -> None:
        # document_controller = window._document_controller
        # selected_display_item = document_controller.selected_display_item
        input_data_item = window.target_data_item
        if not input_data_item:
            return
        double_gaussian(self.__api, window, input_data_item)


class DoubleGaussianExtension:

    # required for Swift to recognize this as an extension class.
    extension_id = "nionswift.extension.double_gaussian"

    def __init__(self, api_broker: typing.Any) -> None:
        # grab the api object.
        api = api_broker.get_api(version="1", ui_version="1")
        # be sure to keep a reference or it will be closed immediately.
        self.__menu_item_ref = api.create_menu_item(DoubleGaussianMenuItem(api))

    def close(self) -> None:
        self.__menu_item_ref.close()


Symbolic.register_computation_type(DoubleGaussian.computation_id, DoubleGaussian)