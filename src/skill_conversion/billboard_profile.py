"""Bounded local3D rotation and native scene-refraction coordinate contracts.

Local rotations have independent Unity2022/6000 BakeMesh evidence. Captured
Unity2020 and game acceptance remain separate. Scene UVs use the reviewed native
transparent donor's global backbuffer and resolution scale, never a copied image.
"""
from __future__ import annotations
import math


# Captured Honor_KJniuqu fragment divides vs_TEXCOORD1.xy by signed .ww.
# Preserve undefined w=0 arithmetic in HLSL rather than inventing an epsilon.
REFRACTION_SCREEN_HLSL = 'projected.xy/projected.w*.5+.5'


def _finite(values, count, label):
    if (not isinstance(values, (tuple, list)) or len(values) != count
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in values)):
        raise ValueError(f'{label} requires {count} finite numbers')
    return values


def _constant(curve):
    mode = curve.get('minMaxState', 0)
    if mode == 0:
        result = curve.get('scalar', 0)
    elif mode == 3 and curve.get('minScalar') == curve.get('scalar'):
        result = curve['scalar']
    elif mode == 1:
        keys = curve.get('maxCurve', {}).get('m_Curve', [])
        if not keys or not all(k['value'] == keys[0]['value'] and k.get('inSlope', 0) == 0 and k.get('outSlope', 0) == 0 for k in keys):
            raise ValueError('Animated3D billboard X/Y needs a complete source rotation bake')
        result = curve.get('scalar', 1) * keys[0]['value']
    else:
        raise ValueError('Randomized3D billboard X/Y needs a complete source rotation bake')
    if type(result) not in (int, float) or not math.isfinite(result):
        raise ValueError('Nonfinite3D billboard rotation')
    return float(result)


def validate_local_3d(node):
    """Return constant source X/Y angles; reject unsupported owner/angle clocks."""
    ps, renderer = node['ps'], node['renderer']
    if (ps['renderMode'] != 'Billboard' or renderer['alignment'] != 2
            or type(ps['dynamics']['simulationSpace']) is not int
            or ps['dynamics']['simulationSpace'] != 0):
        raise ValueError('3D billboard requires local alignment and current local simulation')
    if ps.get('dynamics', {}).get('rotation') or ps.get('dynamics', {}).get('angularVelocity'):
        raise ValueError('3D billboard lifetime rotation needs a complete source rotation bake')
    axes = ps.get('startRotationAxes', [])
    if len(axes) != 3:
        raise ValueError('Missing3D billboard source axes')
    return _constant(axes[0]), _constant(axes[1])


def _multiply(a, b):
    ax, ay, az, aw = a; bx, by, bz, bw = b
    return [aw*bx+ax*bw+ay*bz-az*by, aw*by-ax*bz+ay*bw+az*bx,
            aw*bz+ax*by-ay*bx+az*bw, aw*bw-ax*bx-ay*by-az*bz]


def _axis(axis, angle):
    q = [0., 0., 0., math.cos(angle / 2)]
    q[axis] = math.sin(angle / 2)
    return q


def local_billboard_quaternion(node, sample):
    """Convert the captured basis/Z-angle to the reflected native XYZW rotation.

    This is a rotation-only operation. The caller must first admit the renderer,
    owner, pivot and lifetime profile; the 2D branch does not validate them.
    The old source sample already contains its Z spin. Remove it before adding
    the complete3D rotation; preserving it would apply Z twice. Source quad
    geometry is reflected in X by the paired native vertex profile.
    """
    q = list(_finite(sample.get('rotation'), 4, 'Source quaternion'))
    if abs(sum(v*v for v in q) - 1) > 1e-4:
        raise ValueError('Source billboard quaternion is not normalized')
    angle = sample.get('angle')
    if type(angle) not in (int, float) or not math.isfinite(angle):
        raise ValueError('Source billboard angle is not finite')
    if not node['ps'].get('rotation3D'):
        result = _multiply(q, _axis(2, -2 * angle))
    else:
        x, y = validate_local_3d(node)
        result = _multiply(q, _axis(2, -angle))
        for axis, value in ((1, y), (0, -x), (2, -angle)):
            result = _multiply(result, _axis(axis, value))
    length = math.sqrt(sum(v*v for v in result))
    return [v / length for v in result]


def native_scene_uv(projected, offset, resolution):
    """Evaluate source GL screen offset then native DX resolution scaling.

    This CPU reference accepts positive clip w only. Zero is source-undefined;
    negative w is outside the reviewed rasterized profile. No near-zero epsilon
    changes a finite result. The HLSL preserves the captured signed division.
    Returned coordinates are intentionally unclamped. The native scene sampler
    owns edge addressing. This arithmetic does not establish draw ordering or
    which scene layers the game's backbuffer contains.
    """
    x, y, _, w = _finite(projected, 4, 'Native clip position')
    sx, sy = _finite(resolution, 2, 'Native resolution scale')
    if type(offset) not in (int, float) or not math.isfinite(offset):
        raise ValueError('Source screen offset is not finite')
    if sx <= 0 or sy <= 0:
        raise ValueError('Native resolution scale must be positive')
    if w <= 0:
        raise ValueError('Native scene reference requires positive clip w')
    result = [(x / w * .5 + .5 + offset) * sx,
              (.5 - y / w * .5 - offset) * sy]
    if not all(math.isfinite(v) for v in result):
        raise ValueError('Native scene projection produced a nonfinite coordinate')
    return result
