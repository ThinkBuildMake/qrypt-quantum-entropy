"""Quantum Entropy MCP Server and Secure Token Agent.

Layers (nothing imports a layer above it): transport -> service -> upstream -> storage.
`agent` is a separate process that talks to the server only over MCP.
"""
