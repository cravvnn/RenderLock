import numpy as np
import pytest
from agr.installed_effects import UnsupportedEffect
from agr.particle_depth_card_uv import corrected_uv,FACES


@pytest.mark.parametrize('corners',[
 ((0,0),(1,0),(1,1),(0,1)),((0,0),(0,1),(1,1),(1,0)),
 ((1,1),(0,1),(0,0),(1,0)),((0,1),(0,0),(1,0),(1,1))])
def test_correction_recovers_original_perspective_interpolation(corners):
 rng=np.random.default_rng(840199)
 for face in FACES:
  for _ in range(20):
   bary=rng.random(3);bary/=sum(bary)
   original_w=rng.uniform(.5,8,4);proxy_w=original_w+rng.uniform(.1,4,4)
   ratios=proxy_w/original_w
   expected_weights=bary/original_w[list(face)];expected_weights/=sum(expected_weights)
   proxy_weights=bary/proxy_w[list(face)];proxy_weights/=sum(proxy_weights)
   corner=np.asarray(corners)[list(face)]
   proxy_uv=proxy_weights@corner;expected=expected_weights@corner
   assert corrected_uv(proxy_uv,ratios,corners)==pytest.approx(expected,abs=1e-12)


def test_neutral_ratios_preserve_uv_and_diagonal_is_continuous():
 c=((0,0),(1,0),(1,1),(0,1))
 assert corrected_uv((.4,.7),(1,1,1,1),c)==pytest.approx((.4,.7))
 for a,b in zip(corrected_uv((.5-1e-10,.5),(1,2,3,4),c),corrected_uv((.5+1e-10,.5),(1,2,3,4),c)):
  assert abs(a-b)<1e-9


@pytest.mark.parametrize('ratios',[(1,2,3),(1,0,1,1),(1,-1,1,1),(1,float('inf'),1,1)])
def test_invalid_ratio_never_falls_back_to_uncorrected_uv(ratios):
 with pytest.raises(UnsupportedEffect):corrected_uv((.5,.5),ratios,((0,0),(1,0),(1,1),(0,1)))


def test_nonrectangular_or_crossed_uv_topology_refuses():
 with pytest.raises(UnsupportedEffect):corrected_uv((.5,.5),(1,1,1,1),((0,0),(1,1),(1,0),(0,1)))
