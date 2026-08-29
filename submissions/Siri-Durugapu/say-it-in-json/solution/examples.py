import json

# --- acme deploy layers: multi-file collision + conditional, from acme-corp/pipeline.pfcfg ---
acme_deploy = {
    "entry": "customers/acme-corp/pipeline.pfcfg",
    "keys": {
        "deploy.strategy": {
            "layers": [
                {"value": {"type": "literal", "text": "rolling"}},          # container-publish.pfcfg
                {"value": {"type": "literal", "text": "blue-green"}}        # staging.pfcfg
            ]
        },
        "deploy.target": {
            "layers": [
                {"value": {"type": "literal", "text": "staging"}},          # staging.pfcfg
                {"value": {                                                  # pipeline.pfcfg body
                    "type": "env", "var": "ACME_DEPLOY_TARGET",
                    "default": {"type": "literal", "text": "staging"}
                }}
            ]
        },
        "deploy.requires_approval": {
            "layers": [
                {"value": {                                                  # container-publish.pfcfg
                    "type": "env", "var": "DEPLOY_APPROVAL",
                    "default": {"type": "literal", "text": "true"}
                }},
                {"value": {"type": "literal", "text": "true"}},              # pipeline.pfcfg body
                {"condition": [{"type": "ifdef", "var": "ACME_DEPLOY_TARGET"}],
                 "value": {"type": "literal", "text": "false"}}             # @ifdef block
            ]
        }
    }
}

# --- container.tag: env -> concat -> ref + nested env, from acme-corp/pipeline.pfcfg line 16 ---
# tag = ${ACME_RELEASE_TAG:-$(build.node_version)-${GIT_SHA:-dev}}
acme_tag = {
    "entry": "customers/acme-corp/pipeline.pfcfg",
    "keys": {
        "container.tag": {
            "layers": [
                {"value": {
                    "type": "env", "var": "ACME_RELEASE_TAG",
                    "default": {
                        "type": "concat",
                        "parts": [
                            {"type": "ref", "path": "build.node_version"},
                            {"type": "literal", "text": "-"},
                            {"type": "env", "var": "GIT_SHA",
                             "default": {"type": "literal", "text": "dev"}}
                        ]
                    }
                }}
            ]
        }
    }
}

# --- on-prem.pfcfg included conditionally under globex/pipeline.pfcfg: condition propagated
# onto every layer this file contributes, even though on-prem.pfcfg itself has no @ifdef ---
globex_deploy = {
    "entry": "customers/globex/pipeline.pfcfg",
    "keys": {
        "deploy.strategy": {
            "layers": [
                {"condition": [{"type": "ifdef", "var": "PRODUCTION"}],
                 "value": {"type": "literal", "text": "manual"}}   # from on-prem.pfcfg, condition propagated from include site
            ]
        },
        "deploy.target": {
            "layers": [
                {"value": {"type": "env", "var": "GLOBEX_ENV",
                           "default": {"type": "literal", "text": "development"}}},
                {"condition": [{"type": "ifdef", "var": "PRODUCTION"}],
                 "value": {"type": "literal", "text": "on-prem"}}
            ]
        }
    }
}

# --- cascade chain + cycle, from edge-cases/interpolation-cascade.pfcfg ---
cascade = {
    "entry": "edge-cases/interpolation-cascade.pfcfg",
    "keys": {
        "cascade.alpha": {
            "layers": [{"value": {"type": "env", "var": "CASCADE_ALPHA",
                                   "default": {"type": "literal", "text": "unset"}}}]
        },
        "cascade.beta": {
            "layers": [{"value": {
                "type": "concat",
                "parts": [
                    {"type": "literal", "text": "prefix-"},
                    {"type": "ref", "path": "cascade.alpha"},
                    {"type": "literal", "text": "-suffix"}
                ]
            }}]
        },
        "cascade.gamma": {
            "layers": [{"value": {"type": "env", "var": "CASCADE_GAMMA",
                                   "default": {"type": "ref", "path": "cascade.beta"}}}]
        },
        "cascade.delta": {
            "layers": [{"value": {
                "type": "env", "var": "CASCADE_DELTA",
                "default": {"type": "concat", "parts": [
                    {"type": "ref", "path": "cascade.gamma"},
                    {"type": "literal", "text": "-final"}
                ]}
            }}]
        },
        "cascade.epsilon": {
            "layers": [
                {"value": {"type": "concat", "parts": [
                    {"type": "literal", "text": "local-"},
                    {"type": "ref", "path": "cascade.delta"}
                ]}},
                {"condition": [{"type": "ifdef", "var": "CI"}],
                 "value": {"type": "concat", "parts": [
                     {"type": "literal", "text": "ci-"},
                     {"type": "ref", "path": "cascade.delta"}
                 ]}}
            ]
        },
        "cascade.loop.a": {
            "layers": [{"value": {"type": "ref", "path": "cascade.loop.b"}}]
        },
        "cascade.loop.b": {
            "layers": [{"value": {"type": "ref", "path": "cascade.loop.a"}}]
        }
    }
}

# --- initech cross-file chain: release.bundle_name -> release.version -> build.node_version
#     -> toolchain.node.version, plus the required-no-default vs referenced-optional-default pair ---
initech = {
    "entry": "customers/initech/pipeline.pfcfg",
    "keys": {
        "toolchain.node.version": {
            "layers": [{"value": {"type": "env", "var": "NODE_VERSION",
                                   "default": {"type": "literal", "text": "20"}}}]
        },
        "build.node_version": {
            "layers": [{"value": {"type": "ref", "path": "toolchain.node.version"}}]
        },
        "release.version": {
            "layers": [{"value": {
                "type": "env", "var": "RELEASE_VERSION",
                "default": {"type": "concat", "parts": [
                    {"type": "literal", "text": "0.0.0-"},
                    {"type": "ref", "path": "build.node_version"}
                ]}
            }}]
        },
        "release.bundle_name": {
            "layers": [{"value": {"type": "concat", "parts": [
                {"type": "literal", "text": "initech-"},
                {"type": "ref", "path": "release.version"},
                {"type": "literal", "text": ".tar.gz"}
            ]}}]
        }
    }
}

# --- migration.api_endpoint (bare required var, no default) referenced by fallback_endpoint ---
migration = {
    "entry": "edge-cases/conditional-includes.pfcfg",
    "keys": {
        "migration.api_endpoint": {
            "layers": [{"value": {"type": "env", "var": "REQUIRED_API_ENDPOINT"}}]  # bare: no default, no alt
        },
        "migration.fallback_endpoint": {
            "layers": [{"value": {
                "type": "env", "var": "OPTIONAL_API_ENDPOINT",
                "default": {"type": "ref", "path": "migration.api_endpoint"}
            }}]
        }
    }
}

# --- key_prefix: two concatenated interpolations in one value, ${CI:+ci-}${CACHE_NAMESPACE:-default} ---
key_prefix = {
    "entry": "_base/defaults.pfcfg",
    "keys": {
        "cache.key_prefix": {
            "layers": [{"value": {
                "type": "concat",
                "parts": [
                    {"type": "env", "var": "CI", "alt": {"type": "literal", "text": "ci-"}},
                    {"type": "env", "var": "CACHE_NAMESPACE", "default": {"type": "literal", "text": "default"}}
                ]
            }}]
        }
    }
}

ALL_EXAMPLES = {
    "acme_deploy": acme_deploy,
    "acme_tag": acme_tag,
    "globex_deploy": globex_deploy,
    "cascade": cascade,
    "initech": initech,
    "migration": migration,
    "key_prefix": key_prefix,
}

if __name__ == "__main__":
    import jsonschema
    schema = json.load(open("schema.json"))      
    validator = jsonschema.Draft202012Validator(schema)
    for name, doc in ALL_EXAMPLES.items():
        errors = list(validator.iter_errors(doc))
        if errors:
            print(f"FAIL {name}:")
            for e in errors:
                print(f"   {list(e.path)}: {e.message}")
        else:
            print(f"OK   {name}")
