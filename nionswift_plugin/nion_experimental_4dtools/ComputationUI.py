# -*- coding: utf-8 -*-
"""
Created on Mon Dec 17 18:17:31 2018

@author: Andreas
"""

from nion.swift.model import Symbolic


class ComputationUIPanelDelegate(object):

    def __init__(self, api):
        self.__api = api
        self.panel_id = 'ComputationUI-Panel'
        self.panel_name = 'Computation'
        self.panel_positions = ['left', 'right']
        self.panel_position = 'right'
        self.api = api

    def create_panel_widget(self, ui, document_controller):
        self.ui = ui
        self.document_controller = document_controller
        self.__display_item_changed_event_listener = (
                                 document_controller._document_controller.focused_display_item_changed_event.listen(
                                                                                         self.__display_item_changed))
        self.__computation_updated_event_listeners = []

        self.column = ui.create_column_widget()
        self.column.add(self.ui.create_label_widget('test'))

        return self.column

    def __update_computation_ui(self, computations):
        self.column._widget.remove_all()
        for computation in computations:
            create_panel_widget = getattr(computation, 'create_panel_widget', None)
            if create_panel_widget is None:
                compute_class = Symbolic._computation_types.get(computation.processing_id)
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
                    self.column.add_spacing(5)
        self.column.add_stretch()


    def __display_item_changed(self, display_item):
        data_item = display_item.data_item if display_item else None
        if data_item:
            for listener in self.__computation_updated_event_listeners:
                listener.close()
            self.__computation_updated_event_listeners = []
            computations = self.document_controller._document_controller.document_model.computations
            computations_involved = []
            for computation in computations:
                for result in computation.results:
                    if result.specifier.get('uuid') == str(data_item.uuid):
                        computations_involved.append(computation)
                src = computation.get_input('src')
                if src and str(src.uuid) == str(data_item.uuid):
                    computations_involved.append(computation)

            self.__update_computation_ui(computations_involved)
            for _ in computations_involved:
                self.__computation_updated_event_listeners.append(
                        self.document_controller._document_controller.document_model.computation_updated_event.listen(
                                lambda data_item: self.__update_computation_ui(computations_involved)))
            if not computations_involved:
                self.column._widget.remove_all()

    def close(self):
        self.__display_item_changed_event_listener.close()
        self.__display_item_changed_event_listener = None

class ComputationUIExtension(object):
    extension_id = 'nion.extension.computation_ui'

    def __init__(self, api_broker):
        api = api_broker.get_api(version='1', ui_version='1')
        self.__panel_ref = api.create_panel(ComputationUIPanelDelegate(api))

    def close(self):
        self.__panel_ref.close()
        self.__panel_ref = None
