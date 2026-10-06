"""Private deterministic transitions. Never distributed with the participant SDK."""
from decimal import Decimal as D, getcontext, ROUND_HALF_EVEN
getcontext().prec = 28
COMPONENTS = ('a0', 'a1', 'b', 'i')
DEFAULT_CONFIG = {'heater_gain': '1', 'activation_threshold': '70', 'analyzer_gain': '1', 'analyzer_offset': '0'}

def mass(s):
    return sum((D(s[k]) for k in COMPONENTS), D(0))

def fmt(n):
    return str(D(n).quantize(D('.000001'), rounding=ROUND_HALF_EVEN))

def fresh(fractions, amount):
    return dict(zip(COMPONENTS, (str(D(x)*D(amount)) for x in fractions)), temperature='20')

def activate(s, config):
    if D(s['temperature']) >= D(config['activation_threshold']):
        s['a1'] = str(D(s['a1']) + D(s['a0']))
        s['a0'] = '0'
    return s

def transition(kind, states, params, config):
    s = dict(states[0])
    if kind == 'heat':
        s['temperature'] = str(min(D(200), D(s['temperature']) + 10*D(config['heater_gain'])*params['power']*params['duration']/mass(s)))
        return [activate(s, config)]
    if kind == 'cool':
        s['temperature'] = str(min(D(s['temperature']), D(params['target_temperature_c'])))
        return [s]
    if kind in ('split', 'separate'):
        factors = [D(params['fraction'])]*4 if kind == 'split' else list(map(D, ['.5','.9','.4','.2']))
        left = {k: str(D(s[k])*f) for k,f in zip(COMPONENTS, factors)}
        right = {k: str(D(s[k])-D(left[k])) for k in COMPONENTS}
        for out in (left, right):
            out['temperature'] = s['temperature']
            if mass(out) < D('.001'):
                raise ValueError('Output below minimum portion size')
        return [left, right]
    if kind == 'mix':
        t = states[1]
        out = {k: str(D(s[k])+D(t[k])) for k in COMPONENTS}
        out['temperature'] = str((mass(s)*D(s['temperature'])+mass(t)*D(t['temperature']))/mass(out))
        return [activate(out, config)]
    raise ValueError('Unknown transition')

def measure(s, quantity, config):
    if quantity == 'mass': return mass(s)
    if quantity == 'temperature': return D(s['temperature'])
    response = max(D(0), min(D(100), 100*(D(s['a0'])+D('1.3')*D(s['a1'])-D(s['i']))/mass(s)))
    return D(config['analyzer_gain'])*response+D(config['analyzer_offset'])

def cost(kind, p):
    return D(2)+D(p['power'])*D(p['duration'])/60 if kind == 'heat' else D({'obtain':1,'reference':2,'split':1,'cool':1,'separate':3,'mix':2}.get(kind,1))
