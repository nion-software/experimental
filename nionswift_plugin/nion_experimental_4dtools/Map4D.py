# system imports
import gettext
from nion.swift.model import DocumentModel

# local libraries
from nion.typeshed import API_1_0 as API

_ = gettext.gettext

map_4d_script = """
import numpy as np
mask_data = region.mask_xdata_with_shape(src.xdata.data_shape[2:]).data
data = src.xdata.data
data_shape = np.array(data.shape)
crop_area = np.rint(np.array(region.bounds) * np.array((data_shape[2:], data_shape[2:]))).astype(np.int)
new_data = np.sum(src.xdata.data[...,
                                 crop_area[0,0]:crop_area[0,0]+crop_area[1,0],
                                 crop_area[0,1]:crop_area[0,1]+crop_area[1,1]], axis=(-2, -1))
target.set_data(new_data)
target.set_dimensional_calibrations(src.xdata.dimensional_calibrations[:2])
target.set_intensity_calibration(src.xdata.intensity_calibration)
"""

processing_descriptions = {
    "nion.map_4d":
        {'script': map_4d_script,
         'sources': [
                     {'name': 'src', 'label': 'Source',
                      'regions': [{'name': 'region', 'type': 'rectangle', 'params': {'label': 'Map Region'}}],
                      'requirements': [{'type': 'dimensionality', 'min': 4, 'max': 4}]}
                     ],
         'title': 'Map 4D'
         }
}

class Map4DMenuItem:

    menu_id = "4d_tools_menu"  # required, specify menu_id where this item will go
    menu_name = _("4D Tools") # optional, specify default name if not a standard menu
    menu_before_id = "window_menu" # optional, specify before menu_id if not a standard menu
    menu_item_name = _("Map 4D")  # menu item name

    DocumentModel.DocumentModel.register_processing_descriptions(processing_descriptions)

    def menu_item_execute(self, window: API.DocumentWindow) -> None:
        document_controller = window._document_controller
        selected_display_item = document_controller.selected_display_item
        
        data_item = document_controller.document_model.make_data_item_with_computation("nion.map_4d",
                                                                                       [(selected_display_item, None)],
                                                                                       {'src': [None]})
        if data_item:
            display_item = document_controller.document_model.get_display_item_for_data_item(data_item)
            document_controller.show_display_item(display_item)

class Map4DExtension:

    # required for Swift to recognize this as an extension class.
    extension_id = "nion.extension.map_4d"

    def __init__(self, api_broker):
        # grab the api object.
        api = api_broker.get_api(version="1", ui_version="1")
        # be sure to keep a reference or it will be closed immediately.
        self.__menu_item_ref = api.create_menu_item(Map4DMenuItem())

    def close(self):
        self.__menu_item_ref.close()