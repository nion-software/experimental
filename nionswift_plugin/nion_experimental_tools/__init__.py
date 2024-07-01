import typing
import inspect

from nion.swift.model import DocumentModel
from nion.swift.model import Symbolic

from . import DoubleGaussian
from . import GraphicsTools
from . import SequenceSplitJoin
from . import AlignMultiSI
from . import MakeIDPC
from . import AffineTransformImage
from . import MakeColorCOM
from . import AlignSequenceOfMultiDimensionalData
from . import MultiDimensionalProcessing
from . import IESquarePlot
from . import FindLocalMaxima


@typing.runtime_checkable
class _ComputationHandler(Symbolic.ComputationHandlerLike, typing.Protocol):
    computation_id: str
    label: str
    inputs: dict[str, dict[str, str]]
    outputs: dict[str, dict[str, str]]


def _create_processing_description_from_computation_handler(handler_class: type[_ComputationHandler]) -> dict[str, typing.Any]:
    processing_description = {"title": handler_class.label}
    sources = []
    for key, value in handler_class.inputs.items():
        source = {"name": key}
        source.update(value)
        sources.append(source)
    processing_description["sources"]= sources
    return {handler_class.computation_id: processing_description}


_processing_descriptions: dict[str, typing.Any] = dict()
_dict = globals().copy()

for module in _dict.values():
    if inspect.ismodule(module) and module.__package__ == __package__:
        for name, cls in inspect.getmembers(module):
            if inspect.isclass(cls) and isinstance(cls, _ComputationHandler):
                _processing_descriptions.update(_create_processing_description_from_computation_handler(cls))

DocumentModel.DocumentModel.register_processing_descriptions(_processing_descriptions)

del _processing_descriptions
del _dict
