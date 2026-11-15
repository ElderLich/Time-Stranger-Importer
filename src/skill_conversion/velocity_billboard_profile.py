"""Bounded velocity-aligned billboard geometry, independent of the camera.

This profile preserves source 3D rotation, width-based depth pivot and the
unnormalized velocity frame. The palette contains the current external owner
only; particle dimensions and root-space velocity use separate material tracks.
The external owner must have independently evidenced positive scale and an
orthogonal TRS basis. Its product with the rotated prefab may have hierarchy
shear: source lossyScale is a rotation projection, not matrix column lengths.
Zero/axial velocity, world-space velocity, animated rotation and arbitrary
sheared/reflected external owners are deliberately outside this finite route.
Source geometry was measured on Unity 2022/6000, not the captured 2020 runtime.
"""
from __future__ import annotations
import copy
import math

PROFILE = 'velocity-billboard-current-owner-fixed-rotation-v1'
FLOAT_BINDINGS = ((301, 1), (301, 2), (36, 0), (36, 1), (36, 2),
                  (178, 0), (178, 1), (178, 2))


def _vector(value, count, label):
    if (not isinstance(value, (list, tuple)) or len(value) != count
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in value)):
        raise ValueError(f'{label} requires {count} finite numbers')
    return list(value)


def _mul(a, b):
    return [[sum(x*y for x,y in zip(row,col)) for col in zip(*b)] for row in a]


def _apply(a, v):
    return [sum(x*y for x,y in zip(row,v)) for row in a]


def _cross(a,b):
    return [a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]]


def _inverse(a):
    columns=[_cross(a[1],a[2]),_cross(a[2],a[0]),_cross(a[0],a[1])]
    determinant=sum(x*y for x,y in zip(a[0],columns[0]))
    if determinant <= 0:
        raise ValueError('Prefab basis must have positive determinant')
    return [[v/determinant for v in row] for row in zip(*columns)]


def _constant(curve):
    if not isinstance(curve,dict) or curve.get('minMaxState') not in (0,3):
        raise ValueError('Velocity billboard requires constant source rotation')
    value=_vector([curve.get('scalar')],1,'Source rotation')[0]
    if curve['minMaxState']==3 and curve.get('minScalar') != value:
        raise ValueError('Random source rotation needs sampled source Euler tracks')
    return value


def profile_constants(node):
    """Validate captured prefab TRS and produce fixed reflected shader matrices."""
    flat=_vector(node.get('prefabBasis'),9,'Source prefab basis')
    basis=[flat[i:i+3] for i in (0,3,6)]
    scale=_vector(node.get('prefabScale'),3,'Source prefab scale')
    if min(scale)<=0 or max(scale)-min(scale)>1e-6*max(scale):
        raise ValueError('Source prefab requires independently captured positive uniform scale')
    columns=list(zip(*basis));lengths=[math.sqrt(sum(v*v for v in c)) for c in columns]
    for i in range(3):
        if abs(lengths[i]-scale[i])>1e-6*max(1.,scale[i]):
            raise ValueError('Captured prefab basis and scale disagree')
        for j in range(3):
            dot=sum(a*b for a,b in zip(columns[i],columns[j]))/(lengths[i]*lengths[j])
            if abs(dot-(1 if i==j else 0))>1e-6:
                raise ValueError('Source prefab shear is unsupported')
    inverse=_inverse(basis)
    reflection=[[-1,0,0],[0,1,0],[0,0,1]]
    cross_z=[[0,-1,0],[1,0,0],[0,0,0]]
    tangent=_mul(_mul(_mul(_mul(reflection,basis),cross_z),inverse),reflection)
    weights=[[v*v/lengths[j] for v in columns[j]] for j in range(3)]
    ps=node['ps']
    if not ps.get('rotation3D'):
        raise ValueError('Velocity profile requires captured 3D rotation axes')
    axes=ps.get('startRotationAxes')
    if not isinstance(axes,list) or len(axes)!=3:
        raise ValueError('Missing captured rotation axes')
    x,y,z=[-_constant(c) for c in axes]
    cx,sx,cy,sy,cz,sz=math.cos(x),math.sin(x),math.cos(y),math.sin(y),math.cos(z),math.sin(z)
    spin=_mul(_mul([[cy,0,sy],[0,1,0],[-sy,0,cy]],[[1,0,0],[0,cx,-sx],[0,sx,cx]]),
              [[cz,-sz,0],[sz,cz,0],[0,0,1]])
    return {'tangent':tangent,'scale_weights':weights,'spin':spin,'size_divisor':scale}


def validate_velocity_billboard(node):
    ps,r=node['ps'],node['renderer'];d=ps.get('dynamics',{})
    if ps.get('renderMode')!='Billboard' or r.get('renderMode')!='Billboard' or r.get('alignment')!=4:
        raise ValueError('Velocity profile requires Billboard alignment4')
    if type(d.get('simulationSpace')) is not int or d['simulationSpace']!=0 or ps.get('scalingMode')!=0:
        raise ValueError('Velocity profile requires local simulation and hierarchy scaling')
    _vector(r.get('pivot'),3,'Billboard pivot')
    if any(_vector(r.get('flip'),3,'Billboard flip')):
        raise ValueError('Flipped velocity billboards are unsupported')
    if any(d.get(k) for k in ('trail','rotation','rotationBySpeed','angularVelocity')):
        raise ValueError('Trails and animated source rotation require another profile')
    if node.get('actionPose') is not None or node.get('actionRenderer'):
        raise ValueError('Animated renderer overrides are unsupported')
    if ps.get('flipbook') or ps.get('tilesX',1)!=1 or ps.get('tilesY',1)!=1:
        raise ValueError('Velocity atlas animation is unsupported')
    materials=node.get('material_infos',[])
    if len(materials)!=1 or materials[0].get('shader')!='Legacy Shaders/Particles/Additive':
        raise ValueError('Velocity profile requires the verified additive source family')
    expected={'srcBlend':5,'dstBlend':1,'srcBlendAlpha':5,'dstBlendAlpha':1,
              'blendOp':0,'blendOpAlpha':0,'colorMask':14,'cull':0,'zTest':4,'zWrite':0}
    states=materials[0].get('passStates',[])
    if len(states)!=1 or any(states[0].get(k)!=v for k,v in expected.items()):
        raise ValueError('Velocity additive render state differs from the verified contract')
    profile_constants(node)
    return PROFILE


def prepare_velocity_samples(node,particle):
    """Use unshifted renderer origins; never reuse the old generic pivot bake."""
    validate_velocity_billboard(node);constants=profile_constants(node)
    if particle.get('renderMode')!='Billboard' or particle.get('alignment')!=4:
        raise ValueError('Particle renderer differs from source velocity profile')
    samples=copy.deepcopy(particle.get('samples'))
    if not isinstance(samples,list) or not samples:
        raise ValueError('Velocity profile requires captured samples')
    previous=None
    for sample in samples:
        f=sample.get('frame')
        if (type(f) not in (int,float) or not math.isfinite(f) or int(f)!=f or not 0<=f<32766
                or previous is not None and f!=previous+1):
            raise ValueError('Velocity samples need contiguous native-range integer frames')
        previous=f
        origin=_vector(sample.get('renderOrigin'),3,'Unshifted renderer origin')
        world=_vector(sample.get('worldOffset'),3,'Separate world offset')
        dimensions=_vector(sample.get('renderSize'),3,'Renderer dimensions')
        if min(dimensions)<0:
            raise ValueError('Negative particle dimensions are unsupported')
        velocity=_vector(sample.get('localVelocity'),3,'Root-space local velocity')
        tangent=_apply(constants['tangent'],velocity)
        # This is an admission guard, not an arithmetic epsilon or fallback.
        if sum(v*v for v in velocity)<1e-12 or sum(v*v for v in tangent)<1e-12:
            raise ValueError('Zero/near-zero or axial velocity needs the source fallback profile')
        if any(_vector(sample.get('worldVelocity'),3,'World velocity')):
            raise ValueError('World velocity needs a separate current-owner orientation contract')
        _vector(sample.get('color'),4,'Particle color')
        for field in ('customData','customData2'):
            if any(_vector(sample.get(field),4,field)):
                raise ValueError('Custom streams need additional native material carriers')
        if sample.get('flipbook')!=0:
            raise ValueError('Sampled atlas animation is unsupported')
        sample['dimensions']=dimensions[:2]
        sample['position']=[v-w for v,w in zip(origin,world)]
        sample['rotationDegrees']=[0.,0.,0.]
        sample['size']=[1.,1.,1.]
    return samples


def vertex_source(node):
    validate_velocity_billboard(node);c=profile_constants(node)
    def matrix(value):return 'float3x3('+','.join(f'{v:.17g}' for row in value for v in row)+')'
    return '''
cbuffer Constants : register(b0) {
  column_major float4x4 ViewProj;
  float3 EmissiveColor;
  float3 SurfaceColor;
  float3 SpecularColorRgb;
};
cbuffer BoneConstants : register(b1) { float4 MatrixPalette[750]; };
struct VertexOutput {float2 uv:TEXCOORD0;float4 position:SV_Position;float4 clip:TEXCOORD1;};
VertexOutput main(float3 position:POSITION0,float2 uv:TEXCOORD0) {
  VertexOutput o;
  float3x3 owner=float3x3(MatrixPalette[0].xyz,MatrixPalette[1].xyz,MatrixPalette[2].xyz);
  float3 ownerScale=float3(length(float3(owner[0].x,owner[1].x,owner[2].x)),
                           length(float3(owner[0].y,owner[1].y,owner[2].y)),
                           length(float3(owner[0].z,owner[1].z,owner[2].z)));
  float3 normal=normalize(mul(owner,SurfaceColor));
  float3 tangent=normalize(mul(owner,mul(TANGENT,SurfaceColor)));
  float3 up=cross(tangent,normal); // Reflected source cross(normal,tangent), unnormalized.
  float3 sourceScale=mul(SCALE_WEIGHTS,ownerScale);
  float width=EmissiveColor.y/DIVISOR_X,height=EmissiveColor.z/DIVISOR_Y;
  float3 rotatedPoint=mul(SPIN,position*float3(width,height,width))*sourceScale;
  float3 center=float3(MatrixPalette[0].w,MatrixPalette[1].w,MatrixPalette[2].w)+SpecularColorRgb;
  float3 world=center+tangent*rotatedPoint.x+up*rotatedPoint.y+normal*rotatedPoint.z;
  o.position=mul(ViewProj,float4(world,1));o.clip=o.position;o.uv=uv;return o;
}
'''.replace('TANGENT',matrix(c['tangent'])).replace('SCALE_WEIGHTS',matrix(c['scale_weights'])).replace(
        'SPIN',matrix(c['spin'])).replace('DIVISOR_X',f"{c['size_divisor'][0]:.17g}").replace('DIVISOR_Y',f"{c['size_divisor'][1]:.17g}")
