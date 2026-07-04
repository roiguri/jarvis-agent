"""Media skill — split into sub-skills (radarr / sonarr / prowlarr /
jellyseerr / system).

The parent ``media`` namespace owns no tools; it is a discovery index. Each
subpackage import runs its ``@tool_register`` side-effects so the registry is
populated when ``import_all()`` imports this package.
"""

from tools.media.radarr import radarr  # noqa: F401
from tools.media.sonarr import sonarr  # noqa: F401
from tools.media.prowlarr import prowlarr  # noqa: F401
from tools.media.jellyseerr import jellyseerr  # noqa: F401
from tools.media.system import system  # noqa: F401
