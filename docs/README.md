# Importer guides

## Models and animation

| Task | Guide |
| --- | --- |
| Port a New Century Digimon, add its skills and art, and stage the mod | [Add a playable Digimon](add-a-playable-digimon.md) |
| Port a ReArise actor and check its skill, camera, effect and sound bindings | [ReArise port workflow](rearise-port-workflow.md) |
| Edit a native mesh or animation, choose a donor or export a moveset | [Models and animation](models-and-animation.md) |
| Reimport a model after an orientation or facial rest-scale correction | [Import and animation corrections](import-animation-fixes.md) |
| Animate a texture on a native material | [Texture animation](texture-animation.md) |
| Check dependencies and package exported geometry | [Native model packaging](native-model-package.md) |
| Work with Cyber Sleuth PC models, animations and cameras | [Cyber Sleuth](cyber-sleuth.md) |

## Attacks, cameras and scene data

| Task | Guide |
| --- | --- |
| Choose a captured cast, approach, return or cinematic and inspect its cues | [Skill phases and effect graphs](skill-effect-workflows.md) |
| Compare official attacks with the Demon Lords and Four Sovereigns ports | [Official attack references](official-attack-references.md) |
| Map a captured phase and build a native review candidate | [Skill recipes](skill-conversion.md) |
| Bind the actor motion, caster and target effects, hit frames and audio | [Native skill bindings](native-skill-bindings.md) |
| Combine phases, cameras and effects into one attack | [Native skill sequences](native-skill-sequence.md) |
| Fit a native camera and preserve its rig, lens and name list | [Native camera fitting](native-camera-fit.md) |
| Edit native effect timing or collision shapes | [Timelines and collisions](native-timeline-collision.md) |
| Import cameras and lights with their owning model | [Native scenes](native-scene.md) · [Light records](native-lights.md) |
| Import a native rigid effect, play its animation and read import warnings | [Native rigid effects](native-rigid-effects.md) |
| Reconstruct an ordinary attack’s stand timing and actor motion | [Actor lifecycle](actor-lifecycle.md) |

## Convert particle effects

Start with [component jobs and native donors](finite-producer.md). Choose a profile for the source effect you are converting.

| Source effect | Conversion guide |
| --- | --- |
| Billboard or local mesh particles | [Particle profiles](skill-particle-profiles.md) |
| Velocity-aligned billboards | [Velocity billboard profile](SKILL_VELOCITY_BILLBOARD_PROFILE.md) |
| View-aligned mesh particles | [View mesh profile](skill-view-mesh-profile.md) |
| Billboards sampled from a source capture | [Sampled particle profile](skill-sampled-particle-profile.md) |
| Layered dissolves | [Dissolve profile](skill-dissolve-profile.md) |
| Effects attached to a stationary root | [Stationary root profile](stationary-root-profile.md) |

## Maintain the add-on

- [Source layout, compatibility and tests](architecture.md)
- [Compare an installed add-on with the packaged ZIP](installed-addon-sync.md)
- [Release notes](release-notes.md) and [project credits](credits.md)
