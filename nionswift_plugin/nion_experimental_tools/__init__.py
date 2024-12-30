import typing

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


_computation_classes = [
    DoubleGaussian.DoubleGaussian,
    SequenceSplitJoin.SequenceJoin,
    SequenceSplitJoin.SequenceSplit,
    AlignMultiSI.AlignMultiSI2,
    MakeIDPC.MakeIDPC,
    AffineTransformImage.AffineTransformImage,
    MakeColorCOM.MakeColorCOM,
    AlignSequenceOfMultiDimensionalData.AlignMultiDimensionalSequence,
    MultiDimensionalProcessing.AlignImageSequence,
    MultiDimensionalProcessing.ApplyShifts,
    MultiDimensionalProcessing.Crop,
    MultiDimensionalProcessing.IntegrateAlongAxis,
    MultiDimensionalProcessing.MakeTableau,
    MultiDimensionalProcessing.MeasureShifts,
    MultiDimensionalProcessing.AlignImageSequence,
    FindLocalMaxima.FindLocalMaxima
]


class _ComputationHandler(Symbolic.ComputationHandlerLike, typing.Protocol):
    computation_id: str
    label: str
    inputs: dict[str, dict[str, str]]
    outputs: dict[str, dict[str, str]]


def _create_processing_description_from_computation_handler(handler_class: type[_ComputationHandler]) -> dict[str, typing.Any]:
    processing_description: dict[str, typing.Any] = {"title": handler_class.label}
    sources = list[dict[str, str]]()
    for key, value in handler_class.inputs.items():
        source = {"name": key}
        source.update(value)
        sources.append(source)
    processing_description["sources"] = sources
    return {handler_class.computation_id: processing_description}


def register_computation_classes() -> None:
    processing_descriptions: dict[str, typing.Any] = dict()
    for computation_class in _computation_classes:
        computation_handler = typing.cast(_ComputationHandler, computation_class)
        processing_description: dict[str, typing.Any] = {"title": computation_handler.label}
        sources = list[dict[str, str]]()
        for key, value in computation_handler.inputs.items():
            source = {"name": key}
            source.update(value)
            sources.append(source)
        processing_description["sources"] = sources
        processing_descriptions[computation_handler.computation_id] = processing_description
    DocumentModel.DocumentModel.register_processing_descriptions(processing_descriptions)


def run() -> None:
    register_computation_classes()
    # register these here so they only get registered once. This is a bit of a hack.
    DocumentModel.DocumentModel.register_processing_descriptions(IESquarePlot.processing_descriptions)
