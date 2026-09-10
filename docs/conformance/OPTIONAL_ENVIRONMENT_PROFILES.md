# Optional environment conformance profiles

Issue #46 requires retained evidence for the optional MCP and LiteLLM paths in addition to the reference/native baseline. These checks are environment-matrix adjuncts rather than new A–X product scenarios.

## MCP (`ENV-MCP`)

The MCP profile requires the exact optional dependency `mcp==2.1.1`. It starts the maintained real stdio fixture through the official Model Context Protocol Python SDK and invokes `tool.lookup` through the same canonical `CapabilityRegistry` and `CapabilityInvoker` path used by native capabilities.

The profile fails closed when the MCP distribution is absent or its installed version differs from the repository pin. A passing run produces a normal `ai-multi-agent-platform/platform-conformance/v1` report with deployment profile `mcp-sdk-pinned` and records `mcp-python-sdk=2.1.1` in adapter versions.

CI retains the report as `platform-conformance-mcp` / `conformance-mcp.json` from the required `test` job. This does not make MCP a production dependency: the normal package dependencies remain MCP-free and the SDK stays in the optional `mcp` extra (also present in the development test environment).

## LiteLLM (`ENV-LITELLM`)

The LiteLLM profile requires the exact optional dependency `litellm==1.99.0`. It runs the repository's pinned real-library integration path. LiteLLM's supported `mock_response` mechanism exercises the installed library's request/response machinery without a paid provider, network credential or external model service, while the platform adapter preserves the canonical `ModelProvider` request/response boundary.

The profile fails closed when LiteLLM is absent or its installed version differs from the repository pin. A passing run produces the same versioned conformance report with deployment profile `litellm-pinned` and records `litellm=1.99.0` in adapter versions.

The existing required `litellm-compat` CI context runs this profile and retains `platform-conformance-litellm` / `conformance-litellm.json`.

## Boundary

Neither profile changes canonical lifecycle ownership or the reference single-node compatibility claim. Their reports make only the exact optional environment claim they actually execute. The reference-only platform must continue to install and operate without either optional package, while releases or deployments that claim these adapters have machine-readable evidence tied to the tested platform commit and exact adapter version.
