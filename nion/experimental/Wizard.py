import typing
import threading
import time
import asyncio
import functools

from nion.swift.model import PlugInManager
from nion.ui import Declarative
from nion.typeshed import API_1_0 as API
from nion.utils import Event
from nion.utils import ListModel


def line_break(text: str, line_length: int = 79) -> str:
    split_str = text.split()
    res_str = ''

    lines = text.split('\n')
    for line in lines:
        if len(line) > line_length:
            processed_line = ''
            split_str = line.split()
            for sub_str in split_str:
                while len(sub_str) > line_length:
                    res_str += '\n' + (sub_str[:line_length]).strip()
                    sub_str = sub_str[line_length:]
                if len(processed_line) + len(sub_str) > line_length:
                    res_str += '\n' + processed_line.strip()
                    processed_line = sub_str
                else:
                    processed_line += ' ' + sub_str
        else:
            processed_line = line

        res_str += '\n' + processed_line.strip()

    return res_str.strip()


class WizardStep:
    step_index: int
    title: str
    long_description: typing.Optional[str] = None

    def __init__(self, api: API.API):
        self.api = api
        self.property_changed_event = Event.Event()

    @staticmethod
    def get_ui_description(ui: Declarative.DeclarativeUI) -> Declarative.UIDescription:
        raise NotImplementedError()

    def run(self) -> int:
        raise NotImplementedError()

    def cancel(self) -> None:
        raise NotImplementedError()


class AsyncWizardStep:
    step_index: int
    title: str
    long_description: typing.Optional[str] = None
    depends_on_previous_step: bool = False
    show_output_field: bool = True
    requirements: typing.List[str] = list()

    def __init__(self, api: API.API):
        self.api = api
        self.property_changed_event = Event.Event()
        self.canceled = False
        self.__output_text = ''
        self.__instructions_text = ''

    @property
    def output_text(self) -> str:
        return self.__output_text

    @output_text.setter
    def output_text(self, text: str) -> None:
        self.__output_text = text
        self.property_changed_event.fire('output_text')

    @property
    def instructions_text(self) -> str:
        return self.__instructions_text

    @instructions_text.setter
    def instructions_text(self, text: str) -> None:
        self.__instructions_text = text
        self.property_changed_event.fire('instructions_text')

    @staticmethod
    def get_ui_description(ui: Declarative.DeclarativeUI) -> Declarative.UIDescription:
        return ui.create_column() # Don't return an empty dict, because this will cause an assertion error in nionui

    def init_step(self) -> int:
        return 0

    async def run(self) -> int:
        raise NotImplementedError()


class AsyncWizardUIHandler(Declarative.Handler):
    def __init__(self,
                 api: API,
                 ui_view: Declarative.UIDescription,
                 wizard_steps: typing.Sequence[AsyncWizardStep], *,
                 event_loop: typing.Optional[asyncio.AbstractEventLoop] = None,
                 open_help: typing.Callable[[], None] | None = None) -> None:
        super().__init__()
        self.__event_loop = event_loop or asyncio.get_event_loop()
        self.__wizard_steps = wizard_steps
        self.content_list: ListModel.ListModel[str] = ListModel.ListModel()
        self.__current_step_index = 0
        self.__continue_enabled = False
        self.__restart_enabled = False
        self.__api = api
        self.property_changed_event = Event.Event()
        self.ui_view = ui_view
        self.__thread: typing.Optional[threading.Thread] = None
        self.__task: typing.Optional[asyncio.Task[None]] = None
        self.on_closed: typing.Optional[typing.Callable[[], typing.Any]] = None
        self.__canceled_ui_visible = False
        self.__cancel_button_visible = False
        self.__skip_button_visible = True
        self.__status_text = ''
        self.__menu_button_visible = True
        self.__continue_event = asyncio.Event()
        self.__requirement_values: dict[str, typing.Any] = dict()
        self.__output_text_listener: typing.Optional[Event.EventListener] = None
        self.__instructions_text_listener: typing.Optional[Event.EventListener] = None
        self.__run_step_button_enabled = True
        self.instructions_background_default_color = '#f0f0f0'
        self.instructions_background_action_color = 'peachpuff'
        self.status_text_default_color = '#f0f0f0'
        self.status_text_info_color = 'peachpuff'
        self.status_text_error_color = 'lightcoral'
        self.__status_text_color = self.status_text_default_color
        self.__open_help = open_help
        self.__create_requirement_properties()

    @staticmethod
    def _get_requirement_property_name(step_index: int, requirement_index: int) -> str:
        return f'requirement_{step_index}_{requirement_index}_checked'

    def __create_property(self, name: str, value: typing.Any = None) -> None:
        self.__requirement_values[name] = value
        def getter(self: AsyncWizardUIHandler) -> typing.Any:
            return self.__requirement_values[name]

        def setter(self: AsyncWizardUIHandler, value: typing.Any) -> None:
            self.__requirement_values[name] = value
            self.property_changed_event.fire(name)
            self.property_changed_event.fire('run_step_button_enabled')

        setattr(AsyncWizardUIHandler, name, property(getter, setter))

    def __create_requirement_properties(self) -> None:
        for wizard_step in self.__wizard_steps:
            for i in range(len(wizard_step.requirements)):
                self.__create_property(self._get_requirement_property_name(wizard_step.step_index, i), value=False)

    # For testing
    @property
    def _task(self) -> typing.Optional[asyncio.Task[None]]:
        return self.__task

    @property
    def current_step_index(self) -> int:
        return self.__current_step_index

    @current_step_index.setter
    def current_step_index(self, step: int) -> None:
        if step < 0:
            step = len(self.__wizard_steps) + step
        if step != self.__current_step_index:
            self.__current_step_index = step
            self.property_changed_event.fire('current_step_index')
            self.property_changed_event.fire('current_step_title')
            self.property_changed_event.fire('current_step_description')
            self.property_changed_event.fire('output_field_visible')

    @property
    def current_step(self) -> AsyncWizardStep:
        return self.__wizard_steps[self.__current_step_index]

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
        if self.current_step_index < len(self.__wizard_steps) and not self.canceled_ui_visible:
            return self.current_step.title
        return ''

    @current_step_title.setter
    def current_step_title(self, text: str) -> None:
        ...

    @property
    def current_step_description(self) -> str:
        if self.current_step_index < len(self.__wizard_steps) and not self.canceled_ui_visible:
            if description := self.current_step.long_description:
                return description
        return ''

    @current_step_description.setter
    def current_step_description(self, text: str) -> None:
        ...

    @property
    def ui_stack_current_index(self) -> int:
        return int(self.canceled_ui_visible)

    @ui_stack_current_index.setter
    def ui_stack_current_index(self, index: int) -> None:
        ...

    @property
    def canceled_ui_visible(self) -> bool:
        return self.__canceled_ui_visible

    @canceled_ui_visible.setter
    def canceled_ui_visible(self, visible: bool) -> None:
        self.__canceled_ui_visible = visible
        self.property_changed_event.fire('current_step_title')
        self.property_changed_event.fire('current_step_description')
        self.property_changed_event.fire('ui_stack_current_index')
        self.cancel_button_visible = False
        self.restart_enabled = True
        if visible:
            self.content_list.items = []
        else:
            self.content_list.items = [self.current_step_title]

    @property
    def output_field_visible(self) -> bool:
        return self.current_step.show_output_field and not self.canceled_ui_visible

    @property
    def status_text(self) -> str:
        return self.__status_text

    @status_text.setter
    def status_text(self, status_text: str) -> None:
        self.__status_text = status_text
        self.property_changed_event.fire('status_text')
        if not status_text:
            self.status_text_color = self.status_text_default_color

    @property
    def status_text_color(self) -> str:
        return self.__status_text_color

    @status_text_color.setter
    def status_text_color(self, color: str) -> None:
        self.__status_text_color = color
        self.property_changed_event.fire('status_text_color')

    @property
    def output_text(self) -> str:
        return self.current_step.output_text

    @output_text.setter
    def output_text(self, text: str) -> None:
        ...

    @property
    def instructions_background_color(self) -> str:
        return self.instructions_background_action_color if self.current_step.instructions_text else self.instructions_background_default_color

    @instructions_background_color.setter
    def instructions_background_color(self, color: str) -> None:
        ...

    @property
    def instructions_field_visible(self) -> bool:
        return bool(self.current_step.instructions_text)

    @instructions_field_visible.setter
    def instructions_field_visible(self, visible: bool) -> None:
        ...

    @property
    def requirements_visible(self) -> bool:
        return bool(self.current_step.requirements) and not self.canceled_ui_visible

    @requirements_visible.setter
    def requirements_visible(self, visible: bool) -> None:
        ...

    @property
    def instructions_text(self) -> str:
        return line_break(self.current_step.instructions_text)

    @instructions_text.setter
    def instructions_text(self, text: str) -> None:
        ...

    @property
    def cancel_button_visible(self) -> bool:
        return self.__cancel_button_visible

    @cancel_button_visible.setter
    def cancel_button_visible(self, visible: bool) -> None:
        self.__cancel_button_visible = visible
        self.property_changed_event.fire('skip_button_visible')
        self.property_changed_event.fire('cancel_button_visible')
        self.property_changed_event.fire('restart_button_visible')

    @property
    def skip_button_visible(self) -> bool:
        return self.__skip_button_visible

    @skip_button_visible.setter
    def skip_button_visible(self, visible: bool) -> None:
        self.__skip_button_visible = visible
        self.property_changed_event.fire('skip_button_visible')
        self.property_changed_event.fire('cancel_button_visible')
        self.property_changed_event.fire('restart_button_visible')

    @property
    def restart_button_visible(self) -> bool:
        return not self.cancel_button_visible

    @restart_button_visible.setter
    def restart_button_visible(self, visible: bool) -> None:
        ...

    @property
    def menu_button_visible(self) -> bool:
        return self.__menu_button_visible

    @menu_button_visible.setter
    def menu_button_visible(self, visible: bool) -> None:
        self.__menu_button_visible = visible
        self.property_changed_event.fire('menu_button_visible')

    @property
    def run_step_button_enabled(self) -> bool:
        if not self.__run_step_button_enabled:
            return False
        current_step_index = self.current_step_index
        checked = []
        for i in range(len(self.current_step.requirements)):
            checked.append(getattr(self, self._get_requirement_property_name(current_step_index, i)))
        return all(checked)

    @run_step_button_enabled.setter
    def run_step_button_enabled(self, enabled: bool) -> None:
        ...

    def init_handler(self) -> None:
        self.__task = self.__event_loop.create_task(self.run_current_step())

    def close(self) -> None:
        if callable(self.on_closed):
            self.on_closed()
        super().close()

    def open_help(self, widget: Declarative.UIWidget) -> None:
        if self.__open_help is not None:
            self.__open_help()

    def __set_up_ui_for_pre_wizard_step(self) -> None:
        def listen_fn(listen_to: set[str], fire: set[str], name: str) -> None:
            if name in listen_to:
                for fire_event in fire:
                    self.property_changed_event.fire(fire_event)
        self.__run_step_button_enabled = True
        self.__output_text_listener = self.current_step.property_changed_event.listen(functools.partial(listen_fn, {'output_text'}, {'output_text'}))
        self.__instructions_text_listener = self.current_step.property_changed_event.listen(functools.partial(listen_fn, {'instructions_text'}, {'instructions_text', 'instructions_field_visible', 'instructions_background_color'}))
        self.canceled_ui_visible = False
        self.cancel_button_visible = True
        self.skip_button_visible = True
        self.continue_enabled = False
        self.restart_enabled = False
        self.status_text = ''
        self.current_step.output_text = ''
        self.current_step.instructions_text = ''
        for i in range(len(self.current_step.requirements)):
            setattr(self, self._get_requirement_property_name(self.current_step.step_index, i), False)

    def __set_up_ui_for_post_wizard_step(self) -> None:
        self.__output_text_listener = None
        if self.current_step_index < len(self.__wizard_steps) - 1:
            self.continue_enabled = True
            self.cancel_button_visible = False
            self.skip_button_visible = not self.current_step.canceled
            self.status_text = 'Done. Use the buttons below to restart the current or continue with the next step.'
            self.status_text_color = self.status_text_info_color
        else:
            self.continue_enabled = False
            self.cancel_button_visible = False
            self.skip_button_visible = False
            self.status_text = 'Wizard finished. You can close the dialog now or re-run the whole wizard.'
            self.status_text_color = self.status_text_info_color
        self.restart_enabled = True

    async def run_next_step(self) -> None:
        self.current_step_index += 1
        await self.run_current_step()

    async def run_current_step(self) -> None:
        self.__set_up_ui_for_pre_wizard_step()
        self.__continue_event.clear()
        selected_wizard = self.current_step
        selected_wizard.canceled = False
        exception = False
        error = 0
        try:
            selected_wizard.init_step()
            if selected_wizard.requirements:
                await self.__continue_event.wait()
            error = await selected_wizard.run()
        except asyncio.CancelledError:
            selected_wizard.canceled = True
        except:
            import traceback
            traceback.print_exc()
            exception = True
        finally:
            self.__set_up_ui_for_post_wizard_step()
            if exception:
                self.status_text = ('An error occured during the current step. Check the terminal output for more details.\n'
                                    'You can still continue with the wizard regardless of the error.')
                self.status_text_color = self.status_text_error_color
            elif error:
                self.status_text = ('The current step did not finish successfully. You can re-run it or continue with\n'
                                    'the wizard regardless of the failure.')
                self.status_text_color = self.status_text_error_color

    def skip_clicked(self, widget: Declarative.UIWidget) -> None:
        def callback(task: asyncio.Task[None]) -> None:
            step = self.current_step_index + 1
            while step < len(self.__wizard_steps) - 1 and self.__wizard_steps[step].depends_on_previous_step:
                step += 1
            self.current_step_index = step
            if self.current_step_index < len(self.__wizard_steps) and not self.current_step.depends_on_previous_step:
                self.__task = self.__event_loop.create_task(self.run_current_step())
            else:
                self.canceled_ui_visible = True
                self.__set_up_ui_for_post_wizard_step()

        self.current_step.canceled = True

        if self.__task:
            self.__task.add_done_callback(callback)
            self.__task.cancel()

    def cancel_clicked(self, widget: Declarative.UIWidget) -> None:
        self.current_step.canceled = True
        if self.__task:
            self.__task.add_done_callback(lambda task: setattr(self, 'canceled_ui_visible', True))
            self.__task.cancel()

    def continue_clicked(self, widget: Declarative.UIWidget) -> None:
        self.__task = self.__event_loop.create_task(self.run_next_step())

    def restart_step_clicked(self, widget: Declarative.UIWidget) -> None:
        self.__task = self.__event_loop.create_task(self.run_current_step())

    def restart_clicked(self, widget: Declarative.UIWidget) -> None:
        self.current_step_index = 0
        self.__task = self.__event_loop.create_task(self.run_current_step())

    def show_menu_clicked(self, widget: Declarative.UIWidget) -> None:
        def jump_to_step(step: int) -> None:
            def callback(task: asyncio.Task[None]) -> None:
                self.current_step_index = step
                self.__task = self.__event_loop.create_task(self.run_current_step())
            if self.__task:
                self.__task.add_done_callback(callback)
                self.__task.cancel()
            else:
                callback(typing.cast(asyncio.Task[None], None))
        document_controller = self.__api.application.document_controllers[0]._document_controller
        menu = document_controller.create_context_menu()
        for item in self.__wizard_steps:
            action = menu.add_menu_item(item.title, functools.partial(jump_to_step, item.step_index))
            if item.depends_on_previous_step:
                action.enabled = False

        position = widget._behavior.map_to_global(widget.size.as_point())
        menu.popup(position.x, position.y)

    def run_step_clicked(self, widget: Declarative.UIWidget) -> None:
        self.__run_step_button_enabled = False
        self.property_changed_event.fire('run_step_button_enabled')
        self.__continue_event.set()

    def create_handler(self, component_id: str, **kwargs: typing.Any) -> typing.Optional[AsyncWizardStep]:
        if component_id != 'wizard':
            return None
        return self.current_step

    @property
    def resources(self) -> typing.Dict[str, Declarative.UIDescription]:
        ui = Declarative.DeclarativeUI()
        component = ui.define_component(self.current_step.get_ui_description(ui))
        return {'wizard': component}


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

    def get_ui_handler_v2(self, api_broker: PlugInManager.APIBroker, wizard_steps: typing.Sequence[AsyncWizardStep], title: str, *, open_help: typing.Callable[[], None] | None  = None) -> AsyncWizardUIHandler:
        api = api_broker.get_api('~1.0')
        ui = api_broker.get_ui('~1.0')
        ui_view = self.__create_ui_view_v2(ui, wizard_steps, title, open_help is not None)
        return AsyncWizardUIHandler(api, ui_view, wizard_steps, open_help=open_help)

    def __create_ui_view(self, ui: Declarative.DeclarativeUI, wizard_steps: typing.Sequence[WizardStep], title: str) -> Declarative.UIDescription:
        steps = [ui.create_radio_button(text=' ', value=step.step_index, group_value='@binding(current_step)', enabled=False) for step in wizard_steps]
        steps.insert(0, ui.create_stretch())
        steps.append(ui.create_stretch())
        step_row = ui.create_row(*steps, margin=5)
        title_row = ui.create_row(ui.create_label(text='@binding(current_step_title)'), spacing=5, margin=5)
        description_row = ui.create_row(ui.create_label(text='@binding(current_step_description)'), spacing=5, margin=5)
        content_row = ui.create_row(items='content_list.items', item_component_id='wizard', spacing=5, margin=5)

        canceled_ui = ui.create_column(ui.create_row(ui.create_label(text='Wizard canceled.'), spacing=5, margin=5),
                                       ui.create_row(ui.create_push_button(text='Restart Wizard', on_clicked='restart_clicked'),
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

    def __generate_requirements_ui(self, ui: Declarative.DeclarativeUI, wizard_step: AsyncWizardStep) -> Declarative.UIDescription:
        requirements: typing.List[Declarative.UIDescription] = list()
        for i, requirement in enumerate(wizard_step.requirements):
            requirements.append(ui.create_row(ui.create_check_box(text=line_break(requirement), checked=f'@binding({AsyncWizardUIHandler._get_requirement_property_name(wizard_step.step_index, i)})'), ui.create_stretch(), spacing=5, margin=5))
        if requirements:
            requirements.insert(0, ui.create_row(ui.create_label(text='Please go through the list of requirements below and click "Run step" once you have checked all boxes.'), ui.create_stretch(), spacing=5, margin=5))
            requirements.append(ui.create_row(ui.create_push_button(text='Run step', on_clicked='run_step_clicked', enabled='@binding(run_step_button_enabled)'), ui.create_stretch(), margin=5, spacing=5))
        return ui.create_column(*requirements)

    def __create_ui_view_v2(self, ui: Declarative.DeclarativeUI, wizard_steps: typing.Sequence[AsyncWizardStep], title: str, has_help: bool) -> Declarative.UIDescription:
        steps = [ui.create_radio_button(text=' ', value=step.step_index, group_value='@binding(current_step_index)', enabled=False) for step in wizard_steps]
        steps.insert(0, ui.create_stretch())
        steps.append(ui.create_stretch())
        step_row = ui.create_row(ui.create_push_button(text='\u2630', on_clicked='show_menu_clicked', size_policy_horizontal='minimum', size_policy_vertical='minimum', background_color='#f0f0f0', border_color='#f0f0f0', font='bold', style='minimal'),
                                 *steps,
                                 ui.create_push_button(text='\u2754', on_clicked='open_help', visible=has_help, size_policy_horizontal='minimum', size_policy_vertical='minimum', background_color='#f0f0f0', border_color='#f0f0f0', font='bold', style='minimal'),
                                 margin=5)
        title_row = ui.create_row(ui.create_label(text='@binding(current_step_title)', font='bold'), spacing=5, margin=5)
        description_row = ui.create_row(ui.create_label(text='@binding(current_step_description)'), spacing=5, margin=5)
        requirements = [self.__generate_requirements_ui(ui, wizard_step) for wizard_step in wizard_steps]
        requirements_row = ui.create_row(ui.create_stack(*requirements, current_index='@binding(current_step_index)'), visible='@binding(requirements_visible)')
        instructions_row = ui.create_row(ui.create_label(text='@binding(instructions_text)', background_color='@binding(instructions_background_color)', visible='@binding(instructions_field_visible)'), spacing=5)
        content_row = ui.create_row(items='content_list.items', item_component_id='wizard', spacing=5, margin=5)

        canceled_row = ui.create_row(ui.create_label(text='Current step canceled.\nYou can restart the step or the entire wizard with the buttons below.'), spacing=5, margin=5)
        output_row = ui.create_row(ui.create_text_edit(text='@binding(output_text)', editable=False, min_height=220, visible='@binding(output_field_visible)'))
        status_row = ui.create_row(ui.create_label(text='@binding(status_text)'), background_color='@binding(status_text_color)', spacing=5, margin=5)
        active_ui_column = ui.create_column(requirements_row, instructions_row, content_row, output_row, status_row)
        canceled_ui_colum = ui.create_column(canceled_row)
        ui_stack = ui.create_stack(active_ui_column, canceled_ui_colum, current_index='@binding(ui_stack_current_index)')
        control_row = ui.create_row(ui.create_push_button(text='Cancel Step', on_clicked='cancel_clicked', visible='@binding(cancel_button_visible)'),
                                    ui.create_push_button(text='Skip Step', on_clicked='skip_clicked', visible='@binding(skip_button_visible)'),
                                    ui.create_push_button(text='Restart Wizard', on_clicked='restart_clicked', visible='@binding(restart_button_visible)'),
                                    ui.create_stretch(),
                                    ui.create_push_button(text='Restart Step', on_clicked='restart_step_clicked', enabled='@binding(restart_enabled)'),
                                    ui.create_push_button(text='Continue', on_clicked='continue_clicked', enabled='@binding(continue_enabled)'),
                                    spacing=5, margin=5)
        column = ui.create_column(step_row, title_row, description_row, ui_stack, control_row, spacing=5, margin=5,
                                  size_policy_vertical='min-expanding', size_policy_horizontal='min-expanding')
        return ui.create_modeless_dialog(column, title=title, margin=4)
