import typing
import gettext

from nion.swift import Facade

_ = gettext.gettext

processing_descriptions = {"nion.processing.i_e_square_plot":
                               {"title": _("I E^2 Plot"), "expression": "src.xdata * xd.axis_coordinates(src.xdata, -1)**2","sources": [{"name": "src", "label": _("Source")}]}
                           }


class IESquarePlotMenuItemDelegate:
    def __init__(self, api: Facade.API_1) -> None:
        self.__api = api
        self.menu_id = "eels_menu"
        self.menu_name = _("EELS")
        self.menu_before_id = "window_menu"

    @property
    def menu_item_name(self) -> str:
        return _("[EXPERIMENTAL] I E^2 Plot")

    def menu_item_execute(self, window: Facade.DocumentWindow) -> None:
        selected_display_item = window._document_window.selected_display_item
        if not selected_display_item or not selected_display_item.data_item or not selected_display_item.data_item.xdata:
            return
        window._document_window.document_model.get_processing_new("nion.processing.i_e_square_plot", selected_display_item, selected_display_item.data_item)


class IESquarePlotExtension:

    extension_id = "nion.experimental.i_e_square_plot"

    def __init__(self, api_broker: typing.Any):
        api = typing.cast(Facade.API_1, api_broker.get_api(version="~1.0"))
        self.__i_e_square_plot_menu_item_ref = api.create_menu_item(IESquarePlotMenuItemDelegate(api))

    def close(self) -> None:
        self.__i_e_square_plot_menu_item_ref.close()
        self.__i_e_square_plot_menu_item_ref = typing.cast(typing.Any, None)
