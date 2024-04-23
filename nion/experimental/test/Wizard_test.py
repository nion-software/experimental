import unittest
import asyncio

from nion.ui import Declarative
from nion.swift import Facade
from nion.swift.model import PlugInManager

from nion.experimental import Wizard


class TestAsyncWizardStep(Wizard.AsyncWizardStep):
    title = 'Test Step'

    def __init__(self, api, step_index, depends_on_previous_step, event, requirements = None):
        super().__init__(api)
        self.step_index = step_index
        self.depends_on_previous_step = depends_on_previous_step
        self.event = event
        self.started = False
        self.completed = False
        self.requirements = requirements or list()

    async def run(self):
        self.started = True
        await self.event.wait()
        self.completed = True
        return 0


class TestWizard(unittest.IsolatedAsyncioTestCase):

    @classmethod
    def setUpClass(cls):
        Facade.initialize()

    async def test_skip_button_skips_to_next_step_without_dependencies(self):
        event_loop = asyncio.get_running_loop()
        api_broker = PlugInManager.APIBroker()
        api = api_broker.get_api('~1.0')

        event = asyncio.Event()

        wizard_steps = [TestAsyncWizardStep(api, 0, False, event),
                        TestAsyncWizardStep(api, 1, True, event),
                        TestAsyncWizardStep(api, 2, True, event),
                        TestAsyncWizardStep(api, 3, False, event)]

        wizard_ui_handler = Wizard.WizardUI().get_ui_handler_v2(api_broker, wizard_steps, 'Test Wizard')
        event_loop.call_later(1.0, wizard_ui_handler.skip_clicked, None)
        wizard_ui_handler.init_handler()
        await asyncio.wait_for(wizard_ui_handler._task, 3.0)
        event.set()
        await asyncio.sleep(0.0)
        await asyncio.wait_for(wizard_ui_handler._task, 3.0)
        self.assertTrue(wizard_steps[0].canceled)
        self.assertEqual(wizard_ui_handler.current_step_index, 3)
        self.assertTrue(wizard_steps[-1].started)
        self.assertTrue(wizard_steps[-1].completed)

    async def test_cancel_button_shows_cancel_ui(self):
        event_loop = asyncio.get_running_loop()
        api_broker = PlugInManager.APIBroker()
        api = api_broker.get_api('~1.0')

        event = asyncio.Event()

        wizard_steps = [TestAsyncWizardStep(api, 0, False, event)]

        wizard_ui_handler = Wizard.WizardUI().get_ui_handler_v2(api_broker, wizard_steps, 'Test Wizard')
        event_loop.call_later(1.0, wizard_ui_handler.cancel_clicked, None)
        wizard_ui_handler.init_handler()
        self.assertFalse(wizard_ui_handler.canceled_ui_visible)
        await asyncio.wait_for(wizard_ui_handler._task, 3.0)
        self.assertTrue(wizard_steps[0].canceled)
        self.assertEqual(wizard_ui_handler.current_step_index, 0)
        self.assertTrue(wizard_steps[-1].started)
        self.assertFalse(wizard_steps[-1].completed)
        self.assertTrue(wizard_ui_handler.canceled_ui_visible)

    async def test_requriements_are_initialized_with_false(self):
        api_broker = PlugInManager.APIBroker()
        api = api_broker.get_api('~1.0')

        event = asyncio.Event()

        wizard_steps = [TestAsyncWizardStep(api, 0, False, event, requirements=['req 1', 'req 2'])]

        wizard_ui_handler = Wizard.WizardUI().get_ui_handler_v2(api_broker, wizard_steps, 'Test Wizard')
        wizard_ui_handler.init_handler()
        self.assertFalse(getattr(wizard_ui_handler, 'requirement_0_0_checked'))
        self.assertFalse(getattr(wizard_ui_handler, 'requirement_0_1_checked'))
        wizard_ui_handler._task.cancel()
