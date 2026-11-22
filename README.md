# Time Stranger Importer

A Blender add-on for editing *Digimon Story: Time Stranger* models and animations, porting New Century Digimon and their skills, and importing Cyber Sleuth PC assets.

## Install

1. Download **Time_Stranger_Importer.zip** from the [latest release](https://github.com/ElderLich/Time-Stranger-Importer/releases/latest).
2. In Blender, open **Edit → Preferences → Add-ons → Install from Disk**, select the ZIP, and enable **Time Stranger Importer**. Disable any older DSTS importer that registers the same menus.
3. Open **File → Import** and choose the source game. Time Stranger exports are under **File → Export → Digimon Time Stranger**. Press **N** in the 3D Viewport to open the **DSTS** sidebar.

The included geometry backend supports Windows x64 with CPython 3.13. The project tests against Blender 5.3. [Blender compatibility](docs/architecture.md#blender-5x-compatibility)

## Choose a workflow

| I want to… | Open this guide |
| --- | --- |
| Add a playable Digimon, from its library model to a staged mod | [New Century port walkthrough](docs/add-a-playable-digimon.md) |
| Import a ReArise character, plan its captured skills and check a staged battle pack | [ReArise port workflow](docs/rearise-port-workflow.md) |
| Edit a native model, retarget donor textures or export animations | [Models and animation](docs/models-and-animation.md) |
| Watch a source attack with its cinematic camera and audio | [Preview a cinematic in Blender](docs/add-a-playable-digimon.md#preview-the-cinematic) |
| Build a native skill with converted effects, hits and audio | [Skill recipes](docs/skill-conversion.md) · [Actor and effect bindings](docs/native-skill-bindings.md) |
| Join a cinematic and its cast into one native attack | [Native skill sequences](docs/native-skill-sequence.md) |
| Validate an exported model and gather its dependencies | [Package a native model](docs/native-model-package.md) |
| Import a Cyber Sleuth PC model, animation or camera | [Cyber Sleuth workflow](docs/cyber-sleuth.md) |

[Browse all guides](docs/README.md) · [Build from source and run checks](docs/architecture.md#run-the-checks) · [All releases and change notes](https://github.com/ElderLich/Time-Stranger-Importer/releases)

Developed and maintained by **Senator John (ElderLich)**. [Earlier tools and contributors](docs/credits.md)
