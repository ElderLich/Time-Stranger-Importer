# Velocity-aligned finite billboards

`src/skill_conversion/velocity_billboard_profile.py` supplies the bounded
`velocity-billboard-current-owner-fixed-rotation-v1` geometry profile. It is
consumed by the isolated native resource producer. It does not establish a
playable skill, target placement, socket binding or native lifecycle timing.

The admitted source contract is Billboard alignment4, local simulation,
hierarchy scaling, a positive uniform prefab scale with a captured rotated
basis, constant three-dimensional start rotation, unflipped geometry and the
verified Legacy Particles Additive pass. Sample dimensions may differ in X/Y.
Depth pivot uses the particle's width. Negative dimensions, zero or axial
velocity, world-space velocity, animated/random rotation, trails, custom
streams and atlas animation are rejected.

The sample adapter consumes the unshifted `renderOrigin`, `renderSize`,
root-space `localVelocity` and separated `worldOffset`. The old generic bake's
pivot-shifted `position` and billboard quaternion are not velocity-alignment
evidence. Source start angles are validated directly before generating the
fixed rotation matrix. Native SPRK position has the world offset removed;
particle scale and rotation are identity. Eight material tracks carry width,
height, local velocity XYZ and separate world offset XYZ.

Source measurements on Unity2022.3.15f1 and6000.4.4f1 establish this order:

1. Normal is normalized owner-matrix times local velocity. Tangent is
   normalized owner-matrix times `cross(localZ, localVelocity)`.
2. Up is `cross(normal, tangent)`, without another normalization. The camera
   does not enter this alignment.
3. Source hierarchy scale is `diag(ownerRotation^T * ownerMatrix)`. A rotated
   prefab under a nonuniform external owner can introduce hierarchy shear;
   replacing this projection with matrix column lengths is incorrect.
4. Add the source pivot to the quad, multiply by `(width,height,width)`, apply
   `Ry(-y) * Rx(-x) * Rz(-z)`, then source hierarchy scale, then the velocity
   frame and translated center. Reflect source X when converting to native.

The native palette supplies only the external owner's matrix. Callers must
independently establish a positive-scale orthogonal external TRS; the existing
`view_mesh_profile.validate_runtime_owner(matrix, source_scale)` can validate
that evidence. The shader cannot infer scale signs or arbitrary external shear
from column lengths. The rotated prefab's induced hierarchy shear is handled
by the measured projection formula. No arbitrary runtime owner has been
accepted by the offline producer.

The source probe also records world-X fallback behavior for zero/axial
velocities, but this profile deliberately rejects those cases. Its lower
velocity threshold is an input-admission bound, not an epsilon substituted
into source arithmetic. The sampled source fixture is well above that bound.

Validation includes504 source cases per installed Unity version, pure Python
regressions, identical resources from Blender5.1/5.3, all-sample native channel
readback, actual D3D11 vertex stream output, and actual additive pixel passes
with RGB-only writes and native dither. Captured Unity2020 parity, source
screen-size clamps, scene composition and native game acceptance remain open.
