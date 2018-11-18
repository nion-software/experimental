# standard libraries
import gettext

# third party libraries
# none

# local libraries
from nion.typeshed import API_1_0 as API
from nion.swift.model import DataItem
from nion.swift.model import DocumentModel

_ = gettext.gettext


double_gaussian_script = """
# imports
import math
import numpy
import scipy

# get the data
data = src.data

# first calculate the FFT
fft_data = scipy.fftpack.fftshift(scipy.fftpack.fft2(data))

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
filter = numpy.exp(-0.5 * numpy.square(rr / sigma1)) - (1.0 - weight2) * numpy.exp(
    -0.5 * numpy.square(rr / sigma2))
filtered_fft_data = fft_data * filter

# and then do invert FFT and take the real value.
result = scipy.fftpack.ifft2(scipy.fftpack.ifftshift(filtered_fft_data)).real

intensity_calibration = src.xdata.intensity_calibration
dimensional_calibrations = src.xdata.dimensional_calibrations
target.xdata = api.create_data_and_metadata_from_data(result, intensity_calibration, dimensional_calibrations)
"""


processing_descriptions = {
    "nion.extension.doublegaussian":
        { 'script': double_gaussian_script,
          'sources': [
              {"name": "src", "label": _("Source"), "requirements": [{"type": "dimensionality", "min": 2, "max": 2}]}
          ],
          "parameters": [
              {"name": "sigma1", "label": _("Sigma 1"), "type": "real", "value": 0.3, "value_default": 0.3, "value_min": 0, "value_max": 1, "control_type": "slider"},
              {"name": "sigma2", "label": _("Sigma 2"), "type": "real", "value": 0.3, "value_default": 0.3, "value_min": 0, "value_max": 1, "control_type": "slider"},
              {"name": "weight2", "label": _("Weight 2"), "type": "real", "value": 0.3, "value_default": 0.3, "value_min": 0, "value_max": 1, "control_type": "slider"},
          ],
          'title': 'Double Gaussian',
        }
}


DocumentModel.DocumentModel.register_processing_descriptions(processing_descriptions)


class DoubleGaussianMenuItem:

    menu_id = "_processing_menu"  # required, specify menu_id where this item will go
    menu_item_name = _("Double Gaussian")  # menu item name

    def menu_item_execute(self, window: API.DocumentWindow) -> None:
        document_controller = window._document_controller
        selected_display_item = document_controller.selected_display_item
        data_item = document_controller.document_model.make_data_item_with_computation("nion.extension.doublegaussian", [(selected_display_item, None)])
        if data_item:
            display_item = document_controller.document_model.get_display_item_for_data_item(data_item)
            document_controller.show_display_item(display_item)


class DoubleGaussianExtension:

    # required for Swift to recognize this as an extension class.
    extension_id = "nionswift.extension.double_gaussian"

    def __init__(self, api_broker):
        # grab the api object.
        api = api_broker.get_api(version="1", ui_version="1")
        # be sure to keep a reference or it will be closed immediately.
        self.__menu_item_ref = api.create_menu_item(DoubleGaussianMenuItem())

    def close(self):
        self.__menu_item_ref.close()
