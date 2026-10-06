from decimal import Decimal
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

class DTO(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)

class Ref(DTO):
    sample_id: str = Field(min_length=1, max_length=100)
    revision: int = Field(ge=1)

Kind = Literal['obtain','reference','split','measure_mass','measure_temperature','measure_response','heat','cool','separate','mix']

class Operation(DTO):
    kind: Kind
    instrument_id: str | None = None
    inputs: list[Ref] = Field(default_factory=list, max_length=2)
    parameters: dict = Field(default_factory=dict)
    prediction_id: str | None = None

    @model_validator(mode='after')
    def check(self):
        validate_parameters(self.kind, self.parameters)
        expected = 0 if self.kind in ('obtain','reference') else 2 if self.kind == 'mix' else 1
        if len(self.inputs) != expected: raise ValueError('Invalid input count')
        if len({r.sample_id for r in self.inputs}) != len(self.inputs): raise ValueError('Inputs must be distinct')
        device = INSTRUMENT.get(self.kind)
        if self.instrument_id != device: raise ValueError('Incorrect instrument')
        return self

INSTRUMENT = {'measure_mass':'scale-1','measure_temperature':'thermometer-1','measure_response':'analyzer-1','heat':'heater-1','cool':'cooler-1','separate':'separator-1','mix':'mixer-1'}
PARAMS = {'obtain':{'batch_id','mass_g'},'reference':{'standard_id'},'split':{'fraction'},'heat':{'power','duration'},'cool':{'target_temperature_c'},'separate':{'mode'}}

def decimal_string(value, low, high, exclusive=False):
    if not isinstance(value,str): raise ValueError('Decimal must be a string')
    try: n = Decimal(value)
    except Exception: raise ValueError('Invalid decimal')
    if not n.is_finite() or (not low < n < high if exclusive else not low <= n <= high): raise ValueError('Decimal outside limits')
    return n

def validate_parameters(kind, p):
    if set(p) != PARAMS.get(kind,set()): raise ValueError('Invalid parameter fields')
    if kind == 'obtain':
        n = decimal_string(p['mass_g'],Decimal('.1'),Decimal(100))
        if n.as_tuple().exponent < -3: raise ValueError('Maximum precision is .001 g')
        if not isinstance(p['batch_id'],str): raise ValueError('Invalid batch')
    if kind == 'reference' and not isinstance(p['standard_id'],str): raise ValueError('Invalid standard')
    if kind == 'split': decimal_string(p['fraction'],Decimal(0),Decimal(1),True)
    if kind == 'cool': decimal_string(p['target_temperature_c'],Decimal(20),Decimal(200))
    if kind == 'heat':
        for k,limit in [('power',10),('duration',120)]:
            if type(p[k]) is not int or not 1 <= p[k] <= limit: raise ValueError('Invalid level or ticks')
    if kind == 'separate' and p['mode'] != 'selective': raise ValueError('Invalid mode')

class Step(DTO):
    kind: Literal['heat','cool','separate']
    parameters: dict
    keep_output: Literal['selected','remainder'] | None = None
    @model_validator(mode='after')
    def check(self):
        validate_parameters(self.kind,self.parameters)
        if (self.kind == 'separate') != (self.keep_output is not None): raise ValueError('keep_output required only for separate')
        return self

class Recipe(DTO):
    name: str = Field(min_length=1,max_length=200)
    steps: list[Step] = Field(min_length=1,max_length=20)
    applicability: str = Field(default='',max_length=5000)
    evidence_ids: list[str] = Field(default_factory=list,max_length=100)

class Note(DTO):
    text: str = Field(min_length=1,max_length=10000)
    sample_ids: list[str] = Field(default_factory=list,max_length=100)
    observation_ids: list[str] = Field(default_factory=list,max_length=100)
    order_id: str | None = None
    supersedes: str | None = None

class Prediction(DTO):
    quantity: Literal['mass','temperature','response']
    expected_value: str
    interval: list[str] | None = None
    planned_operation: Operation
    hypothesis: str = Field(default='',max_length=10000)
    @model_validator(mode='after')
    def check(self):
        decimal_string(self.expected_value,Decimal('-1000000'),Decimal('1000000'))
        op = self.planned_operation
        if op.prediction_id or op.kind != 'measure_'+self.quantity: raise ValueError('Prediction must target a measurement')
        if self.interval is not None:
            if len(self.interval)!=2: raise ValueError('Invalid interval')
            a,b = [decimal_string(x,Decimal('-1000000'),Decimal('1000000')) for x in self.interval]
            if not a <= Decimal(self.expected_value) <= b: raise ValueError('Invalid interval')
        return self

class Submission(DTO):
    order_id: str
    recipe_id: str

class PublicSample(DTO):
    id: str
    revision: int
    status: Literal['available','busy','consumed']
    batch_id: str | None
    provenance: dict
    history: list[str]
    observation_ids: list[str]
    created_at: str

class Observation(DTO):
    id: str
    sample_id: str
    sample_revision: int
    quantity: Literal['mass','temperature','response']
    value: str
    unit: str
    instrument_id: str
    recorded_at: str

class PublicError(DTO):
    code: str
    message: str
    retryable: bool

class ErrorResponse(DTO):
    error: PublicError
    request_id: str

class OperationResult(DTO):
    samples: list[PublicSample]
    observations: list[Observation]

class ValidationRepeat(DTO):
    repeat: int
    measured_response: str
    yield_value: str = Field(alias='yield')
    recipe_credits: str
    reasons: list[str]
    observation_ids: list[str]

class ValidationResult(DTO):
    passed: bool = Field(alias='pass')
    repeats: list[ValidationRepeat]
    observations: list[Observation]

class PublicOperation(DTO):
    id: str
    status: Literal['accepted','running','succeeded','failed']
    kind: str
    instrument_id: str | None
    inputs: list[Ref]
    parameters: dict
    prediction_id: str | None
    result: OperationResult | ValidationResult | None
    error: PublicError | None
    created_at: str
    completed_at: str | None

class PublicEvent(DTO):
    event_id: str
    sequence: int
    kind: str
    operation_id: str | None
    recorded_at: str
    payload: dict

class EventPage(DTO):
    items: list[PublicEvent]
    next_after: int
    has_more: bool

# Public action schemas describe controls only; transitions stay private.
PARAMETER_SCHEMAS = {
    'obtain': {'batch_id': {'type':'string'}, 'mass_g':{'type':'string','description':'Decimal, 0.1–100 g, precision .001 g'}},
    'reference': {'standard_id': {'type':'string'}},
    'split': {'fraction': {'type':'string','description':'Decimal, 0 < fraction < 1'}},
    'heat': {'power': {'type':'integer','minimum':1,'maximum':10},'duration': {'type':'integer','minimum':1,'maximum':120}},
    'cool': {'target_temperature_c': {'type':'string','description':'Decimal, 20–200 °C'}},
    'separate': {'mode': {'const':'selective'}},
}
Operation.model_config['json_schema_extra']={'allOf':[
    {'if':{'properties':{'kind':{'const':kind}}},'then':{'properties':{'parameters':{'type':'object','properties':properties,'required':list(properties),'additionalProperties':False}}}}
    for kind in Kind.__args__ for properties in [PARAMETER_SCHEMAS.get(kind,{})]
]}

from typing import Generic, TypeVar
Item = TypeVar('Item')

class Page(DTO, Generic[Item]):
    items: list[Item]
    next_cursor: str | None
    has_more: bool

class Items(DTO, Generic[Item]):
    items: list[Item]

class PublicBatch(DTO):
    id: str
    label: str
    remaining_mass_g: str
    verification_reserve_g: str
    min_mass_g: str
    max_mass_g: str

class PublicStandard(DTO):
    id: str
    label: str
    remaining_count: int
    certificate: dict[str,str]

class PublicOrder(DTO):
    id: str
    brief: str
    batch_id: str
    input_mass_g: str
    response_min: str
    yield_min: str
    max_recipe_credits: str
    validation_repeats: int
    validation_cost: str
    status: Literal['open','completed']

class SavedNote(Note):
    id: str
    created_at: str

class SavedRecipe(Recipe):
    id: str
    created_at: str

class SavedPrediction(Prediction):
    id: str
    created_at: str
    operation_id: str | None = None

class PublicSubmission(Submission):
    id: str
    submission_id: str
    operation_id: str
    status: Literal['accepted','succeeded','failed']
    result: ValidationResult | None
    created_at: str

class Budget(DTO):
    remaining_credits: str
    reserved_credits: str
    available_credits: str
    research_spent: str
    verification_spent: str
    llm_cost: str

class Session(DTO):
    run_id: str
    actor: dict[str,str]
    available_actions: list[str]
    budget: Budget
    orders: list[PublicOrder]
    last_event_sequence: int

class PublicInstrument(DTO):
    id: str | None
    type: str
    status: Literal['available','busy']
    cost_credits: str
    limits: dict
    action_schema: dict
