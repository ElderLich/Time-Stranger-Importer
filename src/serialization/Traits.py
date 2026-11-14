from ...external.exbip.serializables import traits
from . import DSCSParsers
from ..spark import atomic_write

DSCSReadable         = traits.ReadableTrait(DSCSParsers.DSCSReader)
class DSCSWritable(traits.WriteableTrait(DSCSParsers.DSCSWriter)):
    def write(self, filepath, *args, **kwargs):
        # Finish serialization before touching the selected destination. Keep
        # the established overwrite behavior used by Blender's export dialog.
        atomic_write(filepath, self.tobytes(*args, **kwargs), overwrite=True)


DSCSOffsetCalculable = traits.OffsetsCalculableTrait(DSCSParsers.DSCSOffsetCalculator)
DSCSValidatable      = traits.ValidatableTrait(DSCSParsers.DSCSValidator)
