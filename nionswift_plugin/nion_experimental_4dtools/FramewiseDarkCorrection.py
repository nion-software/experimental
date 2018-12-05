# system imports
import gettext
from nion.swift.model import DocumentModel
from nion.swift import Facade

# local libraries
from nion.typeshed import API_1_0 as API

_ = gettext.gettext

correct_dark_script = """
import numpy as np
import uuid

data = src1.xdata.data
data_shape = np.array(src1.xdata.data.shape)
cam_center = int(round(data_shape[-2]/2)) if camera_center == -1 else camera_center
spectrum_area = np.rint(np.array(spectrum_region.bounds) * data_shape[2:]).astype(np.int)
top_dark_area = np.rint(np.array(top_dark_region.bounds) * data_shape[2:]).astype(np.int)
bottom_dark_area = np.rint(np.array(bottom_dark_region.bounds) * data_shape[2:]).astype(np.int)
spectrum_range_y = np.array((spectrum_area[0,0], spectrum_area[0,0] + spectrum_area[1,0]))
spectrum_range_x = np.array((spectrum_area[0,1], spectrum_area[0,1] + spectrum_area[1,1]))
top_dark_area_range_y = np.array((top_dark_area[0,0], top_dark_area[0,0] + top_dark_area[1, 0]))
bottom_dark_area_range_y = np.array((bottom_dark_area[0,0], bottom_dark_area[0,0] + bottom_dark_area[1, 0]))

if (cam_center >= spectrum_range_y).all(): # spectrum is above center
    dark_image = np.mean(data[..., top_dark_area_range_y[0]:top_dark_area_range_y[1],
                              spectrum_range_x[0]:spectrum_range_x[1]], axis=-2, keepdims=True)
    corrected_image = (data[..., spectrum_range_y[0]:spectrum_range_y[1], spectrum_range_x[0]:spectrum_range_x[1]] - 
                       np.repeat(dark_image, spectrum_range_y[1]-spectrum_range_y[0], axis=-2))
elif (cam_center <= spectrum_range_y).all(): # spectrum is below center
    dark_image = np.mean(data[..., bottom_dark_area_range_y[0]:bottom_dark_area_range_y[1],
                              spectrum_range_x[0]:spectrum_range_x[1]], axis=-2, keepdims=True)
    corrected_image = (data[..., spectrum_range_y[0]:spectrum_range_y[1], spectrum_range_x[0]:spectrum_range_x[1]] -
                       np.repeat(dark_image, spectrum_range_y[1]-spectrum_range_y[0], axis=-2))
else: # spectrum is on top of center
    dark_image = np.mean(data[..., top_dark_area_range_y[0]:top_dark_area_range_y[1],
                              spectrum_range_x[0]:spectrum_range_x[1]], axis=-2, keepdims=True)
    corrected_image_top = (data[..., spectrum_range_y[0]:cam_center, spectrum_range_x[0]:spectrum_range_x[1]] -
                           np.repeat(dark_image, cam_center-spectrum_range_y[0], axis=-2))
    dark_image = np.mean(data[..., bottom_dark_area_range_y[0]:bottom_dark_area_range_y[1],
                              spectrum_range_x[0]:spectrum_range_x[1]], axis=-2, keepdims=True)
    corrected_image_bot = (data[..., cam_center:spectrum_range_y[1], spectrum_range_x[0]:spectrum_range_x[1]] -
                           np.repeat(dark_image, spectrum_range_y[1]-cam_center, axis=-2))
    corrected_image = np.concatenate((corrected_image_top, corrected_image_bot), axis=-2)
    corrected_image_top = None
    corrected_image_bot = None
dark_image = None # don't hold references to unused objects so that garbage collector can free the memory

try:
    gain_uuid = uuid.UUID(gain_image_uuid)
except ValueError:
    pass
else:
    gain_image = api.library.get_data_item_by_uuid(gain_uuid)
    if gain_image:
        gain_data = gain_image.xdata.data
        if gain_data.shape == corrected_image.shape[2:]:
            corrected_image *= gain_image.xdata.data
        elif gain_data.shape == data.shape[2:]:
            corrected_image *= gain_image.xdata.data[spectrum_range_y[0]:spectrum_range_y[1],
                                                     spectrum_range_x[0]:spectrum_range_x[1]]
        else:
            raise ValueError('Shape of gain image has to match last two dimensions of input data.')

if bin_spectrum:
    target.set_data(np.sum(corrected_image, axis=-2))
    target.set_dimensional_calibrations(src1.xdata.dimensional_calibrations[:2] +
                                        src1.xdata.dimensional_calibrations[3:])
else:
    target.set_data(corrected_image)
    target.set_dimensional_calibrations(src1.xdata.dimensional_calibrations[:])
target.set_intensity_calibration(src1.xdata.intensity_calibration)
"""

correct_dark_processing_descriptions = {
    "nion.framewise_dark_correction":
        {'script': correct_dark_script,
         'sources': [
                     {'name': 'src1', 'label': 'Source',
                      'regions': [],
                      'requirements': [{'type': 'dimensionality', 'min': 4, 'max': 4}]},
                     {'name': 'src2', 'label': 'Average Data Item',
                      'regions': [{'name': 'spectrum_region', 'type': 'rectangle'},
                                  {'name': 'top_dark_region', 'type': 'rectangle'},
                                  {'name': 'bottom_dark_region', 'type': 'rectangle'}]}
                     ],
         'parameters': [{'name': 'bin_spectrum', 'type': 'boolean', 'value_default': True, 'value': True,
                         'label': 'Bin spectra to 1d'},
                        {'name': 'camera_center', 'type': 'integral', 'value_default': -1, 'value': -1,
                         'label': 'Camera center', 'value_min': -1, 'value_max': 2048},
                         {'name': 'gain_image_uuid', 'type': 'string', 'label': 'Gain image uuid', 'value': '',
                         'value_default': ''}],
         'title': 'Framewise dark correction'
         }
}

calculate_average_script = """
import numpy as np
target.set_data(np.mean(src.xdata.data, axis=(0, 1)))
target.set_dimensional_calibrations(src.xdata.dimensional_calibrations[2:])
target.set_intensity_calibration(src.xdata.intensity_calibration)
"""
calculate_average_processing_descriptions = {
    "nion.calculate_4d_average":
        {'script': calculate_average_script,
         'sources': [
                     {'name': 'src', 'label': 'Source',
                      'regions': [],
                      'requirements': [{'type': 'dimensionality', 'min': 4, 'max': 4}]}
                     ],
         'title': 'Frame Average'
         }
}

class FramewiseDarkMenuItem:

    menu_id = "4d_tools_menu"  # required, specify menu_id where this item will go
    menu_name = _("4D Tools") # optional, specify default name if not a standard menu
    menu_before_id = "window_menu" # optional, specify before menu_id if not a standard menu
    menu_item_name = _("Framewise Dark Correction")  # menu item name

    DocumentModel.DocumentModel.register_processing_descriptions(correct_dark_processing_descriptions)
    DocumentModel.DocumentModel.register_processing_descriptions(calculate_average_processing_descriptions)

    def menu_item_execute(self, window: API.DocumentWindow) -> None:
        document_controller = window._document_controller
        selected_display_item = document_controller.selected_display_item
        
        average_data_item = document_controller.document_model.make_data_item_with_computation(
                                                                                "nion.calculate_4d_average",
                                                                                [(selected_display_item, None)],
                                                                                {'src': []})
        if average_data_item:
            average_display_item = document_controller.document_model.get_display_item_for_data_item(average_data_item)
            document_controller.show_display_item(average_display_item)
            api_average_data_item = Facade.DataItem(average_data_item)
            spectrum_graphic = api_average_data_item.add_rectangle_region(0.5, 0.5, 0.1, 1.0)
            spectrum_graphic.label = 'Spectrum'
            bottom_dark_graphic = api_average_data_item.add_rectangle_region(0.7, 0.5, 0.1, 1.0)
            bottom_dark_graphic.label = 'Bottom dark area'
            top_dark_graphic = api_average_data_item.add_rectangle_region(0.3, 0.5, 0.1, 1.0)
            top_dark_graphic.label = 'Top dark area'
            spectrum_graphic._graphic.is_bounds_constrained = True
            bottom_dark_graphic._graphic.is_bounds_constrained = True
            top_dark_graphic._graphic.is_bounds_constrained = True
            dark_corrected_data_item = document_controller.document_model.make_data_item_with_computation(
                    "nion.framewise_dark_correction", [(selected_display_item, None), (average_display_item, None)],
                    {"src1": [], "src2": [spectrum_graphic._graphic, top_dark_graphic._graphic,
                                          bottom_dark_graphic._graphic]})
            if dark_corrected_data_item:
                dark_corrected_display_item = document_controller.document_model.get_display_item_for_data_item(dark_corrected_data_item)
                document_controller.show_display_item(dark_corrected_display_item)
            
class FramewiseDarkExtension:

    # required for Swift to recognize this as an extension class.
    extension_id = "nion.extension.framewise_dark_correction"

    def __init__(self, api_broker):
        # grab the api object.
        api = api_broker.get_api(version="1", ui_version="1")
        # be sure to keep a reference or it will be closed immediately.
        self.__menu_item_ref = api.create_menu_item(FramewiseDarkMenuItem())

    def close(self):
        self.__menu_item_ref.close()