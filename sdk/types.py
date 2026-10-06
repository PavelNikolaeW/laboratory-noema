"""Public wire types; distributable independently of the server."""
from typing import Literal, TypedDict, NotRequired

class SampleRef(TypedDict):
    sample_id: str
    revision: int

class OperationRequest(TypedDict):
    kind: Literal['obtain','reference','split','measure_mass','measure_temperature','measure_response','heat','cool','separate','mix']
    instrument_id: str | None
    inputs: list[SampleRef]
    parameters: dict
    prediction_id: NotRequired[str | None]

class Observation(TypedDict):
    id: str
    sample_id: str
    sample_revision: int
    quantity: Literal['mass','temperature','response']
    value: str
    unit: str
    instrument_id: str
    recorded_at: str

class PublicSample(TypedDict):
    id: str
    revision: int
    status: Literal['available','busy','consumed']
    batch_id: str | None
    provenance: dict
    history: list[str]
    observation_ids: list[str]
    created_at: str

class PublicOperation(TypedDict):
    id: str
    status: Literal['accepted','running','succeeded','failed']
    kind: str
    instrument_id: str | None
    inputs: list[SampleRef]
    parameters: dict
    prediction_id: str | None
    result: dict | None
    error: dict | None
    created_at: str
    completed_at: str | None

class Event(TypedDict):
    event_id: str
    sequence: int
    kind: str
    operation_id: str | None
    recorded_at: str
    payload: dict

class EventPage(TypedDict):
    items: list[Event]
    next_after: int
    has_more: bool
