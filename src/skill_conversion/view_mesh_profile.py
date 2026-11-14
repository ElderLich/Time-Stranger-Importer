"""Bounded live-camera mesh geometry with independent owner and particle clocks.

Uniform sampled particle size is required. The native palette carries owner
position and scale; material channels carry the camera-relative quaternion.
Geometry is C_camera * S_owner * R_particle * S_particle, preserving the source
nonuniform-scale order. The caller supplies a mounted bake with identity camera
and the captured reflected mesh. This module does not establish game acceptance.
Runtime owners require independently known positive scales and an orthogonal
TRS basis. Negative/reflected or sheared owners are outside this profile; the
native shader alone cannot recover that information from column lengths.
"""
from __future__ import annotations
import copy
import math

PROFILE = 'view-mesh-current-owner-uniform-particle-v1'
ROTATION_BINDINGS = ((36, 0), (36, 1), (36, 2), (302, 0))
WORLD_BINDINGS = ((178, 0), (178, 1), (178, 2))

VERTEX_HLSL = '''
cbuffer Constants : register(b0) {
  column_major float4x4 ViewProj;
  row_major float4x4 ViewInverse;
  float3 SurfaceColor;
  float EmissiveIntensity;
  float3 SpecularColorRgb;
};
cbuffer BoneConstants : register(b1) { float4 MatrixPalette[750]; };
struct VertexOutput {
  float4 color:COLOR0; float2 uv:TEXCOORD0;
  float4 clip:TEXCOORD1; float4 position:SV_Position;
};
VertexOutput main(float3 position:POSITION0,float2 uv:TEXCOORD0,float4 color:COLOR0) {
  VertexOutput o;
  float3 scale=float3(length(float3(MatrixPalette[0].x,MatrixPalette[1].x,MatrixPalette[2].x)),
                      length(float3(MatrixPalette[0].y,MatrixPalette[1].y,MatrixPalette[2].y)),
                      length(float3(MatrixPalette[0].z,MatrixPalette[1].z,MatrixPalette[2].z)));
  float4 q=normalize(float4(SurfaceColor,EmissiveIntensity));
  float3 rotated=position+2.0*cross(q.xyz,cross(q.xyz,position)+q.w*position);
  // Uniform particle size is already in the palette column lengths. Scale
  // after rotation: a nonuniform owner scale cannot commute across q.
  float3 p=rotated*scale;
  float3 center=float3(MatrixPalette[0].w,MatrixPalette[1].w,MatrixPalette[2].w)+SpecularColorRgb;
  float3 world=center+ViewInverse[0].xyz*p.x+ViewInverse[1].xyz*p.y+ViewInverse[2].xyz*p.z;
  o.position=mul(ViewProj,float4(world,1));o.clip=o.position;o.color=color;o.uv=uv;
  return o;
}
'''


def _vector(value, count, label):
    if (not isinstance(value, (list, tuple)) or len(value) != count
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in value)):
        raise ValueError(f'{label} requires {count} finite numbers')
    return value


def _uniform(value, label):
    value = _vector(value, 3, label)
    if min(value) < 0 or max(value) - min(value) > 1e-6 * max(1., max(value)):
        raise ValueError(f'{label} must be uniform and nonnegative; no size coercion is supported')
    return value


def validate_runtime_owner(matrix, source_scale):
    """Check an explicitly supplied runtime-owner proof before using this route.

    source_scale must come from source/owner evidence, not column norms inferred
    from this same matrix: norms cannot reveal scale signs. The offline producer
    has no runtime owner and therefore leaves this as an acceptance blocker.
    """
    scale = _vector(source_scale, 3, 'Runtime owner scale')
    if min(scale) <= 0:
        raise ValueError('Runtime owner scale must be positive; reflected owners are unsupported')
    if not isinstance(matrix, (list, tuple)) or len(matrix) != 4:
        raise ValueError('Runtime owner requires a finite affine4x4 matrix')
    rows = [_vector(row, 4, 'Runtime owner matrix row') for row in matrix]
    if any(abs(a-b) > 1e-7 for a, b in zip(rows[3], (0, 0, 0, 1))):
        raise ValueError('Runtime owner matrix must be affine')
    axes = [[rows[r][c] / scale[c] for r in range(3)] for c in range(3)]
    for a in range(3):
        for b in range(3):
            if abs(sum(x*y for x,y in zip(axes[a], axes[b])) - (1 if a == b else 0)) > 1e-5:
                raise ValueError('Runtime owner has shear or scale inconsistent with supplied evidence')
    x,y,z=axes
    determinant=x[0]*(y[1]*z[2]-y[2]*z[1])-y[0]*(x[1]*z[2]-x[2]*z[1])+z[0]*(x[1]*y[2]-x[2]*y[1])
    if determinant <= 0:
        raise ValueError('Reflected runtime owner matrix is unsupported')
    return True


def validate_view_mesh(node):
    ps, renderer = node['ps'], node['renderer']
    if (ps.get('renderMode') != 'Mesh' or renderer.get('renderMode') != 'Mesh'
            or renderer.get('alignment') != 0):
        raise ValueError('Live view-mesh profile requires Mesh with view alignment0')
    space = ps.get('dynamics', {}).get('simulationSpace')
    if type(space) is not int or space != 0 or ps.get('scalingMode') != 0:
        raise ValueError('View mesh requires local simulation and hierarchy scaling')
    if any(_vector(renderer.get('pivot'), 3, 'Mesh pivot')) or any(renderer.get('flip', [])):
        raise ValueError('View mesh requires centered unflipped geometry')
    if ps.get('size3D') or ps.get('dynamics', {}).get('size', {}).get('separate'):
        raise ValueError('View mesh requires uniform source particle size')
    if ps.get('dynamics', {}).get('trail') or node.get('actionPose') is not None or node.get('actionRenderer'):
        raise ValueError('View mesh trails and animated renderer overrides are unsupported')
    if ps.get('flipbook') or ps.get('tilesX', 1) != 1 or ps.get('tilesY', 1) != 1:
        raise ValueError('View mesh atlas animation needs a separate UV profile')
    basis = _vector(node.get('prefabBasis'), 9, 'Source prefab basis')
    if any(abs(basis[i]) > 1e-7 for i in (1, 2, 3, 5, 6, 7)):
        raise ValueError('View mesh source prefab rotation/shear needs separate source proof')
    _uniform([basis[0], basis[4], basis[8]], 'Source prefab scale')
    if min(basis[0], basis[4], basis[8]) <= 0:
        raise ValueError('View mesh source prefab scale must be positive')
    materials = node.get('material_infos', [])
    if len(materials) != 1 or materials[0].get('shader') != 'Honor/FX/Honor_KJniuqu':
        raise ValueError('View mesh currently requires the captured scene-refraction material')
    states = materials[0].get('passStates', [])
    required = {'srcBlend': 5, 'dstBlend': 10, 'srcBlendAlpha': 5, 'dstBlendAlpha': 10,
                'blendOp': 0, 'blendOpAlpha': 0, 'colorMask': 15, 'cull': 0, 'zTest': 4, 'zWrite': 0}
    if len(states) != 1 or any(states[0].get(k) != v for k, v in required.items()):
        raise ValueError('View mesh source pass state is outside the verified refraction profile')
    return PROFILE


def prepare_view_mesh_samples(node, particle):
    """Validate the source bake and return separate center, size and quaternion.

    Quaternions include the mounted source camera's reflected optical basis.
    Only signs are changed to keep adjacent keys in the same hemisphere; no
    source angle, size, lifetime or emission schedule is reconstructed here.
    """
    validate_view_mesh(node)
    if particle.get('renderMode') != 'Mesh' or particle.get('alignment') != 0:
        raise ValueError('Particle renderer differs from the admitted view-mesh profile')
    samples = copy.deepcopy(particle.get('samples'))
    if not isinstance(samples, list) or not samples:
        raise ValueError('View mesh needs captured samples')
    previous_frame = None; previous_q = None
    for sample in samples:
        frame = sample.get('frame')
        if (type(frame) not in (int, float) or not math.isfinite(frame) or int(frame) != frame
                or not 0 <= frame < 32766 or previous_frame is not None and frame != previous_frame + 1):
            raise ValueError('View mesh samples need contiguous native-range integer frames')
        previous_frame = frame
        if sample.get('viewAligned') is not True or sample.get('flipbook') != 0:
            raise ValueError('View mesh source sample has an unbound camera or atlas profile')
        position = _vector(sample.get('position'), 3, 'Particle position')
        world = _vector(sample.get('worldOffset'), 3, 'World offset')
        _uniform(sample.get('size'), 'Sampled particle size')
        q = list(_vector(sample.get('rotation'), 4, 'View quaternion'))
        if abs(sum(v*v for v in q) - 1) > 1e-4:
            raise ValueError('View mesh quaternion is not normalized')
        _vector(sample.get('color'), 4, 'Particle color')
        for key in ('customData', 'customData2'):
            if any(_vector(sample.get(key), 4, key)):
                raise ValueError('View mesh custom material streams need additional native carriers')
        if previous_q is not None and sum(a*b for a, b in zip(q, previous_q)) < 0:
            q = [-v for v in q]
        previous_q = q
        sample['materialRotation'] = q
        sample['rotationDegrees'] = [0., 0., 0.]
        sample['position'] = [p-w for p, w in zip(position, world)]
    return samples


def vertex_source(node):
    validate_view_mesh(node)
    return VERTEX_HLSL
