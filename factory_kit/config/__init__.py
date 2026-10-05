"""factory_kit.config — validated configuration and registration contract.

PRD §6.3 (CFG01–CFG09, CFG-C01/C02) and §6.4 (REC01). The public surface:

- :mod:`factory_kit.config.yamlmini` — restricted-YAML parser for manifests
- :mod:`factory_kit.config.schema` — manifest schema, validation, digests
- :mod:`factory_kit.config.precedence` — `.gitissue.yml` ownership mapping
- :mod:`factory_kit.config.registration` — durable registration records
"""

from . import precedence, registration, schema, yamlmini

__all__ = ["precedence", "registration", "schema", "yamlmini"]
