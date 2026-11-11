"""Perspective interpolation correction for an explicitly triangulated quad.

The proxy changes clip W. Interpolate source UV*proxy/source W and the same
weight, then divide, so all downstream sheet/layer transforms see source UVs.
No cache, Blender state, ownership or native depth-floor fallback lives here.
"""
import math
from .installed_effects import UnsupportedEffect


def numeric(operation, a, b):
    """Standalone arithmetic from the handoff's texture-layer dependency.

    The published renderer does not ship that broader layer module. Keep its
    exact arithmetic here without admitting unrelated shader/provider features.
    """
    if operation == 'FRACT': return a-math.floor(a)
    if operation == 'ADD': return a+b
    if operation == 'SUBTRACT': return a-b
    if operation == 'MULTIPLY': return a*b
    if operation == 'MAXIMUM': return max(a,b)
    if operation == 'MINIMUM': return min(a,b)
    if operation == 'DIVIDE': return a/b if b else 0.
    if operation == 'POWER': return a**b
    if operation == 'GREATER_THAN': return float(a>b)
    raise ValueError('Unknown layer arithmetic operation')

FACES=((0,1,2),(0,2,3))

def square_basis(corners):
 if (len(corners)!=4 or any(len(c)!=2 or any(type(v)not in(int,float) or not math.isfinite(v)for v in c)for c in corners)):
  raise UnsupportedEffect('Depth card requires four finite unit-square UV corners')
 a,b,c,d=corners
 u=tuple(y-x for x,y in zip(a,b));v=tuple(y-x for x,y in zip(a,d))
 if (set(map(tuple,corners))!={(0,0),(1,0),(1,1),(0,1)} or sum(x*y for x,y in zip(u,v))!=0
   or any(c[i]!=a[i]+u[i]+v[i]for i in range(2))):
  raise UnsupportedEffect('Depth card UV order is not an adjacent unit square')
 return a,u,v

def corrected_uv(uv,ratios,corners,op=numeric):
 a,u,v=square_basis(corners)
 if len(ratios)!=4:raise UnsupportedEffect('Depth card requires four interpolation ratios')
 if any(isinstance(q,(int,float)) and (not math.isfinite(q)or q<=0)for q in ratios):
  raise UnsupportedEffect('Depth card interpolation ratios must be positive and finite')
 def dot(axis):
  return op('ADD',op('MULTIPLY',op('SUBTRACT',uv[0],a[0]),axis[0]),
                  op('MULTIPLY',op('SUBTRACT',uv[1],a[1]),axis[1]))
 s,t=dot(u),dot(v)
 weights=(op('SUBTRACT',1,op('MAXIMUM',s,t)),op('MAXIMUM',op('SUBTRACT',s,t),0),
          op('MINIMUM',s,t),op('MAXIMUM',op('SUBTRACT',t,s),0))
 weighted=[op('MULTIPLY',w,q)for w,q in zip(weights,ratios)]
 denominator=0
 for q in weighted:denominator=op('ADD',denominator,q)
 result=[]
 for axis in range(2):
  numerator=0
  for point,q in zip(corners,weighted):numerator=op('ADD',numerator,op('MULTIPLY',point[axis],q))
  result.append(op('DIVIDE',numerator,denominator))
 return tuple(result)
