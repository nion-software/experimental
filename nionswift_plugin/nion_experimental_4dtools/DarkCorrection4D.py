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
dark_area = np.rint(np.array(dark_area_region.bounds) * np.array((data_shape[:2], data_shape[:2]))).astype(np.int)
crop_area = np.rint(np.array(crop_region.bounds) * np.array((data_shape[2:], data_shape[2:]))).astype(np.int)

dark_image = np.mean(data[dark_area[0, 0]:dark_area[0, 0]+dark_area[1, 0],
                          dark_area[0, 1]:dark_area[0, 1]+dark_area[1, 1],
                          crop_area[0, 0]:crop_area[0, 0]+crop_area[1, 0],
                          crop_area[0, 1]:crop_area[0, 1]+crop_area[1, 1]], axis=(0, 1))

new_data = data[..., crop_area[0, 0]:crop_area[0, 0]+crop_area[1, 0],
                     crop_area[0, 1]:crop_area[0, 1]+crop_area[1, 1]] - dark_image
try:
    gain_uuid = uuid.UUID(gain_image_uuid)
except ValueError:
    pass
else:
    gain_image = api.library.get_data_item_by_uuid(gain_uuid)
    if gain_image:
        gain_data = gain_image.xdata.data
        if gain_data.shape == new_data.shape[2:]:
            new_data *= gain_image.xdata.data
        elif gain_data.shape == data.shape[2:]:
            new_data *= gain_image.xdata.data[crop_area[0, 0]:crop_area[0, 0]+crop_area[1, 0],
                                              crop_area[0, 1]:crop_area[0, 1]+crop_area[1, 1]]
        else:
            raise ValueError('Shape of gain image has to match last two dimensions of input data.')

if bin_spectrum:
    target.set_data(np.sum(new_data, axis=-2))
    target.set_dimensional_calibrations(src1.xdata.dimensional_calibrations[:2] + src1.xdata.dimensional_calibrations[3:])
else:
    target.set_data(new_data)
    target.set_dimensional_calibrations(src1.xdata.dimensional_calibrations[:])
target.set_intensity_calibration(src1.xdata.intensity_calibration)
"""

correct_dark_processing_descriptions = {
    "nion.4d_dark_correction":
        {'script': correct_dark_script,
         'sources': [
                     {'name': 'src1', 'label': 'Source',
                      'regions': [{'name': 'crop_region', 'type': 'rectangle'}],
                      'requirements': [{'type': 'dimensionality', 'min': 4, 'max': 4}]},
                     {'name': 'src2', 'label': 'Total Bin Data Item',
                      'regions': [{'name': 'dark_area_region', 'type': 'rectangle'}]}
                     ],
         'parameters': [{'name': 'bin_spectrum', 'type': 'boolean', 'value_default': True, 'value': True,
                         'label': 'Bin spectra to 1d'},
                        {'name': 'gain_image_uuid', 'type': 'string', 'label': 'Gain image uuid', 'value': '',
                         'value_default': ''}],
         'title': '4D dark correction'
         }
}

total_bin_4D_SI_script = """
import numpy as np
target.set_data(np.mean(src.xdata.data, axis=(-2, -1)))
target.set_dimensional_calibrations(src.xdata.dimensional_calibrations[:2])
target.set_intensity_calibration(src.xdata.intensity_calibration)
"""
calculate_average_processing_descriptions = {
    "nion.total_bin_4d_SI":
        {'script': total_bin_4D_SI_script,
         'sources': [
                     {'name': 'src', 'label': 'Source',
                      'regions': [],
                      'requirements': [{'type': 'dimensionality', 'min': 4, 'max': 4}]}
                     ],
         'title': 'Total bin 4D'
         }
}

class DarkCorrection4DMenuItem:

    menu_id = "4d_tools_menu"  # required, specify menu_id where this item will go
    menu_name = _("4D Tools") # optional, specify default name if not a standard menu
    menu_before_id = "window_menu" # optional, specify before menu_id if not a standard menu
    menu_item_name = _("4D Dark Correction")  # menu item name

    DocumentModel.DocumentModel.register_processing_descriptions(correct_dark_processing_descriptions)
    DocumentModel.DocumentModel.register_processing_descriptions(calculate_average_processing_descriptions)

    def menu_item_execute(self, window: API.DocumentWindow) -> None:
        document_controller = window._document_controller
        selected_display_item = document_controller.selected_display_item
        data_item = (selected_display_item.data_items[0] if
                     selected_display_item and len(selected_display_item.data_items) > 0 else None)

        total_bin_data_item = document_controller.document_model.make_data_item_with_computation(
            "nion.total_bin_4d_SI", [(selected_display_item, None)],
            {'src': []})
        if total_bin_data_item and data_item:
            total_bin_display_item = document_controller.document_model.get_display_item_for_data_item(
                                                                                                  total_bin_data_item)
            document_controller.show_display_item(total_bin_display_item)
            api_total_bin_data_item = Facade.DataItem(total_bin_data_item)
            api_data_item = Facade.DataItem(data_item)
            dark_subtract_area_graphic = api_total_bin_data_item.add_rectangle_region(0.8, 0.5, 0.4, 1.0)
            dark_subtract_area_graphic.label = 'Dark subtract area'
            crop_region = api_data_item.add_rectangle_region(0.5, 0.5, 1.0, 1.0)
            crop_region.label = 'Crop'
            
            dark_subtract_area_graphic._graphic.is_bounds_constrained = True
            crop_region._graphic.is_bounds_constrained = True
            
            dark_corrected_data_item = document_controller.document_model.make_data_item_with_computation(
                    "nion.4d_dark_correction", [(selected_display_item, None), (total_bin_display_item, None)],
                    {"src1": [crop_region._graphic], "src2": [dark_subtract_area_graphic._graphic]})
            if dark_corrected_data_item:
                 dark_corrected_display_item = document_controller.document_model.get_display_item_for_data_item(
                                                                                             dark_corrected_data_item)
                 document_controller.show_display_item(dark_corrected_display_item)

class DarkCorrection4DExtension:

    # required for Swift to recognize this as an extension class.
    extension_id = "nion.extension.4d_dark_correction"

    def __init__(self, api_broker):
        # grab the api object.
        api = api_broker.get_api(version="1", ui_version="1")
        # be sure to keep a reference or it will be closed immediately.
        self.__menu_item_ref = api.create_menu_item(DarkCorrection4DMenuItem())

    def close(self):
        self.__menu_item_ref.close()