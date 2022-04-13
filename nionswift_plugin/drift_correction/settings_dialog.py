import asyncio
import typing

from nion.ui import Declarative
from nion.utils import Event, Converter
from nion.swift.model import PlugInManager
from nion.typeshed import API_1_0


class SettingsUIHandler(Declarative.Handler):
    def __init__(self, api: API_1_0.API, settings_object: typing.Any, stem_controller: typing.Any, ui_view: typing.Mapping[str, typing.Any]):
        super().__init__()
        self.__api = api
        self.__stem_controller = stem_controller
        self.ui_view = ui_view
        self.property_changed_event = Event.Event()
        self.on_closed = None
        self.float_to_string_converter = Converter.FloatToStringConverter(format='{:.3g}')
        self.integer_to_string_converter = Converter.IntegerToStringConverter()
        self.bool_to_float_converter = BoolToFloatConverter()
        self.__settings_object = settings_object
        self.__settings_enabled_list = []
        self.__settings_value_property_list = []
        for i, setting in enumerate(getattr(settings_object, '_settings_dialog_ui_elements', list())):
            self.__create_settings_value_property(f'setting_{i}_value', setting['property_name'], multiplier=setting.get('multiplier'))
            self.__settings_value_property_list.append(f'setting_{i}_value')
            self.__create_property(f'setting_{i}_enabled', value=True)
            self.__settings_enabled_list.append(f'setting_{i}_enabled')
        self.__create_property('restore_defaults_button_enabled', value=True)
        self.__settings_enabled_list.append('restore_defaults_button_enabled')

    def init_handler(self) -> None:
        self.update_ui({'state': None})

    def close(self) -> None:
        if callable(self.on_closed):
            self.on_closed()
        super().close()

    def update_ui(self, result_dict: typing.Mapping[str, typing.Any]) -> None:
        if result_dict.get('state') == 'running':
            for setting in self.__settings_enabled_list:
                setattr(self, setting, False)
        else:
            for setting in self.__settings_enabled_list:
                setattr(self, setting, True)

    def update_settings_values(self) -> None:
        for settings_value_name in self.__settings_value_property_list:
            self.property_changed_event.fire(settings_value_name)

    def restore_defaults_clicked(self, widget: typing.Any) -> None:
        default_settings = self.__settings_object.__class__()
        for setting in getattr(default_settings, '_settings_dialog_ui_elements', list()):
            setattr(self.__settings_object, setting['property_name'], getattr(default_settings, setting['property_name']))
            self.__settings_object.save_to_as2(self.__stem_controller, setting['property_name'])
        self.update_settings_values()

    def __create_property(self, name: str, value: typing.Any=None) -> None:
        mangled_name = '_SettingsUIHandler__' + name
        setattr(self, mangled_name, value)
        def getter(self: SettingsUIHandler) -> typing.Any:
            return getattr(self, mangled_name)

        def setter(self: SettingsUIHandler, value: typing.Any) -> None:
            setattr(self, mangled_name, value)
            self.property_changed_event.fire(name)

        setattr(SettingsUIHandler, name, property(getter, setter))

    def __create_settings_value_property(self, name: str, property_name: str, multiplier: typing.Optional[float]=None) -> None:
        def getter(self: SettingsUIHandler) -> typing.Any:
            if multiplier is not None:
                return getattr(self.__settings_object, property_name) / multiplier
            else:
                return getattr(self.__settings_object, property_name)

        def setter(self: SettingsUIHandler, value: typing.Any) -> None:
            if multiplier is not None:
                setattr(self.__settings_object, property_name, value * multiplier)
            else:
                setattr(self.__settings_object, property_name, value)
            self.__settings_object.save_to_as2(self.__stem_controller, property_name)
            self.property_changed_event.fire(name)

        setattr(SettingsUIHandler, name, property(getter, setter))


class SettingsUI:

    def get_ui_handler(self, settings_object: typing.Any, api_broker: PlugInManager.APIBroker, event_loop: typing.Optional[asyncio.AbstractEventLoop]=None, **kwargs: typing.Any) -> SettingsUIHandler:
        api = api_broker.get_api('~1.0')
        ui = api_broker.get_ui('~1.0')
        stem_controller = kwargs.get('stem_controller')
        ui_view = self.__create_ui_view(ui, settings_object, title=str(kwargs.get('title')))
        return SettingsUIHandler(api, settings_object, stem_controller, ui_view)

    def __create_ui_view(self, ui: Declarative.DeclarativeUI, settings_object: typing.Any, title: typing.Optional[str]=None, **kwargs: typing.Any) -> typing.Mapping[str, typing.Any]:
        def spacing(size: int)  -> typing.Mapping[str, typing.Any]:
            return ui.create_spacing(size)

        def stretch() -> typing.Mapping[str, typing.Any]:
            return ui.create_stretch()

        steps = []
        for i, setting in enumerate(getattr(settings_object, '_settings_dialog_ui_elements', list())):
            ui_element = setting['ui_element']
            value_type = setting['value_type']
            row = None
            if ui_element == 'line_edit':
                label = ui.create_label(text=setting['display_name'], tool_tip=setting.get('description'))
                converter_str = ''
                if value_type == 'int':
                    converter_str = ', converter=integer_to_string_converter'
                elif value_type == 'float':
                    converter_str = ', converter=float_to_string_converter'
                widget = ui.create_line_edit(text=f'@binding(setting_{i}_value{converter_str})',
                                             enabled=f'@binding(setting_{i}_enabled)')
                row = ui.create_row(label, spacing(5), widget, stretch(), margin=4)
            elif ui_element == 'check_box':
                widget = ui.create_check_box(text=setting['display_name'], tool_tip=setting.get('description'),
                                             checked=f'@binding(setting_{i}_value, converter=bool_to_float_converter)',
                                             enabled=f'@binding(setting_{i}_enabled)')
                row = ui.create_row(widget, stretch(), margin=4)

            steps.append(row)

        restore_defaults_button = ui.create_push_button(text='Restore defaults', on_clicked='restore_defaults_clicked', enabled='@binding(restore_defaults_button_enabled)')
        row = ui.create_row(stretch(), restore_defaults_button, margin=4)
        steps.append(row)

        column = ui.create_column(*steps, spacing=4) # type: ignore

        return ui.create_modeless_dialog(column, title=title, margin=4)


class BoolToFloatConverter:

    def convert(self, value: bool) -> float:
        return float(value)

    def convert_back(self, value: float) -> bool:
        return bool(value)
