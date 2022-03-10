import typing
import threading
import time

from nion.swift.model import PlugInManager
from nion.ui import Declarative
from nion.typeshed import API_1_0 as API
from nion.utils import Event
from nion.utils import ListModel


class WizardStep:
    step_index: int
    title: str
    long_description: typing.Optional[str] = None

    def __init__(self, api: API):
        self.api = api
        self.property_changed_event = Event.Event()

    @staticmethod
    def get_ui_description(ui: Declarative.DeclarativeUI) -> Declarative.UIDescription:
        ...

    def run(self) -> int:
        ...

    def cancel(self) -> None:
        ...


class WizardUIHandler(Declarative.Handler):
    def __init__(self, api: API, ui_view: Declarative.UIDescription, wizard_steps: typing.Sequence[WizardStep]) -> None:
        super().__init__()
        self.__wizard_steps = wizard_steps
        self.content_list: ListModel.ListModel[str] = ListModel.ListModel()
        self.__current_step = 0
        self.__continue_enabled = False
        self.__restart_enabled = False
        self.__api = api
        self.property_changed_event = Event.Event()
        self.ui_view = ui_view
        self.__thread: typing.Optional[threading.Thread] = None
        self.on_closed: typing.Optional[typing.Callable[[], typing.Any]] = None
        self.__canceled_ui_visible = False
        self.__cancel_button_visible = True
        self.__status_text = ''

    @property
    def current_step(self) -> int:
        return self.__current_step

    @current_step.setter
    def current_step(self, step: int) -> None:
        if step < 0:
            step = len(self.__wizard_steps) + step
        if step != self.__current_step:
            self.__current_step = step
            self.property_changed_event.fire('current_step')
            self.property_changed_event.fire('current_step_title')
            self.property_changed_event.fire('current_step_description')

    @property
    def continue_enabled(self) -> bool:
        return self.__continue_enabled

    @continue_enabled.setter
    def continue_enabled(self, enabled: bool) -> None:
        self.__continue_enabled = enabled
        self.property_changed_event.fire('continue_enabled')

    @property
    def restart_enabled(self) -> bool:
        return self.__restart_enabled

    @restart_enabled.setter
    def restart_enabled(self, enabled: bool) -> None:
        self.__restart_enabled = enabled
        self.property_changed_event.fire('restart_enabled')

    @property
    def current_step_title(self) -> str:
        if self.current_step < len(self.__wizard_steps) and not self.canceled_ui_visible:
            return self.__wizard_steps[self.current_step].title
        return ''

    @current_step_title.setter
    def current_step_title(self, text: str) -> None:
        ...

    @property
    def current_step_description(self) -> str:
        if self.current_step < len(self.__wizard_steps) and not self.canceled_ui_visible:
            if description := self.__wizard_steps[self.current_step].long_description:
                return description
        return ''

    @current_step_description.setter
    def current_step_description(self, text: str) -> None:
        ...

    @property
    def canceled_ui_visible(self) -> bool:
        return self.__canceled_ui_visible

    @canceled_ui_visible.setter
    def canceled_ui_visible(self, visible: bool) -> None:
        self.__canceled_ui_visible = visible
        self.property_changed_event.fire('canceled_ui_visible')
        self.property_changed_event.fire('current_step_title')
        self.property_changed_event.fire('current_step_description')
        if visible:
            self.content_list.items = []
        else:
            self.content_list.items = [self.__wizard_steps[self.current_step].title]

    @property
    def status_text(self) -> str:
        return self.__status_text

    @status_text.setter
    def status_text(self, status_text: str) -> None:
        self.__status_text = status_text
        self.property_changed_event.fire('status_text')

    @property
    def cancel_button_visible(self) -> bool:
        return self.__cancel_button_visible

    @cancel_button_visible.setter
    def cancel_button_visible(self, visible: bool) -> None:
        self.__cancel_button_visible = visible
        self.property_changed_event.fire('cancel_button_visible')
        self.property_changed_event.fire('restart_button_visible')

    @property
    def restart_button_visible(self) -> bool:
        return not self.cancel_button_visible

    @restart_button_visible.setter
    def restart_button_visible(self, visible: bool) -> None:
        ...

    def init_handler(self) -> None:
        self.__current_step = -1
        self.run_next_step()

    def close(self) -> None:
        if callable(self.on_closed):
            self.on_closed()
        super().close()

    def __set_up_ui_for_pre_wizard_step(self) -> None:
        self.canceled_ui_visible = False
        self.cancel_button_visible = True
        self.continue_enabled = False
        self.restart_enabled = False
        self.status_text = ''

    def __set_up_ui_for_post_wizard_step(self) -> None:
        if self.current_step < len(self.__wizard_steps) - 1:
            self.continue_enabled = True
            self.cancel_button_visible = True
            self.status_text = 'Done. Use the buttons below to restart the current or continue with the next step.'
        else:
            self.continue_enabled = False
            self.cancel_button_visible = False
            self.status_text = 'Wizard finished. You can close the dialog now or re-run the whole wizard.'
        self.restart_enabled = True

    def run_next_step(self) -> None:
        self.current_step += 1
        self.__set_up_ui_for_pre_wizard_step()
        def run_on_thread() -> None:
            selected_wizard = self.__wizard_steps[self.current_step]
            exception = False
            error = 0
            try:
                error = selected_wizard.run()
            except:
                import traceback
                traceback.print_exc()
                exception = True
            finally:
                self.__set_up_ui_for_post_wizard_step()
                if exception:
                    self.status_text = ('An error occured during the current step. Check the terminal output for more details.\n'
                                        'You can still continue with the wizard regardless of the error.')
                elif error:
                    self.status_text = ('The current step did not finish successfully. You can re-run it or continue with\n'
                                        'the wizard regardless of the failure.')

        self.__thread = threading.Thread(target=run_on_thread)
        self.__thread.start()

    def cancel_clicked(self, widget: Declarative.UIWidget) -> None:
        self.__wizard_steps[self.current_step].cancel()
        def run_on_thread() -> None:
            while self.__thread and self.__thread.is_alive():
                time.sleep(0.1)
            self.__api.queue_task(lambda: setattr(self, 'canceled_ui_visible', True))
        threading.Thread(target=run_on_thread).start()

    def continue_clicked(self, widget: Declarative.UIWidget) -> None:
        self.run_next_step()

    def restart_step_clicked(self, widget: Declarative.UIWidget) -> None:
        self.__current_step -= 1
        self.run_next_step()

    def restart_clicked(self, widget: Declarative.UIWidget) -> None:
        self.__current_step = -1
        self.run_next_step()

    def create_handler(self, component_id: str, **kwargs: typing.Any) -> typing.Optional[WizardStep]:
        if component_id != 'wizard':
            return None
        return self.__wizard_steps[self.current_step]

    @property
    def resources(self) -> typing.Dict[str, Declarative.UIDescription]:
        ui = Declarative.DeclarativeUI()
        component = ui.define_component(self.__wizard_steps[self.current_step].get_ui_description(ui))
        return {'wizard': component}


class WizardUI:

    def get_ui_handler(self, api_broker: PlugInManager.APIBroker, wizard_steps: typing.Sequence[WizardStep], title: str) -> WizardUIHandler:
        api = api_broker.get_api('~1.0')
        ui = api_broker.get_ui('~1.0')
        ui_view = self.__create_ui_view(ui, wizard_steps, title)
        return WizardUIHandler(api, ui_view, wizard_steps)

    def __create_ui_view(self, ui: Declarative.DeclarativeUI, wizard_steps: typing.Sequence[WizardStep], title: str) -> Declarative.UIDescription:
        steps = [ui.create_radio_button(text=' ', value=step.step_index, group_value='@binding(current_step)', enabled=False) for step in wizard_steps]
        steps.insert(0, ui.create_stretch())
        steps.append(ui.create_stretch())
        step_row = ui.create_row(*steps, margin=5)
        title_row = ui.create_row(ui.create_label(text='@binding(current_step_title)'), spacing=5, margin=5)
        description_row = ui.create_row(ui.create_label(text='@binding(current_step_description)'), spacing=5, margin=5)
        content_row = ui.create_row(items='content_list.items', item_component_id='wizard', spacing=5, margin=5)

        canceled_ui = ui.create_column(ui.create_row(ui.create_label(text='Wizard canceled.'), spacing=5, margin=5),
                                       ui.create_row(ui.create_push_button(text='Restart', on_clicked='restart_clicked'),
                                                     ui.create_stretch(), spacing=5),
                                       spacing=5, margin=5)
        canceled_row = ui.create_row(canceled_ui, visible='@binding(canceled_ui_visible)')
        status_row = ui.create_row(ui.create_label(text='@binding(status_text)'), spacing=5, margin=5)
        control_row = ui.create_row(ui.create_push_button(text='Cancel', on_clicked='cancel_clicked', visible='@binding(cancel_button_visible)'),
                                    ui.create_push_button(text='Restart Wizard', on_clicked='restart_clicked', visible='@binding(restart_button_visible)'),
                                    ui.create_stretch(),
                                    ui.create_push_button(text='Restart Step', on_clicked='restart_step_clicked', enabled='@binding(restart_enabled)'),
                                    ui.create_push_button(text='Continue', on_clicked='continue_clicked', enabled='@binding(continue_enabled)'),
                                    spacing=5, margin=5)
        column = ui.create_column(step_row, title_row, description_row, content_row, canceled_row, status_row, control_row, spacing=5, margin=5)
        return ui.create_modeless_dialog(column, title=title, margin=4)
