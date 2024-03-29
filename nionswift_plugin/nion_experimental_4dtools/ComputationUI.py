# -*- coding: utf-8 -*-
"""
Created on Mon Dec 17 18:17:31 2018

@author: Andreas
"""

import functools
import typing

from nion.swift import Facade
from nion.swift.model import DataItem
from nion.swift.model import DisplayItem
from nion.swift.model import Symbolic
from nion.ui import UserInterface
from nion.utils import Event


class ComputationUIPanelDelegate(object):

    def __init__(self, api: Facade.API_1) -> None:
        self.__api = api
        self.panel_id = 'ComputationUI-Panel'
        self.panel_name = 'Computation'
        self.panel_positions = ['left', 'right']
        self.panel_position = 'right'
        self.api = api

    def create_panel_widget(self, ui: Facade.UserInterface, document_controller: Facade.DocumentWindow) -> Facade.ColumnWidget:
        self.ui = ui
        self.document_controller = document_controller
        self.__display_item_changed_event_listener = (
                                 document_controller._document_controller.focused_display_item_changed_event.listen(
                                                                                         self.__display_item_changed))
        self.__computation_updated_event_listener: typing.Optional[Event.EventListener] = None
        self.__progress_updated_event_listeners: typing.List[Event.EventListener] = []
        self.column = ui.create_column_widget()

        return self.column

    def __update_progress(self, progress_bar_widget: Facade.ProgressBarWidget, minimum: int, maximum: int, value: int) -> None:
        if minimum != progress_bar_widget.minimum:
            progress_bar_widget.minimum = minimum
        if maximum != progress_bar_widget.maximum:
            progress_bar_widget.maximum = maximum
        progress_bar_widget.value = value

    def __update_computation_ui(self, computations: typing.Sequence[Symbolic.Computation]) -> None:
        typing.cast(UserInterface.BoxWidget, self.column._widget).remove_all()
        for computation in computations:
            create_panel_widget = getattr(computation, 'create_panel_widget', None)
            if create_panel_widget is None:
                compute_class = Symbolic._computation_types.get(computation.processing_id or str())
                if compute_class:
                    api_computation = self.api._new_api_object(computation)
                    api_computation.api = self.api
                    try:
                        compute_class(api_computation)
                    except IndexError:
                        pass
                    create_panel_widget = getattr(computation, 'create_panel_widget', None)
            if create_panel_widget:
                try:
                    widget = create_panel_widget(self.ui, self.document_controller)
                except Exception as e:
                    print(str(e))
                    import traceback
                    traceback.print_exc()
                else:
                    self.column.add(self.ui.create_label_widget(computation.processing_id))
                    self.column.add_spacing(2)
                    self.column.add(widget)
                    if hasattr(computation, 'progress_updated_event'):
                        progress_bar = self.ui.create_progress_bar_widget()
                        listener = computation.progress_updated_event.listen(functools.partial(self.__update_progress, progress_bar))
                        self.__progress_updated_event_listeners.append(listener)
                        self.column.add_spacing(10)
                        progress_row = self.ui.create_row_widget()
                        progress_row.add_spacing(80)
                        progress_row.add(progress_bar)
                        progress_row.add_spacing(5)
                        self.column.add(progress_row)
                    self.column.add_spacing(15)
        self.column.add_stretch()

    def __get_computations_involved(self, data_item: DataItem.DataItem) -> typing.Sequence[Symbolic.Computation]:
        computations = self.document_controller._document_controller.document_model.computations
        computations_involved: typing.List[Symbolic.Computation] = []
        for computation in computations:
            for result in computation.results:
                if data_item in result.output_items:
                    if not computation in computations_involved:
                        computations_involved.append(computation)
                    break
            for variable in computation.variables:
                if data_item in variable.input_items:
                    if not computation in computations_involved:
                        computations_involved.append(computation)
                    break
        return computations_involved

    def __display_item_changed(self, display_item: DisplayItem.DisplayItem) -> None:
        data_item = display_item.data_item if display_item else None
        if data_item:
            if self.__computation_updated_event_listener:
                self.__computation_updated_event_listener.close()
                self.__computation_updated_event_listener = None
            for listener in self.__progress_updated_event_listeners:
                listener.close()
            self.__progress_updated_event_listeners = []
            self.__update_computation_ui(self.__get_computations_involved(data_item))

            def computation_updated(computation: Symbolic.Computation) -> None:
                # NOTE: computation_updated_event may be called from a background thread.
                # queue the update to the main thread.

                def _computation_updated(computation: Symbolic.Computation) -> None:
                    assert data_item
                    self.__update_computation_ui(self.__get_computations_involved(data_item))

                self.document_controller._document_controller.queue_task(functools.partial(_computation_updated, computation))

            self.__computation_updated_event_listener = self.document_controller._document_controller.document_model.computation_updated_event.listen(computation_updated)
        else:
            typing.cast(UserInterface.BoxWidget, self.column._widget).remove_all()

    def close(self) -> None:
        self.__display_item_changed_event_listener.close()
        self.__display_item_changed_event_listener = typing.cast(typing.Any, None)

class ComputationUIExtension(object):
    extension_id = 'nion.extension.computation_ui'

    def __init__(self, api_broker: typing.Any) -> None:
        api = api_broker.get_api(version='1', ui_version='1')
        self.__panel_ref = api.create_panel(ComputationUIPanelDelegate(api))

    def close(self) -> None:
        self.__panel_ref.close()
        self.__panel_ref = None
